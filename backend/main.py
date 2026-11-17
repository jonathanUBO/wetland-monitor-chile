import os
import sys
import json
import math
import logging
import io
import re
import unicodedata
import urllib.parse
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

import asyncio
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dateutil.relativedelta import relativedelta

from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ==========================================
# MODULE: UTILS & LOGGING
# ==========================================

def setup_logging(log_file: str = 'wetland_analysis.log', level: int = logging.INFO) -> logging.Logger:
    """Configure logging for the application."""
    logger = logging.getLogger('wetland_monitor')
    logger.setLevel(level)
    if logger.handlers:
        return logger
    
    # Ensure stdout handles UTF-8 gracefully on Windows without charmap encoding crashes
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    simple_formatter = logging.Formatter('%(message)s')
    
    # Check if we can write to log file, otherwise skip file logging
    try:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass # Skip file logging if permission denied
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)
    
    return logger

def format_analysis_summary(stats: dict, mode: str) -> str:
    """Format analysis statistics into human-readable string."""
    if not stats:
        return f"{mode}: No data available"
    def _fmt(val, code=".4f"):
        if val is None or val == 'N/A': return "N/A"
        try: return f"{float(val):{code}}"
        except Exception: return str(val)
    return f"""
{mode} Analysis Summary:
  Current Median: {_fmt(stats.get('median'))}
  Range: [{_fmt(stats.get('min'))}, {_fmt(stats.get('max'))}]
  Std Dev: {_fmt(stats.get('std'))}
  Data Points: {stats.get('count', 0)}
  CV: {_fmt(stats.get('cv'), '.2f')}%
""".strip()

def create_error_response(error: Exception, mode: str = None) -> dict:
    """Create standardized error response."""
    return {
        "status": "error",
        "error": str(error),
        "error_type": type(error).__name__,
        "mode": mode,
        "timestamp": datetime.now().isoformat()
    }

def log_process_stage(stage: str, mode: str = None, status: str = 'processing') -> str:
    """Create formatted log message for process stages."""
    icons = {'processing': '[*]', 'completed': '[OK]', 'error': '[ERR]', 'info': '[i]'}
    icon = icons.get(status, '')
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    if mode:
        msg = f"[{timestamp}] {icon}  {mode}"
        if stage: msg += f": {stage}"
    else:
        msg = f"[{timestamp}] {icon}  {stage}"
    
    if status == 'completed' and not stage:
        msg += " completed"
        
    return msg

# Initialize Logger
logger = setup_logging()

# ==========================================
# MODULE: CATALOGUE & GEOMETRY
# ==========================================

from app.catalogue import WetlandCatalogueDB, normalize_wetland_key, prune_disk_cache
from app.sar import analyze_sentinel1_sar

WETLANDS_JSON_PATH = Path(__file__).resolve().parent / "wetlands.json"
WETLANDS_DB_PATH = Path(__file__).resolve().parent / ".cache_analysis" / "wetlands.db"
_catalogue_db = WetlandCatalogueDB(WETLANDS_JSON_PATH, WETLANDS_DB_PATH)
_wetlands_cache = None
_wetlands_geom_by_key: Dict[str, dict] = {}

def _normalize_wetland_key(s: str) -> str:
    return normalize_wetland_key(s)

def load_wetlands_catalogue():
    """Return lightweight wetlands metadata backed by SQLite (avoids keeping 23MB+ JSON in RAM)."""
    return _catalogue_db.list_metadata()

def get_wetland_geometry(name_or_code: str) -> Optional[dict]:
    """Retrieve geometry for a single wetland on-demand from the SQLite catalogue."""
    return _catalogue_db.get_geometry(name_or_code)

# ==========================================
# MODULE: VALIDATORS
# ==========================================

from app.validators import (
    ValidationError, validate_date_range, count_geometry_vertices,
    simplify_geometry_if_needed, validate_geometry,
)
from app.statistics import (
    _is_finite_num, calculate_robust_statistics, calculate_sen_slope,
    calculate_mann_kendall, get_southern_season, detect_outliers,
    validate_temporal_coverage, calculate_trend_statistics,
)

def is_blank_image(png_bytes: Optional[bytes]) -> bool:
    """Check if a PNG image is empty, zero-sized, or entirely transparent/black."""
    if not png_bytes or len(png_bytes) < 300:
        return True
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes))
        arr = np.array(im)
        if arr.size == 0 or arr.max() == 0:
            return True
        if arr.ndim == 3 and arr.shape[2] == 4 and arr[:, :, 3].max() == 0:
            return True
        return False
    except Exception:
        return False

