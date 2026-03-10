import os
import sys
import ee
import json
import math
import logging
import io
import numpy as np
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import concurrent.futures

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dateutil.relativedelta import relativedelta

from fastapi import FastAPI, HTTPException, Header, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import geopandas as gpd
import tempfile
import shutil
import zipfile
import fiona
import pandas as pd
from shapely.ops import transform

def to_2d(x, y, z=None):
    """Force 2D coordinates for GE compatibility."""
    return (x, y)

fiona.drvsupport.supported_drivers['KML'] = 'rw' # Enable KML support
fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# MODULE: UTILS & LOGGING
# ==========================================

def setup_logging(log_file: str = 'wetland_analysis.log', level: int = logging.INFO) -> logging.Logger:
    """Configure logging for the application."""
    logger = logging.getLogger('wetland_monitor')
    logger.setLevel(level)
    if logger.handlers:
        return logger
    
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
    icons = {'processing': '⚙️', 'completed': '✓', 'error': '✗', 'info': 'ℹ️'}
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
        
    return start_date, end_date

def validate_geometry(geojson: Dict[str, Any], min_area_km2: float = 0.01, max_area_km2: float = 1000) -> ee.Geometry:
    """Validate GeoJSON geometry for analysis."""
    try:
        geometry = geojson.get('geometry')
        if not geometry:
            logger.error("Missing 'geometry' field in GeoJSON")
            raise ValidationError("Missing 'geometry' field in GeoJSON")
        
        geom_type = geometry.get('type')
        
        # Force 2D geometries (GEE can fail with 3D from KML)
        # Simple recursion to strip Z if present in coordinates
        def strip_z(coords):
            if not isinstance(coords, (list, tuple)):
                return coords
            if len(coords) > 0 and isinstance(coords[0], (int, float)):
                return list(coords[:2])
            return [strip_z(c) for c in coords]
        
        if 'coordinates' in geometry:
            geometry['coordinates'] = strip_z(geometry['coordinates'])

        aoi = ee.Geometry(geometry)
        aoi = aoi.simplify(maxError=1)
        return aoi
    except Exception as e:
        logger.error(f"Geometry validation failed: {e}")
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

def detect_outliers(data: List[Dict[str, Any]], method: str = 'iqr', threshold: float = 1.5) -> List[Dict[str, Any]]:
    """Detect and flag outliers in time series data."""
    values = [d['value'] for d in data if d.get('value') is not None]
    if len(values) < 4:
        for d in data: d['is_outlier'] = False
        return data
    
    values_array = np.array(values)
    q1 = np.percentile(values_array, 25)
    q3 = np.percentile(values_array, 75)
    iqr = q3 - q1
    lower = q1 - threshold * iqr
    upper = q3 + threshold * iqr
    
    for d in data:
        if d.get('value') is not None:
            d['is_outlier'] = bool(d['value'] < lower or d['value'] > upper)
        else:
            d['is_outlier'] = False
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
    """Calculate trend statistics comparing two periods."""
    current_stats = calculate_robust_statistics(current_data)
    previous_stats = calculate_robust_statistics(previous_data)
    
    if not current_stats or not previous_stats: return None
    
    curr_med = current_stats['median']
    prev_med = previous_stats['median']
    
    trend_pct = None
    # Lower threshold for detecting trends in wetlands (from 0.01 to 0.001)
    if abs(prev_med) > 0.001:
        trend_pct = ((curr_med - prev_med) / prev_med) * 100
        trend_pct = max(min(trend_pct, 1000), -1000)
    
    return {
        'previous_median': prev_med,
        'current_median': curr_med,
        'trend_percent': trend_pct,
        'absolute_change': curr_med - prev_med
    }

# ==========================================
# MODULE: REPORT GENERATOR
# ==========================================

