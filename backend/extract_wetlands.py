import ijson
import json
from pyproj import Transformer
import os

source_path = r"C:\Users\jruiz\Downloads\InventarioNacionalHumedales.geojson"
# Change output to public folder so it can be fetched
output_path = r"C:\Users\jruiz\.gemini\antigravity\scratch\wetland-monitor-chile\frontend\public\wetlands.json"

# Transformer from EPSG:32719 (WGS 84 / UTM zone 19S) to EPSG:4326 (WGS 84 Lat/Lon)
# Note: Check if the CRS varies. The file property said "EPSG:32719".
transformer = Transformer.from_crs("EPSG:32719", "EPSG:4326", always_xy=True)

wetlands = []

try:
    print(f"Opening {source_path}...")
    with open(source_path, 'rb') as f:
        # Stream features to avoid memory issues
        features = ijson.items(f, 'features.item')
        
        count = 0
        for feature in features:
            props = feature.get('properties', {})
            name = props.get('NOM_HUMDET') or props.get('NOM_HUMMAS') or "Sin Nombre"
            
            geom = feature.get('geometry')
            if not geom:
                continue

            coords = []
            if geom['type'] == 'Polygon':
                coords = geom['coordinates'][0] # Outer ring
            elif geom['type'] == 'MultiPolygon':
                coords = geom['coordinates'][0][0] # First polygon, outer ring
            else:
                continue # Skip points or lines if any
                
            if not coords:
                continue

            # Filtering logic
            name_lower = name.lower()
            invalid_terms = ["sin informacion", "sin información", "sin nombre", "no informado", "desconocido"]
            if any(term in name_lower for term in invalid_terms):
                continue

            # Calculate bbox in original CRS (ijson returns decimals)
            # Cast to float for math and serialization
            xs = [float(p[0]) for p in coords]
            ys = [float(p[1]) for p in coords]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            
            center_x = (min_x + max_x) / 2
            center_y = (min_y + max_y) / 2
            
            # Transform to Lat/Lon
            center_lon, center_lat = transformer.transform(center_x, center_y)
            sw_lon, sw_lat = transformer.transform(min_x, min_y)
            ne_lon, ne_lat = transformer.transform(max_x, max_y)
            
            code = props.get('COD_HUMEDA') or props.get('COD_HUMMAS') or "S/C"

            wetlands.append({
                "id": str(props.get('FID', count)),
                "code": code,
                "name": name,
                "center": [float(center_lon), float(center_lat)],
                "bbox": [float(sw_lon), float(sw_lat), float(ne_lon), float(ne_lat)], 
                "area_ha": float(props.get('HECTAREAS', 0) or 0)
            })
            
            count += 1
            if count % 500 == 0:
                print(f"Processed {count} wetlands...")

    # Ensure output dir exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(wetlands, f, ensure_ascii=False) # Minified for size? Or indent=2?
        # indent=None default is minified.
        
    print(f"Done. Saved {len(wetlands)} wetlands to {output_path}")

except Exception as e:
    print(f"Error: {e}")