def compute_comparative_windows(start_date: str, end_date: str, valid_dates: Optional[List[str]] = None) -> Tuple[str, str, str, str, str, str]:
    """Compute canonical Initial and Final comparative time windows (s_start, s_end, e_start, e_end, start_year, end_year).
    Shared across perform_single_analysis and generate_wetland_report to guarantee 100% cache hit rate in _overlay_cache.
    """
    try:
        start_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
        end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
        if valid_dates:
            sorted_dates = sorted(d[:10] for d in valid_dates if d)
            earliest_dt = max(start_dt, datetime.strptime(sorted_dates[0], "%Y-%m-%d"))
            latest_dt = min(end_dt, datetime.strptime(sorted_dates[-1], "%Y-%m-%d"))
            if earliest_dt > latest_dt:
                earliest_dt, latest_dt = start_dt, end_dt
        else:
            earliest_dt, latest_dt = start_dt, end_dt

        start_year = str(earliest_dt.year)
        end_year = str(latest_dt.year)
        days_span = max(1, (latest_dt - earliest_dt).days)
        if days_span >= 730:
            s_start = earliest_dt.strftime("%Y-%m-%d")
            s_end = (earliest_dt + relativedelta(years=1)).strftime("%Y-%m-%d")
            e_start = (latest_dt - relativedelta(years=1)).strftime("%Y-%m-%d")
            e_end = latest_dt.strftime("%Y-%m-%d")
        else:
            half_days = max(15, days_span // 2)
            s_start = earliest_dt.strftime("%Y-%m-%d")
            s_end = (earliest_dt + timedelta(days=half_days)).strftime("%Y-%m-%d")
            e_start = (latest_dt - timedelta(days=half_days)).strftime("%Y-%m-%d")
            e_end = latest_dt.strftime("%Y-%m-%d")
        return s_start, s_end, e_start, e_end, start_year, end_year
    except Exception:
        sy = start_date[:4] if len(start_date) >= 4 else "Inicial"
        ey = end_date[:4] if len(end_date) >= 4 else "Final"
        return start_date[:10], start_date[:10], end_date[:10], end_date[:10], sy, ey

def fetch_thumbnail_bytes(mode: str, start_date: str, end_date: str, geometry: Optional[dict] = None, wetland_name: Optional[str] = None, token: Optional[str] = None) -> Optional[io.BytesIO]:
    """Fetch Sentinel-2 PNG thumbnail directly using get_overlay_bytes.
    Leverages in-memory cache, aspect ratio preservation, deduplication, and multi-tier cloud fallbacks.
    """
    if not geometry and wetland_name:
        geometry = get_wetland_geometry(wetland_name)
        
    if not geometry:
        return None

    try:
        norm_geom = validate_geometry(geometry)
        import shapely.geometry as sg
        geom = sg.shape(norm_geom)
        bounds = list(geom.bounds)
        content = get_overlay_bytes(mode, start_date, end_date, bounds, geometry=norm_geom, token=token)
        
        # If the specific initial window has no clear scenes at all, expand the window by 1 year max to conserve PU quota
        if not content or is_blank_image(content):
            try:
                e_dt = datetime.strptime(end_date[:10], "%Y-%m-%d") + relativedelta(years=1)
                content = get_overlay_bytes(mode, start_date, e_dt.strftime("%Y-%m-%d"), bounds, geometry=norm_geom, token=token)
            except Exception:
                pass
                
        if content and len(content) >= 500 and not is_blank_image(content):
            return io.BytesIO(content)
        return None
    except Exception as e:
        logger.warning(f"Failed fetching thumbnail for {mode} ({start_date} - {end_date}): {e}")
        return None

def download_image(url: str) -> Optional[io.BytesIO]:
    """Deprecated external URL image downloader disabled to prevent SSRF (Server-Side Request Forgery).
    Thumbnails are generated directly in-memory via fetch_thumbnail_bytes / get_overlay_bytes.
    """
    return None

def get_vis_params(mode: str) -> Dict[str, Any]:
    """Get visualization parameters."""
    if mode == "Hydrology": return {'min': -1, 'max': 1, 'palette': ['FF0000', 'FFFFFF', '0000FF']}
    elif mode == "Vegetation": return {'min': 0, 'max': 0.8, 'palette': ['FF0000', 'FFFF00', '00FF00', '006400']}
    elif mode == "WaterQuality": return {'min': -0.1,  'max': 0.5, 'palette': ['0000FF', '00FFFF', '00FF00', 'FFFF00', 'FF0000']}
    elif mode == "SoilVegetation": return {'min': 0, 'max': 0.8, 'palette': ['8B4513', 'CE7E45', 'FCD163', '90EE90', '22C55E', '006400']}
    elif mode == "AlgaeBloom": return {'min': -0.05, 'max': 0.2, 'palette': ['0000FF', '00FFFF', '00FF00', 'FFFF00', 'FF0000', '8B0000']}
    elif mode == "WaterRatio": return {'min': 0, 'max': 2, 'palette': ['FF0000', 'FFA500', 'FFFF00', 'FFFFFF', '00FFFF', '0000FF']}
    return {'min': 0, 'max': 1, 'palette': ['000000', 'FFFFFF']}

def get_index_name(mode: str) -> str:
    """Get the index name for a mode."""
    names = {'Hydrology': 'MNDWI', 'Vegetation': 'NDRE', 'WaterQuality': 'NDCI', 'SoilVegetation': 'SAVI', 'AlgaeBloom': 'FAI', 'WaterRatio': 'WRI'}
    return names.get(mode, mode)

def get_mode_description(mode: str) -> str:
    """Get description for each analysis mode."""
    descs = {
        'Hydrology': 'Análisis de humedad y cuerpos de agua superficial mediante índice MNDWI',
        'Vegetation': 'Análisis de salud vegetativa mediante índice NDRE (clorofila)',
        'WaterQuality': 'Análisis de calidad de agua y turbidez mediante índice NDCI',
        'SoilVegetation': 'Análisis de vegetación ajustado por influencia del suelo (SAVI)',
        'AlgaeBloom': 'Detección de floraciones algales mediante índice FAI',
        'WaterRatio': 'Ratio agua-tierra mediante índice WRI'
    }
    return descs.get(mode, mode)

def create_legend_image(mode: str) -> io.BytesIO:
    """Create a legend image for the specific mode (100% thread-safe without pyplot global state)."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import matplotlib.colors as mcolors
    import matplotlib.cm as cm

    params = get_vis_params(mode)
    palette_hex = [f"#{c}" for c in params['palette']]
    fig = Figure(figsize=(6, 1))
    fig.subplots_adjust(bottom=0.5)
    ax = fig.add_subplot(111)
    cmap = mcolors.LinearSegmentedColormap.from_list("custom_cmap", palette_hex)
    norm = mcolors.Normalize(vmin=params['min'], vmax=params['max'])
    cb = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), cax=ax, orientation='horizontal')
    cb.set_label(f'Valor {get_index_name(mode)}')
    img_buffer = io.BytesIO()
    canvas = FigureCanvasAgg(fig)
    canvas.print_png(img_buffer)
    fig.clf()
    img_buffer.seek(0)
    return img_buffer

def create_temporal_chart(time_series: List[Dict], mode: str) -> Optional[io.BytesIO]:
    """Create a temporal chart for a specific mode (100% thread-safe without pyplot global state)."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    valid_points = [p for p in time_series if _is_finite_num(p.get('value'))]
    if not valid_points: return None
    
    dates = [p['date'] for p in valid_points]
    values = [float(p['value']) for p in valid_points]
    outliers = [p for p in valid_points if p.get('is_outlier')]
    
    fig = Figure(figsize=(10, 4))
    ax = fig.add_subplot(111)
    ax.plot(dates, values, marker='o', linewidth=2, markersize=4, color='#0284c7', label='Observación Satelital')
    if outliers:
        outlier_dates = [p['date'] for p in outliers]
        outlier_vals = [float(p['value']) for p in outliers]
        ax.scatter(outlier_dates, outlier_vals, color='#ef4444', marker='x', s=40, zorder=5, label='Atípico (Filtro SCL)')
        ax.legend(loc='best', fontsize=8)

    ax.set_title(f'Serie Temporal - {mode}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Fecha', fontsize=10)
    ax.set_ylabel('Valor del Índice', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    if len(dates) > 20:
        nth = max(1, len(dates) // 10)
        for i, label in enumerate(ax.xaxis.get_ticklabels()):
            if i % nth != 0: label.set_visible(False)
            
    fig.tight_layout()
    img_buffer = io.BytesIO()
    canvas.print_png(img_buffer)
    fig.clf()
    img_buffer.seek(0)
    return img_buffer

# ==========================================
# MODULE: SITUATIONAL STATUS & ECOLOGICAL DIAGNOSIS
# ==========================================

from app.scoring import (
    calculate_index_situational_status,
    calculate_consolidated_wetland_status,
    infer_chile_macrozone,
)

def generate_wetland_report(wetland_name: str, wetland_metadata: Dict, analysis_results: Dict, start_date: str, end_date: str, token: Optional[str] = None) -> io.BytesIO:
    """Generate a comprehensive Word report for wetland analysis comparing initial and final periods."""
    doc = Document()
    header = doc.sections[0].header
    header.paragraphs[0].text = "WETLAND MONITOR - REPORTE DE ANÁLISIS COMPARATIVO"
    header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    header.paragraphs[0].runs[0].font.size = Pt(10)
    header.paragraphs[0].runs[0].font.bold = True
    header.paragraphs[0].runs[0].font.color.rgb = RGBColor(37, 99, 235)
    
    title = doc.add_heading(f'Reporte de Análisis: {wetland_name}', level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    doc.add_heading('Información del Humedal', level=2)
    meta_table = doc.add_table(rows=5, cols=2)
    meta_table.style = 'Light Grid Accent 1'
    meta_table.rows[0].cells[0].text = 'Nombre'
    meta_table.rows[0].cells[1].text = wetland_name
    meta_table.rows[1].cells[0].text = 'Región'
    meta_table.rows[1].cells[1].text = wetland_metadata.get('region', 'N/A')
    meta_table.rows[2].cells[0].text = 'Código'
    meta_table.rows[2].cells[1].text = wetland_metadata.get('code', 'N/A')
    meta_table.rows[3].cells[0].text = 'Coordenadas'
    meta_table.rows[3].cells[1].text = wetland_metadata.get('coordinates', 'N/A')
    meta_table.rows[4].cells[0].text = 'Fecha de Emisión'
    meta_table.rows[4].cells[1].text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    doc.add_paragraph()
    
    # Extract actual observed dates from analysis_results to anchor windows to real data
    obs_dates = []
    for m_res in analysis_results.values():
        if isinstance(m_res, dict) and 'time_series' in m_res:
            for pt in m_res['time_series']:
                if pt.get('value') is not None and pt.get('date'):
                    obs_dates.append(pt['date'])
    
    s_start, s_end, e_start, e_end, start_year, end_year = compute_comparative_windows(start_date, end_date, obs_dates)

    doc.add_heading('Período de Análisis Comparativo', level=2)
    p = doc.add_paragraph()
    p.add_run('Rango Completo: ').bold = True
    p.add_run(f'{start_date} a {end_date}\n')
    p.add_run(f'• Período Inicial ({start_year}): ').bold = True
    p.add_run(f'{s_start} a {s_end}\n')
    p.add_run(f'• Período Final ({end_year}): ').bold = True
    p.add_run(f'{e_start} a {e_end}')
    doc.add_paragraph()

    modes = ['Hydrology', 'Vegetation', 'WaterQuality', 'SoilVegetation', 'AlgaeBloom', 'WaterRatio']
    geometry = wetland_metadata.get('geometry') or get_wetland_geometry(wetland_name)
    if not geometry and wetland_metadata.get('bbox'):
        b = wetland_metadata['bbox']
        if isinstance(b, (list, tuple)) and len(b) == 4:
            geometry = {
                "type": "Polygon",
                "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]
            }
    import docx
    def safe_insert_picture(target_obj, img_stream, width=None, fallback_text="[Imagen no disponible]") -> bool:
        if not img_stream:
            return False
        try:
            if hasattr(img_stream, 'seek'):
                img_stream.seek(0)
            if isinstance(target_obj, docx.table._Cell):
                p = target_obj.paragraphs[0] if target_obj.paragraphs else target_obj.add_paragraph()
            elif hasattr(target_obj, 'add_paragraph'):
                p = target_obj.add_paragraph()
            elif hasattr(target_obj, 'add_run'):
                p = target_obj
            elif hasattr(target_obj, 'paragraphs'):
                p = target_obj.paragraphs[0]
            else:
                p = target_obj
            run = p.add_run()
            if width:
                run.add_picture(img_stream, width=width)
            else:
                run.add_picture(img_stream)
            return True
        except Exception as ex:
            logger.warning(f"Error inserting picture in DOCX: {ex}")
            try:
                p = target_obj.paragraphs[0] if isinstance(target_obj, docx.table._Cell) else (target_obj.add_paragraph() if hasattr(target_obj, 'add_paragraph') else target_obj)
                p.add_run(fallback_text).italic = True
            except Exception:
                pass
            return False

    thumbnails_map: Dict[Tuple[str, str], Optional[io.BytesIO]] = {}
    
    if geometry:
        import concurrent.futures
        tasks = []
        # Pre-fetch RGB (natural color satellite images) and all active modes
        prefetch_modes = ['RGB'] + [m for m in modes if m in analysis_results]
        for m in prefetch_modes:
            tasks.append((m, 'start', s_start, s_end))
            tasks.append((m, 'end', e_start, e_end))

        # Strict rate limit prevention: Use max 2 concurrent workers to never trigger CDSE HTTP 429
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_task = {
                executor.submit(fetch_thumbnail_bytes, m, s, e, geometry, wetland_name, token): (m, kind)
                for (m, kind, s, e) in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task):
                m, kind = future_to_task[future]
                try:
                    img_io = future.result()
                    if img_io:
                        thumbnails_map[(m, kind)] = img_io
                except Exception as ex:
                    logger.warning(f"Thumbnail prefetch error for {m} {kind}: {ex}")

    # Section: Natural Color Optical Satellite Imagery (RGB)
    rgb_start = thumbnails_map.get(('RGB', 'start'))
    rgb_end = thumbnails_map.get(('RGB', 'end'))
    if rgb_start or rgb_end:
        doc.add_heading('Registro Fotográfico Satelital (Color Natural RGB)', level=2)
        doc.add_paragraph('Comparativa visual óptica Sentinel-2 (L2A) en color verdadero entre el estado inicial y final del humedal.', style='Intense Quote')
        
        rgb_table = doc.add_table(rows=2, cols=2)
        rgb_table.autofit = True
        rgb_table.style = 'Table Grid'
        
        c_rgb_start = rgb_table.rows[0].cells[0]
        c_rgb_end = rgb_table.rows[0].cells[1]
        c_rgb_cap_start = rgb_table.rows[1].cells[0]
        c_rgb_cap_end = rgb_table.rows[1].cells[1]
        
        c_rgb_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if rgb_start and safe_insert_picture(c_rgb_start, rgb_start, width=Inches(2.8)):
            c_rgb_cap_start.text = f"Fotografía Inicial ({s_start[:10]} - {s_end[:10]})"
        else:
            c_rgb_cap_start.text = f"Fotografía Inicial ({s_start[:10]}): Sin imagen disponible"
        c_rgb_cap_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
        c_rgb_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if rgb_end and safe_insert_picture(c_rgb_end, rgb_end, width=Inches(2.8)):
            c_rgb_cap_end.text = f"Fotografía Final ({e_start[:10]} - {e_end[:10]})"
        else:
            c_rgb_cap_end.text = f"Fotografía Final ({e_end[:10]}): Sin imagen disponible"
        c_rgb_cap_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()

    # =========================================================================
    # SECTION: EXECUTIVE ECOLOGICAL DIAGNOSIS & CONSOLIDATED CONDITION (IMCE)
    # =========================================================================
    consolidated = calculate_consolidated_wetland_status(analysis_results)
    
    doc.add_heading('Diagnóstico Ejecutivo: Condición Ecológica Consolidada (IMCE)', level=2)
    doc.add_paragraph(
        'Evaluación sintética multivariable sustentada en el Proceso de Análisis Jerárquico (AHP, Saaty, 1980) y '
        'el marco de evaluación de Carácter Ecológico de la Convención Ramsar (Resolución IX.1) y US EPA NWCA. '
        'Integra funciones de respuesta no lineal para tres dimensiones biofísicas de Sentinel-2 MSI: '
        'Régimen Hidrológico, Vigor Vegetacional y Calidad Óptica de Aguas Abiertas.',
        style='Intense Quote'
    )
    
    score_table = doc.add_table(rows=4, cols=2)
    score_table.style = 'Light Grid Accent 1'
    
    score_table.rows[0].cells[0].text = 'Índice Multicriterio de Condición Ecológica (IMCE)'
    c_score = score_table.rows[0].cells[1]
    imce_val = consolidated.get('imce_score')
    if imce_val is not None:
        c_score.text = f"{imce_val:.1f} / 100 — {consolidated.get('health_badge', 'N/D')}"
    else:
        c_score.text = f"N/D — {consolidated.get('health_badge', 'DATOS INSUFICIENTES')}"
    c_score.paragraphs[0].runs[0].bold = True
    if consolidated.get('health_level') in ['optimal', 'good']:
        c_score.paragraphs[0].runs[0].font.color.rgb = RGBColor(16, 185, 129)
    elif consolidated.get('health_level') == 'alert':
        c_score.paragraphs[0].runs[0].font.color.rgb = RGBColor(245, 158, 11)
    elif consolidated.get('health_level') == 'unknown':
        c_score.paragraphs[0].runs[0].font.color.rgb = RGBColor(148, 163, 184)
    else:
        c_score.paragraphs[0].runs[0].font.color.rgb = RGBColor(239, 68, 68)

    score_table.rows[1].cells[0].text = 'Diagnóstico Situacional Dinámico'
    c_diag = score_table.rows[1].cells[1]
    diag_obj = consolidated.get('diagnosis', {})
    c_diag.text = f"{diag_obj.get('title', 'N/D')}\n{diag_obj.get('description', '')}"

    score_table.rows[2].cells[0].text = 'Desglose Dimensional (AHP)'
    dims = consolidated.get('dimensions', {})
    h_s = dims.get('hydrology', {}).get('score')
    v_s = dims.get('vegetation', {}).get('score')
    q_s = dims.get('water_quality', {}).get('score')
    h_str = f"{h_s:.1f} / 100" if h_s is not None else "Sin Datos"
    v_str = f"{v_s:.1f} / 100" if v_s is not None else "Sin Datos"
    q_str = f"{q_s:.1f} / 100" if q_s is not None else ("N/A" if dims.get('water_quality', {}).get('weight', 0) == 0 else "Sin Datos")
    dim_txt = (
        f"• Régimen Hidrológico (MNDWI/WRI): {h_str} (Peso: {dims.get('hydrology', {}).get('weight', 35)}%)\n"
        f"• Vigor y Cobertura Vegetal (NDRE/SAVI): {v_str} (Peso: {dims.get('vegetation', {}).get('weight', 35)}%)\n"
        f"• Calidad de Agua Superficial (NDCI/FAI): {q_str} (Peso: {dims.get('water_quality', {}).get('weight', 30)}% - {dims.get('water_quality', {}).get('label', '')})"
    )
    score_table.rows[2].cells[1].text = dim_txt

    score_table.rows[3].cells[0].text = 'Dictamen Normativo Ley 21.202 / SMA'
    c_verdict = score_table.rows[3].cells[1]
    c_verdict.text = diag_obj.get('regulatory_verdict', 'N/A')
    c_verdict.paragraphs[0].runs[0].bold = True
    doc.add_paragraph()

    # SECTION: NOTAS METODOLÓGICAS Y LIMITACIONES PERICIALES
    doc.add_heading('Alcances Metodológicos y Limitaciones Periciales (Protocolo Científico)', level=2)
    p_lim = doc.add_paragraph()
    p_lim.add_run('1. Naturaleza de los Datos Satelitales: ').bold = True
    p_lim.add_run(
        'Los índices espectrales derivados de Sentinel-2 MSI (resolución nativa de 10 m a 20 m por píxel) '
        'constituyen estimadores biofísicos indirectos calibrados radiométricamente a nivel de reflectancia en superficie (L2A, BOA). '
        'Proveen evidencia continua de la dinámica espacio-temporal del humedal.\n'
    )
    p_lim.add_run('2. Condicionalidad Física de Calidad de Agua: ').bold = True
    p_lim.add_run(
        'Los índices NDCI y FAI se evalúan exclusivamente en presencia de lámina de agua libre verificable '
        '(MNDWI ≥ -0.15). En periodos o zonas de estiaje sin agua superficial, la dimensión de calidad se inhibe para '
        'evitar falsos positivos de eutrofización provocados por la reflectancia de vegetación terrestre o suelo seco.\n'
    )
    p_lim.add_run('3. Criterio de Suelos Hídricos (Ley N° 21.202): ').bold = True
    p_lim.add_run(
        'De acuerdo con la Guía Técnica de Delimitación y Caracterización de Humedales Urbanos de Chile (MMA), '
        'la teledetección satelital acredita fehacientemente los criterios de régimen hidrológico de saturación y '
        'presencia de vegetación hidrófita. En controversias de borde o deslindes milimétricos, los resultados satelitales '
        'deben complementarse con muestreos edafológicos in situ (presencia de rasgos redoximórficos / gleyzación).'
    )
    doc.add_paragraph()

    # =========================================================================
    # SECTION: MATRIZ DE ESTADOS POR ÍNDICE ESPECTRAL
    # =========================================================================
    doc.add_heading('Matriz Comparativa de Estado Situacional por Índice', level=2)
    doc.add_paragraph(
        'Declaración del estado biofísico particular determinado a partir de umbrales empíricos validados en la literatura científica para Sentinel-2 MSI.',
        style='Intense Quote'
    )
    
    mat_table = doc.add_table(rows=1, cols=6)
    mat_table.style = 'Light List Accent 1'
    hdr_cells = mat_table.rows[0].cells
    hdr_cells[0].text = 'Índice'
    hdr_cells[1].text = f'Inicial ({start_year})'
    hdr_cells[2].text = f'Final ({end_year})'
    hdr_cells[3].text = 'Variación (Δ)'
    hdr_cells[4].text = 'Estado Situacional Declarado'
    hdr_cells[5].text = 'Criterio / Umbral Científico'
    for hc in hdr_cells:
        hc.paragraphs[0].runs[0].bold = True

    for m in modes:
        if m not in analysis_results: continue
        m_res = analysis_results[m]
        m_stats = m_res.get('stats', {})
        i_val = m_stats.get('initial') if m_stats.get('initial') is not None else m_stats.get('last')
        f_val = m_stats.get('current')
        
        i_num = float(i_val) if i_val is not None else 0.0
        f_num = float(f_val) if f_val is not None else 0.0
        d_raw = m_stats.get('trend_delta')
        if d_raw is not None:
            try: d_val = float(d_raw)
            except Exception: d_val = 0.0
        else:
            d_val = f_num - i_num
        
        status_info = m_res.get('situational_status') or calculate_index_situational_status(m, f_num, d_val)
        
        row_cells = mat_table.add_row().cells
        row_cells[0].text = f"{get_index_name(m)} ({m})"
        row_cells[1].text = f"{i_val:.4f}" if i_val is not None else "---"
        row_cells[2].text = f"{f_val:.4f}" if f_val is not None else "---"
        
        row_cells[3].text = f"{d_val:+.4f}"
        delta_color = RGBColor(34, 197, 94) if d_val >= 0 else RGBColor(239, 68, 68)
        row_cells[3].paragraphs[0].runs[0].font.color.rgb = delta_color
        
        c_status = row_cells[4]
        c_status.text = f"{status_info.get('status_label', 'N/D')}"
        c_status.paragraphs[0].runs[0].bold = True
        if status_info.get('status_level') in ['optimal', 'good']:
            c_status.paragraphs[0].runs[0].font.color.rgb = RGBColor(16, 185, 129)
        elif status_info.get('status_level') == 'alert':
            c_status.paragraphs[0].runs[0].font.color.rgb = RGBColor(245, 158, 11)
        else:
            c_status.paragraphs[0].runs[0].font.color.rgb = RGBColor(239, 68, 68)
            
        row_cells[5].text = f"{status_info.get('threshold', '')}\n({status_info.get('scientific_basis', '')})"

    doc.add_paragraph()

    doc.add_heading('Resultados del Análisis Espectral Detallado', level=2)
    
    for mode in modes:
        if mode not in analysis_results: continue
        res = analysis_results[mode]
        stats = res.get('stats', {})
        maps = res.get('maps', {})
        
        initial_val = stats.get('initial') if stats.get('initial') is not None else stats.get('last')
        final_val = stats.get('current')
        trend = stats.get('trend', 0.0) or 0.0
        trend_delta = stats.get('trend_delta')
        if trend_delta is None:
            trend_delta = (final_val - initial_val) if (final_val is not None and initial_val is not None) else trend
        try: trend_delta = float(trend_delta)
        except Exception: trend_delta = 0.0

        initial_std = stats.get('initial_std')
        final_std = stats.get('current_std')
        sen_slope = stats.get('sen_slope')
        try: sen_slope = float(sen_slope) if sen_slope is not None else 0.0
        except Exception: sen_slope = 0.0
        data_count = stats.get('data_count', 0)
        outlier_count = stats.get('outlier_count', 0)
        status_info = res.get('situational_status') or calculate_index_situational_status(mode, final_val or 0.0, trend_delta)
        
        doc.add_heading(f'{mode} - {get_index_name(mode)}', level=3)
        doc.add_paragraph(
            f"Estado Declarado: {status_info.get('status_label', 'N/D')} ({status_info.get('threshold', '')}) — {status_info.get('scientific_basis', '')}\n"
            f"Diagnóstico Biofísico: {status_info.get('description', '')}",
            style='Intense Quote'
        )
        
        st_table = doc.add_table(rows=9, cols=2)
        st_table.style = 'Light List Accent 1'
        st_table.rows[0].cells[0].text = f'Valor Período Inicial ({start_year})'
        st_table.rows[0].cells[1].text = f"{initial_val:.4f}" if initial_val is not None else "---"
        st_table.rows[1].cells[0].text = f'Valor Período Final ({end_year})'
        st_table.rows[1].cells[1].text = f"{final_val:.4f}" if final_val is not None else "---"
        
        st_table.rows[2].cells[0].text = 'Variación Absoluta (Δ Mediana)'
        trend_cell = st_table.rows[2].cells[1]
        trend_cell.text = f"{trend_delta:+.4f} (Δ Mediana)"
        color = RGBColor(34, 197, 94) if trend_delta >= 0 else RGBColor(239, 68, 68)
        trend_cell.paragraphs[0].runs[0].font.color.rgb = color

        st_table.rows[3].cells[0].text = 'Tasa de Cambio Anual (Theil-Sen / Mann-Kendall)'
        mk_p = stats.get('mk_p_value')
        mk_sig = stats.get('trend_significant')
        if mk_p is not None and isinstance(mk_p, (int, float)):
            sig_label = f"Significativa (p={mk_p:.4f})" if mk_sig else f"No significativa (p={mk_p:.4f})"
            st_table.rows[3].cells[1].text = f"{sen_slope:+.4f} / año · {sig_label}"
        else:
            st_table.rows[3].cells[1].text = f"{sen_slope:+.4f} / año"
        
        st_table.rows[4].cells[0].text = f'Desviación Estándar Inicial ({start_year})'
        if initial_std is not None and initial_std != 0:
            st_table.rows[4].cells[1].text = f"{initial_std:.4f}"
        elif final_std is not None:
            st_table.rows[4].cells[1].text = f"{final_std:.4f}"
        else:
            st_table.rows[4].cells[1].text = "---"
        
        st_table.rows[5].cells[0].text = f'Desviación Estándar Final ({end_year})'
        st_table.rows[5].cells[1].text = f"{final_std:.4f}" if final_std is not None else "---"
        
        st_table.rows[6].cells[0].text = 'Total Puntos de Datos'
        st_table.rows[6].cells[1].text = str(data_count)
        
        st_table.rows[7].cells[0].text = 'Valores Atípicos Detectados'
        st_table.rows[7].cells[1].text = str(outlier_count)

        st_table.rows[8].cells[0].text = 'Estado Situacional Declarado'
        c_st = st_table.rows[8].cells[1]
        c_st.text = f"{status_info['status_label']} ({status_info['threshold']})"
        c_st.paragraphs[0].runs[0].bold = True
        doc.add_paragraph()
        
        doc.add_heading(f'Mapas Comparativos del Índice {get_index_name(mode)}', level=4)
        
        img_start = thumbnails_map.get((mode, 'start'))
        img_end = thumbnails_map.get((mode, 'end'))
        
        # Fallback to URL only if pre-fetch did not produce it
        if not img_start and 'start_year' in maps and 'thumb_url' in maps['start_year']:
            img_start = download_image(maps['start_year']['thumb_url'])
            
        if not img_end and 'end_year' in maps and 'thumb_url' in maps['end_year']:
            img_end = download_image(maps['end_year']['thumb_url'])
            
        if img_start or img_end:
            map_table = doc.add_table(rows=2, cols=2)
            map_table.autofit = True
            map_table.style = 'Table Grid'
            
            c_start = map_table.rows[0].cells[0]
            c_end = map_table.rows[0].cells[1]
            c_cap_start = map_table.rows[1].cells[0]
            c_cap_end = map_table.rows[1].cells[1]
            
            c_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if img_start and safe_insert_picture(c_start, img_start, width=Inches(2.8)):
                c_cap_start.text = f"Mapa Inicial ({s_start[:10]} - {s_end[:10]})"
            else:
                c_cap_start.text = f"Mapa Inicial ({s_start[:10]}): Sin imagen disponible"
            c_cap_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                
            c_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if img_end and safe_insert_picture(c_end, img_end, width=Inches(2.8)):
                c_cap_end.text = f"Mapa Final ({e_start[:10]} - {e_end[:10]})"
            else:
                c_cap_end.text = f"Mapa Final ({e_end[:10]}): Sin imagen disponible"
            c_cap_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                
        legend = create_legend_image(mode)
        if legend and safe_insert_picture(doc, legend, width=Inches(5)):
            doc.add_paragraph("Escala de Valores (Válida para ambos mapas)", style='Caption')
            
        doc.add_heading('Evolución Temporal', level=4)
        ts = res.get('time_series', [])
        if ts:
            chart = create_temporal_chart(ts, mode)
            if chart:
                safe_insert_picture(doc, chart, width=Inches(6))
            
        doc.add_page_break()

    audit_trail = analysis_results.get("audit_trail") or {}
    doc.add_heading('Anexo de Trazabilidad Técnica y Auditoría', level=2)
    audit_table = doc.add_table(rows=0, cols=2)
    audit_table.style = 'Table Grid'
    audit_rows = [
        ("Sensor Satelital", audit_trail.get("sensor", "Sentinel-2 L2A (ESA Copernicus CDSE)")),
        ("Enmascaramiento de Nubes", audit_trail.get("cloud_mask", "SCL (clases 3, 8, 9, 10, 11 excluidas)")),
        ("Macrozona Aplicada", audit_trail.get("macrozone_label") or audit_trail.get("macrozone") or "Centro / Mediterránea"),
        ("Superficie Oficial Analizada", f"{audit_trail['area_ha']:.2f} ha ({audit_trail['area_km2']:.4f} km²)" if audit_trail.get("area_ha") is not None else "Calculada por polígono AOI"),
        ("Hash Geometría (SHA-256)", audit_trail.get("geometry_sha256", "No disponible")),
        ("Cobertura Promedio Libre de Nubes", f"{audit_trail['mean_cloud_free_pct']:.1f}%" if audit_trail.get("mean_cloud_free_pct") is not None else "N/D"),
        ("Timestamp Generación (UTC)", audit_trail.get("generated_at_utc", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))),
    ]
    for label, val in audit_rows:
        row_cells = audit_table.add_row().cells
        row_cells[0].text = str(label)
        row_cells[1].text = str(val)
        
    footer = doc.sections[0].footer
    footer.paragraphs[0].text = f"Generado por WETLAND MONITOR | {datetime.now().strftime('%Y-%m-%d')}"
    footer.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraphs[0].runs[0].font.size = Pt(8)
    footer.paragraphs[0].runs[0].font.color.rgb = RGBColor(128, 128, 128)
    
    doc_buffer = io.BytesIO()
    doc.save(doc_buffer)
    doc_buffer.seek(0)
    return doc_buffer

# ==========================================
# APP & GEE CONFIGURATION
# ==========================================

app = FastAPI(title="GEOINT Wetland Monitor API")

_cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None if _cors_origins_env else r"https://.*\.(vercel\.app|onrender\.com)",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CDSE-Username", "X-CDSE-Password", "X-CDSE-Client-Id", "X-CDSE-Client-Secret"],
)

from dotenv import load_dotenv
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path)
load_dotenv()

# Institutional server credentials (from environment)
CDSE_SERVER_USERNAME = os.getenv("CDSE_USERNAME", "").strip()
CDSE_SERVER_PASSWORD = os.getenv("CDSE_PASSWORD", "").strip()
CDSE_SERVER_CLIENT_ID = os.getenv("CDSE_CLIENT_ID", "").strip()
CDSE_SERVER_CLIENT_SECRET = os.getenv("CDSE_CLIENT_SECRET", "").strip()

def is_server_configured() -> bool:
    """Return True if institutional credentials are configured on the server."""
    has_pass = bool(CDSE_SERVER_USERNAME and CDSE_SERVER_PASSWORD)
    has_oauth = bool(CDSE_SERVER_CLIENT_ID and CDSE_SERVER_CLIENT_SECRET)
    return has_pass or has_oauth

# Robust HTTP session with connection pooling and retries
http_session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False
)
http_session.mount("https://", HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20))

