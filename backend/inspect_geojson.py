import json

path = r"C:\Users\jruiz\Downloads\InventarioNacionalHumedales.geojson"

try:
    with open(path, 'r', encoding='utf-8') as f:
        chunk = f.read(5 * 1024 * 1024) # 5MB
        start = 0
        for i in range(5):
            idx = chunk.find('"properties"', start)
            if idx == -1:
                break
            print(f"Match {i+1}:")
            # Print enough context to see keys
            print(chunk[idx:idx+600]) 
            start = idx + 1
            
        if start == 0:
             print("Properties not found in first 5MB")

except Exception as e:
    print(f"Error reading file: {e}")