def download_image(url: str) -> io.BytesIO:
    """Download image from URL to BytesIO."""
    if not url: return None
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return io.BytesIO(response.content)
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
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
        'Hydrology': 'El índice MNDWI (Modified Normalized Difference Water Index) se utiliza para realzar cuerpos de agua abiertos y áreas de alta humedad. Valores positivos indican presencia de agua superficial, mientras que valores negativos representan suelo o vegetación seca.',
        'Vegetation': 'El índice NDRE (Normalized Difference Red Edge) es sensible al contenido de clorofila en la vegetación densa. Es fundamental para monitorear el vigor fotosintético y detectar estrés hídrico temprano en vegetación de humedal.',
        'WaterQuality': 'El índice NDCI (Normalized Difference Chlorophyll Index) permite estimar la concentración de clorofila-a en cuerpos de agua. Es un indicador clave del estado trófico y la posible presencia de fitoplancton en aguas lénticas.',
        'SoilVegetation': 'El índice SAVI (Soil Adjusted Vegetation Index) minimiza la influencia del brillo del suelo en el análisis de vegetación. Es ideal para humedales con cobertura vegetal dispersa o estacional.',
        'AlgaeBloom': 'El índice FAI (Floating Algae Index) detecta vegetación flotante y floraciones algales en la superficie del agua. Es crucial para identificar procesos de eutrofización y blooms de cianobacterias.',
        'WaterRatio': 'El índice WRI (Water Ratio Index) es un clasificador robusto para la discriminación entre superficies de agua y tierra. Valores > 1 indican una alta probabilidad de superficie acuática pura.'
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
    
    for mode in modes:
        if mode not in analysis_results: continue
        res = analysis_results[mode]
        stats = res.get('stats', {})
        maps = res.get('maps', {})
        
        # --- FICHA TÉCNICA HEADER ---
        doc.add_heading(f'FICHA TÉCNICA: {get_index_name(mode)}', level=2)
        p_desc = doc.add_paragraph(get_mode_description(mode))
        p_desc.style = 'Intense Quote'
        
        # --- TABLA DE INDICADORES CLAVE ---
        doc.add_heading('1. Estadísticas y Tendencias', level=3)
        st_table = doc.add_table(rows=5, cols=4)
        st_table.style = 'Table Grid'
        
        def safe_fmt(val, fmt=".4f", suffix=""):
            if val is None: return "N/A"
            if fmt == "": return str(val) + suffix
            return format(val, fmt) + suffix

        # Row 0: Labels
        st_table.rows[0].cells[0].text = 'Indicador'
        st_table.rows[0].cells[1].text = 'Valor Actual'
        
        baseline_period = stats.get('baseline_period')
        header_prev = f'Valor Inicial ({baseline_period})' if baseline_period else 'Valor Anterior'
        st_table.rows[0].cells[2].text = header_prev
        st_table.rows[0].cells[3].text = 'Variación %'
        
        # Data Rows
        st_table.rows[1].cells[0].text = 'Valor Mediano'
        st_table.rows[1].cells[1].text = safe_fmt(stats.get('current'))
        st_table.rows[1].cells[2].text = safe_fmt(stats.get('last'))
        
        trend = stats.get('trend')
        trend_cell = st_table.rows[1].cells[3]
        if trend is not None:
            trend_cell.text = f"{trend:+.1f}%"
            color = RGBColor(34, 197, 94) if trend > 0 else RGBColor(239, 68, 68)
            trend_cell.paragraphs[0].runs[0].font.bold = True
            trend_cell.paragraphs[0].runs[0].font.color.rgb = color
        else:
            trend_cell.text = "N/A"

        st_table.rows[2].cells[0].text = 'Desviación Est.'
        st_table.rows[2].cells[1].text = safe_fmt(stats.get('current_std'))
        st_table.rows[2].cells[2].text = 'Puntos Datos'
        st_table.rows[2].cells[3].text = safe_fmt(stats.get('data_count'), "")

        st_table.rows[3].cells[0].text = 'Confianza (CV)'
        cv = stats.get('cv')
        cv_text = safe_fmt(cv, ".2f", "%")
        st_table.rows[3].cells[1].text = cv_text
        if cv is not None and cv > 30:
            st_table.rows[3].cells[1].paragraphs[0].add_run(" (Variabilidad Alta)").font.color.rgb = RGBColor(245, 158, 11)

        st_table.rows[4].cells[0].text = 'Valores Atípicos'
        st_table.rows[4].cells[1].text = safe_fmt(stats.get('outlier_count'), "")
        
        # --- ANÁLISIS VISUAL ---
        doc.add_heading('2. Cartografía e Imágenes Satelitales', level=3)
        img_start = None
        img_end = None
        
        if 'start_year' in maps and 'thumb_url' in maps['start_year'] and maps['start_year']['thumb_url']:
            img_start = download_image(maps['start_year']['thumb_url'])
            
        if 'end_year' in maps and 'thumb_url' in maps['end_year'] and maps['end_year']['thumb_url']:
            img_end = download_image(maps['end_year']['thumb_url'])
            
        if img_start or img_end:
            map_table = doc.add_table(rows=2, cols=2)
            map_table.autofit = True
            
            c_start = map_table.rows[0].cells[0]
            c_end = map_table.rows[0].cells[1]
            
            if img_start:
                c_start.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                c_start.paragraphs[0].add_run().add_picture(img_start, width=Inches(2.8))
                map_table.rows[1].cells[0].text = f"Mapa Base (S-2 {start_date})"
                map_table.rows[1].cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                
            if img_end:
                c_end.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                c_end.paragraphs[0].add_run().add_picture(img_end, width=Inches(2.8))
                map_table.rows[1].cells[1].text = f"Mapa Actual (S-2 {end_date})"
                map_table.rows[1].cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Legend
        legend = create_legend_image(mode)
        if legend:
            p_leg = doc.add_paragraph()
            p_leg.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_leg.add_run().add_picture(legend, width=Inches(4.5))
            doc.add_paragraph(f"Escala de Intensidad del Índice {get_index_name(mode)}", style='Caption').alignment = WD_ALIGN_PARAGRAPH.CENTER
            
        # --- SERIE TEMPORAL ---
        doc.add_heading('3. Comportamiento en el Tiempo', level=3)
        ts = res.get('time_series', [])
        if ts:
            chart = create_temporal_chart(ts, mode)
            if chart: 
                p_chart = doc.add_paragraph()
                p_chart.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_chart.add_run().add_picture(chart, width=Inches(6))
            
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