# Thread-safe multi-tenant token cache keyed by credentials
_cdse_token_cache: Dict[str, Dict[str, Any]] = {}
_cdse_token_lock = threading.Lock()

def invalidate_cdse_token(key: Optional[str] = None):
    """Invalidate token cache for a key or all keys."""
    with _cdse_token_lock:
        if key:
            _cdse_token_cache.pop(key, None)
        else:
            _cdse_token_cache.clear()

def get_cdse_token(
    token: Optional[str] = None,
    username: str = "",
    password: str = "",
    client_id: str = "",
    client_secret: str = "",
    bearer_token: str = ""
) -> str:
    """Retrieve OAuth token for Copernicus Data Space Ecosystem.
    Resolution priority:
    1. Direct bearer token if supplied
    2. Per-request credentials passed in parameters / headers
    3. Institutional server credentials from environment
    """
    now = time.time()

    if token:
        return token
    if bearer_token:
        clean_b = bearer_token.replace("Bearer ", "").strip()
        if clean_b:
            return clean_b

    u = username.strip() or CDSE_SERVER_USERNAME
    p = password.strip() or CDSE_SERVER_PASSWORD
    cid = client_id.strip() or CDSE_SERVER_CLIENT_ID
    csec = client_secret.strip() or CDSE_SERVER_CLIENT_SECRET

    if not (u and p) and not (cid and csec):
        raise HTTPException(
            401,
            detail="Credenciales de Copernicus CDSE no configuradas. Por favor ingresa tus credenciales en el panel lateral o define variables institucionales en el servidor."
        )

    cache_key = hashlib.sha256(
        (f"pass:{u}:{p}" if (u and p) else f"oauth:{cid}:{csec}").encode("utf-8")
    ).hexdigest()

    with _cdse_token_lock:
        entry = _cdse_token_cache.get(cache_key)
        if entry and now < (entry["expires_at"] - 60):
            return entry["token"]

    token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

    try:
        if u and p:
            from oauthlib.oauth2 import LegacyApplicationClient
            from requests_oauthlib import OAuth2Session
            client = LegacyApplicationClient(client_id="cdse-public")
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, username=u, password=p)
        else:
            from oauthlib.oauth2 import BackendApplicationClient
            from requests_oauthlib import OAuth2Session
            client = BackendApplicationClient(client_id=cid)
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, client_id=cid, client_secret=csec)

        access_token = token_resp['access_token']
        expires_in = float(token_resp.get('expires_in', 600))

        with _cdse_token_lock:
            if len(_cdse_token_cache) > 100:
                _cdse_token_cache.pop(next(iter(_cdse_token_cache)), None)
            new_entry = {
                "token": access_token,
                "expires_at": now + expires_in
            }
            _cdse_token_cache[cache_key] = new_entry
        return access_token
    except Exception as e:
        err_msg = str(e)
        if "invalid_grant" in err_msg.lower() or "unauthorized" in err_msg.lower():
            raise HTTPException(401, detail="Credenciales incorrectas de Copernicus CDSE. Verifique su correo/clave o Client ID/Secret.")
        raise HTTPException(502, detail=f"Error al conectar con Copernicus OAuth: {err_msg}")

