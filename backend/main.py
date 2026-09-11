import os
import sys
import json
import math
import logging
import io
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
    return f"""
{mode} Analysis Summary:
  Current Median: {stats.get('median', 'N/A'):.4f}
  Range: [{stats.get('min', 'N/A'):.4f}, {stats.get('max', 'N/A'):.4f}]
  Std Dev: {stats.get('std', 'N/A'):.4f}
  Data Points: {stats.get('count', 0)}
  CV: {stats.get('cv', 'N/A'):.2f}%
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

WETLANDS_CATALOGUE_PATH = Path("../frontend/public/wetlands.json")
_wetlands_cache = None

def load_wetlands_catalogue():
    global _wetlands_cache
    if _wetlands_cache is not None:
        return _wetlands_cache
    
    try:
        # Try both relative to current and absolute based on workspace structure
        paths_to_try = [
            WETLANDS_CATALOGUE_PATH,
            Path("wetlands.json"),
            Path("d:/Proyectos_software/wetland-monitor-chile/frontend/public/wetlands.json")
        ]
        
        for p in paths_to_try:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _wetlands_cache = data
                    return data
    except Exception as e:
        logger.error(f"Error loading wetlands catalogue: {e}")
    return []

def get_wetland_geometry(name_or_code: str) -> Optional[dict]:
    """Retrieve geometry for a wetland by name or code from catalogue."""
    catalogue = load_wetlands_catalogue()
    for w in catalogue:
        if str(w.get("name")) == name_or_code or str(w.get("code")) == name_or_code:
            return w.get("geometry")
    return None

# ==========================================
# MODULE: VALIDATORS
# ==========================================

class ValidationError(Exception):
    """Custom exception for validation failures"""
    pass

def validate_date_range(start_date_str: str, end_date_str: str, max_range_days: int = 4015) -> Tuple[datetime, datetime]:
    """Validate date range inputs."""
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValidationError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    
    if start_date >= end_date:
        raise ValidationError("start_date must be before end_date")
    
    if (end_date - start_date).days < 7:
        raise ValidationError("Date range must be at least 7 days")

    SENTINEL2_LAUNCH_DATE = datetime(2015, 6, 23)
    if start_date < SENTINEL2_LAUNCH_DATE:
        raise ValidationError("La constelación Sentinel-2 inició operaciones el 23 de junio de 2015. La fecha de inicio debe ser a partir de julio de 2015.")
        
    return start_date, end_date

def validate_geometry(geojson: Dict[str, Any], min_area_km2: float = 0.01, max_area_km2: float = 1000) -> Dict[str, Any]:
    """Validate GeoJSON geometry for analysis."""
    try:
        geometry = geojson.get('geometry')
        if not geometry:
            raise ValidationError("Missing 'geometry' field in GeoJSON")
        return geometry
    except Exception as e:
        raise ValidationError(f"Geometry validation failed: {e}")

# ==========================================
# MODULE: ROBUST STATS
# ==========================================

def calculate_robust_statistics(data: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Calculate robust statistics resistant to outliers."""
    values = [d['value'] for d in data if d.get('value') is not None]
    if len(values) < 3: return None
    
    values_array = np.array(values)
    mean_val = np.mean(values_array)
    std_val = np.std(values_array)
    cv = (std_val / mean_val * 100) if mean_val != 0 else 0
    p25 = np.percentile(values_array, 25)
    p75 = np.percentile(values_array, 75)
    
    return {
        'mean': float(mean_val),
        'median': float(np.median(values_array)),
        'std': float(std_val),
        'min': float(np.min(values_array)),
        'max': float(np.max(values_array)),
        'count': len(values),
        'cv': float(cv),
        'iqr': float(p75 - p25)
    }

def calculate_sen_slope(dates: List[str], values: List[float]) -> float:
    """Calculate Sen's slope estimator (rate of change in index value per year).
    Robust non-parametric estimator (Sen, 1968; Theil, 1950) immune to missing data and non-normality.
    """
    if len(values) < 3:
        return 0.0
    t = []
    for d in dates:
        try:
            dt = datetime.strptime(d[:10], '%Y-%m-%d')
            year_fraction = dt.year + (dt.timetuple().tm_yday - 1) / 365.25
            t.append(year_fraction)
        except Exception:
            continue
            
    if len(t) != len(values):
        return 0.0

    slopes = []
    n = len(values)
    for i in range(n):
        for j in range(i + 1, n):
            dt = t[j] - t[i]
            if dt > 0.01: # at least ~4 days apart
                slopes.append((values[j] - values[i]) / dt)
    if not slopes:
        return 0.0
    return float(np.median(slopes))

def get_southern_season(month: int) -> str:
    """Classify month into Southern Hemisphere meteorological season."""
    if month in (12, 1, 2):
        return 'summer'  # Verano (DJF)
    elif month in (3, 4, 5):
        return 'autumn'  # Otoño (MAM)
    elif month in (6, 7, 8):
        return 'winter'  # Invierno (JJA)
    else:
        return 'spring'  # Primavera (SON)

