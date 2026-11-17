import logging
import math
from datetime import datetime
from typing import Any, Dict, Tuple

logger = logging.getLogger('wetland_monitor')

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

def count_geometry_vertices(geom: Any) -> int:
    """Recursively count total vertices in a GeoJSON geometry or Shapely shape."""
    if hasattr(geom, 'geoms'):
        return sum(count_geometry_vertices(g) for g in geom.geoms)
    if hasattr(geom, 'exterior'):
        count = len(geom.exterior.coords)
        for interior in geom.interiors:
            count += len(interior.coords)
        return count
    if isinstance(geom, dict):
        coords = geom.get('coordinates', [])
        geom_type = geom.get('type', '')
        if geom_type == 'Polygon':
            return sum(len(ring) for ring in coords)
        elif geom_type == 'MultiPolygon':
            return sum(len(ring) for poly in coords for ring in poly)
    return 0

def simplify_geometry_if_needed(geom_obj: Any, max_vertices: int = 1200) -> Any:
    """Ensure geometry has <= max_vertices to strictly respect Copernicus CDSE 1500 vertex limit.
    Uses Douglas-Peucker topology-preserving simplification (Shapely) and sub-pixel micro-hole filtering.
    At 10m-20m Sentinel-2 resolution, eliminates sub-pixel vertex bloat without losing ecological boundary integrity.
    """
    import shapely.geometry as sg
    try:
        if isinstance(geom_obj, dict):
            s_geom = sg.shape(geom_obj)
        else:
            s_geom = geom_obj
            
        v_count = count_geometry_vertices(s_geom)
        if v_count <= max_vertices:
            return s_geom.__geo_interface__ if not isinstance(geom_obj, dict) else geom_obj
            
        # Stage 1: progressive simplification tolerances
        for tol in [0.00005, 0.0001, 0.0002, 0.0004, 0.0008, 0.0015, 0.003]:
            simplified = s_geom.simplify(tolerance=tol, preserve_topology=True)
            if not simplified.is_empty:
                if not simplified.is_valid:
                    simplified = simplified.buffer(0)
                if simplified.is_valid and not simplified.is_empty:
                    s_geom = simplified
                    if count_geometry_vertices(s_geom) <= max_vertices:
                        return s_geom.__geo_interface__

        # Stage 2: filter out sub-pixel micro holes (interior rings) if still > max_vertices
        if count_geometry_vertices(s_geom) > max_vertices:
            minx_h, miny_h, maxx_h, maxy_h = s_geom.bounds
            lat_mid_h = (miny_h + maxy_h) / 2.0
            cos_lat_h = max(0.1, abs(math.cos(math.radians(lat_mid_h))))
            # 1 deg^2 in m^2 at latitude lat_mid_h: (111320 * cos_lat) * 110570
            deg2_to_m2 = (111320.0 * cos_lat_h) * 110570.0
            min_hole_deg2 = 400.0 / deg2_to_m2  # 400 m^2 (~1 Sentinel-2 20m pixel), NOT 45 hectares

            def filter_holes(poly):
                if not hasattr(poly, 'interiors') or len(poly.interiors) == 0:
                    return poly
                # Keep all ecologically relevant holes (> 400 m^2)
                keep_holes = [h for h in poly.interiors if sg.Polygon(h).area > min_hole_deg2]
                if len(keep_holes) > 25:
                    keep_holes = sorted(keep_holes, key=lambda h: sg.Polygon(h).area, reverse=True)[:25]
                return sg.Polygon(poly.exterior, keep_holes)

            if s_geom.geom_type == 'Polygon':
                s_geom = filter_holes(s_geom)
            elif s_geom.geom_type == 'MultiPolygon':
                s_geom = sg.MultiPolygon([filter_holes(p) for p in s_geom.geoms if not p.is_empty])

            for tol in [0.001, 0.002, 0.004, 0.008, 0.015]:
                if count_geometry_vertices(s_geom) <= max_vertices:
                    break
                simplified = s_geom.simplify(tolerance=tol, preserve_topology=True)
                if not simplified.is_empty:
                    if not simplified.is_valid:
                        simplified = simplified.buffer(0)
                    if simplified.is_valid and not simplified.is_empty:
                        s_geom = simplified
                    
        return s_geom.__geo_interface__
    except Exception as e:
        logger.warning(f"Geometry simplification skipped/failed: {e}")
        return geom_obj.__geo_interface__ if hasattr(geom_obj, '__geo_interface__') else geom_obj