class AnalysisRequest(BaseModel):
    geojson: Dict[str, Any]
    startDate: str
    endDate: str
    projectId: Optional[str] = None
    mode: str = "Hydrology"
    wetlandName: Optional[str] = None
    region: Optional[str] = None
    macrozone: Optional[str] = None

def normalize_index_value(value, mode):
    """Safely validate and return index values for charting without destructive clamping.
    Preserves authentic negative signatures (e.g. open water in NDRE, oligotrophic clarity in FAI).
    """
    if value is None: return None
    try:
        f = float(value)
        if not math.isfinite(f): return None
        return f
    except (ValueError, TypeError):
        return None

def hex_to_rgb01(hex_str: str) -> List[float]:
    """Convert hex color string to normalized RGB float array [0, 1]."""
    hex_str = hex_str.lstrip('#')
    return [
        round(int(hex_str[0:2], 16) / 255.0, 4),
        round(int(hex_str[2:4], 16) / 255.0, 4),
        round(int(hex_str[4:6], 16) / 255.0, 4)
    ]

def get_optimal_aggregation_params(start_date: str, end_date: str, aoi_geom: Any = None) -> Dict[str, Any]:
    """Calculate optimal time interval and spatial resolution based on date range and polygon size.
    Prevents exceeding Copernicus 1500 intervals limit and aggressively minimizes Processing Units (PUs) consumption.
    """
    try:
        d1 = datetime.strptime(start_date[:10], "%Y-%m-%d")
        d2 = datetime.strptime(end_date[:10], "%Y-%m-%d")
        days = max(1, abs((d2 - d1).days))
        years = days / 365.25
    except Exception:
        years = 2.0
        days = 730

    # Calculate true geodesic polygon area in km2 (not Bounding Box area) to scale resolution accurately
    area_km2 = 2.0
    if aoi_geom is not None:
        try:
            import shapely.geometry as sg
            g = sg.shape(aoi_geom) if isinstance(aoi_geom, dict) else aoi_geom
            minx, miny, maxx, maxy = g.bounds
            lat_mid = (miny + maxy) / 2.0
            cos_lat = max(0.1, abs(math.cos(math.radians(lat_mid))))
            poly_area_km2 = g.area * (111.32 * cos_lat) * 110.57
            if poly_area_km2 > 0.0001:
                area_km2 = max(0.01, poly_area_km2)
            else:
                w_km = abs(maxx - minx) * 111.32 * cos_lat
                h_km = abs(maxy - miny) * 110.57
                area_km2 = max(0.01, w_km * h_km)
        except Exception:
            area_km2 = 5.0

    # Dynamic PU optimization: large polygons don't need sub-20m resolution for regional ecological trends
    if years <= 2.0:
        interval = "P5D"
        res_deg = 0.00020 if area_km2 < 10.0 else (0.00030 if area_km2 < 50.0 else 0.00045)
    elif years <= 5.0:
        interval = "P5D"
        res_deg = 0.00027 if area_km2 < 10.0 else (0.00035 if area_km2 < 50.0 else 0.00050)
    elif years <= 8.0:
        interval = "P5D"   # ~584 intervals max, well under 1500 limit
        res_deg = 0.00032 if area_km2 < 10.0 else 0.00042
    else:
        # 8 to 10+ years
        interval = "P10D"  # ~365 intervals max
        res_deg = 0.00035 if area_km2 < 10.0 else 0.00048

    return {
        "interval": interval,
        "deg_res": res_deg,
        "years": round(years, 2),
        "area_km2": round(area_km2, 2)
    }

MODE_TO_UNIFIED_OUTPUT = {
    'Hydrology': 'MNDWI',
    'Vegetation': 'NDRE',
    'WaterQuality': 'NDCI',
    'SoilVegetation': 'SAVI',
    'AlgaeBloom': 'FAI',
    'WaterRatio': 'WRI'
}

def get_unified_statistical_evalscript() -> str:
    """Generate unified Sentinel-2 Evalscript for CDSE Statistical API.
    Computes all 6 remote sensing indices simultaneously in a SINGLE execution:
    - MNDWI (Hydrology)
    - NDRE (Vegetation)
    - NDCI (WaterQuality)
    - SAVI (SoilVegetation)
    - FAI (AlgaeBloom, Hu 2009 calibrated to MSI central wavelengths)
    - WRI (WaterRatio)
    With pixel-level SCL quality masking:
    Excludes no-data (dataMask===0, SCL===0), saturated / sun glint (1), cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11).
    """
    return """//VERSION=3
function setup() {
  return {
    input: ["B03", "B04", "B05", "B08", "B11", "SCL", "dataMask"],
    output: [
      { id: "MNDWI", bands: 1, sampleType: "FLOAT32" },
      { id: "NDRE",  bands: 1, sampleType: "FLOAT32" },
      { id: "NDCI",  bands: 1, sampleType: "FLOAT32" },
      { id: "SAVI",  bands: 1, sampleType: "FLOAT32" },
      { id: "FAI",   bands: 1, sampleType: "FLOAT32" },
      { id: "WRI",   bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}

function evaluatePixel(sample) {
  // SCL Quality Filter: mask no data (0), saturated/sun glint (1), cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11)
  if (sample.dataMask === 0 || sample.SCL === 0 || sample.SCL === 1 || sample.SCL === 3 || sample.SCL === 8 || sample.SCL === 9 || sample.SCL === 10 || sample.SCL === 11) {
    return {
      MNDWI: [NaN], NDRE: [NaN], NDCI: [NaN],
      SAVI: [NaN],  FAI: [NaN],  WRI: [NaN],
      dataMask: [0]
    };
  }

  function safeRatio(num, den) {
    if (den === 0 || !isFinite(den)) return NaN;
    let r = num / den;
    return isFinite(r) ? r : NaN;
  }

  let mndwi = safeRatio(sample.B03 - sample.B11, sample.B03 + sample.B11);
  let wri   = safeRatio(sample.B03 + sample.B04, sample.B08 + sample.B11);

  // Physical intra-polygon water mask for optical water quality (NDCI & FAI):
  // Prevents shoreline/terrestrial vegetation reflectance from biasing water column chlorophyll-a statistics
  let isWaterPixel = (isFinite(mndwi) && mndwi >= -0.15) || (isFinite(wri) && wri >= 0.85);
  let ndci  = isWaterPixel ? safeRatio(sample.B05 - sample.B04, sample.B05 + sample.B04) : NaN;
  let faiVal = sample.B08 - (sample.B04 + (sample.B11 - sample.B04) * 0.1873);
  let fai   = (isWaterPixel && isFinite(faiVal)) ? faiVal : NaN;

  // Physical intra-polygon vegetated/emergent mask for NDRE & SAVI:
  // Excludes pure deep open-water pixels (MNDWI > 0.35 and B08 < 0.08) so open water absorption does not depress riparian/palustrine vegetation medians
  let isDeepOpenWater = isFinite(mndwi) && mndwi > 0.35 && sample.B08 < 0.08;
  let ndre  = isDeepOpenWater ? NaN : safeRatio(sample.B08 - sample.B05, sample.B08 + sample.B05);
  let saviDen = sample.B08 + sample.B04 + 0.5;
  let savi  = isDeepOpenWater ? NaN : safeRatio((sample.B08 - sample.B04) * 1.5, saviDen);

  return {
    MNDWI: [mndwi],
    NDRE:  [ndre],
    NDCI:  [ndci],
    SAVI:  [savi],
    FAI:   [fai],
    WRI:   [wri],
    dataMask: [1]
  };
}"""

def get_evalscript(mode: str, is_process: bool = True) -> str:
    """Generate Sentinel-2 Evalscript for CDSE Process API (visual map tiles and overlay PNGs).
    Scientifically audited and calibrated:
    1. FAI (Floating Algae Index) calibrated to Sentinel-2 MSI exact central wavelengths:
       Factor = (842 - 665) / (1610 - 665) = 177 / 945 = 0.1873 (Hu, 2009).
    2. Pixel-level quality filtering using Sen2Cor Scene Classification Layer (SCL):
       Excludes no data (0), saturated / sun glint (1), cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11).
    """
    bands_map = {
        'Hydrology': ('"B03", "B11"', 'let den = sample.B03 + sample.B11; let val = (den !== 0 && isFinite(den)) ? (sample.B03 - sample.B11) / den : NaN;'),
        'Vegetation': ('"B08", "B05"', 'let den = sample.B08 + sample.B05; let val = (den !== 0 && isFinite(den)) ? (sample.B08 - sample.B05) / den : NaN;'),
        'WaterQuality': ('"B04", "B05"', 'let den = sample.B05 + sample.B04; let val = (den !== 0 && isFinite(den)) ? (sample.B05 - sample.B04) / den : NaN;'),
        'SoilVegetation': ('"B04", "B08"', 'let den = sample.B08 + sample.B04 + 0.5; let val = (den !== 0 && isFinite(den)) ? ((sample.B08 - sample.B04) / den) * 1.5 : NaN;'),
        'AlgaeBloom': ('"B04", "B08", "B11"', 'let val = sample.B08 - (sample.B04 + (sample.B11 - sample.B04) * 0.1873);'),
        'WaterRatio': ('"B03", "B04", "B08", "B11"', 'let den = sample.B08 + sample.B11; let val = (den !== 0 && isFinite(den)) ? (sample.B03 + sample.B04) / den : NaN;')
    }

    if mode == "RGB":
        return """//VERSION=3
function setup() {
  return { input: ["B04", "B03", "B02", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(sample) {
  if (sample.dataMask === 0) return [0, 0, 0, 0];
  return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02, 1.0];
}"""

    # Process API (Visual map tiles / PNG) using ColorRampVisualizer with SCL Quality Masking
    vis_params = get_vis_params(mode)
    palette = vis_params.get('palette', ['000000', 'FFFFFF'])
    min_v = vis_params.get('min', -1.0)
    max_v = vis_params.get('max', 1.0)

    ramp_items = []
    n_colors = len(palette)
    for i, hex_code in enumerate(palette):
        val = min_v + i * (max_v - min_v) / (n_colors - 1)
        rgb = hex_to_rgb01(hex_code)
        ramp_items.append(f"[{val:.4f}, [{rgb[0]}, {rgb[1]}, {rgb[2]}]]")
    ramp_js = "[\n  " + ",\n  ".join(ramp_items) + "\n]"

    in_bands, expr = bands_map.get(mode, ('"B04", "B08"', 'let den = sample.B08 + sample.B04; let val = (den !== 0 && isFinite(den)) ? (sample.B08 - sample.B04) / den : NaN;'))

    return f"""//VERSION=3
const ramp = {ramp_js};
const visualizer = new ColorRampVisualizer(ramp);

function setup() {{
  return {{
    input: [{in_bands}, "SCL", "dataMask"],
    output: {{ bands: 4 }}
  }};
}}

function evaluatePixel(sample) {{
  // 1. Mask no-data boundary
  if (sample.dataMask === 0) return [0, 0, 0, 0];
  // 2. SCL Quality Filter: mask no-data (0), saturated/sun glint (1), cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11)
  if (sample.SCL === 0 || sample.SCL === 1 || sample.SCL === 3 || sample.SCL === 8 || sample.SCL === 9 || sample.SCL === 10 || sample.SCL === 11) {{
    return [0, 0, 0, 0];
  }}
  {expr}
  if (!isFinite(val)) return [0, 0, 0, 0];
  let rgb = visualizer.process(val);
  return [rgb[0], rgb[1], rgb[2], 0.85];
}}"""


