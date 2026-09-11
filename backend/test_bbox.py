import json
with open("../frontend/public/wetlands.json", encoding="utf-8") as f:
    data = json.load(f)
for obj in data:
    if "Cauquenes" in obj.get("name", ""):
        print(obj["name"], obj["bbox"])
