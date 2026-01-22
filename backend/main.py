import os
import ee
import json
import math
from datetime import datetime
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from utils import log_process_stage

app = FastAPI(title="GEOINT Wetland Monitor API")

# --- CORS CONFIGURATION ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GEE AUTHENTICATION ---
# IMPORTANT: For production, use a Service Account.
# 1. Create a Service Account in Google Cloud Console.
# 2. Download the JSON key.
# 3. Set the environment variable: GOOGLE_APPLICATION_CREDENTIALS="path/to/key.json"
# Or initialize directly with the key.

try:
    # Attempt to initialize GEE
    # If using Service Account:
    # ee.Initialize(ee.ServiceAccountCredentials(SA_EMAIL, KEY_PATH))
    ee.Initialize() 
    print("Google Earth Engine Initialized Successfully")
except Exception as e:
    print(f"GEE Initialization Error: {e}")

# --- MODELS ---
class AnalysisRequest(BaseModel):
    geojson: Dict[str, Any]
    startDate: str
    endDate: str
    projectId: str = None
    mode: str = "Hydrology" # Hydrology, Vegetation, WaterQuality

# --- GEE LOGIC ---
def get_sentinel_data(aoi, start_date, end_date, mode):
    """
    Get and process Sentinel-2 (and Sentinel-1 for Hydrology) data with improved cloud masking.
    
    Args:
        aoi: Earth Engine Geometry
        start_date: Start date string 'YYYY-MM-DD'
        end_date: End date string 'YYYY-MM-DD'
        mode: Analysis mode ('Hydrology', 'Vegetation', 'WaterQuality')
    
    Returns:
        ee.ImageCollection with spectral index as 'Value' band
    """
    # IMPROVED Cloud Masking for Sentinel-2
    s2_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(aoi)
              .filterDate(start_date, end_date)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))  # Pre-filter heavy cloud cover
    
    def mask_clouds_robust(img):
        """Improved cloud masking using QA60 and SCL bands"""
        qa = img.select('QA60')
        scl = img.select('SCL')  # Scene Classification Layer
        
        # QA60 bitmask for clouds (bit 10) and cirrus (bit 11)
        cloudBitMask = 1 << 10
        cirrusBitMask = 1 << 11
        cloud_mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
        
        # SCL mask: Remove clouds (3=shadow, 8=cloud_med, 9=cloud_high, 10=cirrus)
        scl_mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        
        # Combine masks
        final_mask = cloud_mask.And(scl_mask)
        
        return img.updateMask(final_mask).divide(10000).copyProperties(img, ["system:time_start"])
    
    s2_processed = s2_col.map(mask_clouds_robust)

    if mode == "Vegetation":
        # NDRE - Normalized Difference Red Edge Index
        # Referencia: Gitelson & Merzlyak (1994)
        # Rango típico: 0 a 0.8 (vegetación saludable)
        # Fórmula: (NIR - RedEdge) / (NIR + RedEdge) -> (B8 - B5) / (B8 + B5)
        def calc_ndre(img):
            ndre = img.normalizedDifference(['B8', 'B5']).rename('Value')
            return img.addBands(ndre)
        return s2_processed.map(calc_ndre)

    elif mode == "WaterQuality":
        # NDCI - Normalized Difference Chlorophyll Index  
        # Referencia: Mishra & Mishra (2012)
        # Rango típico: -0.1 a 0.5 (detección de clorofila-a en aguas turbias)
        # Fórmula: (RedEdge - Red) / (RedEdge + Red) -> (B5 - B4) / (B5 + B4)
        def calc_ndci(img):
            ndci = img.normalizedDifference(['B5', 'B4']).rename('Value')
            return img.addBands(ndci)
        return s2_processed.map(calc_ndci)

    elif mode == "SoilVegetation":
        # SAVI - Soil Adjusted Vegetation Index
        # Referencia: Huete (1988)
        # Rango típico: -0.5 a 0.8 (minimiza influencia del suelo)
        # Fórmula: [(NIR - Red) / (NIR + Red + L)] × (1 + L) donde L = 0.5
        def calc_savi(img):
            L = 0.5
            nir = img.select('B8')
            red = img.select('B4')
            savi = nir.subtract(red).divide(nir.add(red).add(L)).multiply(1 + L).rename('Value')
            return img.addBands(savi)
        return s2_processed.map(calc_savi)

    elif mode == "AlgaeBloom":
        # FAI - Floating Algae Index
        # Referencia: Hu (2009)
        # Ajuste: Usamos B8 (842nm) en lugar de B8A (865nm) para mayor resolución espacial (10m vs 20m)
        # Rango típico: -0.1 a 0.5 (detección de algas flotantes)
        # Fórmula: NIR - [Red + (SWIR - Red) × ((λNIR - λRed) / (λSWIR - λRed))]
        # λNIR=842nm (B8), λRed=665nm, λSWIR=1610nm
        def calc_fai(img):
            b8 = img.select('B8')   # NIR (842nm)
            b4 = img.select('B4')   # Red (665nm)
            b11 = img.select('B11') # SWIR (1610nm)
            fai = b8.subtract(
                b4.add(
                    b11.subtract(b4).multiply((842 - 665) / (1610 - 665))
                )
            ).rename('Value')
            return img.addBands(fai)
        return s2_processed.map(calc_fai)

    elif mode == "WaterRatio":
        # WRI - Water Ratio Index
        # Referencia: Shen & Li (2010)
        # Rango típico: 0 a 5 (valores >1 indican agua)
        # Fórmula: (Green + Red) / (NIR + SWIR)
        def calc_wri(img):
            wri = img.select('B3').add(img.select('B4')).divide(
                img.select('B8').add(img.select('B11'))
            ).rename('Value')
            return img.addBands(wri)
        return s2_processed.map(calc_wri)

    else:  # Hydrology (Default) - IMPLEMENT REAL SAR FUSION
        # MNDWI - Modified Normalized Difference Water Index
        # Referencia: Xu (2006)
        # Rango: -1 a +1 (valores >0 indican agua)
        # Fórmula: (Green - SWIR) / (Green + SWIR) -> (B3 - B11) / (B3 + B11)
        # Mejor para humedales que NDWI estándar
        
        # Load Sentinel-1 SAR data
        s1_col = (ee.ImageCollection("COPERNICUS/S1_GRD")
                  .filterBounds(aoi)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
                  .filter(ee.Filter.eq('orbitProperties_pass', 'DESCENDING')))
        
        def reduce_speckle(img):
            """Apply speckle filtering to SAR image"""
            return img.focal_median(30, 'circle', 'meters').copyProperties(img, ["system:time_start"])
        
        s1_processed = s1_col.map(reduce_speckle)
        
        def fuse_optical_sar(s2_img):
            """
            Fuse Sentinel-2 optical with Sentinel-1 SAR for robust water detection.
            Finds temporally closest S1 image (within ±3 days).
            """
            # Get S2 image date
            s2_date = s2_img.date()
            
            # Find closest S1 image (within ±3 days window)
            time_window_start = s2_date.advance(-3, 'day')
            time_window_end = s2_date.advance(3, 'day')
            
            closest_s1 = s1_processed.filterDate(time_window_start, time_window_end).first()
            
            # Calculate MNDWI (optical water index)
            mndwi = s2_img.normalizedDifference(['B3', 'B11']).rename('MNDWI')
            
            # Check if SAR data exists
            def with_sar():
                vv = closest_s1.select('VV')
                vh = closest_s1.select('VH')
                
                # VV/VH ratio (water has low ratio, vegetation has high ratio)
                sar_ratio = vv.divide(vh).rename('SAR_Ratio')
                
                # Normalize VV to [-1, 1] range for fusion (typical range: -25 to 0 dB)
                vv_norm = vv.add(25).divide(25).clamp(-1, 1).rename('VV_norm')
                
                # FUSION: Weighted combination of MNDWI and normalized VV
                # Water has high MNDWI (>0) and low VV (~-20dB)
                # Combined index emphasizes agreement between sensors
                fused = mndwi.multiply(0.7).add(vv_norm.multiply(-0.3)).rename('Value')
                
                return s2_img.addBands([mndwi, fused, vv, vh, sar_ratio]).select('Value')
            
            def without_sar():
                # Fallback: Use only MNDWI if no SAR available
                return s2_img.addBands(mndwi.rename('Value')).select('Value')
            
            # Conditional: Use SAR if available, otherwise fallback to optical only
            return ee.Algorithms.If(
                closest_s1,
                with_sar(),
                without_sar()
            )
        
        # Apply fusion to all S2 images
        fused_collection = s2_processed.map(lambda img: ee.Image(fuse_optical_sar(img)))
        
        return fused_collection