def analyze_period_unified(aoi, start_date, end_date, token: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Perform a SINGLE unified Copernicus Statistical API query that retrieves all 6 spectral indices simultaneously.
    Saves ~95% of Processing Units (PUs) compared to looping over each mode individually.
    """
    token = token or get_cdse_token()
    url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    evalscript = get_unified_statistical_evalscript()
    opt_params = get_optimal_aggregation_params(start_date, end_date, aoi_geom=aoi)

    import shapely.geometry as sg
    import concurrent.futures

    try:
        geom = sg.shape(aoi)
    except Exception:
        return {}

    minx, miny, maxx, maxy = geom.bounds
    lon_span = abs(maxx - minx)
    lat_span = abs(maxy - miny)

    # Max chunk size: 0.22 degrees (~24km). If polygon fits within 24km, do 1 single request to save PUs!
    max_chunk_deg = 0.22
    if lon_span > 1.5 or lat_span > 1.5:
        max_chunk_deg = max(lon_span, lat_span) / 8.0

    deg_res = max(opt_params["deg_res"], max_chunk_deg / 1500.0)

    chunks = []
    # If the wetland fits within a single chunk, do not split — saves 75% API calls and PUs!
    if lon_span <= max_chunk_deg and lat_span <= max_chunk_deg:
        chunks = [geom]
    else:
        x = minx
        while x < maxx:
            y = miny
            while y < maxy:
                box = sg.box(x, y, x + max_chunk_deg, y + max_chunk_deg)
                intersection = geom.intersection(box)
                if not intersection.is_empty:
                    if intersection.geom_type == 'Polygon':
                        chunks.append(intersection)
                    elif intersection.geom_type == 'MultiPolygon':
                        for poly in intersection.geoms:
                            if not poly.is_empty and poly.area > 1e-9:
                                chunks.append(poly)
                    elif intersection.geom_type == 'GeometryCollection':
                        for g in intersection.geoms:
                            if g.geom_type == 'Polygon' and not g.is_empty and g.area > 1e-9:
                                chunks.append(g)
                            elif g.geom_type == 'MultiPolygon':
                                for poly in g.geoms:
                                    if not poly.is_empty and poly.area > 1e-9:
                                        chunks.append(poly)
                y += max_chunk_deg
            x += max_chunk_deg

    if not chunks:
        chunks = [geom]

    def fetch_chunk(chunk_geom):
        safe_geom = simplify_geometry_if_needed(chunk_geom, max_vertices=1200)
        payload = {
            "input": {
                "bounds": {
                    "geometry": safe_geom,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                        "maxCloudCoverage": 30
                    },
                    "processing": {
                        "upsampling": "BILINEAR",
                        "downsampling": "BILINEAR"
                    }
                }]
            },
            "aggregation": {
                "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                "aggregationInterval": {"of": opt_params["interval"]},
                "evalscript": evalscript,
                "resx": deg_res,
                "resy": deg_res
            },
            "calculations": {
                "default": {
                    "percentiles": {
                        "k": [10.0, 50.0, 90.0]
                    }
                }
            }
        }
        try:
            resp = http_session.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=45
            )
            if resp.status_code != 200:
                err_text = resp.text
                logger.error(f"CDSE API Error on unified chunk ({resp.status_code}): {err_text[:300]}")
                err_upper = err_text.upper()
                if resp.status_code == 401 or "UNAUTHORIZED" in err_upper:
                    raise ValidationError("Credenciales o token de acceso de Copernicus CDSE rechazados (HTTP 401). Verifica tu usuario y contraseña en el panel lateral.")
                if resp.status_code == 403:
                    if "PROCESSING_UNITS" in err_upper or "INSUFFICIENT" in err_upper or "CREDIT" in err_upper:
                        raise ValidationError("Cuota de Unidades de Procesamiento (PUs) agotada en Copernicus CDSE (HTTP 403). Cambia de credenciales en el panel lateral.")
                    raise ValidationError(f"Acceso denegado en Copernicus CDSE (HTTP 403): {err_text[:200]}")
                if resp.status_code == 429 or "RATE_LIMIT" in err_upper:
                    raise ValidationError("Límite de tasa excedido en Copernicus CDSE (HTTP 429). Espera 60 segundos antes de reintentar.")
                if resp.status_code >= 400:
                    raise ValidationError(f"Error en servicio satelital Copernicus CDSE ({resp.status_code}): {err_text[:200]}")
                return []

            chunk_items = []
            data = resp.json()
            for interval in data.get('data', []):
                date_str = interval.get('interval', {}).get('from', '')[:10]
                outputs = interval.get('outputs', {})
                if not outputs:
                    continue
                interval_record = {'date': date_str, 'metrics': {}}
                for mode_name, output_id in MODE_TO_UNIFIED_OUTPUT.items():
                    if output_id in outputs:
                        b_stats = outputs[output_id].get('bands', {}).get('B0', {}).get('stats', {})
                        # Prefer robust spatial median (p50) if returned, fallback to spatial mean
                        p_dict = b_stats.get('percentiles', {})
                        val_raw = None
                        if isinstance(p_dict, dict):
                            val_raw = p_dict.get('50.0') if '50.0' in p_dict else p_dict.get('50')
                        if val_raw is None:
                            val_raw = b_stats.get('mean')
                        count = b_stats.get('sampleCount', 0)
                        no_data = b_stats.get('noDataCount', 0)
                        total_px = count + no_data
                        # Spatial representativeness guard: for basin-wide indices (Hydrology, WaterRatio, Vegetation, SoilVegetation),
                        # discard scenes where >=92% of the wetland is masked by clouds/shadows.
                        # For open-water optical quality indices (WaterQuality, AlgaeBloom), accept when >=10 valid water pixels exist.
                        if mode_name in ("WaterQuality", "AlgaeBloom"):
                            if count < 10 and total_px > 25 and (count / total_px) < 0.03:
                                continue
                        else:
                            if total_px > 25 and (count / total_px) < 0.08:
                                continue
                        try:
                            val = float(val_raw) if val_raw is not None else None
                            if val is not None and (math.isnan(val) or math.isinf(val)):
                                val = None
                        except (ValueError, TypeError):
                            val = None
                        if val is not None and count > 0:
                            p10 = p_dict.get('10.0') if isinstance(p_dict, dict) else None
                            p90 = p_dict.get('90.0') if isinstance(p_dict, dict) else None
                            cov_pct = round((count / total_px) * 100.0, 1) if total_px > 0 else 100.0
                            interval_record['metrics'][mode_name] = {
                                'value': val,
                                'weight': count,
                                'p10': float(p10) if p10 is not None and math.isfinite(float(p10)) else None,
                                'p90': float(p90) if p90 is not None and math.isfinite(float(p90)) else None,
                                'valid_coverage_pct': cov_pct,
                            }
                if interval_record['metrics']:
                    chunk_items.append(interval_record)
            return chunk_items
        except ValidationError:
            raise
        except Exception as ex:
            logger.error(f"Network error on unified chunk: {ex}")
            return []

    all_chunk_records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_chunk, ch) for ch in chunks]
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res:
                    all_chunk_records.extend(res)
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Error fetching unified chunk: {e}")

    # Aggregate by date and mode
    aggregated_by_mode: Dict[str, Dict[str, Dict[str, float]]] = {m: {} for m in MODE_TO_UNIFIED_OUTPUT}
    for rec in all_chunk_records:
        d = rec['date']
        for mode_name, m_data in rec.get('metrics', {}).items():
            acc_dict = aggregated_by_mode[mode_name]
            if d not in acc_dict:
                acc_dict[d] = {'sum_val': 0.0, 'sum_wt': 0.0, 'sum_p10': 0.0, 'sum_p90': 0.0, 'sum_cov': 0.0, 'has_pct': 0.0}
            acc_dict[d]['sum_val'] += m_data['value'] * m_data['weight']
            acc_dict[d]['sum_wt'] += m_data['weight']
            if m_data.get('p10') is not None and m_data.get('p90') is not None:
                acc_dict[d]['sum_p10'] += m_data['p10'] * m_data['weight']
                acc_dict[d]['sum_p90'] += m_data['p90'] * m_data['weight']
                acc_dict[d]['has_pct'] += m_data['weight']
            if m_data.get('valid_coverage_pct') is not None:
                acc_dict[d]['sum_cov'] += m_data['valid_coverage_pct'] * m_data['weight']

    final_by_mode: Dict[str, List[Dict[str, Any]]] = {}
    for mode_name, dates_dict in aggregated_by_mode.items():
        series = []
        for d, acc in dates_dict.items():
            if acc['sum_wt'] > 0:
                pt: Dict[str, Any] = {'date': d, 'value': acc['sum_val'] / acc['sum_wt']}
                if acc['has_pct'] > 0:
                    pt['p10'] = round(acc['sum_p10'] / acc['has_pct'], 4)
                    pt['p90'] = round(acc['sum_p90'] / acc['has_pct'], 4)
                if acc['sum_cov'] > 0:
                    pt['valid_coverage_pct'] = round(acc['sum_cov'] / acc['sum_wt'], 1)
                series.append(pt)
        series.sort(key=lambda x: x['date'])
        final_by_mode[mode_name] = series

    return final_by_mode

def generate_map_url(aoi, start, end, mode, wetland_name=None):
    """Generate absolute URL map paths for RGB, metric tiles, and fast single-image overlays."""
    env_backend = os.getenv("BACKEND_URL") or os.getenv("RENDER_EXTERNAL_URL")
    BASE_URL = (env_backend or "http://localhost:8000").rstrip("/")
    suffix = f"?wetland={wetland_name}" if wetland_name else ""
    
    overlay_coords = None
    bbox_str = None
    try:
        import shapely.geometry as sg
        geom = sg.shape(aoi)
        minx, miny, maxx, maxy = geom.bounds
        # Coordinates in clockwise order: Top-Left, Top-Right, Bottom-Right, Bottom-Left
        overlay_coords = [
            [minx, maxy],
            [maxx, maxy],
            [maxx, miny],
            [minx, miny]
        ]
        bbox_str = f"{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}"
        if aoi:
            geom_obj = aoi.get("geometry", aoi) if isinstance(aoi, dict) else aoi
            _aoi_geometry_cache[bbox_str] = geom_obj
            if wetland_name:
                _aoi_geometry_cache[wetland_name] = geom_obj
    except Exception:
        overlay_coords = None

    params = []
    if bbox_str:
        params.append(f"bbox={bbox_str}")
    if wetland_name:
        params.append(f"wetland={wetland_name}")
    ov_suffix = f"?{'&'.join(params)}" if params else ""

    return {
        "rgb": f"{BASE_URL}/api/tiles/rgb/{start}/{end}/{{z}}/{{x}}/{{y}}{suffix}",
        "metric": f"{BASE_URL}/api/tiles/metric/{mode}/{start}/{end}/{{z}}/{{x}}/{{y}}{suffix}",
        "thumb_url": f"{BASE_URL}/api/thumb/{mode}/{start}/{end}{ov_suffix}",
        "overlay": {
            "rgb_url": f"{BASE_URL}/api/overlay/RGB/{start}/{end}{ov_suffix}",
            "metric_url": f"{BASE_URL}/api/overlay/{mode}/{start}/{end}{ov_suffix}",
            "coordinates": overlay_coords
        } if overlay_coords else None
    }

def perform_single_analysis(request: AnalysisRequest, mode: str, token: Optional[str] = None, preloaded_data: Optional[List[Dict[str, Any]]] = None):
    """Perform robust analysis for a single mode.
    If preloaded_data is provided (from unified query), bypasses duplicate network fetch.
    Computes baseline trend statistics in RAM without redundant historical API calls.
    """
    try:
        logger.info(log_process_stage('', mode, 'processing'))
        start_obj, end_obj = validate_date_range(request.startDate, request.endDate)
        aoi = validate_geometry(request.geojson)
        wetland_name = request.wetlandName
        
        if preloaded_data is not None:
            current_data = preloaded_data
        else:
            unified_dict = analyze_period_unified(aoi, request.startDate, request.endDate, token=token)
            current_data = unified_dict.get(mode, [])

        if not current_data:
            raise ValidationError(
                f"Sin observaciones satelitales para {get_index_name(mode)} ({mode}): No se detectó ninguna escena Sentinel-2 procesable entre {request.startDate} y {request.endDate} con las condiciones de calidad atmosférica. Se recomienda ampliar el rango de fechas (ej: varios años o temporada de verano)."
            )
        if len(current_data) < 3:
            raise ValidationError(
                f"Adquisiciones insuficientes para {get_index_name(mode)} ({mode}): Solo se encontraron {len(current_data)} fecha(s) útiles entre {request.startDate} y {request.endDate}. Se requieren al menos 3 fechas para estimar medianas y tendencias robustas."
            )
            
        coverage = validate_temporal_coverage(current_data)
        if not coverage['valid']:
            raise ValidationError(f"Temporal coverage issue: {coverage['reason']}")
            
        current_stats = calculate_robust_statistics(current_data)
        current_data_flagged = detect_outliers(current_data, mode=mode)
        outlier_count = sum(1 for d in current_data_flagged if d.get('is_outlier'))
        
        # Determine actual observed time boundaries from real acquisitions using canonical windowing
        valid_dates = sorted([d['date'] for d in current_data_flagged if d.get('value') is not None])
        s_start, s_end, e_start, e_end, _, _ = compute_comparative_windows(request.startDate, request.endDate, valid_dates)

        maps_start = generate_map_url(aoi, s_start, s_end, mode, wetland_name)
        maps_end = generate_map_url(aoi, e_start, e_end, mode, wetland_name)
        
        # Calculate distinct initial vs final period statistics directly from time series (0 extra API calls)
        initial_points = [d for d in current_data_flagged if d.get('date', '') <= s_end and d.get('value') is not None]
        final_points = [d for d in current_data_flagged if d.get('date', '') >= e_start and d.get('value') is not None]
        
        # If strict 1-year window has fewer than 2 points, dynamically take the earliest available acquisitions
        if len(initial_points) < 2 and len(current_data_flagged) >= 2:
            initial_points = current_data_flagged[:max(2, min(len(current_data_flagged), 10))]
        if len(final_points) < 2 and len(current_data_flagged) >= 2:
            final_points = current_data_flagged[-max(2, min(len(current_data_flagged), 10)):]

        initial_stats = calculate_robust_statistics(initial_points) or current_stats
        final_stats = calculate_robust_statistics(final_points) or current_stats
        
        init_med = initial_stats.get('median', current_stats['median'])
        fin_med = final_stats.get('median', current_stats['median'])
        delta_init_final = fin_med - init_med
        trend_stats = calculate_trend_statistics(final_points, initial_points, full_series=current_data_flagged) or {}

        maps = {
            "rgb": maps_end["rgb"],
            "metric": maps_end["metric"],
            "start_year": maps_start,
            "end_year": maps_end,
            "overlay": maps_end.get("overlay")
        }
        
        def safe_float(v, fallback=0):
            if v is None: return fallback
            try:
                f = float(v)
                if math.isnan(f) or math.isinf(f): return fallback
                return f
            except: return fallback
        
        result = {
            "mode": mode,
            "stats": {
                "current": safe_float(fin_med),
                "current_mean": safe_float(final_stats.get('mean', current_stats['mean'])),
                "current_std": safe_float(final_stats.get('std', current_stats['std'])),
                "initial": safe_float(init_med),
                "initial_mean": safe_float(initial_stats.get('mean', current_stats['mean'])),
                "initial_std": safe_float(initial_stats.get('std', current_stats['std'])),
                "last": safe_float(trend_stats.get('previous_median', init_med)),
                "trend": safe_float(trend_stats.get('trend', delta_init_final)),
                "trend_delta": safe_float(delta_init_final),
                "sen_slope": safe_float(trend_stats.get('sen_slope_per_year', 0)),
                "mk_z": safe_float(trend_stats.get('mk_z', 0)),
                "mk_p_value": safe_float(trend_stats.get('mk_p_value', 1.0)),
                "trend_significant": bool(trend_stats.get('trend_significant', False)),
                "outlier_count": outlier_count,
                "data_count": current_stats['count'],
                "cv": safe_float(final_stats.get('cv', current_stats['cv']))
            },
            "situational_status": calculate_index_situational_status(mode, safe_float(fin_med), safe_float(delta_init_final)),
            "initial_situational_status": calculate_index_situational_status(mode, safe_float(init_med)),
            "time_series": current_data_flagged,
            "maps": maps,
            "coverage": coverage
        }
        
        # Preserve authentic physical observational values in time series (no destructive truncation)
        normalized_series = []
        for point in current_data_flagged:
            p = point.copy()
            raw_v = safe_float(point.get('value'), None)
            p['value_raw'] = raw_v
            p['value'] = raw_v
            normalized_series.append(p)
        result['time_series'] = normalized_series
        
        logger.info(log_process_stage('', mode, 'completed'))
        return result
        
    except Exception as e:
        logger.error(f"{mode} error: {e}")
        return create_error_response(e, mode)

@app.post("/analyze")
def analyze(
    request: AnalysisRequest, 
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    token = get_cdse_token(
        username=x_cdse_username or "",
        password=x_cdse_password or "",
        client_id=x_cdse_client_id or "",
        client_secret=x_cdse_client_secret or "",
        bearer_token=authorization or ""
    )
    try:
        res = perform_single_analysis(request, request.mode, token=token)
        if not res or 'error' in res: 
            raise HTTPException(500, str(res.get('error', 'Unknown Error')))
            
        formatted_series = []
        for d in res['time_series']:
            formatted_series.append({
                "date": d['date'],
                "value": d['value'],
                "metric_name": get_index_name(request.mode)
            })
        formatted_series.sort(key=lambda x: x['date'])
        
        return {
            "status": "success",
            "data": {
                "time_series": formatted_series,
                "summary": {
                    "current_avg": res['stats']['current'],
                    "last_year_avg": res['stats']['last'],
                    "trend": res['stats']['trend'],
                    "mode": request.mode
                },
                "situational_status": res.get('situational_status'),
                "maps": res['maps']
            }
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ==========================================
# MODULE: FAST OVERLAYS, TILES & CACHING
# ==========================================
import hashlib
import re

ANALYSIS_CACHE_DIR = Path(__file__).parent / ".cache_analysis"
OVERLAY_CACHE_DIR = Path(__file__).parent / ".cache_overlays"
ANALYSIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OVERLAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _get_safe_cache_path(cache_dir: Path, key: str, ext: str = ".json") -> Path:
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', key)
    if len(safe_name) > 100:
        hash_suffix = hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
        safe_name = f"{safe_name[:80]}_{hash_suffix}"
    return cache_dir / f"{safe_name}{ext}"

_tile_cache: Dict[str, bytes] = {}
_overlay_cache: Dict[str, bytes] = {}
_aoi_geometry_cache: Dict[str, dict] = {}
_analysis_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()
_inflight_overlays: Dict[str, threading.Event] = {}
_MAX_CACHE_SIZE = 3000

def get_transparent_png(width: int = 256, height: int = 256) -> bytes:
    """Generate an authentic transparent RGBA PNG.
    Used when a requested tile/overlay contains no satellite acquisitions or outside bounds,
    guaranteeing zero visual artifacts (no artificial fake circles/gradients).
    """
    from PIL import Image
    import io
    img = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def get_overlay_bytes(mode: str, start_date: str, end_date: str, bounds: List[float], geometry: Optional[dict] = None, token: Optional[str] = None) -> Optional[bytes]:
    """Fetch or retrieve from RAM/Disk cache a high-speed BBOX composite PNG.
    If geometry is provided and mode != 'RGB', clips the index to the exact wetland polygon,
    leaving pixels outside the wetland 100% transparent.
    Authentic Copernicus CDSE Process API: zero artificial synthetic mock data.
    """
    has_geom = bool(geometry and mode != "RGB")
    geom_hash = ""
    if has_geom and isinstance(geometry, dict):
        coords_str = str(geometry.get("coordinates", ""))
        if coords_str:
            geom_hash = "_" + hashlib.sha256(coords_str.encode('utf-8')).hexdigest()[:8]
    cache_key = f"{mode}_{start_date[:10]}_{end_date[:10]}_{bounds[0]:.4f}_{bounds[1]:.4f}_{bounds[2]:.4f}_{bounds[3]:.4f}_{'clipped' if has_geom else 'full'}{geom_hash}"
    disk_path = _get_safe_cache_path(OVERLAY_CACHE_DIR, cache_key, ".png")
    
    with _cache_lock:
        if cache_key in _overlay_cache:
            return _overlay_cache[cache_key]
        if disk_path.exists():
            try:
                disk_bytes = disk_path.read_bytes()
                if disk_bytes and len(disk_bytes) > 200:
                    _overlay_cache[cache_key] = disk_bytes
                    return disk_bytes
            except Exception:
                pass
        event = _inflight_overlays.get(cache_key)
        if event is None:
            event = threading.Event()
            _inflight_overlays[cache_key] = event
            is_initiator = True
        else:
            is_initiator = False

    if not is_initiator:
        event.wait(timeout=25)
        with _cache_lock:
            return _overlay_cache.get(cache_key)

    # PU Quota Optimization: Cap dimensions at 512x512 so request strictly consumes 1 PU (not 3 PUs)
    max_dim = 512
    width = max_dim
    height = max_dim

    try:
        token = token or get_cdse_token()
        evalscript = get_evalscript(mode, is_process=True)
        
        minx, miny, maxx, maxy = bounds
        lat_mid = (miny + maxy) / 2.0
        cos_lat = math.cos(math.radians(lat_mid)) if abs(lat_mid) < 89 else 1.0
        w_km = abs(maxx - minx) * 111.32 * abs(cos_lat)
        h_km = max(1e-6, abs(maxy - miny) * 110.57)
        aspect = max(0.2, min(5.0, w_km / h_km))
        
        if aspect >= 1.0:
            width = max_dim
            height = max(128, min(max_dim, int(max_dim / aspect)))
        else:
            height = max_dim
            width = max(128, min(max_dim, int(max_dim * aspect)))

        # CDSE Process API bounds specification: Pass EITHER 'geometry' OR 'bbox', never both
        bounds_payload: Dict[str, Any] = {
            "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
        }
        g_to_use = None
        if geometry and mode != "RGB":
            g = geometry
            if isinstance(g, dict):
                if g.get("type") == "Feature":
                    g = g.get("geometry")
                elif g.get("type") == "FeatureCollection" and g.get("features"):
                    g = g["features"][0].get("geometry")
            if g and isinstance(g, dict) and g.get("type") in ("Polygon", "MultiPolygon"):
                g_to_use = simplify_geometry_if_needed(g, max_vertices=1200)

        if g_to_use:
            bounds_payload["geometry"] = g_to_use
        else:
            bounds_payload["bbox"] = bounds

        payload = {
            "input": {
                "bounds": bounds_payload,
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                        "maxCloudCoverage": 20,
                        "mosaickingOrder": "leastCC"
                    },
                    "processing": {
                        "upsampling": "BILINEAR",
                        "downsampling": "BILINEAR"
                    }
                }]
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
            },
            "evalscript": evalscript
        }

        resp = http_session.post(
            "https://sh.dataspace.copernicus.eu/api/v1/process",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20
        )
        
        # Fallback 1: if geometry caused an API error, retry with bbox only
        if resp.status_code != 200 and "geometry" in payload["input"]["bounds"]:
            payload["input"]["bounds"].pop("geometry", None)
            payload["input"]["bounds"]["bbox"] = bounds
            resp = http_session.post(
                "https://sh.dataspace.copernicus.eu/api/v1/process",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=20
            )

        # Fallback 2: if still blank or no acquisitions found, relax to leastCC across entire window
        if resp.status_code != 200 or not resp.content or len(resp.content) < 800 or is_blank_image(resp.content):
            payload["input"]["data"][0]["dataFilter"].pop("maxCloudCoverage", None)
            resp = http_session.post(
                "https://sh.dataspace.copernicus.eu/api/v1/process",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=20
            )

        if resp.status_code == 200 and resp.content and not is_blank_image(resp.content):
            with _cache_lock:
                if len(_overlay_cache) > _MAX_CACHE_SIZE:
                    keys = list(_overlay_cache.keys())[:int(_MAX_CACHE_SIZE * 0.2)]
                    for k in keys: _overlay_cache.pop(k, None)
                _overlay_cache[cache_key] = resp.content
                try:
                    tmp_path = disk_path.with_suffix(".png.tmp")
                    tmp_path.write_bytes(resp.content)
                    os.replace(tmp_path, disk_path)
                except Exception:
                    pass
            return resp.content
        else:
            logger.warning(f"Process API returned {resp.status_code} for {mode}. Returning clean transparent image.")
            return get_transparent_png(width, height)
    except Exception as e:
        logger.error(f"Error fetching overlay for {mode}: {e}. Returning clean transparent image.")
        return get_transparent_png(width, height)
    finally:
        with _cache_lock:
            _inflight_overlays.pop(cache_key, None)
            event.set()

@app.post("/analyze-all")
def analyze_all(
    request: AnalysisRequest, 
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    # Resolve token: client headers have priority, fallback to server institutional credentials
    token = get_cdse_token(
        username=x_cdse_username or "",
        password=x_cdse_password or "",
        client_id=x_cdse_client_id or "",
        client_secret=x_cdse_client_secret or "",
        bearer_token=authorization or ""
    )

    try:
        results = {}
        modes = ["Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"]
        
        # Strict geodetic and area validation BEFORE caching or querying Copernicus
        aoi = validate_geometry(request.geojson)

        # Cache active polygon geometry in RAM for on-demand overlay clipping
        geom_sha = ""
        aoi_bounds = [-70.0, -33.0, -70.0, -33.0]
        try:
            import shapely.geometry as sg
            aoi_geom = sg.shape(aoi)
            aoi_bounds = list(aoi_geom.bounds)
            bbox_key = f"{aoi_bounds[0]:.6f},{aoi_bounds[1]:.6f},{aoi_bounds[2]:.6f},{aoi_bounds[3]:.6f}"
            geom_sha = hashlib.sha256(json.dumps(aoi, sort_keys=True).encode("utf-8")).hexdigest()[:10]
            with _cache_lock:
                if len(_aoi_geometry_cache) > 1000:
                    for k in list(_aoi_geometry_cache.keys())[:200]:
                        _aoi_geometry_cache.pop(k, None)
                _aoi_geometry_cache[bbox_key] = aoi
                if request.wetlandName:
                    _aoi_geometry_cache[request.wetlandName] = aoi
        except Exception as pfe:
            logger.warning(f"Could not cache geometry: {pfe}")

        # Fast Cache: 1ms RAM check, 3ms Disk check (<3 seconds response guaranteed)
        # Custom areas bind to spatial BBOX + geometry hash to prevent cross-wetland cache collisions
        if request.wetlandName:
            cache_key = f"{request.wetlandName}_{request.startDate[:10]}_{request.endDate[:10]}"
        else:
            b = aoi_bounds if len(aoi_bounds) == 4 else [-70.0, -33.0, -70.0, -33.0]
            cache_key = f"custom_{b[0]:.4f}_{b[1]:.4f}_{b[2]:.4f}_{b[3]:.4f}_{geom_sha}_{request.startDate[:10]}_{request.endDate[:10]}"
        disk_path = _get_safe_cache_path(ANALYSIS_CACHE_DIR, cache_key, ".json")
        with _cache_lock:
            if cache_key in _analysis_cache:
                logger.info(f"Serving cached analysis for {cache_key} (RAM instant <1ms)")
                return {"status": "success", "data": _analysis_cache[cache_key], "cached": True}
            if disk_path.exists():
                try:
                    with open(disk_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    _analysis_cache[cache_key] = cached_data
                    logger.info(f"Serving cached analysis for {cache_key} (Disk instant <3ms)")
                    return {"status": "success", "data": cached_data, "cached": True}
                except Exception as de:
                    logger.warning(f"Error reading disk cache {disk_path}: {de}")

        # V2 OPTIMIZATION:
        # 1 Single unified Statistical API query for ALL 6 indices across the entire period (1 to 10 years).
        # Saves ~95% of Processing Units (PUs) vs running 12 separate queries.
        unified_series = analyze_period_unified(aoi, request.startDate, request.endDate, token=token)

        for m in modes:
            logger.info(log_process_stage('', m, 'processing'))
            preloaded = unified_series.get(m, [])
            results[m] = perform_single_analysis(request, m, token=token, preloaded_data=preloaded)
            
        # Compute consolidated ecological status (IMCE / MCEI) calibrated by Chilean macrozone
        centroid_lat = None
        area_km2 = None
        area_ha = None
        try:
            import shapely.geometry as sg
            import geopandas as gpd
            _geom_obj = sg.shape(aoi)
            centroid_lat = float(_geom_obj.centroid.y)
            utm_epsg = 32718 if _geom_obj.centroid.x < -72.0 else 32719
            _area_m2 = float(gpd.GeoSeries([_geom_obj], crs="EPSG:4326").to_crs(epsg=utm_epsg).area.iloc[0])
            area_ha = round(_area_m2 / 10000.0, 2)
            area_km2 = round(_area_m2 / 1_000_000.0, 4)
        except Exception:
            pass

        macrozone = request.macrozone or infer_chile_macrozone(lat=centroid_lat, region=request.region)
        consolidated = calculate_consolidated_wetland_status(results, macrozone=macrozone)
        results["consolidated_status"] = consolidated
        results["_consolidated"] = consolidated

        cov_samples = []
        for m in modes:
            for pt in (unified_series.get(m) or []):
                if isinstance(pt, dict) and pt.get("valid_coverage_pct") is not None:
                    cov_samples.append(float(pt["valid_coverage_pct"]))

        results["audit_trail"] = {
            "geometry_sha256": hashlib.sha256(json.dumps(aoi, sort_keys=True).encode("utf-8")).hexdigest(),
            "area_ha": area_ha,
            "area_km2": area_km2,
            "macrozone": consolidated.get("macrozone", macrozone),
            "macrozone_label": consolidated.get("macrozone_label"),
            "sensor": "Sentinel-2 L2A (ESA Copernicus CDSE)",
            "cloud_mask": "SCL (classes 3, 8, 9, 10, 11 excluded)",
            "mean_cloud_free_pct": round(sum(cov_samples) / len(cov_samples), 1) if cov_samples else None,
            "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # If all modes failed with error status, raise HTTPException with true cause
        error_modes = [m for m, r in results.items() if isinstance(r, dict) and r.get("status") == "error"]
        if len(error_modes) == len(modes):
            first_err = results[modes[0]].get("error", "Error desconocido al procesar datos satelitales")
            logger.error(f"All modes failed in analyze-all: {first_err}")
            err_lower = str(first_err).lower()
            if "credentials" in err_lower or "credenciales" in err_lower or "401" in err_lower or "unauthorized" in err_lower:
                raise HTTPException(401, detail=f"{first_err}")
            if "cuota" in err_lower or "processing units" in err_lower or "unidades de procesamiento" in err_lower or "403" in err_lower:
                raise HTTPException(403, detail=f"{first_err}")
            if "rate limit" in err_lower or "límite de tasa" in err_lower or "429" in err_lower:
                raise HTTPException(429, detail=f"{first_err}")
            if "sin observaciones" in err_lower or "adquisiciones insuficientes" in err_lower or "insufficient data" in err_lower:
                raise HTTPException(422, detail=f"{first_err}")
            raise HTTPException(500, detail=f"Error en procesamiento satelital: {first_err}")

        # Store in RAM analysis cache and write to disk atomically for permanent instant retrieval
        with _cache_lock:
            if len(_analysis_cache) > 200:
                _analysis_cache.pop(next(iter(_analysis_cache)), None)
            _analysis_cache[cache_key] = results
            try:
                tmp_json = disk_path.with_suffix(".json.tmp")
                with open(tmp_json, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False)
                os.replace(tmp_json, disk_path)
                prune_disk_cache(ANALYSIS_CACHE_DIR, max_files=300, max_age_days=14)
                prune_disk_cache(OVERLAY_CACHE_DIR, max_files=600, max_age_days=14)
            except Exception as we:
                logger.warning(f"Error saving analysis to disk cache: {we}")

        logger.info(log_process_stage('', None, 'final'))
        return {"status": "success", "data": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analyze-all Error: {e}")
        raise HTTPException(500, detail=f"Backend Error: {str(e)}")

VALID_VISUAL_MODES = {"RGB", "Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"}

@app.get("/api/overlay/{mode}/{start_date}/{end_date}")
async def get_overlay(
    mode: str, 
    start_date: str, 
    end_date: str, 
    wetland: Optional[str] = None, 
    bbox: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    """Serve a high-speed georeferenced composite PNG covering the wetland bounding box.
    If wetland or bbox geometry is known, clips the index overlay to the exact wetland perimeter.
    """
    if mode not in VALID_VISUAL_MODES:
        raise HTTPException(400, f"Invalid visual mode '{mode}'. Valid modes: {sorted(VALID_VISUAL_MODES)}")
    bounds = None
    geometry = None
    
    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) == 4:
                bounds = parts
        except Exception:
            bounds = None
        if not geometry:
            geometry = _aoi_geometry_cache.get(bbox)

    if wetland:
        if not geometry:
            geometry = _aoi_geometry_cache.get(wetland)
            if not geometry:
                geometry = get_wetland_geometry(wetland)
        if not bounds:
            geom_dict = get_wetland_geometry(wetland)
            if geom_dict:
                try:
                    import shapely.geometry as sg
                    bounds = list(sg.shape(geom_dict).bounds)
                except Exception:
                    bounds = None

    if not bounds:
        raise HTTPException(400, "Must provide valid 'bbox=minx,miny,maxx,maxy' or 'wetland'")

    token = get_cdse_token(
        username=x_cdse_username or "",
        password=x_cdse_password or "",
        client_id=x_cdse_client_id or "",
        client_secret=x_cdse_client_secret or "",
        bearer_token=authorization or ""
    )
    content = await asyncio.to_thread(get_overlay_bytes, mode, start_date, end_date, bounds, geometry, token)
    if not content:
        content = get_transparent_png(512, 512)

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=604800, stale-while-revalidate=86400, immutable",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*"
        }
    )

@app.options("/api/overlay/{mode}/{start_date}/{end_date}")
async def options_overlay(mode: str, start_date: str, end_date: str):
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*"
        }
    )

# TILE PROXY
def xyz_to_bbox(x, y, z):
    n = 2.0 ** z
    lon_left = x / n * 360.0 - 180.0
    lat_top = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lon_right = (x + 1) / n * 360.0 - 180.0
    lat_bottom = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return [lon_left, lat_bottom, lon_right, lat_top]

@app.get("/api/tiles/rgb/{start_date}/{end_date}/{z}/{x}/{y}")
def get_rgb_tile(
    start_date: str, end_date: str, z: int, x: int, y: int, wetland: str = None,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    """Serve an RGB Sentinel-2 tile with RAM caching and browser cache-control."""
    cache_key = f"rgb_{start_date[:10]}_{end_date[:10]}_{z}_{x}_{y}"
    
    with _cache_lock:
        if cache_key in _tile_cache:
            return Response(
                content=_tile_cache[cache_key],
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800, immutable"}
            )
            
    bbox = xyz_to_bbox(x, y, z)
    evalscript = get_evalscript("RGB", is_process=True)

    url = "https://sh.dataspace.copernicus.eu/api/v1/process"
    payload = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                    "maxCloudCoverage": 30,
                    "mosaickingOrder": "leastCC"
                }
            }]
        },
        "output": {
            "width": 256,
            "height": 256,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
        },
        "evalscript": evalscript
    }

    try:
        token = get_cdse_token(
            username=x_cdse_username or "",
            password=x_cdse_password or "",
            client_id=x_cdse_client_id or "",
            client_secret=x_cdse_client_secret or "",
            bearer_token=authorization or ""
        )
        resp = http_session.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=25)
        if resp.status_code == 200 and resp.content and not is_blank_image(resp.content):
            with _cache_lock:
                if len(_tile_cache) > _MAX_CACHE_SIZE:
                    keys = list(_tile_cache.keys())[:int(_MAX_CACHE_SIZE * 0.2)]
                    for k in keys: _tile_cache.pop(k, None)
                _tile_cache[cache_key] = resp.content
            return Response(
                content=resp.content,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800, immutable"}
            )
        else:
            # Clean fallback: Return authentic transparent PNG with HTTP 200 to prevent map error floods
            blank_png = get_transparent_png(256, 256)
            return Response(
                content=blank_png,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"}
            )
    except Exception as ex:
        logger.debug(f"RGB tile fetch unfulfilled ({ex}): returning transparent fallback")
        return Response(content=get_transparent_png(256, 256), media_type="image/png", headers={"Cache-Control": "public, max-age=60"})

@app.get("/api/tiles/metric/{mode}/{start_date}/{end_date}/{z}/{x}/{y}")
def get_metric_tile(
    mode: str, start_date: str, end_date: str, z: int, x: int, y: int, wetland: str = None,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    """Serve a spectral index tile with RAM caching and browser cache-control."""
    if mode not in VALID_VISUAL_MODES:
        raise HTTPException(400, f"Invalid metric mode '{mode}'.")
    cache_key = f"{mode}_{start_date[:10]}_{end_date[:10]}_{z}_{x}_{y}"
    
    with _cache_lock:
        if cache_key in _tile_cache:
            return Response(
                content=_tile_cache[cache_key],
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800, immutable"}
            )

    bbox = xyz_to_bbox(x, y, z)
    evalscript = get_evalscript(mode, is_process=True)

    url = "https://sh.dataspace.copernicus.eu/api/v1/process"
    payload = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                    "maxCloudCoverage": 30,
                    "mosaickingOrder": "leastCC"
                }
            }]
        },
        "output": {
            "width": 256,
            "height": 256,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
        },
        "evalscript": evalscript
    }

    try:
        token = get_cdse_token(
            username=x_cdse_username or "",
            password=x_cdse_password or "",
            client_id=x_cdse_client_id or "",
            client_secret=x_cdse_client_secret or "",
            bearer_token=authorization or ""
        )
        resp = http_session.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=25)
        if resp.status_code == 200 and resp.content and not is_blank_image(resp.content):
            with _cache_lock:
                if len(_tile_cache) > _MAX_CACHE_SIZE:
                    keys = list(_tile_cache.keys())[:int(_MAX_CACHE_SIZE * 0.2)]
                    for k in keys: _tile_cache.pop(k, None)
                _tile_cache[cache_key] = resp.content
            return Response(
                content=resp.content,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800, immutable"}
            )
        else:
            # Clean fallback: Return authentic transparent PNG with HTTP 200 to prevent map error floods
            blank_png = get_transparent_png(256, 256)
            return Response(
                content=blank_png,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"}
            )
    except Exception as ex:
        logger.debug(f"Metric tile fetch unfulfilled ({ex}): returning transparent fallback")
        return Response(content=get_transparent_png(256, 256), media_type="image/png", headers={"Cache-Control": "public, max-age=60"})

@app.get("/api/thumb/{mode}/{start_date}/{end_date}")
async def get_thumbnail(
    mode: str, start_date: str, end_date: str, wetland: Optional[str] = None, bbox: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    """Serve a static Sentinel-2 thumbnail image with caching (supports both catalog wetlands and custom BBOX areas)."""
    if mode not in VALID_VISUAL_MODES:
        raise HTTPException(400, f"Invalid mode '{mode}'.")
    geom = None
    if bbox:
        geom = _aoi_geometry_cache.get(bbox)
        if not geom:
            try:
                parts = [float(x.strip()) for x in bbox.split(",")]
                if len(parts) == 4:
                    geom = {
                        "type": "Polygon",
                        "coordinates": [[[parts[0], parts[1]], [parts[2], parts[1]], [parts[2], parts[3]], [parts[0], parts[3]], [parts[0], parts[1]]]]
                    }
            except Exception:
                geom = None
    token = get_cdse_token(
        username=x_cdse_username or "",
        password=x_cdse_password or "",
        client_id=x_cdse_client_id or "",
        client_secret=x_cdse_client_secret or "",
        bearer_token=authorization or ""
    )
    img_io = await asyncio.to_thread(fetch_thumbnail_bytes, mode, start_date, end_date, geom, wetland, token)
    if not img_io:
        return Response(content=b"", media_type="image/png", status_code=404)
    return Response(
        content=img_io.getvalue(), 
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, stale-while-revalidate=86400, immutable"}
    )

@app.post("/verify-credentials")
def verify_credentials(payload: dict):
    """Verify CDSE credentials with Copernicus servers without permanently saving state."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    client_id = payload.get("client_id", "").strip()
    client_secret = payload.get("client_secret", "").strip()
    
    token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    try:
        if username and password:
            from oauthlib.oauth2 import LegacyApplicationClient
            from requests_oauthlib import OAuth2Session
            client = LegacyApplicationClient(client_id="cdse-public")
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, username=username, password=password)
        elif client_id and client_secret:
            from oauthlib.oauth2 import BackendApplicationClient
            from requests_oauthlib import OAuth2Session
            client = BackendApplicationClient(client_id=client_id)
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, client_id=client_id, client_secret=client_secret)
        else:
            raise HTTPException(400, "Debe ingresar email y contraseña o credenciales OAuth")
            
        expires_in = token_resp.get('expires_in', 600)
        return {
            "status": "ok",
            "message": "Acceso verificado exitosamente con Copernicus Hub (CDSE)",
            "expires_in": expires_in
        }
    except HTTPException:
        raise
    except Exception as e:
        err_text = str(e)
        if "invalid_grant" in err_text.lower() or "unauthorized" in err_text.lower():
            raise HTTPException(401, "Credenciales incorrectas. Verifique su correo y contraseña de dataspace.copernicus.eu")
        raise HTTPException(400, f"Error al verificar credenciales con Copernicus: {err_text}")

