"""
Input Validation Module
Validates user inputs for date ranges, geometries, and analysis parameters
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
import ee


class ValidationError(Exception):
    """Custom exception for validation failures"""
    pass


def validate_date_range(start_date_str: str, end_date_str: str, max_range_days: int = 4015) -> Tuple[datetime, datetime]:
    """
    Validate date range inputs.
    
    Args:
        start_date_str: Start date in 'YYYY-MM-DD' format
        end_date_str: End date in 'YYYY-MM-DD' format
        max_range_days: Maximum allowed range in days (default: 11 years / 4015 days)
        
    Returns:
        Tuple of (start_datetime, end_datetime)
        
    Raises:
        ValidationError: If dates are invalid
    """
    # Parse dates
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValidationError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    
    # Check logical order
    if start_date >= end_date:
        raise ValidationError("start_date must be before end_date")
    
    # Check if dates are in the future
    today = datetime.now()
    if end_date > today:
        raise ValidationError("end_date cannot be in the future")
    
    if start_date > today:
        raise ValidationError("start_date cannot be in the future")
    
    # Check maximum range
    date_range = (end_date - start_date).days
    if date_range > max_range_days:
        raise ValidationError(
            f"Date range ({date_range} days) exceeds maximum allowed ({max_range_days} days / ~{max_range_days//365} years)"
        )
    
    # Check minimum range (at least 7 days for meaningful analysis)
    if date_range < 7:
        raise ValidationError("Date range must be at least 7 days for meaningful analysis")
    
    return start_date, end_date


def validate_geometry(geojson: Dict[str, Any], min_area_km2: float = 0.01, max_area_km2: float = 1000) -> ee.Geometry:
    """
    Validate GeoJSON geometry for analysis.
    
    Args:
        geojson: GeoJSON Feature dict with geometry
        min_area_km2: Minimum area in km² (default: 0.01 km² = 10,000 m²)
        max_area_km2: Maximum area in km² (default: 1000 km²)
        
    Returns:
        ee.Geometry object
        
    Raises:
        ValidationError: If geometry is invalid
    """
    try:
        geometry = geojson.get('geometry')
        if not geometry:
            raise ValidationError("Missing 'geometry' field in GeoJSON")
        
        # Create Earth Engine geometry
        aoi = ee.Geometry(geometry)
        
        # Calculate area
        area_m2 = aoi.area().getInfo()
        area_km2 = area_m2 / 1_000_000
        
        # Validate area bounds
        if area_km2 < min_area_km2:
            raise ValidationError(
                f"Area too small: {area_km2:.4f} km². Minimum: {min_area_km2} km²"
            )
        
        if area_km2 > max_area_km2:
            raise ValidationError(
                f"Area too large: {area_km2:.2f} km². Maximum: {max_area_km2} km²"
            )
        
        return aoi
        
    except ee.EEException as e:
        raise ValidationError(f"Invalid geometry for Earth Engine: {e}")
    except Exception as e:
        raise ValidationError(f"Geometry validation failed: {e}")


def validate_mode(mode: str) -> str:
    """
    Validate analysis mode.
    
    Args:
        mode: Analysis mode string
        
    Returns:
        Validated mode string
        
    Raises:
        ValidationError: If mode is invalid
    """
    valid_modes = ["Hydrology", "Vegetation", "WaterQuality"]
    
    if mode not in valid_modes:
        raise ValidationError(
            f"Invalid mode '{mode}'. Must be one of: {', '.join(valid_modes)}"
        )
    
    return mode


def validate_project_id(project_id: str) -> str:
    """
    Validate Google Earth Engine project ID format.
    
    Args:
        project_id: GEE project ID
        
    Returns:
        Validated project ID
        
    Raises:
        ValidationError: If project ID format is invalid
    """
    if not project_id or not isinstance(project_id, str):
        raise ValidationError("Project ID must be a non-empty string")
    
    # Basic format validation (GEE project IDs are typically alphanumeric with hyphens)
    if not all(c.isalnum() or c in ['-', '_'] for c in project_id):
        raise ValidationError("Project ID contains invalid characters")
    
    if len(project_id) < 3:
        raise ValidationError("Project ID too short (minimum 3 characters)")
    
    return project_id
