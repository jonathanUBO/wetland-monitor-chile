import os
import sys
import requests
import json
from oauthlib.oauth2 import LegacyApplicationClient
from requests_oauthlib import OAuth2Session

username = os.getenv("CDSE_USERNAME")
password = os.getenv("CDSE_PASSWORD")

if not username or not password:
    print("Set CDSE_USERNAME and CDSE_PASSWORD in environment to run this standalone script.")
    sys.exit(0)

client = LegacyApplicationClient(client_id='cdse-public')
oauth = OAuth2Session(client=client)
token = oauth.fetch_token(token_url='https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token', username=username, password=password)['access_token']

aoi = { 'type': 'Polygon', 'coordinates': [[[-70.7, -33.3], [-70.8, -33.3], [-70.8, -33.4], [-70.7, -33.4], [-70.7, -33.3]]] }

payload = {
    'input': {
        'bounds': { 'geometry': aoi, 'properties': {'crs': 'http://www.opengis.net/def/crs/EPSG/0/4326'} },
        'data': [{'type': 'sentinel-2-l2a', 'dataFilter': {'timeRange': {'from': '2024-01-01T00:00:00Z', 'to': '2024-02-01T23:59:59Z'}, 'maxCloudCoverage': 20}}]
    },
    'aggregation': {
        'timeRange': {'from': '2024-01-01T00:00:00Z', 'to': '2024-02-01T23:59:59Z'},
        'aggregationInterval': {'of': 'P10D'},
        'evalscript': '''//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: [
      { id: "default", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(sample) {
  if (sample.dataMask === 0) {
    return { default: [NaN], dataMask: [0] };
  }
  return { default: [(sample.B08 - sample.B04)/(sample.B08 + sample.B04)], dataMask: [1] };
}''',
        'resx': 0.00009,
        'resy': 0.00009
    }
}
res = requests.post('https://sh.dataspace.copernicus.eu/api/v1/statistics', headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}, json=payload)
print(res.status_code)
print(json.dumps(res.json(), indent=2)[:1000])