@app.post("/set-credentials")
def set_credentials(payload: dict):
    """Verify CDSE credentials at runtime and return status/token without saving to global state."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    client_id = payload.get("client_id", "").strip()
    client_secret = payload.get("client_secret", "").strip()
    
    # Test token retrieval (populates user token cache)
    token = get_cdse_token(
        username=username,
        password=password,
        client_id=client_id,
        client_secret=client_secret
    )
    return {
        "status": "ok",
        "message": "Credenciales CDSE verificadas y activadas para esta sesión",
        "token": token
    }

@app.post("/clear-credentials")
def clear_credentials(payload: Optional[dict] = None):
    """Clear only the requesting tenant's cached token without affecting other users."""
    with _cdse_token_lock:
        if isinstance(payload, dict):
            u = payload.get("username", "").strip()
            p = payload.get("password", "").strip()
            cid = payload.get("client_id", "").strip()
            csec = payload.get("client_secret", "").strip()
            if (u and p) or (cid and csec):
                cache_key = hashlib.sha256(
                    (f"pass:{u}:{p}" if (u and p) else f"oauth:{cid}:{csec}").encode("utf-8")
                ).hexdigest()
                _cdse_token_cache.pop(cache_key, None)
    return {"status": "ok", "message": "Credenciales y sesión desconectadas exitosamente"}

