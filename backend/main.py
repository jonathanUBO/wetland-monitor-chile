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

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dateutil.relativedelta import relativedelta

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
            raise ValidationError("Missing 'geometry' field in GeoJSON")
        aoi = ee.Geometry(geometry)
        return aoi
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
    if abs(prev_med) > 0.01:
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
        print(f"Error downloading image: {e}")
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
    
    for mode in modes:
        if mode not in analysis_results: continue
        res = analysis_results[mode]
        stats = res.get('stats', {})
        maps = res.get('maps', {})
        
        doc.add_heading(f'{mode} - {get_index_name(mode)}', level=3)
        doc.add_paragraph(get_mode_description(mode), style='Intense Quote')
        
        st_table = doc.add_table(rows=7, cols=2)
        st_table.style = 'Light List Accent 1'
        st_table.rows[0].cells[0].text = 'Valor Actual (Mediana)'
        st_table.rows[0].cells[1].text = f"{stats.get('current', 0):.4f}"
        st_table.rows[1].cells[0].text = 'Valor Año Anterior'
        st_table.rows[1].cells[1].text = f"{stats.get('last', 0):.4f}"
        
        trend = stats.get('trend', 0)
        trend_cell = st_table.rows[2].cells[1]
        trend_cell.text = f"{trend:+.2f}%"
        st_table.rows[2].cells[0].text = 'Tendencia'
        color = RGBColor(34, 197, 94) if trend > 0 else RGBColor(239, 68, 68)
        trend_cell.paragraphs[0].runs[0].font.color.rgb = color
        
        st_table.rows[3].cells[0].text = 'Desviación Estándar'
        st_table.rows[3].cells[1].text = f"{stats.get('current_std', 0):.4f}"
        st_table.rows[4].cells[0].text = 'Coeficiente de Variación'
        st_table.rows[4].cells[1].text = f"{stats.get('cv', 0):.2f}%"
        st_table.rows[5].cells[0].text = 'Puntos de Datos'
        st_table.rows[5].cells[1].text = str(stats.get('data_count', 0))
        st_table.rows[6].cells[0].text = 'Valores Atípicos'
        st_table.rows[6].cells[1].text = str(stats.get('outlier_count', 0))
        doc.add_paragraph()
        
        doc.add_heading('Mapas del Índice', level=4)
        
        img_start = None
        img_end = None
        
        if 'start_year' in maps and 'thumb_url' in maps['start_year'] and maps['start_year']['thumb_url']:
            img_start = download_image(maps['start_year']['thumb_url'])
            
        if 'end_year' in maps and 'thumb_url' in maps['end_year'] and maps['end_year']['thumb_url']:
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

# GEE Initialization
try:
    ee.Initialize()
    logger.info("Google Earth Engine Initialized Successfully")
except Exception as e:
    logger.error(f"GEE Initialization Error: {e}")

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
    """Get and process Sentinel-2 data."""
    if mode == "Hydrology":
        # MNDWI (B3 Green, B11 SWIR)
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def add_mndwi(img):
            mndwi = img.normalizedDifference(['B3', 'B11']).rename('MNDWI')
            return img.addBands(mndwi).select('MNDWI').rename('Value') \
                .copyProperties(img, ['system:time_start'])
                
        return s2.map(add_mndwi)

    elif mode == "Vegetation":
        # NDRE (B8 NIR, B5 RedEdge)
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def add_ndre(img):
            ndre = img.normalizedDifference(['B8', 'B5']).rename('NDRE')
            return img.addBands(ndre).select('NDRE').rename('Value') \
                .copyProperties(img, ['system:time_start'])
        return s2.map(add_ndre)

    elif mode == "WaterQuality":
        # NDCI (B5 RedEdge, B4 Red)
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def calc_ndci(img):
            ndci = img.normalizedDifference(['B5', 'B4']).rename('Value')
            return img.addBands(ndci).select('Value').copyProperties(img, ['system:time_start'])
        return s2.map(calc_ndci)

    elif mode == "SoilVegetation":
        # SAVI ((B8 - B4) / (B8 + B4 + 0.5)) * 1.5
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def calc_savi(img):
            savi = img.expression(
                '((NIR - RED) / (NIR + RED + 0.5)) * 1.5',
                {'NIR': img.select('B8'), 'RED': img.select('B4')}
            ).rename('Value')
            return img.addBands(savi).select('Value').copyProperties(img, ['system:time_start'])
        return s2.map(calc_savi)
        
    elif mode == "AlgaeBloom":
        # FAI (B8 - (B4 + (B11-B4) * (832.8-664.6)/(1613.7-664.6)))
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def calc_fai(img):
            fai = img.expression(
                'NIR - (RED + (SWIR - RED) * 0.177)',
                {'NIR': img.select('B8'), 'RED': img.select('B4'), 'SWIR': img.select('B11')}
            ).rename('Value')
            return img.addBands(fai).select('Value').copyProperties(img, ['system:time_start'])
        return s2.map(calc_fai)
    
    elif mode == "WaterRatio":
        # WRI (Green + Red) / (NIR + SWIR) -> (B3 + B4) / (B8 + B11)
        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi).filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
        
        def calc_wri(img):
            wri = img.expression(
                '(GREEN + RED) / (NIR + SWIR)',
                {'GREEN': img.select('B3'), 'RED': img.select('B4'), 'NIR': img.select('B8'), 'SWIR': img.select('B11')}
            ).rename('Value')
            return img.addBands(wri).select('Value').copyProperties(img, ['system:time_start'])
        return s2.map(calc_wri)

    return ee.ImageCollection([])

def analyze_period(aoi, start_date, end_date, mode):
    """Analyze a specific period for time series data."""
    col = get_sentinel_data(aoi, start_date, end_date, mode)
    
    def reduce_img(img):
        date = img.date().format("YYYY-MM-dd")
        mean_val = img.reduceRegion(
            reducer=ee.Reducer.median(), # Median is robust to outliers
            geometry=aoi,
            scale=10,
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
        s2_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(aoi).filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))
        rgb_map = s2_col.median().clip(aoi).divide(10000).getMapId(rgb_vis)
    
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
        logger.info(log_process_stage('', mode, 'processing'))
        start_obj, end_obj = validate_date_range(request.startDate, request.endDate)
        aoi = validate_geometry(request.geojson)
        
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
                "cv": current_stats['cv']
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
        
        for m in modes:
            logger.info(log_process_stage('', m, 'processing'))
            results[m] = perform_single_analysis(request, m)
            
        logger.info(log_process_stage('', None, 'final'))
        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"Analyze-all Error: {e}")
        raise HTTPException(500, detail=f"Backend Error: {str(e)}")

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
        print(f"Report Error: {e}")
        raise HTTPException(500, detail=f"Report generation failed: {str(e)}")

@app.get("/")
def read_root():
    return {"status": "Backend running", "service": "Wetland Monitor AI"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
