import math
from datetime import datetime
from typing import Any, Dict, List, Optional

S1_STATISTICAL_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{
      bands: ["VV", "VH", "dataMask"],
      units: "LINEAR_POWER"
    }],
    output: [
      { id: "VV_DB", bands: 1, sampleType: "FLOAT32" },
      { id: "VH_DB", bands: 1, sampleType: "FLOAT32" },
      { id: "WATER_SAR", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}

function evaluatePixel(sample) {
  if (sample.dataMask === 0 || sample.VV <= 0 || sample.VH <= 0) {
    return {
      VV_DB: [0],
      VH_DB: [0],
      WATER_SAR: [0],
      dataMask: [0]
    };
  }
  var vv_db = 10.0 * Math.log10(sample.VV);
  var vh_db = 10.0 * Math.log10(sample.VH);
  // Open water specular reflection threshold in Sentinel-1 GRD (VV < -15 dB)
  var is_water = vv_db < -15.0 ? 1.0 : 0.0;
  return {
    VV_DB: [vv_db],
    VH_DB: [vh_db],
    WATER_SAR: [is_water],
    dataMask: [1]
  };
}
"""


def analyze_sentinel1_sar(
    http_session: Any,
    aoi: Dict[str, Any],
    start_date: str,
    end_date: str,
    token: str,
    deg_res: float = 0.0002,
    interval: str = "P12D",
) -> Dict[str, Any]:
    """Query Sentinel-1 GRD (SAR) via Copernicus Statistical API for cloud-penetrating water/wetland monitoring."""
    url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    payload = {
        "input": {
            "bounds": {
                "geometry": aoi,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{start_date[:10]}T00:00:00Z",
                            "to": f"{end_date[:10]}T23:59:59Z",
                        },
                        "acquisitionMode": "IW",
                        "polarization": "DV",
                    },
                    "processing": {
                        "backCoeff": "SIGMA0_ELLIPSOID",
                        "orthorectify": True,
                    },
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{start_date[:10]}T00:00:00Z",
                "to": f"{end_date[:10]}T23:59:59Z",
            },
            "aggregationInterval": {"of": interval},
            "evalscript": S1_STATISTICAL_EVALSCRIPT,
            "resx": deg_res,
            "resy": deg_res,
        },
        "calculations": {
            "default": {
                "percentiles": {"k": [50.0]},
            }
        },
    }

    resp = http_session.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"CDSE Sentinel-1 SAR error ({resp.status_code}): {resp.text[:250]}")

    data = resp.json()
    series: List[Dict[str, Any]] = []
    for item in data.get("data", []):
        date_str = item.get("interval", {}).get("from", "")[:10]
        outputs = item.get("outputs", {})
        vv_stats = outputs.get("VV_DB", {}).get("bands", {}).get("B0", {}).get("stats", {})
        vh_stats = outputs.get("VH_DB", {}).get("bands", {}).get("B0", {}).get("stats", {})
        wat_stats = outputs.get("WATER_SAR", {}).get("bands", {}).get("B0", {}).get("stats", {})

        count = vv_stats.get("sampleCount", 0)
        if not count or count <= 0:
            continue

        vv_val = vv_stats.get("percentiles", {}).get("50.0", vv_stats.get("mean"))
        vh_val = vh_stats.get("percentiles", {}).get("50.0", vh_stats.get("mean"))
        wat_mean = wat_stats.get("mean")

        if vv_val is None or not math.isfinite(float(vv_val)):
            continue

        water_pct = round(float(wat_mean) * 100.0, 2) if wat_mean is not None and math.isfinite(float(wat_mean)) else None
        series.append(
            {
                "date": date_str,
                "vv_db": round(float(vv_val), 3),
                "vh_db": round(float(vh_val), 3) if vh_val is not None and math.isfinite(float(vh_val)) else None,
                "water_pct": water_pct,
                "sample_count": int(count),
            }
        )

    series.sort(key=lambda x: x["date"])
    water_vals = [p["water_pct"] for p in series if p.get("water_pct") is not None]
    vv_vals = [p["vv_db"] for p in series]

    return {
        "status": "success",
        "sensor": "Sentinel-1 GRD (SAR C-Band, Cloud-Free)",
        "observations": len(series),
        "summary": {
            "mean_water_pct": round(sum(water_vals) / len(water_vals), 2) if water_vals else None,
            "latest_water_pct": water_vals[-1] if water_vals else None,
            "mean_vv_db": round(sum(vv_vals) / len(vv_vals), 3) if vv_vals else None,
            "latest_vv_db": vv_vals[-1] if vv_vals else None,
        },
        "time_series": series,
    }
