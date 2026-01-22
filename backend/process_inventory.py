import geopandas as gpd
import json
import os
import zipfile

# 1. Unzip
zip_path = "InventarioNacionalHumedales.zip"
extract_dir = "wetlands_temp"

if not os.path.exists(extract_dir):
    os.makedirs(extract_dir)

print("Unzipping...")
with zipfile.ZipFile(zip_path, 'r') as zip_ref:
    zip_ref.extractall(extract_dir)

# Find .shp file
shp_file = None
for root, dirs, files in os.walk(extract_dir):
    for file in files:
        if file.endswith(".shp"):
            shp_file = os.path.join(root, file)
            break

if not shp_file:
    print("No .shp file found!")
    exit()

print(f"Loading {shp_file}...")
gdf = gpd.read_file(shp_file)

# 2. Select Relevant Columns & Reproject
# Assuming columns usually found in Chilean inventory: 'NOMBRE_HUM', 'COD_HUM'
# We need to inspect columns if names differ.
print("Columns:", gdf.columns)

# Standardize to WGS84 (Lat/Lon)
if gdf.crs != "EPSG:4326":
    print("Reprojecting to WGS84...")
    gdf = gdf.to_crs("EPSG:4326")

# 3. Simplify & Filter
# Simplify topology to reduce JSON size (0.0001 deg ~ 11 meters precision)
print("Simplifying geometries...")
gdf['geometry'] = gdf['geometry'].simplify(0.0001)

# Construct Output List
output_wetlands = []

# Iterate
for _, row in gdf.iterrows():
    # Try to guess name column
    name = row.get('NOM_HUMDET') or row.get('NOMBRE_HUM') or row.get('Nombre') or row.get('NAME') or f"Humedal {row.get('OBJECTID', '')}"
    code = row.get('COD_HUM') or str(row.get('OBJECTID', ''))
    region = row.get('REGION') or "Chile"
    
    # Get Bbox
    bounds = row.geometry.bounds # minx, miny, maxx, maxy
    
    # Get Geometry (geojson dict)
    geom_json = json.loads(gpd.GeoSeries([row.geometry]).to_json())['features'][0]['geometry']
    
    output_wetlands.append({
        "name": name,
        "code": str(code),
        "region": str(region),
        "bbox": [bounds[0], bounds[1], bounds[2], bounds[3]],
        "geometry": geom_json
    })

# 4. Save
output_file = "../frontend/public/wetlands.json" # Overwrite existing
print(f"Saving {len(output_wetlands)} wetlands to {output_file}...")

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(output_wetlands, f, ensure_ascii=False)

print("Done! Frontend updated.")
