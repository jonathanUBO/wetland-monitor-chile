import math
from datetime import datetime
from typing import Any, Dict, List, Optional
import numpy as np

def _is_finite_num(v: Any) -> bool:
    """Check if value is a real finite number, eliminating None, NaN, and Inf."""
    if v is None: return False
    try: return math.isfinite(float(v))
    except (ValueError, TypeError): return False

def calculate_robust_statistics(data: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Calculate robust statistics resistant to outliers."""
    values = [float(d['value']) for d in data if _is_finite_num(d.get('value'))]
    if not values:
        return None
    
    values_array = np.array(values)
    mean_val = float(np.mean(values_array))
    std_val = float(np.std(values_array)) if len(values) > 1 else 0.0
    cv = (std_val / mean_val * 100) if mean_val != 0 else 0.0
    p25 = float(np.percentile(values_array, 25))
    p75 = float(np.percentile(values_array, 75))
    
    return {
        'mean': mean_val,
        'median': float(np.median(values_array)),
        'std': std_val,
        'min': float(np.min(values_array)),
        'max': float(np.max(values_array)),
        'count': len(values),
        'cv': cv,
        'iqr': float(p75 - p25)
    }

def calculate_sen_slope(dates: List[str], values: List[float]) -> float:
    """Calculate Sen's slope estimator (rate of change in index value per year).
    Robust non-parametric estimator (Sen, 1968; Theil, 1950) immune to missing data, non-normality, and NaNs.
    """
    if len(values) < 3 or len(dates) != len(values):
        return 0.0
    
    pairs = []
    for d, v in zip(dates, values):
        if not _is_finite_num(v):
            continue
        try:
            dt = datetime.strptime(d[:10], '%Y-%m-%d')
            year_fraction = dt.year + (dt.timetuple().tm_yday - 1) / 365.25
            pairs.append((year_fraction, float(v)))
        except Exception:
            continue
            
    if len(pairs) < 3:
        return 0.0

    # Strict chronological sort to eliminate directional slope bias
    pairs.sort(key=lambda x: x[0])

    slopes = []
    n = len(pairs)
    for i in range(n):
        ti, vi = pairs[i]
        for j in range(i + 1, n):
            tj, vj = pairs[j]
            dt = tj - ti
            if dt > 0.005:  # at least ~2 days apart (filters dual S2A/S2B same-orbit scenes)
                slopes.append((vj - vi) / dt)
    if not slopes:
        return 0.0
    return float(np.median(slopes))

def calculate_mann_kendall(dates: List[str], values: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Perform the non-parametric Mann-Kendall monotonic trend test (Mann, 1945; Kendall, 1975)
    with tie correction to evaluate the statistical significance of Sen's slope.
    """
    pairs = []
    for d, v in zip(dates, values):
        if not _is_finite_num(v):
            continue
        try:
            dt = datetime.strptime(d[:10], '%Y-%m-%d')
            year_fraction = dt.year + (dt.timetuple().tm_yday - 1) / 365.25
            pairs.append((year_fraction, float(v)))
        except Exception:
            continue
    pairs.sort(key=lambda x: x[0])
    n = len(pairs)
    if n < 4:
        return {"s_stat": 0, "z_score": 0.0, "p_value": 1.0, "significant": False}

    y = [p[1] for p in pairs]
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = y[j] - y[i]
            if diff > 1e-9:
                s += 1
            elif diff < -1e-9:
                s -= 1

    # Tie correction for variance Var(S)
    _, counts = np.unique(np.round(y, 5), return_counts=True)
    tie_term = sum(int(t) * (int(t) - 1) * (2 * int(t) + 5) for t in counts if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:
        return {"s_stat": int(s), "z_score": 0.0, "p_value": 1.0, "significant": False}

    if s > 0:
        z = (s - 1.0) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1.0) / math.sqrt(var_s)
    else:
        z = 0.0

    # Two-tailed p-value via complementary error function erfc(|Z| / sqrt(2))
    p_val = math.erfc(abs(z) / math.sqrt(2.0))
    return {
        "s_stat": int(s),
        "z_score": round(float(z), 4),
        "p_value": round(float(p_val), 5),
        "significant": bool(p_val < alpha)
    }

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

def detect_outliers(data: List[Dict[str, Any]], method: str = 'seasonal_iqr', threshold: float = 1.5, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Detect and flag outliers in time series data with seasonal awareness.
    Preserves natural seasonal hydroperiod pulses (winter floods, summer lows)
    and genuine episodic algal blooms (FAI, NDCI) while flagging true sensor artifacts.
    """
    valid_points = [d for d in data if _is_finite_num(d.get('value'))]
    if len(valid_points) < 4:
        for d in data: d['is_outlier'] = False
        return data

    # Partition by season
    seasonal_groups: Dict[str, List[float]] = {'summer': [], 'autumn': [], 'winter': [], 'spring': []}
    for d in valid_points:
        try:
            m = int(d['date'][5:7])
            season = get_southern_season(m)
            seasonal_groups[season].append(float(d['value']))
        except Exception:
            pass

    # Overall series fallback bounds with radiometric noise floor (0.02)
    # Enforces minimum IQR floor of 0.02 (Sentinel-2 MSI surface reflectance uncertainty)
    # Prevents purging legitimate observations in calm water bodies or arid salares where IQR -> 0
    all_vals = np.array([float(d['value']) for d in valid_points])
    g_q1 = float(np.percentile(all_vals, 25))
    g_q3 = float(np.percentile(all_vals, 75))
    g_iqr = max(float(g_q3 - g_q1), 0.02)
    global_lower = g_q1 - threshold * g_iqr
    global_upper = g_q3 + threshold * g_iqr
    if mode == "AlgaeBloom":
        global_upper = max(global_upper, 0.35)
    elif mode == "WaterQuality":
        global_upper = max(global_upper, 0.65)

    # Season-specific bounds (if season has at least 4 observations)
    season_bounds = {}
    for season, s_vals in seasonal_groups.items():
        if len(s_vals) >= 4:
            arr = np.array(s_vals)
            q1 = float(np.percentile(arr, 25))
            q3 = float(np.percentile(arr, 75))
            iqr = max(float(q3 - q1), 0.02)
            s_low = q1 - threshold * iqr
            s_high = q3 + threshold * iqr
            if mode == "AlgaeBloom":
                s_high = max(s_high, 0.35)
            elif mode == "WaterQuality":
                s_high = max(s_high, 0.65)
            season_bounds[season] = (s_low, s_high)
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

def calculate_trend_statistics(current_data: List[Dict], previous_data: List[Dict], full_series: Optional[List[Dict]] = None) -> Optional[Dict[str, float]]:
    """Calculate scientifically robust trend statistics comparing two periods and overall time series.
    Uses Absolute Difference Delta between baseline medians, and Sen's Slope (Sen, 1968; Theil, 1950)
    across the entire historical time series to avoid zero-crossing mathematical singularities
    and quantify true multi-year trajectory.
    """
    current_stats = calculate_robust_statistics(current_data)
    previous_stats = calculate_robust_statistics(previous_data)
    
    if not current_stats or not previous_stats: return None
    
    curr_med = current_stats['median']
    prev_med = previous_stats['median']
    delta = curr_med - prev_med
    
    # Calculate Sen's slope across full historical series if provided, otherwise fallback to current_data
    series_for_slope = full_series if full_series and len(full_series) >= 3 else current_data
    # Filter out flagged outliers for robust slope estimation
    valid_points = [d for d in series_for_slope if _is_finite_num(d.get('value')) and not d.get('is_outlier')]
    if len(valid_points) < 3:
        valid_points = [d for d in series_for_slope if _is_finite_num(d.get('value'))]
        
    dates = [d['date'] for d in valid_points]
    vals = [d['value'] for d in valid_points]
    sen_slope = calculate_sen_slope(dates, vals) if len(vals) >= 3 else 0.0
    mk_res = calculate_mann_kendall(dates, vals) if len(vals) >= 4 else {"s_stat": 0, "z_score": 0.0, "p_value": 1.0, "significant": False}

    return {
        'previous_median': float(round(prev_med, 4)),
        'current_median': float(round(curr_med, 4)),
        'absolute_change': float(round(delta, 4)),
        'sen_slope_per_year': float(round(sen_slope, 4)),
        'mk_z': mk_res['z_score'],
        'mk_p_value': mk_res['p_value'],
        'trend_significant': mk_res['significant'],
        'trend': float(round(delta, 4))  # Report absolute change as primary trend metric
    }

# ==========================================
# MODULE: REPORT GENERATOR
# ==========================================

_thumb_cache: Dict[str, bytes] = {}