@app.get("/credentials-status")
async def credentials_status():
    """Check whether CDSE institutional credentials are configured on the server."""
    server_ready = is_server_configured()
    return {
        "configured": server_ready,
        "server_configured": server_ready,
        "has_active_session": False,
        "mode": "institutional" if server_ready else "byoc",
        "method": "password" if CDSE_SERVER_USERNAME else ("oauth" if CDSE_SERVER_CLIENT_ID else "byoc"),
        "message": "Servidor con credenciales institucionales activas" if server_ready else "Modo público BYOC (requiere credenciales del usuario)"
    }

@app.get("/api/wetlands")
async def list_wetlands_metadata(region: Optional[str] = None):
    """Return lightweight wetlands catalogue (without heavy GeoJSON geometries) backed by SQLite."""
    return _catalogue_db.list_metadata(region=region)

@app.post("/api/analyze-sar")
def analyze_sar(
    request: AnalysisRequest,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None),
):
    """Cloud-penetrating Sentinel-1 GRD (SAR) time-series analysis for persistent-cloud regions."""
    token = get_cdse_token(
        username=x_cdse_username or "",
        password=x_cdse_password or "",
        client_id=x_cdse_client_id or "",
        client_secret=x_cdse_client_secret or "",
        bearer_token=authorization or "",
    )
    aoi = validate_geometry(request.geojson)
    validate_date_range(request.startDate, request.endDate)
    opt = get_optimal_aggregation_params(request.startDate, request.endDate, aoi_geom=aoi)
    try:
        return analyze_sentinel1_sar(
            http_session=http_session,
            aoi=aoi,
            start_date=request.startDate,
            end_date=request.endDate,
            token=token,
            deg_res=max(0.00018, opt.get("deg_res", 0.0002)),
            interval="P12D",
        )
    except Exception as e:
        logger.error(f"Sentinel-1 SAR error: {e}")
        raise HTTPException(502, detail=f"Error en análisis radar Sentinel-1 SAR: {e}")