# --- CLASSIFICATION/VISUALIZATION ---
def get_vis_params(mode):
    if mode == "Vegetation":
        return {'min': 0, 'max': 0.8, 'palette': ['red', 'yellow', 'green']} # Health
    elif mode == "WaterQuality":
        return {'min': -0.1, 'max': 0.5, 'palette': ['blue', 'cyan', 'lime', 'yellow', 'red']} # Blooms
    elif mode == "SoilVegetation":
        return {'min': -0.5, 'max': 0.8, 'palette': ['8B4513', 'FFD700', '90EE90', '006400']} # SAVI
    elif mode == "AlgaeBloom":
        return {'min': -0.1, 'max': 0.5, 'palette': ['0000FF', '00FFFF', 'FFFF00', 'FF0000']} # FAI
    elif mode == "WaterRatio":
        return {'min': 0, 'max': 3, 'palette': ['FF0000', 'FFA500', 'FFFFFF', '00FFFF', '0000FF']} # WRI (Red=Land, Blue=Water)
    else: # Hydrology
        return {'min': -1, 'max': 1, 'palette': ['red', 'white', 'blue']} # Water

def normalize_index_value(value, mode):
    """
    Normaliza valores de índices espectrales.
    """
    if value is None:
        return None
    
    # WRI: Normalización Logarítmica para manejar el rango amplio [0, 20+]
    # log10(1) = 0 (Umbral agua/tierra)
    # log10(10) = 1 (Agua nítida)
    # log10(0.1) = -1 (Tierra)
    if mode == "WaterRatio":
        if value <= 0: return -1 # Evitar log(0)
        value = math.log10(value)
    
    # Limitar a [-1, 1] (clamp) para todos los índices
    return max(-1, min(1, value))