# GEE Initialization (Attempt global init, but handle failure gracefully)
try:
    # Try global init without project first (for backward compatibility/local auth)
    ee.Initialize()
    logger.info("Google Earth Engine Initialized globally")
except Exception:
    logger.warning("Global GEE init failed. Will attempt per-request initialization.")

def ensure_ee_initialized(project_id: str = None):
    """Ensure EE is initialized with a project ID."""
    try:
        # Check if already initialized
        ee.Projection('EPSG:4326') 
    except Exception:
        # Not initialized or needs project
        try:
            p = project_id or os.getenv("GEE_PROJECT_ID", "ee-jonathanubo")
            ee.Initialize(project=p)
            logger.info(f"GEE Initialized with project: {p}")
        except Exception as e:
            logger.error(f"Failed to initialize GEE: {e}")
            raise ValidationError(f"Earth Engine initialization failed: {e}")

class AnalysisRequest(BaseModel):
    geojson: Dict[str, Any]
    startDate: str
    endDate: str
    projectId: str = None
    mode: str = "Hydrology"

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

def get_sentinel_data(aoi, start_date, end_date, mode):
    """Get and process Sentinel-2 data with scaling and cloud masking."""
    
    def mask_clouds(img):
        cloud_bit_mask = 1 << 10
        cirrus_bit_mask = 1 << 11
        qa = img.select('QA60')
        # SCL (Scene Classification Layer) for more robust masking if available
        # 3: Cloud Shadows, 8: Cloud Medium Prob, 9: Cloud High Prob, 10: Cirrus
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
               qa.bitwiseAnd(cirrus_bit_mask).eq(0))
        
        # Scale bands from 0-10000 to 0-1 (IMPORTANT for SAVI/FAI/NDCI)
        return img.updateMask(mask).divide(10000).copyProperties(img, ['system:time_start'])

    s2_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi)
              .filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
              .map(mask_clouds))

    if mode == "Hydrology":
        # MNDWI (B3 Green, B11 SWIR)
        def add_mndwi(img):
            mndwi = img.normalizedDifference(['B3', 'B11']).rename('Value')
            return img.addBands(mndwi).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(add_mndwi)

    elif mode == "Vegetation":
        # NDRE (B8 NIR, B5 RedEdge)
        def add_ndre(img):
            ndre = img.normalizedDifference(['B8', 'B5']).rename('Value')
            return img.addBands(ndre).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(add_ndre)

    elif mode == "WaterQuality":
        # NDCI (B5 RedEdge, B4 Red)
        def calc_ndci(img):
            ndci = img.normalizedDifference(['B5', 'B4']).rename('Value')
            return img.addBands(ndci).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(calc_ndci)

    elif mode == "SoilVegetation":
        # SAVI ((B8 - B4) / (B8 + B4 + 0.5)) * 1.5
        def calc_savi(img):
            savi = img.expression(
                '((NIR - RED) / (NIR + RED + 0.5)) * 1.5',
                {'NIR': img.select('B8'), 'RED': img.select('B4')}
            ).rename('Value')
            return img.addBands(savi).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(calc_savi)
        
    elif mode == "AlgaeBloom":
        # FAI (B8 - (B4 + (B11-B4) * (832.8-664.6)/(1613.7-664.6)))
        def calc_fai(img):
            fai = img.expression(
                'NIR - (RED + (SWIR - RED) * 0.177)',
                {'NIR': img.select('B8'), 'RED': img.select('B4'), 'SWIR': img.select('B11')}
            ).rename('Value')
            return img.addBands(fai).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(calc_fai)
    
    elif mode == "WaterRatio":
        # WRI (Green + Red) / (NIR + SWIR) -> (B3 + B4) / (B8 + B11)
        def calc_wri(img):
            wri = img.expression(
                '(GREEN + RED) / (NIR + SWIR)',
                {'GREEN': img.select('B3'), 'RED': img.select('B4'), 'NIR': img.select('B8'), 'SWIR': img.select('B11')}
            ).rename('Value')
            return img.addBands(wri).select('Value').copyProperties(img, ['system:time_start'])
        return s2_col.map(calc_wri)

    return ee.ImageCollection([])