def validate_geometry(geojson: Any, min_area_km2: float = 0.001, max_area_km2: float = 5000) -> Dict[str, Any]:
    """Validate and normalize GeoJSON geometry for analysis.
    Accepts:
      - Feature dict (with 'geometry' key)
      - FeatureCollection dict (extracts/dissolves geometries)
      - Raw geometry dict ({'type': 'Polygon'/'MultiPolygon', 'coordinates': ...})
    Enforces WGS84 degree coordinate bounds and guarantees vertex count <= 1200 (CDSE safety).
    """
    import shapely.geometry as sg
    try:
        geometry = None
        if isinstance(geojson, dict):
            g_type = geojson.get('type')
            if g_type == 'Feature':
                geometry = geojson.get('geometry')
            elif g_type == 'FeatureCollection':
                features = geojson.get('features', [])
                if features:
                    geoms = [sg.shape(f['geometry']) for f in features if f.get('geometry')]
                    if geoms:
                        import shapely.ops as so
                        dissolved = so.unary_union(geoms)
                        geometry = dissolved.__geo_interface__
            elif g_type in ('Polygon', 'MultiPolygon', 'GeometryCollection'):
                geometry = geojson
            elif 'geometry' in geojson:
                geometry = geojson['geometry']

        if not geometry:
            raise ValidationError("Formato GeoJSON no reconocido o sin campo de geometría válido.")

        s_geom = sg.shape(geometry)
        if not s_geom.is_valid:
            s_geom = s_geom.buffer(0)
            if not s_geom.is_valid:
                raise ValidationError("La geometría del polígono no es topológicamente válida.")

        minx, miny, maxx, maxy = s_geom.bounds

        # Scientific Geodetic Guard: Detect inverted [Latitude, Longitude] coordinate order for Chilean territory
        # Continental Chile: Longitude in [-76.0, -66.0], Latitude in [-56.0, -17.5]
        if minx > -60.0 and miny < -60.0 and -60.0 <= minx <= -15.0 and -76.0 <= miny <= -65.0:
            logger.warning(f"Inverted [Lat, Lon] coordinate order detected: x={minx:.2f}, y={miny:.2f}. Auto-transposing to RFC 7946 [Lon, Lat].")
            import shapely.ops as so
            s_geom = so.transform(lambda x, y, *args: (y, x), s_geom)
            minx, miny, maxx, maxy = s_geom.bounds

        if not (-180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0 and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0):
            raise ValidationError("Coordenadas fuera de rango WGS84 (EPSG:4326). Si tus datos están en coordenadas proyectadas UTM, debes reprojectarlos a WGS84 (grados decimales).")

        # Enforce spherical area bounds to prevent continental DoS / PU quota exhaustion
        lat_mid = (miny + maxy) / 2.0
        cos_lat = max(0.1, abs(math.cos(math.radians(lat_mid))))
        approx_area_km2 = s_geom.area * (111.32 * cos_lat) * 110.57
        if approx_area_km2 < min_area_km2:
            raise ValidationError(f"El área del polígono ({approx_area_km2:.4f} km²) es demasiado pequeña para la resolución de Sentinel-2 (mínimo {min_area_km2} km²).")
        if approx_area_km2 > max_area_km2:
            raise ValidationError(f"El área del polígono ({approx_area_km2:.1f} km²) excede el límite máximo permitido de {max_area_km2} km².")

        # Auto-simplify geometry if it exceeds CDSE 1500 vertex limit
        return simplify_geometry_if_needed(s_geom)
    except ValidationError:
        raise
    except Exception as e:
        raise ValidationError(f"Fallo en validación de geometría: {e}")

# ==========================================
# MODULE: ROBUST STATS