def detect_outliers(data: List[Dict[str, Any]], method: str = 'seasonal_iqr', threshold: float = 1.5) -> List[Dict[str, Any]]:
    """Detect and flag outliers in time series data with seasonal awareness.
    Preserves natural seasonal hydroperiod pulses (winter floods, summer lows)
    while flagging true sensor artifacts or residual contamination.
    """
    valid_points = [d for d in data if d.get('value') is not None]
    if len(valid_points) < 4:
        for d in data: d['is_outlier'] = False
        return data

    # Partition by season
    seasonal_groups: Dict[str, List[float]] = {'summer': [], 'autumn': [], 'winter': [], 'spring': []}
    for d in valid_points:
        try:
            m = int(d['date'][5:7])
            season = get_southern_season(m)
            seasonal_groups[season].append(d['value'])
        except Exception:
            pass

    # Overall series fallback bounds
    all_vals = np.array([d['value'] for d in valid_points])
    g_q1 = np.percentile(all_vals, 25)
    g_q3 = np.percentile(all_vals, 75)
    g_iqr = g_q3 - g_q1
    global_lower = g_q1 - threshold * g_iqr
    global_upper = g_q3 + threshold * g_iqr

    # Season-specific bounds (if season has at least 4 observations)
    season_bounds = {}
    for season, s_vals in seasonal_groups.items():
        if len(s_vals) >= 4:
            arr = np.array(s_vals)
            q1 = np.percentile(arr, 25)
            q3 = np.percentile(arr, 75)
            iqr = q3 - q1
            season_bounds[season] = (q1 - threshold * iqr, q3 + threshold * iqr)
        else:
            season_bounds[season] = (global_lower, global_upper)

    for d in data:
        val = d.get('value')
        if val is None:
            d['is_outlier'] = False
            continue
        try:
            m = int(d['date'][5:7])
            season = get_southern_season(m)
            lower, upper = season_bounds.get(season, (global_lower, global_upper))
            d['is_outlier'] = bool(val < lower or val > upper)
        except Exception:
            d['is_outlier'] = bool(val < global_lower or val > global_upper)
            
    return data

def validate_temporal_coverage(data: List[Dict[str, Any]], min_days: int = 30) -> Dict[str, Any]:
    """Validate that time series data has adequate temporal coverage."""
    if not data or len(data) < 2:
        return {'valid': False, 'reason': 'Insufficient data points', 'coverage_days': 0}
    
    dates = []
    for d in data:
        try: dates.append(datetime.strptime(d['date'], '%Y-%m-%d'))
        except: continue
        
    if len(dates) < 2:
        return {'valid': False, 'reason': 'Invalid dates', 'coverage_days': 0}
        
    coverage_days = (max(dates) - min(dates)).days
    return {
        'valid': coverage_days >= min_days,
        'reason': 'Adequate coverage' if coverage_days >= min_days else 'Insufficient coverage',
        'coverage_days': coverage_days,
        'data_points': len(data)
    }

def calculate_trend_statistics(current_data: List[Dict], previous_data: List[Dict]) -> Optional[Dict[str, float]]:
    """Calculate scientifically robust trend statistics comparing two periods and overall time series.
    Uses Absolute Difference Delta and Sen's Slope (Sen, 1968; Theil, 1950) to avoid zero-crossing
    mathematical singularities in normalized remote sensing indices (-1 to +1).
    """
    current_stats = calculate_robust_statistics(current_data)
    previous_stats = calculate_robust_statistics(previous_data)
    
    if not current_stats or not previous_stats: return None
    
    curr_med = current_stats['median']
    prev_med = previous_stats['median']
    delta = curr_med - prev_med
    
    # Calculate Sen's slope across current period time series (rate of change per year)
    dates = [d['date'] for d in current_data if d.get('value') is not None]
    vals = [d['value'] for d in current_data if d.get('value') is not None]
    sen_slope = calculate_sen_slope(dates, vals) if len(vals) >= 3 else 0.0

    return {
        'previous_median': float(round(prev_med, 4)),
        'current_median': float(round(curr_med, 4)),
        'absolute_change': float(round(delta, 4)),
        'sen_slope_per_year': float(round(sen_slope, 4)),
        'trend': float(round(delta, 4))  # Report absolute change as primary trend metric
    }

# ==========================================
# MODULE: REPORT GENERATOR
# ==========================================

_thumb_cache: Dict[str, bytes] = {}

def fetch_thumbnail_bytes(mode: str, start_date: str, end_date: str, geometry: Optional[dict] = None, wetland_name: Optional[str] = None) -> Optional[io.BytesIO]:
    """Fetch Sentinel-2 PNG thumbnail directly from CDSE Process API.
    Bypasses localhost HTTP loopback, uses in-memory caching and strict timeout.
    """
    if not geometry and wetland_name:
        geometry = get_wetland_geometry(wetland_name)
        
    if not geometry:
        return None

    cache_key = f"{wetland_name or 'custom'}_{mode}_{start_date[:10]}_{end_date[:10]}"
    if cache_key in _thumb_cache:
        return io.BytesIO(_thumb_cache[cache_key])

    try:
        token = get_cdse_token()
    except Exception as e:
        logger.error(f"Cannot get CDSE token for thumbnail: {e}")
        return None

    try:
        import shapely.geometry as sg
        geom = sg.shape(geometry)
        bounds = list(geom.bounds)

        evalscript = get_evalscript(mode, is_process=True)
        payload = {
            "input": {
                "bounds": {
                    "bbox": bounds,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                        "maxCloudCoverage": 25
                    }
                }]
            },
            "output": {
                "width": 512,
                "height": 512,
                "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
            },
            "evalscript": evalscript
        }

        resp = http_session.post(
            "https://sh.dataspace.copernicus.eu/api/v1/process",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=12
        )
        if resp.status_code == 200 and resp.content:
            _thumb_cache[cache_key] = resp.content
            return io.BytesIO(resp.content)
        else:
            logger.warning(f"CDSE Process API returned status {resp.status_code} for {mode} thumbnail")
            return None
    except Exception as e:
        logger.warning(f"Failed fetching thumbnail for {mode} ({start_date} - {end_date}): {e}")
        return None