def analyze_period(aoi, start_date, end_date, mode):
    """Analyze a specific period for time series data."""
    col = get_sentinel_data(aoi, start_date, end_date, mode)
    
    def reduce_img(img):
        date = img.date().format("YYYY-MM-dd")
        mean_val = img.reduceRegion(
            reducer=ee.Reducer.median(), # Median is robust to outliers
            geometry=aoi,
            scale=10, # Reverted to native 10m resolution for maximum detail
            maxPixels=1e9
        ).get('Value')
        return ee.Feature(None, {'date': date, 'value': mean_val})
        
    features = col.map(reduce_img).filter(ee.Filter.notNull(['value'])).getInfo()['features']
    return [{'date': f['properties']['date'], 'value': f['properties']['value']} for f in features]

def generate_map_url(aoi, start, end, mode):
    """Generate map tile URLs for RGB and metric visualization."""
    col = get_sentinel_data(aoi, start, end, mode)
    latest = col.median().clip(aoi)
    
    # RGB visualization logic
    if mode == "Hydrology":
        s2_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(aoi).filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))
        s2_rgb = s2_col.median().clip(aoi).divide(10000)
        rgb_vis = {'min': 0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}
        rgb_map = s2_rgb.getMapId(rgb_vis)
    else:
        rgb_vis = {'min': 0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}
        # Fallback to S2 collection for RGB if 'latest' only has Value band
        s2_col_rgb = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(aoi).filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))
        rgb_map = s2_col_rgb.median().clip(aoi).divide(10000).getMapId(rgb_vis)
    
    metric_vis = get_vis_params(mode)
    metric_map = latest.select('Value').getMapId(metric_vis)
    
    # Generate thumbnail URL for report
    thumb_params = metric_vis.copy()
    thumb_params['dimensions'] = 350
    thumb_params['region'] = aoi
    thumb_params['format'] = 'png'
    try:
        thumb_url = latest.select('Value').getThumbURL(thumb_params)
    except Exception:
        thumb_url = None

    return {
        "rgb": rgb_map['tile_fetcher'].url_format,
        "metric": metric_map['tile_fetcher'].url_format,
        "thumb_url": thumb_url
    }