# --- HELPER FOR ANALYSIS ---
def analyze_period(aoi, start, end, mode, project_id=None):
    col = get_sentinel_data(aoi, start, end, mode)
    
    def reduce_img(img):
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=aoi, scale=30, maxPixels=1e9
        )
        return ee.Feature(None, {
            'date': img.date().format('YYYY-MM-dd'),
            'value': stats.get('Value')
        })
    
    # Correct filter order: Map then Filter
    features = col.map(reduce_img).filter(ee.Filter.notNull(['value'])).getInfo()['features']
    return [{'date': f['properties']['date'], 'value': f['properties']['value']} for f in features]

def generate_map_url(aoi, start, end, mode):
    """
    Generate map tile URLs for RGB and metric visualization.
    
    For Hydrology mode, we need to get original S2 data for RGB since
    the fused collection only has 'Value' band.
    """
    col = get_sentinel_data(aoi, start, end, mode)
    latest = col.median().clip(aoi)
    
    # RGB visualization
    if mode == "Hydrology":
        # For Hydrology, get original S2 data (not fused) for RGB
        s2_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(aoi)
                  .filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))
        
        def mask_clouds_rgb(img):
            qa = img.select('QA60')
            cloudBitMask = 1 << 10
            cirrusBitMask = 1 << 11
            cloud_mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
            return img.updateMask(cloud_mask).divide(10000)
        
        s2_rgb = s2_col.map(mask_clouds_rgb).median().clip(aoi)
        rgb_vis = {'min': 0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}
        rgb_map = s2_rgb.getMapId(rgb_vis)
    else:
        # For Vegetation and WaterQuality, use normal RGB
        rgb_vis = {'min': 0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}
        rgb_map = latest.getMapId(rgb_vis)
    
    # Metric visualization (all modes have 'Value' band)
    metric_vis = get_vis_params(mode)
    metric_map = latest.select('Value').getMapId(metric_vis)
    
    # Generate thumbnail URL for report
    thumb_params = metric_vis.copy()
    thumb_params['dimensions'] = 350
    thumb_params['region'] = aoi
    thumb_params['format'] = 'png'
    
    try:
        thumb_url = latest.select('Value').getThumbURL(thumb_params)
    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        thumb_url = None

    return {
        "rgb": rgb_map['tile_fetcher'].url_format,
        "metric": metric_map['tile_fetcher'].url_format,
        "thumb_url": thumb_url
    }