def download_image(url: str) -> Optional[io.BytesIO]:
    """Fallback legacy image downloader with short timeout."""
    if not url: return None
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        return io.BytesIO(response.content)
    except Exception as e:
        logger.warning(f"Error downloading image from URL {url[:60]}: {e}")
        return None

def get_vis_params(mode: str) -> Dict[str, Any]:
    """Get visualization parameters."""
    if mode == "Hydrology": return {'min': -1, 'max': 1, 'palette': ['FF0000', 'FFFFFF', '0000FF']}
    elif mode == "Vegetation": return {'min': 0, 'max': 0.8, 'palette': ['FF0000', 'FFFF00', '00FF00', '006400']}
    elif mode == "WaterQuality": return {'min': -0.1,  'max': 0.5, 'palette': ['0000FF', '00FFFF', 'FFFF00', 'FF0000']}
    elif mode == "SoilVegetation": return {'min': 0, 'max': 1, 'palette': ['FFFFFF', 'CE7E45', 'DF923D', 'F1B555', 'FCD163', '99B718', '74A901', '66A000', '529400', '3E8601', '207401', '056201', '004C00', '023B01', '012E01', '011D01', '011301']}
    elif mode == "AlgaeBloom": return {'min': -0.05, 'max': 0.2, 'palette': ['0000FF', '00FFFF', '00FF00', 'FFFF00', 'FF0000', '8B0000']}
    elif mode == "WaterRatio": return {'min': -1, 'max': 1, 'palette': ['FF0000', 'FFA500', 'FFFF00', 'FFFFFF', '00FFFF', '0000FF']}
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
    """Create a legend image for the specific mode."""
    params = get_vis_params(mode)
    palette_hex = [f"#{c}" for c in params['palette']]
    fig, ax = plt.subplots(figsize=(6, 1))
    fig.subplots_adjust(bottom=0.5)
    cmap = mcolors.LinearSegmentedColormap.from_list("custom_cmap", palette_hex)
    norm = mcolors.Normalize(vmin=params['min'], vmax=params['max'])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=ax, orientation='horizontal')
    cb.set_label(f'Valor {get_index_name(mode)}')
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    img_buffer.seek(0)
    return img_buffer

def create_temporal_chart(time_series: List[Dict], mode: str) -> io.BytesIO:
    """Create a temporal chart for a specific mode."""
    dates = [point['date'] for point in time_series if point.get('value') is not None]
    values = [point['value'] for point in time_series if point.get('value') is not None]
    if not dates: return None
    
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(dates, values, marker='o', linewidth=2, markersize=4)
    ax.set_title(f'Serie Temporal - {mode}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Fecha', fontsize=10)
    ax.set_ylabel('Valor del Índice', fontsize=10)
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45, ha='right')
    
    if len(dates) > 20:
        nth = len(dates) // 10
        for i, label in enumerate(ax.xaxis.get_ticklabels()):
            if i % nth != 0: label.set_visible(False)
            
    plt.tight_layout()
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    img_buffer.seek(0)
    return img_buffer