@app.get("/api/wetland/{name_or_code}/geometry")
async def get_wetland_geom(name_or_code: str):
    """Retrieve detailed GeoJSON polygon geometry on-demand for a single wetland."""
    geom = get_wetland_geometry(name_or_code)
    if not geom:
        raise HTTPException(404, detail=f"Geometría no encontrada para el humedal '{name_or_code}'")
    return {"status": "ok", "name": name_or_code, "geometry": geom}

@app.post("/parse-geometry")
async def parse_geometry(file: UploadFile):
    """Accept a SHP (zip), GeoJSON, KML or KMZ file and return a GeoJSON geometry."""
    import tempfile
    import zipfile as zipmod
    
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB upload limit
    MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB Zip Bomb guard
    MAX_ZIP_FILES = 40

    filename = file.filename or ""
    ext = filename.lower().split(".")[-1]
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) < 4:
        raise HTTPException(400, "El archivo subido está vacío o es inválido.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El archivo excede el tamaño máximo permitido de 25 MB.")

    def _sync_parse_geometry(raw_bytes: bytes, file_ext: str) -> dict:
        import geopandas as gpd
        
        def safe_extract_zip(zip_file_obj, destination_dir):
            dest_abs = os.path.abspath(destination_dir)
            infos = zip_file_obj.infolist()
            if len(infos) > MAX_ZIP_FILES:
                raise HTTPException(400, f"El archivo comprimido contiene demasiados archivos ({len(infos)} > {MAX_ZIP_FILES}).")
            total_uncompressed = 0
            for member in infos:
                total_uncompressed += getattr(member, "file_size", 0)
                if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise HTTPException(413, "El archivo comprimido descomprimido excede el límite de seguridad de 100 MB.")
                member_target = os.path.abspath(os.path.join(dest_abs, member.filename))
                if os.path.commonpath([dest_abs, member_target]) != dest_abs:
                    raise HTTPException(400, "Archivo comprimido inválido: posible secuencia de path traversal detectada.")
            zip_file_obj.extractall(dest_abs)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Handle KMZ (zipped KML)
            if file_ext == "kmz":
                kmz_path = os.path.join(tmpdir, "file.kmz")
                with open(kmz_path, "wb") as f:
                    f.write(raw_bytes)
                with zipmod.ZipFile(kmz_path, "r") as z:
                    safe_extract_zip(z, tmpdir)
                # Find .kml inside
                kml_files = [os.path.join(r, f) for r, _, files in os.walk(tmpdir) for f in files if f.lower().endswith(".kml")]
                if not kml_files:
                    raise HTTPException(400, "No se encontró ningún archivo .kml dentro del archivo .kmz.")
                gdf = gpd.read_file(kml_files[0], driver="KML")
            # Handle Shapefile (expects a zip containing .shp, .dbf, .shx etc)
            elif file_ext == "zip":
                shp_path = os.path.join(tmpdir, "file.zip")
                with open(shp_path, "wb") as f:
                    f.write(raw_bytes)
                with zipmod.ZipFile(shp_path, "r") as z:
                    safe_extract_zip(z, tmpdir)
                shp_files = [os.path.join(r, f) for r, _, files in os.walk(tmpdir) for f in files if f.lower().endswith(".shp")]
                if not shp_files:
                    raise HTTPException(400, "No se encontró ningún archivo .shp dentro del archivo .zip.")
                gdf = gpd.read_file(shp_files[0])
            # Handle KML
            elif file_ext == "kml":
                kml_path = os.path.join(tmpdir, "file.kml")
                with open(kml_path, "wb") as f:
                    f.write(raw_bytes)
                gdf = gpd.read_file(kml_path, driver="KML")
            # Handle GeoJSON
            elif file_ext in ("geojson", "json"):
                geojson_path = os.path.join(tmpdir, "file.geojson")
                with open(geojson_path, "wb") as f:
                    f.write(raw_bytes)
                gdf = gpd.read_file(geojson_path)
            else:
                raise HTTPException(400, f"Unsupported format: .{file_ext}. Use SHP (zip), GeoJSON, KML or KMZ")

            if gdf is None or gdf.empty or len(gdf) == 0:
                raise HTTPException(400, "El archivo cartográfico no contiene entidades espaciales (features) o está vacío.")
            
            # Reproject to WGS84
            if gdf.crs:
                if gdf.crs.to_epsg() != 4326:
                    gdf = gdf.to_crs(epsg=4326)
            else:
                # Detect UTM coordinates in meters for Chilean territory (e.g. Shapefile without .prj)
                bounds_temp = gdf.total_bounds
                if len(bounds_temp) == 4 and bounds_temp[0] > 10000.0 and bounds_temp[1] > 1000000.0:
                    # Southern Chile (< ~5.95M m N, south of ~36.5°S, where coastal Easting > 550km) uses UTM Zone 18S (EPSG:32718);
                    # Central/Northern Chile uses UTM Zone 19S (EPSG:32719).
                    inferred_epsg = 32718 if (bounds_temp[1] < 5950000.0 and bounds_temp[0] > 550000.0) else 32719
                    logger.warning(f"Shapefile without .prj has metric UTM coordinates. Auto-assigning EPSG:{inferred_epsg} and reprojecting to WGS84.")
                    gdf.set_crs(epsg=inferred_epsg, inplace=True)
                    gdf = gdf.to_crs(epsg=4326)
            
            # Merge all features into one geometry
            union_geom = gdf.unary_union
            if union_geom is None or union_geom.is_empty:
                raise HTTPException(400, "No se encontraron geometrías espaciales válidas en el archivo cartográfico.")

            if not union_geom.is_valid:
                union_geom = union_geom.buffer(0)

            minx, miny, maxx, maxy = union_geom.bounds
            if not (math.isfinite(minx) and math.isfinite(miny) and math.isfinite(maxx) and math.isfinite(maxy)):
                raise HTTPException(400, "Las coordenadas de la geometría no son valores numéricos válidos.")

            # Geodetic Guard: Detect inverted [Lat, Lon] coordinate order for Chilean territory BEFORE metric buffering
            if minx > -60.0 and miny < -60.0 and -60.0 <= minx <= -15.0 and -76.0 <= miny <= -65.0:
                logger.warning(f"Inverted [Lat, Lon] detected in uploaded file: x={minx:.2f}, y={miny:.2f}. Auto-transposing to RFC 7946 [Lon, Lat].")
                import shapely.ops as so
                union_geom = so.transform(lambda x, y, *args: (y, x), union_geom)

            # Auto-buffer point or linear geometries into isotropic metric polygons (UTM 18S/19S) for Sentinel-2 spectral indices
            if union_geom.geom_type in ('Point', 'MultiPoint', 'LineString', 'MultiLineString'):
                buf_m = 250.0 if union_geom.geom_type in ('Point', 'MultiPoint') else 50.0
                c_lon = union_geom.centroid.x
                utm_epsg = 32718 if c_lon < -72.0 else 32719
                try:
                    tmp_gs = gpd.GeoSeries([union_geom], crs="EPSG:4326").to_crs(epsg=utm_epsg)
                    union_geom = tmp_gs.buffer(buf_m).to_crs(epsg=4326).iloc[0]
                    logger.info(f"Entidad {union_geom.geom_type} bufferizada isotrópicamente ({buf_m:.0f}m en EPSG:{utm_epsg}).")
                except Exception:
                    deg_buf = buf_m / 111000.0
                    union_geom = union_geom.buffer(deg_buf)

            # Auto-simplify geometry if it exceeds CDSE 1200 vertex limit
            simplified_geom_dict = simplify_geometry_if_needed(union_geom, max_vertices=1200)
            import shapely.geometry as sg
            final_geom = sg.shape(simplified_geom_dict)
            centroid = final_geom.centroid
            bounds = final_geom.bounds  # (minx, miny, maxx, maxy)
            
            return {
                "status": "ok",
                "geometry": simplified_geom_dict,
                "centroid": {"lon": centroid.x, "lat": centroid.y},
                "bounds": list(bounds),
                "feature_count": len(gdf)
            }

    try:
        return await asyncio.to_thread(_sync_parse_geometry, content, ext)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"parse-geometry error: {e}")
        raise HTTPException(500, f"Failed to parse geometry: {e}")

@app.post("/generate-report")
def generate_report(
    request: dict,
    authorization: Optional[str] = Header(None),
    x_cdse_username: Optional[str] = Header(None),
    x_cdse_password: Optional[str] = Header(None),
    x_cdse_client_id: Optional[str] = Header(None),
    x_cdse_client_secret: Optional[str] = Header(None)
):
    try:
        wetland_name = request.get('wetland_name', 'Humedal Desconocido')
        wetland_metadata = request.get('wetland_metadata', {})
        analysis_results = request.get('analysis_results', {})
        start_date = request.get('start_date', '')
        end_date = request.get('end_date', '')
        
        try:
            token = get_cdse_token(
                username=x_cdse_username or "",
                password=x_cdse_password or "",
                client_id=x_cdse_client_id or "",
                client_secret=x_cdse_client_secret or "",
                bearer_token=authorization or ""
            )
        except Exception as te:
            logger.warning(f"Generating report without CDSE token (thumbnails will use fallback): {te}")
            token = None

        doc_buffer = generate_wetland_report(
            wetland_name,
            wetland_metadata,
            analysis_results,
            start_date,
            end_date,
            token
        )
        
        # Clean wetland name for safe ASCII filename
        clean_name = unicodedata.normalize('NFKD', wetland_name).encode('ASCII', 'ignore').decode('ASCII')
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', clean_name)
        safe_name = re.sub(r'_+', '_', safe_name).strip('_') or "Humedal"
        safe_filename = f"Reporte_{safe_name}_{end_date}.docx"
        utf8_filename = urllib.parse.quote(f"Reporte_{wetland_name}_{end_date}.docx")

        return StreamingResponse(
            doc_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{utf8_filename}',
                "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    except Exception as e:
        logger.error(f"Report Error: {e}", exc_info=True)
        raise HTTPException(500, detail=f"Report generation failed: {str(e)}")

@app.get("/")
def read_root():
    return {"status": "Backend running", "service": "Wetland Monitor AI"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