def perform_single_analysis(request, mode):
    """
    Perform robust analysis for a single mode with comprehensive validation and error handling.
    
    Args:
        request: AnalysisRequest object
        mode: Analysis mode string
        
    Returns:
        Dict with analysis results or None on failure
    """
    from robust_stats import calculate_robust_statistics, calculate_trend_statistics, validate_temporal_coverage, detect_outliers
    from validators import validate_geometry, validate_date_range, ValidationError
    from utils import logger, format_analysis_summary, create_error_response, log_process_stage
    
    try:
        # 1. VALIDATE INPUTS
        logger.info(log_process_stage('', mode, 'processing'))
        
        # Validate dates
        start_obj, end_obj = validate_date_range(request.startDate, request.endDate)
        
        # Validate geometry
        aoi = validate_geometry(request.geojson)
        logger.info(f"Validated AOI with area: {aoi.area().getInfo() / 1e6:.2f} km²")
        
        # 2. COLLECT TIME SERIES DATA
        logger.info(f"Collecting time series data for {mode}...")
        current_data = analyze_period(aoi, request.startDate, request.endDate, mode)
        
        # 3. VALIDATE DATA QUALITY
        if not current_data or len(current_data) < 3:
            logger.warning(f"{mode}: Insufficient data (<3 points)")
            raise ValidationError(f"Insufficient data for {mode}: only {len(current_data) if current_data else 0} points found")
        
        # Validate temporal coverage
        coverage = validate_temporal_coverage(current_data, min_days=30)
        if not coverage['valid']:
            logger.warning(f"{mode}: {coverage['reason']}")
            raise ValidationError(f"Temporal coverage issue: {coverage['reason']}")
        
        logger.info(f"{mode}: {coverage['data_points']} data points over {coverage['coverage_days']} days")
        
        # 4. CALCULATE ROBUST STATISTICS FOR CURRENT PERIOD
        current_stats = calculate_robust_statistics(current_data)
        
        if not current_stats:
            logger.error(f"{mode}: Failed to calculate current statistics")
            raise ValueError("Failed to calculate statistics for current period")
        
        logger.info(f"{mode} current stats - Median: {current_stats['median']:.4f}, StdDev: {current_stats['std']:.4f}")
        
        # 5. DETECT OUTLIERS
        current_data_flagged = detect_outliers(current_data, method='iqr', threshold=1.5)
        outlier_count = sum(1 for d in current_data_flagged if d.get('is_outlier', False))
        
        if outlier_count > 0:
            logger.info(f"{mode}: Detected {outlier_count} outliers out of {len(current_data)} points")
        
        # 6. CALCULATE TREND (COMPARE WITH PREVIOUS YEAR)
        last_start = (start_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        last_end = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        
        logger.info(f"Collecting comparison data for {mode} (previous year: {last_start} to {last_end})...")
        last_data = analyze_period(aoi, last_start, last_end, mode)
        
        # Calculate trend statistics
        trend_stats = calculate_trend_statistics(current_data_flagged, last_data)
        
        if trend_stats:
            trend_pct = trend_stats['trend_percent']
            if trend_pct is not None:
                logger.info(f"{mode} trend: {trend_pct:+.2f}% (current median: {trend_stats['current_median']:.4f}, previous: {trend_stats['previous_median']:.4f})")
            else:
                logger.info(f"{mode} trend: N/A (previous value too close to zero: {trend_stats['previous_median']:.4f}, current: {trend_stats['current_median']:.4f})")
                trend_pct = 0  # Use 0 for frontend display when trend is not meaningful
        else:
            logger.warning(f"{mode}: Insufficient data for trend calculation")
            trend_pct = None
            # Use current stats as fallback
            trend_stats = {
                'current_median': current_stats['median'],
                'previous_median': 0,
                'trend_percent': 0,
                'absolute_change': current_stats['median']
            }
        
        # 7. GENERATE MAP TILES
        logger.info(f"Generating map tiles for {mode}...")
        
        # Start Year Map (First 12 months)
        start_year_end = (start_obj + relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_start = generate_map_url(aoi, request.startDate, start_year_end, mode)
        
        # End Year Map (Last 12 months)
        end_year_start = (end_obj - relativedelta(years=1)).strftime("%Y-%m-%d")
        maps_end = generate_map_url(aoi, end_year_start, request.endDate, mode)
        
        # Combine maps (default to end_year for compatibility)
        maps = {
            "rgb": maps_end["rgb"],
            "metric": maps_end["metric"],
            "start_year": maps_start,
            "end_year": maps_end
        }
        
        # 8. COMPILE RESULT
        result = {
            "mode": mode,
            "stats": {
                "current": current_stats['median'],  # Use median instead of mean (more robust)
                "current_mean": current_stats['mean'],
                "current_std": current_stats['std'],
                "current_min": current_stats['min'],
                "current_max": current_stats['max'],
                "last": trend_stats['previous_median'],
                "trend": trend_pct if trend_pct is not None else 0,
                "outlier_count": outlier_count,
                "data_count": current_stats['count'],
                "cv": current_stats['cv']  # Coefficient of variation
            },
            "time_series": current_data_flagged,  # Include outlier flags
            "maps": maps,
            "coverage": coverage
        }
        
        logger.info(log_process_stage('', mode, 'completed'))
        logger.debug(format_analysis_summary(current_stats, mode))
        
        # 9. ADD NORMALIZED VALUES TO TIME SERIES
        normalized_time_series = []
        for point in current_data_flagged:
            normalized_point = point.copy()
            normalized_point['value_raw'] = point['value']  # Keep raw value
            normalized_point['value'] = normalize_index_value(point['value'], mode)  # Normalized
            normalized_time_series.append(normalized_point)
        
        result['time_series'] = normalized_time_series
        
        return result
        
    except ValidationError as e:
        logger.error(f"{mode} validation error: {e}")
        return create_error_response(e, mode)
    
    except ee.EEException as e:
        logger.error(f"{mode} Earth Engine error: {e}")
        return create_error_response(e, mode)
    
    except Exception as e:
        logger.error(f"{mode} unexpected error: {e}", exc_info=True)
        return create_error_response(e, mode)

# --- ENDPOINTS ---
from fastapi import Header
from dateutil.relativedelta import relativedelta

@app.post("/analyze")
async def analyze(request: AnalysisRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing GEE Access Token")
    
    access_token = authorization.split(" ")[1]

    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials(access_token)
        if request.projectId: ee.Initialize(creds, project=request.projectId)
        else: ee.Initialize(creds)
        
        analysis_result = perform_single_analysis(request, request.mode)

        if analysis_result is None:
            raise HTTPException(status_code=500, detail=f"Failed to perform analysis for mode: {request.mode}")

        # Create Graph Data (Merging current for display)
        formatted_series = []
        for d in analysis_result['time_series']:
            formatted_series.append({
                "date": d['date'],
                "value": d['value'], # General "value" key for dynamic chart
                "metric_name": "NDWI" if request.mode == "Hydrology" else ("NDRE" if request.mode == "Vegetation" else "NDCI")
            })
        formatted_series.sort(key=lambda x: x['date'])

        return {
            "status": "success",
            "data": {
                "time_series": formatted_series,
                "summary": {
                    "current_avg": analysis_result['stats']['current'],
                    "last_year_avg": analysis_result['stats']['last'],
                    "trend": analysis_result['stats']['trend'],
                    "mode": request.mode
                },
                "maps": analysis_result['maps']
            }
        }

    except Exception as e:
        print(f"GEE processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze-all")
async def analyze_all(request: AnalysisRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing GEE Access Token")
    
    access_token = authorization.split(" ")[1]

    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials(access_token)
        if request.projectId: ee.Initialize(creds, project=request.projectId)
        else: ee.Initialize(creds)
        
        results = {}
        modes = ["Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"]
        
        # Sequential processing with logging (GEE handles parallelism internally)
        for m in modes:
            print(log_process_stage('', m, 'processing'))  # Console output for frontend
            results[m] = perform_single_analysis(request, m)
        
        print(log_process_stage('', None, 'final'))  # Final completion message
            
        return {
            "status": "success",
            "data": results
        }

    except Exception as e:
        print(f"✗ Error en análisis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-report")
async def generate_report(request: dict):
    """
    Generate a Word report for wetland analysis.
    
    Request body:
        wetland_name: str
        wetland_metadata: dict (region, code, coordinates, bbox)
        analysis_results: dict (results from analyze-all)
        start_date: str
        end_date: str
    """
    try:
        from report_generator import generate_wetland_report
        from fastapi.responses import StreamingResponse
        
        wetland_name = request.get('wetland_name', 'Humedal Desconocido')
        wetland_metadata = request.get('wetland_metadata', {})
        analysis_results = request.get('analysis_results', {})
        start_date = request.get('start_date', '')
        end_date = request.get('end_date', '')
        
        # Generate report
        doc_buffer = generate_wetland_report(
            wetland_name=wetland_name,
            wetland_metadata=wetland_metadata,
            analysis_results=analysis_results,
            start_date=start_date,
            end_date=end_date
        )
        
        # Return as downloadable file
        filename = f"Reporte_{wetland_name.replace(' ', '_')}_{end_date}.docx"
        
        return StreamingResponse(
            doc_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except Exception as e:
        print(f"Error generating report: {e}")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Backend running", "service": "Wetland Monitor AI"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