def generate_wetland_report(wetland_name: str, wetland_metadata: Dict, analysis_results: Dict, start_date: str, end_date: str) -> io.BytesIO:
    """Generate a comprehensive Word report for wetland analysis."""
    doc = Document()
    header = doc.sections[0].header
    header.paragraphs[0].text = "WETLAND MONITOR - REPORTE DE ANÁLISIS"
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
    meta_table.rows[4].cells[0].text = 'Fecha'
    meta_table.rows[4].cells[1].text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    doc.add_paragraph()
    
    doc.add_heading('Período de Análisis', level=2)
    p = doc.add_paragraph()
    p.add_run('Desde: ').bold = True
    p.add_run(start_date)
    p.add_run(' | ')
    p.add_run('Hasta: ').bold = True
    p.add_run(end_date)
    doc.add_paragraph()
    
    doc.add_heading('Resultados del Análisis', level=2)
    modes = ['Hydrology', 'Vegetation', 'WaterQuality', 'SoilVegetation', 'AlgaeBloom', 'WaterRatio']

    # Pre-fetch all required thumbnails in parallel (max 6 workers, direct CDSE, zero localhost loopback)
    geometry = wetland_metadata.get('geometry') or get_wetland_geometry(wetland_name)
    thumbnails_map: Dict[Tuple[str, str], Optional[io.BytesIO]] = {}
    
    if geometry:
        import concurrent.futures
        tasks = []
        try:
            start_dt = datetime.strptime(start_date[:10], '%Y-%m-%d')
            end_dt = datetime.strptime(end_date[:10], '%Y-%m-%d')
            s_end = (start_dt + relativedelta(years=1)).strftime('%Y-%m-%d')
            e_start = (end_dt - relativedelta(years=1)).strftime('%Y-%m-%d')
        except Exception:
            s_end = start_date
            e_start = end_date

        for m in modes:
            if m in analysis_results:
                tasks.append((m, 'start', start_date, s_end))
                tasks.append((m, 'end', e_start, end_date))

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            future_to_task = {
                executor.submit(fetch_thumbnail_bytes, m, s, e, geometry, wetland_name): (m, kind)
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
    
    for mode in modes:
        if mode not in analysis_results: continue
        res = analysis_results[mode]
        stats = res.get('stats', {})
        maps = res.get('maps', {})
        
        doc.add_heading(f'{mode} - {get_index_name(mode)}', level=3)
        doc.add_paragraph(get_mode_description(mode), style='Intense Quote')
        
        st_table = doc.add_table(rows=8, cols=2)
        st_table.style = 'Light List Accent 1'
        st_table.rows[0].cells[0].text = 'Valor Actual (Mediana)'
        st_table.rows[0].cells[1].text = f"{stats.get('current', 0):.4f}"
        st_table.rows[1].cells[0].text = 'Valor Año Anterior'
        st_table.rows[1].cells[1].text = f"{stats.get('last', 0):.4f}"
        
        trend = stats.get('trend', 0)
        trend_cell = st_table.rows[2].cells[1]
        trend_cell.text = f"{trend:+.4f} (Δ Mediana)"
        st_table.rows[2].cells[0].text = 'Tendencia (Cambio Absoluto)'
        color = RGBColor(34, 197, 94) if trend > 0 else RGBColor(239, 68, 68)
        trend_cell.paragraphs[0].runs[0].font.color.rgb = color

        sen_slope = stats.get('sen_slope', 0.0)
        sen_cell = st_table.rows[3].cells[1]
        sen_cell.text = f"{sen_slope:+.4f} / año (Theil-Sen)"
        st_table.rows[3].cells[0].text = 'Pendiente de Tendencia'
        
        st_table.rows[4].cells[0].text = 'Desviación Estándar'
        st_table.rows[4].cells[1].text = f"{stats.get('current_std', 0):.4f}"
        st_table.rows[5].cells[0].text = 'Coeficiente de Variación'
        st_table.rows[5].cells[1].text = f"{stats.get('cv', 0):.2f}%"
        st_table.rows[6].cells[0].text = 'Puntos de Datos'
        st_table.rows[6].cells[1].text = str(stats.get('data_count', 0))
        st_table.rows[7].cells[0].text = 'Valores Atípicos (Estacionales)'
        st_table.rows[7].cells[1].text = str(stats.get('outlier_count', 0))
        doc.add_paragraph()
        
        doc.add_heading('Mapas del Índice', level=4)
        
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
            if img_start:
                c_start.paragraphs[0].add_run().add_picture(img_start, width=Inches(2.8))
                c_cap_start.text = f"Mapa Inicial ({start_date})"
                c_cap_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                
            c_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if img_end:
                c_end.paragraphs[0].add_run().add_picture(img_end, width=Inches(2.8))
                c_cap_end.text = f"Mapa Final ({end_date})"
                c_cap_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                
        legend = create_legend_image(mode)
        if legend:
            doc.add_picture(legend, width=Inches(5))
            doc.add_paragraph("Escala de Valores (Válida para ambos mapas)", style='Caption')
            
        doc.add_heading('Evolución Temporal', level=4)
        ts = res.get('time_series', [])
        if ts:
            chart = create_temporal_chart(ts, mode)
            if chart: doc.add_picture(chart, width=Inches(6))
            
        doc.add_page_break()
        
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from dotenv import load_dotenv
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path)
load_dotenv()

# Support both password grant (email+password) and client_credentials
CDSE_USERNAME = os.getenv("CDSE_USERNAME", "")
CDSE_PASSWORD = os.getenv("CDSE_PASSWORD", "")
CDSE_CLIENT_ID = os.getenv("CDSE_CLIENT_ID", "")
CDSE_CLIENT_SECRET = os.getenv("CDSE_CLIENT_SECRET", "")

if not (CDSE_USERNAME and CDSE_PASSWORD) and not (CDSE_CLIENT_ID and CDSE_CLIENT_SECRET):
    logger.warning("CDSE credentials not found in env. Configure via UI or .env file.")

# Robust HTTP session with connection pooling and retries
http_session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False
)
http_session.mount("https://", HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20))

# Thread-safe token caching
_cdse_token_cache = {
    "token": None,
    "expires_at": 0.0
}
_cdse_token_lock = threading.Lock()

def invalidate_cdse_token():
    """Invalidate token cache (e.g., when credentials are updated)."""
    with _cdse_token_lock:
        _cdse_token_cache["token"] = None
        _cdse_token_cache["expires_at"] = 0.0

def get_cdse_token() -> str:
    """Retrieve OAuth token for Copernicus Data Space Ecosystem with thread-safe caching.
    Supports two grant types:
    1. password grant: email + password (standard user account at dataspace.copernicus.eu)
    2. client_credentials: OAuth client_id + client_secret (service account)
    """
    global _cdse_token_cache
    now = time.time()
    
    with _cdse_token_lock:
        # Re-use cached token if still valid with a 60s safety buffer
        if _cdse_token_cache["token"] and now < (_cdse_token_cache["expires_at"] - 60):
            return _cdse_token_cache["token"]

        token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        
        if CDSE_USERNAME and CDSE_PASSWORD:
            from oauthlib.oauth2 import LegacyApplicationClient
            from requests_oauthlib import OAuth2Session
            client = LegacyApplicationClient(client_id="cdse-public")
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, username=CDSE_USERNAME, password=CDSE_PASSWORD)
            token = token_resp['access_token']
            expires_in = token_resp.get('expires_in', 600)
            _cdse_token_cache["token"] = token
            _cdse_token_cache["expires_at"] = now + float(expires_in)
            return token
            
        elif CDSE_CLIENT_ID and CDSE_CLIENT_SECRET:
            from oauthlib.oauth2 import BackendApplicationClient
            from requests_oauthlib import OAuth2Session
            client = BackendApplicationClient(client_id=CDSE_CLIENT_ID)
            oauth = OAuth2Session(client=client)
            token_resp = oauth.fetch_token(token_url=token_url, client_id=CDSE_CLIENT_ID, client_secret=CDSE_CLIENT_SECRET)
            token = token_resp['access_token']
            expires_in = token_resp.get('expires_in', 600)
            _cdse_token_cache["token"] = token
            _cdse_token_cache["expires_at"] = now + float(expires_in)
            return token
            
        else:
            raise RuntimeError("No CDSE credentials configured. Use the Credentials panel in the app or .env file.")

