"""
Utility Functions and Logging Configuration
Provides logging setup and helper functions
"""

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(log_file: str = 'wetland_analysis.log', level: int = logging.INFO) -> logging.Logger:
    """
    Configure logging for the application.
    
    Args:
        log_file: Path to log file
        level: Logging level (default: INFO)
        
    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger('wetland_monitor')
    logger.setLevel(level)
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Enhanced formatter with process stages
    simple_formatter = logging.Formatter(
        '%(message)s'
    )
    
    # File handler (detailed logs)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # Console handler (simple logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


def format_analysis_summary(stats: dict, mode: str) -> str:
    """
    Format analysis statistics into human-readable string.
    
    Args:
        stats: Statistics dictionary
        mode: Analysis mode
        
    Returns:
        Formatted summary string
    """
    if not stats:
        return f"{mode}: No data available"
    
    summary = f"""
{mode} Analysis Summary:
  Current Median: {stats.get('median', 'N/A'):.4f}
  Range: [{stats.get('min', 'N/A'):.4f}, {stats.get('max', 'N/A'):.4f}]
  Std Dev: {stats.get('std', 'N/A'):.4f}
  Data Points: {stats.get('count', 0)}
  Coefficient of Variation: {stats.get('cv', 'N/A'):.2f}%
"""
    return summary.strip()


def get_sensor_info(mode: str) -> dict:
    """
    Get information about sensors and bands used for each mode.
    
    Args:
        mode: Analysis mode
        
    Returns:
        Dict with sensor information
    """
    sensor_info = {
        "Hydrology": {
            "primary_sensor": "Sentinel-2",
            "secondary_sensor": "Sentinel-1",
            "index": "MNDWI",
            "formula": "(Green - SWIR) / (Green + SWIR)",
            "bands": "B3 (Green), B11 (SWIR)",
            "sar_bands": "VV, VH"
        },
        "Vegetation": {
            "primary_sensor": "Sentinel-2",
            "secondary_sensor": None,
            "index": "NDRE",
            "formula": "(NIR - RedEdge) / (NIR + RedEdge)",
            "bands": "B8 (NIR), B5 (RedEdge)"
        },
        "WaterQuality": {
            "primary_sensor": "Sentinel-2",
            "secondary_sensor": None,
            "index": "NDCI",
            "formula": "(RedEdge - Red) / (RedEdge + Red)",
            "bands": "B5 (RedEdge), B4 (Red)"
        }
    }
    
    return sensor_info.get(mode, {})


def create_error_response(error: Exception, mode: str = None) -> dict:
    """
    Create standardized error response.
    
    Args:
        error: Exception object
        mode: Analysis mode (optional)
        
    Returns:
        Error response dict
    """
    return {
        "status": "error",
        "error": str(error),
        "error_type": type(error).__name__,
        "mode": mode,
        "timestamp": datetime.now().isoformat()
    }


def log_process_stage(stage: str, mode: str = None, status: str = 'processing') -> str:
    """
    Create formatted log message for process stages.
    
    Args:
        stage: Stage name (e.g., 'MNDWI', 'NDRE', 'completed')
        mode: Analysis mode (optional)
        status: 'processing', 'completed', or 'error'
        
    Returns:
        Formatted log message
    """
    # Define index names for each mode
    index_names = {
        'Hydrology': 'MNDWI',
        'Vegetation': 'NDRE',
        'WaterQuality': 'NDCI',
        'SoilVegetation': 'SAVI',
        'AlgaeBloom': 'FAI',
        'WaterRatio': 'WRI'
    }
    
    if status == 'processing':
        index_name = index_names.get(mode, mode)
        return f"⚙️  Procesando {index_name}..."
    elif status == 'completed':
        index_name = index_names.get(mode, mode)
        return f"✓  {index_name} completado"
    elif status == 'final':
        return "✓  Análisis finalizado exitosamente"
    elif status == 'error':
        return f"✗  Error en {stage}"
    else:
        return stage


# Initialize logger on module import
logger = setup_logging()
