"""
Robust Statistical Analysis Module
Provides outlier-resistant statistical calculations for remote sensing time series
"""

import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional


def calculate_robust_statistics(data: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """
    Calculate robust statistics resistant to outliers.
    
    Args:
        data: List of dicts with 'date' and 'value' keys
        
    Returns:
        Dict with statistical metrics or None if insufficient data
    """
    values = [d['value'] for d in data if d.get('value') is not None]
    
    if len(values) < 3:
        return None  # Insufficient data for robust statistics
    
    values_array = np.array(values)
    
    # Calculate comprehensive statistics
    mean_val = np.mean(values_array)
    median_val = np.median(values_array)
    std_val = np.std(values_array)
    
    # Coefficient of Variation (normalized variability)
    cv = (std_val / mean_val * 100) if mean_val != 0 else 0
    
    # Percentiles for understanding distribution
    p25 = np.percentile(values_array, 25)
    p75 = np.percentile(values_array, 75)
    
    return {
        'mean': float(mean_val),
        'median': float(median_val),
        'std': float(std_val),
        'min': float(np.min(values_array)),
        'max': float(np.max(values_array)),
        'count': len(values),
        'cv': float(cv),  # Coefficient of variation (%)
        'p25': float(p25),
        'p75': float(p75),
        'iqr': float(p75 - p25)  # Interquartile range
    }


def detect_outliers(data: List[Dict[str, Any]], method: str = 'iqr', threshold: float = 1.5) -> List[Dict[str, Any]]:
    """
    Detect and flag outliers in time series data.
    
    Args:
        data: List of dicts with 'date' and 'value' keys
        method: 'iqr' (Interquartile Range) or 'zscore' (Z-Score)
        threshold: Multiplier for outlier detection (1.5 for IQR, 3.0 for Z-score)
        
    Returns:
        Original data with added 'is_outlier' boolean field
    """
    values = [d['value'] for d in data if d.get('value') is not None]
    
    if len(values) < 4:
        # Not enough data for outlier detection
        for d in data:
            d['is_outlier'] = False
        return data
    
    values_array = np.array(values)
    
    if method == 'iqr':
        q1 = np.percentile(values_array, 25)
        q3 = np.percentile(values_array, 75)
        iqr = q3 - q1
        lower_bound = q1 - threshold * iqr
        upper_bound = q3 + threshold * iqr
        
        for d in data:
            if d.get('value') is not None:
                # Convert numpy.bool_ to Python bool for JSON serialization
                d['is_outlier'] = bool(d['value'] < lower_bound or d['value'] > upper_bound)
            else:
                d['is_outlier'] = False
                
    elif method == 'zscore':
        mean = np.mean(values_array)
        std = np.std(values_array)
        
        for d in data:
            if d.get('value') is not None and std > 0:
                z_score = abs((d['value'] - mean) / std)
                # Convert numpy.bool_ to Python bool for JSON serialization
                d['is_outlier'] = bool(z_score > threshold)
            else:
                d['is_outlier'] = False
    
    return data


def validate_temporal_coverage(data: List[Dict[str, Any]], min_days: int = 30) -> Dict[str, Any]:
    """
    Validate that time series data has adequate temporal coverage.
    
    Args:
        data: List of dicts with 'date' and 'value' keys
        min_days: Minimum required temporal range in days
        
    Returns:
        Dict with validation results
    """
    if not data or len(data) < 2:
        return {
            'valid': False,
            'reason': 'Insufficient data points',
            'coverage_days': 0,
            'data_points': len(data)
        }
    
    # Parse dates
    dates = []
    for d in data:
        try:
            date_obj = datetime.strptime(d['date'], '%Y-%m-%d')
            dates.append(date_obj)
        except (ValueError, KeyError):
            continue
    
    if len(dates) < 2:
        return {
            'valid': False,
            'reason': 'Invalid date format',
            'coverage_days': 0,
            'data_points': len(data)
        }
    
    # Calculate temporal range
    min_date = min(dates)
    max_date = max(dates)
    coverage_days = (max_date - min_date).days
    
    # Check if coverage meets minimum
    valid = coverage_days >= min_days
    
    return {
        'valid': valid,
        'reason': 'Adequate coverage' if valid else f'Coverage {coverage_days} days < minimum {min_days} days',
        'coverage_days': coverage_days,
        'data_points': len(data),
        'start_date': min_date.strftime('%Y-%m-%d'),
        'end_date': max_date.strftime('%Y-%m-%d')
    }


def calculate_trend_statistics(current_data: List[Dict], previous_data: List[Dict]) -> Optional[Dict[str, float]]:
    """
    Calculate trend statistics comparing two periods.
    
    Args:
        current_data: Recent period data
        previous_data: Historical period data (e.g., previous year)
        
    Returns:
        Dict with trend metrics or None if calculation fails
    """
    current_stats = calculate_robust_statistics(current_data)
    previous_stats = calculate_robust_statistics(previous_data)
    
    if not current_stats or not previous_stats:
        return None
    
    # Use median instead of mean for robustness
    current_median = current_stats['median']
    previous_median = previous_stats['median']
    
    # Calculate trend (% change) with protection against near-zero denominators
    # If previous value is very close to zero (< 0.01), report trend as None
    # This prevents extreme percentages like -49670% from gaps
    if abs(previous_median) > 0.01:
        trend_pct = ((current_median - previous_median) / previous_median) * 100
        # Cap extreme trends at ±1000%
        trend_pct = max(min(trend_pct, 1000), -1000)
    else:
        # Previous value too close to zero - trend not meaningful
        trend_pct = None
    
    # Absolute change (always meaningful)
    absolute_change = current_median - previous_median
    
    return {
        'current_median': current_median,
        'previous_median': previous_median,
        'trend_percent': trend_pct,
        'absolute_change': absolute_change,
        'current_std': current_stats['std'],
        'previous_std': previous_stats['std']
    }