class AnalysisRequest(BaseModel):
    geojson: Dict[str, Any]
    startDate: str
    endDate: str
    projectId: str = None
    mode: str = "Hydrology"
    wetlandName: str = None

def normalize_index_value(value, mode):
    """Normalize index values for consistent charting."""
    if value is None: return None
    
    ranges = {
        'Hydrology': (-1, 1),      # MNDWI
        'Vegetation': (0, 0.8),    # NDRE
        'WaterQuality': (-0.1, 0.5), # NDCI
        'SoilVegetation': (0, 1),  # SAVI
        'AlgaeBloom': (-0.05, 0.2), # FAI
        'WaterRatio': (-1, 1)      # WRI
    }
    
    if mode == 'WaterRatio':
        # Logarithmic normalization for WRI
        if value <= 0: return -1
        try:
            log_val = math.log10(value)
            return max(-1, min(1, log_val))
        except: return -1
        
    min_v, max_v = ranges.get(mode, (-1, 1))
    return max(min_v, min(max_v, value))

def hex_to_rgb01(hex_str: str) -> List[float]:
    """Convert hex color string to normalized RGB float array [0, 1]."""
    hex_str = hex_str.lstrip('#')
    return [
        round(int(hex_str[0:2], 16) / 255.0, 4),
        round(int(hex_str[2:4], 16) / 255.0, 4),
        round(int(hex_str[4:6], 16) / 255.0, 4)
    ]

def get_evalscript(mode: str, is_process: bool = True) -> str:
    """Generate Sentinel-2 Evalscript for CDSE Process API (visual map tiles) or Statistical API (timeseries data).
    Scientifically audited and calibrated:
    1. FAI (Floating Algae Index) calibrated to Sentinel-2 MSI exact central wavelengths:
       Factor = (842 - 665) / (1610 - 665) = 177 / 945 = 0.1873 (Hu, 2009).
    2. Pixel-level quality filtering using Sen2Cor Scene Classification Layer (SCL):
       Excludes cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11).
    """
    bands_map = {
        'Hydrology': ('"B03", "B11"', 'let val = index(sample.B03, sample.B11);'),
        'Vegetation': ('"B08", "B05"', 'let val = index(sample.B08, sample.B05);'),
        'WaterQuality': ('"B04", "B05"', 'let val = index(sample.B05, sample.B04);'),
        'SoilVegetation': ('"B04", "B08"', 'let val = ((sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 0.5)) * 1.5;'),
        'AlgaeBloom': ('"B04", "B08", "B11"', 'let val = sample.B08 - (sample.B04 + (sample.B11 - sample.B04) * 0.1873);'),
        'WaterRatio': ('"B03", "B04", "B08", "B11"', 'let val = (sample.B03 + sample.B04) / (sample.B08 + sample.B11);')
    }

    if is_process:
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

        in_bands, expr = bands_map.get(mode, ('"B04", "B08"', 'let val = index(sample.B08, sample.B04);'))

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
  // 2. SCL Quality Filter: mask cloud shadows (3), clouds (8, 9), thin cirrus (10) and snow/ice (11)
  if (sample.SCL === 3 || sample.SCL === 8 || sample.SCL === 9 || sample.SCL === 10 || sample.SCL === 11) {{
    return [0, 0, 0, 0];
  }}
  {expr}
  if (isNaN(val)) return [0, 0, 0, 0];
  let rgb = visualizer.process(val);
  return [rgb[0], rgb[1], rgb[2], 0.85];
}}"""

    else:
        # Statistical API (aggregations, counts, stats in FLOAT32) with SCL Quality Masking
        in_bands, expr = bands_map.get(mode, ('"B04", "B08"', 'let val = index(sample.B08, sample.B04);'))
        return f"""//VERSION=3