def perform_single_analysis(request, mode):
    """Perform robust analysis for a single mode."""
    try:
        ensure_ee_initialized(request.projectId)
        logger.info(log_process_stage('', mode, 'processing'))
        start_obj, end_obj = validate_date_range(request.startDate, request.endDate)
        aoi = validate_geometry(request.geojson)
        
        # Auto-expand temporal window if insufficient data (Option 1)
        current_data = analyze_period(aoi, request.startDate, request.endDate, mode)
        expansions = 0
        current_start_obj = start_obj

        while (not current_data or len([d for d in current_data if d.get('value') is not None]) < 3) and expansions < 4:
            expansions += 1
            current_start_obj = current_start_obj - relativedelta(months=3)
            logger.info(f"[{mode}] Insufficient data. Expanding temporal window backwards to {current_start_obj.strftime('%Y-%m-%d')}")
            current_data = analyze_period(aoi, current_start_obj.strftime("%Y-%m-%d"), request.endDate, mode)

        if not current_data or len([d for d in current_data if d.get('value') is not None]) < 3:
            logger.warning(f"Still insufficient data for {mode} after expanding 1 year backwards. Using whatever is available.")
            
        coverage = validate_temporal_coverage(current_data)
        if not coverage['valid']:
            raise ValidationError(f"Temporal coverage issue: {coverage['reason']}")
            
        current_stats = calculate_robust_statistics(current_data)
        current_data_flagged = detect_outliers(current_data)
        outlier_count = sum(1 for d in current_data_flagged if d.get('is_outlier'))
        
        # Baseline logic: If range > 2 years, compare against the beginning of the period (Historical Baseline)
        total_range_days = (end_obj - start_obj).days
        if total_range_days > 730:
            last_start = request.startDate
            last_end = (start_obj + relativedelta(years=1)).strftime("%Y-%m-%d")
            logger.info(f"[{mode}] Using Historical Baseline for statistics (Long range: {total_range_days} days)")
            
            last_data = analyze_period(aoi, last_start, last_end, mode)
            # Auto-expansion Forward for Historical Baseline if data is missing (e.g. 2016 clouds)
            b_exp = 0
            while (not last_data or len([d for d in last_data if d.get('value') is not None]) < 3) and b_exp < 4:
                b_exp += 1
                last_end_obj = datetime.strptime(last_end, "%Y-%m-%d") + relativedelta(months=3)
                last_end = last_end_obj.strftime("%Y-%m-%d")
                logger.info(f"[{mode}] Insufficient baseline data. Expanding baseline window forward to {last_end}")
                last_data = analyze_period(aoi, last_start, last_end, mode)
        else:
            last_start = (current_start_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
            last_end = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
            last_data = analyze_period(aoi, last_start, last_end, mode)

        trend_stats = calculate_trend_statistics(current_data_flagged, last_data) or {}
        
        # Start/End Year Maps
        start_year_end = (start_obj + relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_start = generate_map_url(aoi, request.startDate, start_year_end, mode)
        end_year_start = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_end = generate_map_url(aoi, end_year_start, request.endDate, mode)
        
        maps = {
            "rgb": maps_end["rgb"],
            "metric": maps_end["metric"],
            "start_year": maps_start,
            "end_year": maps_end
        }
        
        result = {
            "mode": mode,
            "stats": {
                "current": current_stats['median'],
                "current_mean": current_stats['mean'],
                "current_std": current_stats['std'],
                "last": trend_stats.get('previous_median', 0),
                "trend": trend_stats.get('trend_percent', 0),
                "outlier_count": outlier_count,
                "data_count": current_stats['count'],
                "cv": current_stats['cv'],
                "baseline_period": f"{last_start} a {last_end}"
            },
            "time_series": current_data_flagged,
            "maps": maps,
            "coverage": coverage
        }
        
        # Normalize time series for display
        normalized_series = []
        for point in current_data_flagged:
            p = point.copy()
            p['value_raw'] = point['value']
            p['value'] = normalize_index_value(point['value'], mode)
            normalized_series.append(p)
        result['time_series'] = normalized_series
        
        logger.info(log_process_stage('', mode, 'completed'))
        return result
        
    except Exception as e:
        logger.error(f"{mode} error: {e}")
        return create_error_response(e, mode)

@app.post("/analyze")
async def analyze(request: AnalysisRequest, authorization: str = Header(None)):
    if not authorization: raise HTTPException(401, "Missing Token")
    token = authorization.split(" ")[1]
    
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials(token)
        if request.projectId: ee.Initialize(creds, project=request.projectId)
        else: ee.Initialize(creds)
        
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
    if not authorization: raise HTTPException(401, "Missing Token")
    
    try:
        token = authorization.split(" ")[1]
        from google.oauth2.credentials import Credentials
        creds = Credentials(token)
        
        if request.projectId: 
            ee.Initialize(creds, project=request.projectId)
        else: 
            ee.Initialize(creds)
        
        results = {}
        modes = ["Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"]
        
        # Parallel Execution of Analysis Modes
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(modes)) as executor:
            # Map modes to futures
            future_to_mode = {executor.submit(perform_single_analysis, request, m): m for m in modes}
            
            for future in concurrent.futures.as_completed(future_to_mode):
                m = future_to_mode[future]
                try:
                    results[m] = future.result()
                    logger.info(f"Parallel Task {m}: Success")
                except Exception as exc:
                    logger.error(f"Parallel Task {m} generated an exception: {exc}")
                    results[m] = create_error_response(exc, m)
            
        logger.info(log_process_stage('', None, 'final'))
        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"Analyze-all Error: {e}")
        raise HTTPException(500, detail=f"Backend Error: {str(e)}")