function setup() {{
  return {{
    input: [{in_bands}, "SCL", "dataMask"],
    output: [
      {{ id: "default", bands: 1, sampleType: "FLOAT32" }},
      {{ id: "dataMask", bands: 1, sampleType: "UINT8" }}
    ]
  }};
}}
function evaluatePixel(sample) {{
  // Exclude no-data, cloud shadows (3), clouds (8, 9), cirrus (10) and snow (11)
  if (sample.dataMask === 0 || sample.SCL === 3 || sample.SCL === 8 || sample.SCL === 9 || sample.SCL === 10 || sample.SCL === 11) {{
    return {{ default: [NaN], dataMask: [0] }};
  }}
  {expr}
  if (isNaN(val)) {{
    return {{ default: [NaN], dataMask: [0] }};
  }}
  return {{ default: [val], dataMask: [1] }};
}}"""

def analyze_period(aoi, start_date, end_date, mode):
    """Analyze a specific period for time series data using CDSE API with grid splitting for high resolution."""
    token = get_cdse_token()
    url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    evalscript = get_evalscript(mode, is_process=False)

    import shapely.geometry as sg
    import concurrent.futures

    try:
        geom = sg.shape(aoi)
    except Exception:
        return []

    minx, miny, maxx, maxy = geom.bounds
    lon_span = abs(maxx - minx)
    lat_span = abs(maxy - miny)

    # Max chunk size: 0.1 degrees (~11km). 
    max_chunk_deg = 0.1
    # Safeguard for extremely large areas (e.g., > 100km scale) to prevent generating too many chunks
    if lon_span > 1.0 or lat_span > 1.0:
        max_chunk_deg = max(lon_span, lat_span) / 10.0

    # Base resolution is 10m (~0.00009 deg). 
    # If the chunk is artificially scaled up to prevent excessive chunks, we ensure 
    # the resolution stays within the CDSE 1500px/chunk constraint.
    deg_res = max(0.00009, max_chunk_deg / 1500.0)

    chunks = []
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
                    # Add individual polygons to chunks
                    for poly in intersection.geoms:
                        chunks.append(poly)
            y += max_chunk_deg
        x += max_chunk_deg

    if not chunks:
        chunks = [geom]

    def fetch_chunk(chunk_geom):
        payload = {
            "input": {
                "bounds": {
                    "geometry": chunk_geom.__geo_interface__,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                        "maxCloudCoverage": 20
                    }
                }]
            },
            "aggregation": {
                "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                "aggregationInterval": {"of": "P1D"},
                "evalscript": evalscript,
                "resx": deg_res,
                "resy": deg_res
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
                logger.error(f"CDSE API Error on chunk: {resp.text[:300]}")
                return []
            
            chunk_ts = []
            data = resp.json()
            for interval in data.get('data', []):
                date_str = interval.get('interval', {}).get('from', '')[:10]
                outputs = interval.get('outputs', {})
                if outputs and 'default' in outputs:
                    band_stats = outputs['default']
                    val_raw = band_stats.get('bands', {}).get('B0', {}).get('stats', {}).get('mean')
                    try:
                        val = float(val_raw) if val_raw is not None else None
                        if val is not None and math.isnan(val):
                            val = None
                    except (ValueError, TypeError):
                        val = None
                    
                    metrics = band_stats.get('bands', {}).get('B0', {}).get('stats', {})
                    count = metrics.get('sampleCount', 0)
                    if val is not None and count > 0:
                        chunk_ts.append({'date': date_str, 'value': val, 'weight': count})
            return chunk_ts
        except Exception as ex:
            logger.error(f"Network error on chunk: {ex}")
            return []

    all_series = []
    # Use max 5 workers to parallelize without triggering rate limits excessively
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_chunk, ch) for ch in chunks]
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res:
                    all_series.extend(res)
            except Exception as e:
                logger.error(f"Error fetching chunk: {e}")

    # Aggregate back by date (weighted average of medians based on valid pixels)
    aggregated = {}
    for pt in all_series:
        d = pt['date']
        if d not in aggregated:
            aggregated[d] = {'sum_val': 0, 'sum_wt': 0}
        aggregated[d]['sum_val'] += pt['value'] * pt['weight']
        aggregated[d]['sum_wt'] += pt['weight']
        
    final_series = []
    for d, acc in aggregated.items():
        if acc['sum_wt'] > 0:
            final_series.append({'date': d, 'value': acc['sum_val'] / acc['sum_wt']})
            
    final_series.sort(key=lambda x: x['date'])
    return final_series

def generate_map_url(aoi, start, end, mode, wetland_name=None):
    """Generate absolute URL map tile paths for RGB and metric visualization."""
    BASE_URL = (os.getenv("BACKEND_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://wetland-monitor-chile.onrender.com").rstrip("/")
    suffix = f"?wetland={wetland_name}" if wetland_name else ""
    return {
        "rgb": f"{BASE_URL}/api/tiles/rgb/{start}/{end}/{{z}}/{{x}}/{{y}}{suffix}",
        "metric": f"{BASE_URL}/api/tiles/metric/{mode}/{start}/{end}/{{z}}/{{x}}/{{y}}{suffix}",
        "thumb_url": f"{BASE_URL}/api/thumb/{mode}/{start}/{end}{suffix}"
    }

def perform_single_analysis(request: AnalysisRequest, mode):
    """Perform robust analysis for a single mode."""
    try:
        logger.info(log_process_stage('', mode, 'processing'))
        start_obj, end_obj = validate_date_range(request.startDate, request.endDate)
        aoi = validate_geometry(request.geojson)
        wetland_name = request.wetlandName
        
        current_data = analyze_period(aoi, request.startDate, request.endDate, mode)
        if not current_data or len(current_data) < 3:
            raise ValidationError(f"Insufficient data for {mode}")
            
        coverage = validate_temporal_coverage(current_data)
        if not coverage['valid']:
            raise ValidationError(f"Temporal coverage issue: {coverage['reason']}")
            
        current_stats = calculate_robust_statistics(current_data)
        current_data_flagged = detect_outliers(current_data)
        outlier_count = sum(1 for d in current_data_flagged if d.get('is_outlier'))
        
        last_start = (start_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        last_end = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        last_data = analyze_period(aoi, last_start, last_end, mode)
        trend_stats = calculate_trend_statistics(current_data_flagged, last_data) or {}
        
        # Start/End Year Maps
        start_year_end = (start_obj + relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_start = generate_map_url(aoi, request.startDate, start_year_end, mode, wetland_name)
        end_year_start = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_end = generate_map_url(aoi, end_year_start, request.endDate, mode, wetland_name)
        
        maps = {
            "rgb": maps_end["rgb"],
            "metric": maps_end["metric"],
            "start_year": maps_start,
            "end_year": maps_end
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
                "current": safe_float(current_stats['median']),
                "current_mean": safe_float(current_stats['mean']),
                "current_std": safe_float(current_stats['std']),
                "last": safe_float(trend_stats.get('previous_median', 0)),
                "trend": safe_float(trend_stats.get('trend', 0)),
                "trend_delta": safe_float(trend_stats.get('absolute_change', 0)),
                "sen_slope": safe_float(trend_stats.get('sen_slope_per_year', 0)),
                "outlier_count": outlier_count,
                "data_count": current_stats['count'],
                "cv": safe_float(current_stats['cv'])
            },
            "time_series": current_data_flagged,
            "maps": maps,
            "coverage": coverage
        }
        
        # Normalize time series for display
        normalized_series = []
        for point in current_data_flagged:
            p = point.copy()
            p['value_raw'] = safe_float(point['value'], None)
            p['value'] = safe_float(normalize_index_value(point['value'], mode), None)
            normalized_series.append(p)
        result['time_series'] = normalized_series
        
        logger.info(log_process_stage('', mode, 'completed'))
        return result
        
    except Exception as e:
        logger.error(f"{mode} error: {e}")
        return create_error_response(e, mode)

@app.post("/analyze")
async def analyze(request: AnalysisRequest, authorization: str = Header(None)):
    # Auth validation removed as CDSE will use service credentials
    try:
        res = perform_single_analysis(request, request.mode)
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
                "maps": res['maps']
            }
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/analyze-all")
async def analyze_all(request: AnalysisRequest, authorization: str = Header(None)):
    try:
        results = {}
        modes = ["Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"]
        
        for m in modes:
            logger.info(log_process_stage('', m, 'processing'))
            results[m] = perform_single_analysis(request, m)
            
        logger.info(log_process_stage('', None, 'final'))
        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"Analyze-all Error: {e}")
        raise HTTPException(500, detail=f"Backend Error: {str(e)}")

# TILE PROXY
from fastapi.responses import Response
def xyz_to_bbox(x, y, z):
    n = 2.0 ** z
    lon_left = x / n * 360.0 - 180.0
    lat_top = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lon_right = (x + 1) / n * 360.0 - 180.0
    lat_bottom = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return [lon_left, lat_bottom, lon_right, lat_top]

@app.get("/api/tiles/rgb/{start_date}/{end_date}/{z}/{x}/{y}")
async def get_rgb_tile(start_date: str, end_date: str, z: int, x: int, y: int, wetland: str = None):
    """Serve an RGB Sentinel-2 tile for the given date range and tile coordinates."""
    token = get_cdse_token()
    bbox = xyz_to_bbox(x, y, z)
    evalscript = get_evalscript("RGB", is_process=True)
    
    geometry = None
    if wetland:
        geometry = get_wetland_geometry(wetland)

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
                    "maxCloudCoverage": 20
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
    
    if geometry:
        payload["input"]["bounds"]["geometry"] = geometry

    try:
        resp = http_session.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=30)
        if resp.status_code == 200:
            return Response(content=resp.content, media_type="image/png")
        else:
            logger.error(f"RGB tile error {resp.status_code}: {resp.text[:200]}")
            return Response(content=b"", media_type="image/png", status_code=resp.status_code)
    except Exception as ex:
        logger.error(f"RGB tile network error: {ex}")
        return Response(content=b"", media_type="image/png", status_code=500)

@app.get("/api/tiles/metric/{mode}/{start_date}/{end_date}/{z}/{x}/{y}")
async def get_metric_tile(mode: str, start_date: str, end_date: str, z: int, x: int, y: int, wetland: str = None):
    """Serve a spectral index tile for the given mode, date range, and tile coordinates."""
    token = get_cdse_token()
    bbox = xyz_to_bbox(x, y, z)
    evalscript = get_evalscript(mode, is_process=True)
    
    geometry = None
    if wetland:
        geometry = get_wetland_geometry(wetland)

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
                    "maxCloudCoverage": 20
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
    
    if geometry:
        payload["input"]["bounds"]["geometry"] = geometry

    try:
        resp = http_session.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=30)
        if resp.status_code == 200:
            return Response(content=resp.content, media_type="image/png")
        else:
            logger.error(f"Metric tile error {resp.status_code}: {resp.text[:200]}")
            return Response(content=b"", media_type="image/png", status_code=resp.status_code)
    except Exception as ex:
        logger.error(f"Metric tile network error: {ex}")
        return Response(content=b"", media_type="image/png", status_code=500)

@app.get("/api/thumb/{mode}/{start_date}/{end_date}")
async def get_thumbnail(mode: str, start_date: str, end_date: str, wetland: str = None):
    """Serve a static Sentinel-2 thumbnail image with caching."""
    img_io = await asyncio.to_thread(fetch_thumbnail_bytes, mode, start_date, end_date, None, wetland)
    if not img_io:
        return Response(content=b"", media_type="image/png", status_code=404)
    return Response(content=img_io.getvalue(), media_type="image/png")

@app.post("/verify-credentials")
async def verify_credentials(payload: dict):
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
async def set_credentials(payload: dict):
    """Set CDSE credentials at runtime. Accepts either:  
    - {username, password} for standard Copernicus accounts  
    - {client_id, client_secret} for OAuth service accounts
    """
    global CDSE_USERNAME, CDSE_PASSWORD, CDSE_CLIENT_ID, CDSE_CLIENT_SECRET
    
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    client_id = payload.get("client_id", "").strip()
    client_secret = payload.get("client_secret", "").strip()
    
    if username and password:
        CDSE_USERNAME = username
        CDSE_PASSWORD = password
        CDSE_CLIENT_ID = ""
        CDSE_CLIENT_SECRET = ""
    elif client_id and client_secret:
        CDSE_CLIENT_ID = client_id
        CDSE_CLIENT_SECRET = client_secret
        CDSE_USERNAME = ""
        CDSE_PASSWORD = ""
    else:
        raise HTTPException(400, "Provide either {username + password} or {client_id + client_secret}")
    
    invalidate_cdse_token()
    
    # Verify credentials work
    try:
        get_cdse_token()
    except Exception as e:
        CDSE_USERNAME = ""
        CDSE_PASSWORD = ""
        CDSE_CLIENT_ID = ""
        CDSE_CLIENT_SECRET = ""
        raise HTTPException(401, f"Fallo de autenticación con Copernicus: {e}")
    return {"status": "ok", "message": "Acceso a Copernicus verificado y activado con éxito"}

@app.get("/credentials-status")
async def credentials_status():
    """Check whether CDSE credentials are configured."""
    has_password = bool(CDSE_USERNAME and CDSE_PASSWORD)
    has_oauth = bool(CDSE_CLIENT_ID and CDSE_CLIENT_SECRET)
    return {"configured": has_password or has_oauth, "method": "password" if has_password else ("oauth" if has_oauth else "none")}

@app.post("/parse-geometry")
async def parse_geometry(file: UploadFile):
    """Accept a SHP (zip), GeoJSON, KML or KMZ file and return a GeoJSON geometry."""
    import tempfile
    import zipfile as zipmod
    
    filename = file.filename or ""
    ext = filename.lower().split(".")[-1]
    content = await file.read()
    
    try:
        import geopandas as gpd
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Handle KMZ (zipped KML)
            if ext == "kmz":
                kmz_path = os.path.join(tmpdir, "file.kmz")
                with open(kmz_path, "wb") as f:
                    f.write(content)
                with zipmod.ZipFile(kmz_path, "r") as z:
                    z.extractall(tmpdir)
                # Find .kml inside
                kml_files = [f for f in os.listdir(tmpdir) if f.lower().endswith(".kml")]
                if not kml_files:
                    raise HTTPException(400, "No KML found inside KMZ")
                file_path = os.path.join(tmpdir, kml_files[0])
                gdf = gpd.read_file(file_path, driver="KML")
            # Handle Shapefile (expects a zip containing .shp, .dbf, .shx etc)
            elif ext == "zip":
                shp_path = os.path.join(tmpdir, "file.zip")
                with open(shp_path, "wb") as f:
                    f.write(content)
                gdf = gpd.read_file(f"zip://{shp_path}")
            # Handle KML
            elif ext == "kml":
                kml_path = os.path.join(tmpdir, "file.kml")
                with open(kml_path, "wb") as f:
                    f.write(content)
                gdf = gpd.read_file(kml_path, driver="KML")
            # Handle GeoJSON
            elif ext in ("geojson", "json"):
                geojson_path = os.path.join(tmpdir, "file.geojson")
                with open(geojson_path, "wb") as f:
                    f.write(content)
                gdf = gpd.read_file(geojson_path)
            else:
                raise HTTPException(400, f"Unsupported format: .{ext}. Use SHP (zip), GeoJSON, KML or KMZ")
            
            # Reproject to WGS84
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
            
            # Merge all features into one geometry
            union_geom = gdf.unary_union
            centroid = union_geom.centroid
            bounds = union_geom.bounds  # (minx, miny, maxx, maxy)
            
            return {
                "status": "ok",
                "geometry": json.loads(gpd.GeoSeries([union_geom]).to_json())["features"][0]["geometry"],
                "centroid": {"lon": centroid.x, "lat": centroid.y},
                "bounds": list(bounds),
                "feature_count": len(gdf)
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"parse-geometry error: {e}")
        raise HTTPException(500, f"Failed to parse geometry: {e}")

@app.post("/generate-report")
async def generate_report(request: dict):
    try:
        wetland_name = request.get('wetland_name', 'Humedal Desconocido')
        wetland_metadata = request.get('wetland_metadata', {})
        analysis_results = request.get('analysis_results', {})
        start_date = request.get('start_date', '')
        end_date = request.get('end_date', '')
        
        # Run report generation in worker thread to prevent event loop blocking
        doc_buffer = await asyncio.to_thread(
            generate_wetland_report,
            wetland_name,
            wetland_metadata,
            analysis_results,
            start_date,
            end_date
        )
        filename = f"Reporte_{wetland_name.replace(' ', '_')}_{end_date}.docx"
        
        return StreamingResponse(
            doc_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"Report Error: {e}")
        raise HTTPException(500, detail=f"Report generation failed: {str(e)}")

@app.get("/")
def read_root():
    return {"status": "Backend running", "service": "Wetland Monitor AI"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