@app.post("/process-spatial-file")
async def process_spatial_file(file: UploadFile = File(...)):
    """Process uploaded KML, KMZ, or SHP (zipped) files and return GeoJSON."""
    temp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(temp_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Determine file type and process
        if file.filename.lower().endswith('.zip'):
            # Assume it's a Zipped Shapefile
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Find the .shp file
            shp_files = [f for f in os.listdir(temp_dir) if f.endswith('.shp')]
            if not shp_files:
                raise HTTPException(400, "No .shp file found in ZIP")
            gdf = gpd.read_file(os.path.join(temp_dir, shp_files[0]))
            
        elif file.filename.lower().endswith(('.kml', '.kmz')):
            # KMZ is a zipped KML. Unzip it first for robust processing.
            if file.filename.lower().endswith('.kmz'):
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                # Find the main KML file (usually doc.kml)
                kml_files = [f for f in os.listdir(temp_dir) if f.lower().endswith('.kml')]
                if not kml_files:
                    raise HTTPException(400, "No valid KML file found inside KMZ")
                file_path = os.path.join(temp_dir, kml_files[0])
                logger.info(f"Extracted KML from KMZ: {kml_files[0]}")

            # Robust KML reading (Multi-layer support)
            try:
                gdf = gpd.read_file(file_path)
                if gdf.empty:
                    layers = fiona.listlayers(file_path)
                    all_gdfs = []
                    for layer in layers:
                        try:
                            l_gdf = gpd.read_file(file_path, layer=layer)
                            if not l_gdf.empty: all_gdfs.append(l_gdf)
                        except: continue
                    if all_gdfs:
                        gdf = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True))
            except Exception as e:
                logger.warning(f"Standard KML read failed, scanning layers: {e}")
                layers = fiona.listlayers(file_path)
                all_gdfs = []
                for layer in layers:
                    try:
                        l_gdf = gpd.read_file(file_path, layer=layer)
                        if not l_gdf.empty: all_gdfs.append(l_gdf)
                    except: continue
                if all_gdfs:
                    gdf = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True))
                else:
                    raise HTTPException(400, f"No se pudo procesar el KML/KMZ: {str(e)}")
            
        else:
            raise HTTPException(400, "Unsupported file format. Use .kml, .kmz, or .zip (for SHP)")

        if gdf.empty:
            raise HTTPException(400, "The uploaded file contains no valid geometry")

        # Convert to WGS84
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        
        # Merge all geometries into a single polygon/multipolygon
        combined_geom = gdf.geometry.unary_union
        
        # Force 2D coordinates (GEE can fail with 3D from KML)
        combined_geom = transform(to_2d, combined_geom)
        
        # Convert to GeoJSON
        feature = {
            "type": "Feature",
            "geometry": json.loads(gpd.GeoSeries([combined_geom]).to_json())['features'][0]['geometry'],
            "properties": {"name": file.filename}
        }
        
        # Calculate BBox
        bounds = combined_geom.bounds # (minx, miny, maxx, maxy)
        
        return {
            "status": "success",
            "geojson": feature,
            "bbox": [bounds[0], bounds[1], bounds[2], bounds[3]]
        }

    except Exception as e:
        logger.error(f"Error processing spatial file: {e}")
        raise HTTPException(500, detail=str(e))
    finally:
        # ignore_errors=True is important for Windows where files might be temporarily locked
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.post("/generate-report")
async def generate_report(request: dict):
    try:
        wetland_name = request.get('wetland_name', 'Humedal Desconocido')
        wetland_metadata = request.get('wetland_metadata', {})
        analysis_results = request.get('analysis_results', {})
        start_date = request.get('start_date', '')
        end_date = request.get('end_date', '')
        
        doc_buffer = generate_wetland_report(wetland_name, wetland_metadata, analysis_results, start_date, end_date)
        filename = f"Reporte_{wetland_name.replace(' ', '_')}_{end_date}.docx"
        
        return StreamingResponse(
            doc_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Report Error: {e}")
        raise HTTPException(500, detail=f"Report generation failed: {str(e)}")

@app.get("/")
def read_root():
    return {"status": "Backend running", "service": "Wetland Monitor AI"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
