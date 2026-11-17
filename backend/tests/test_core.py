import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from app.validators import ValidationError, validate_date_range, validate_geometry
from app.statistics import (
    calculate_mann_kendall,
    calculate_robust_statistics,
    calculate_sen_slope,
    detect_outliers,
)
from app.scoring import (
    calculate_consolidated_wetland_status,
    calculate_index_situational_status,
    infer_chile_macrozone,
)
from app.sar import analyze_sentinel1_sar
from app.catalogue import prune_disk_cache
import main


class TestValidators(unittest.TestCase):
    def test_validate_date_range_valid(self):
        s, e = validate_date_range("2024-01-01", "2024-12-31")
        self.assertLess(s, e)

    def test_validate_date_range_invalid_order(self):
        with self.assertRaises(ValidationError):
            validate_date_range("2024-12-31", "2024-01-01")

    def test_validate_geometry_polygon_and_feature(self):
        poly = {
            "type": "Polygon",
            "coordinates": [[
                [-71.5, -33.0],
                [-71.49, -33.0],
                [-71.49, -32.99],
                [-71.5, -32.99],
                [-71.5, -33.0],
            ]],
        }
        res = validate_geometry({"type": "Feature", "geometry": poly, "properties": {}})
        self.assertEqual(res["type"], "Polygon")


class TestStatistics(unittest.TestCase):
    def test_robust_stats_and_trend(self):
        series = [
            {"date": f"2024-0{m}-15", "value": 0.1 * m}
            for m in range(1, 7)
        ]
        stats = calculate_robust_statistics(series)
        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats["median"], 0.35, places=4)

        slope = calculate_sen_slope([p["date"] for p in series], [p["value"] for p in series])
        self.assertGreater(slope, 0)

        mk = calculate_mann_kendall([p["date"] for p in series], [p["value"] for p in series])
        self.assertGreater(mk["s_stat"], 0)
        self.assertTrue(mk["significant"])

    def test_outlier_detection_flags_spike(self):
        series = [
            {"date": "2024-01-05", "value": 0.30},
            {"date": "2024-01-10", "value": 0.31},
            {"date": "2024-01-15", "value": 0.29},
            {"date": "2024-01-20", "value": 0.32},
            {"date": "2024-01-25", "value": 0.95},
        ]
        flagged = detect_outliers(series, method="seasonal_iqr")
        self.assertTrue(any(p["is_outlier"] for p in flagged))


class TestScoring(unittest.TestCase):
    def test_situational_and_consolidated(self):
        mndwi = calculate_index_situational_status("Hydrology", 0.35, 0.02)
        self.assertIn("status_level", mndwi)

        analysis = {
            "Hydrology": {"stats": {"current": 0.35, "trend_delta": 0.02}},
            "Vegetation": {"stats": {"current": 0.45, "trend_delta": 0.01}},
            "WaterQuality": {"stats": {"current": 0.05, "trend_delta": 0.0}},
            "SoilVegetation": {"stats": {"current": 0.40, "trend_delta": 0.0}},
            "AlgaeBloom": {"stats": {"current": 0.005, "trend_delta": 0.0}},
            "WaterRatio": {"stats": {"current": 1.30, "trend_delta": 0.0}},
        }
        consolidated = calculate_consolidated_wetland_status(analysis)
        self.assertIn("weci_score", consolidated)
        self.assertGreater(consolidated["weci_score"], 0)


class TestSecurityAndCatalogue(unittest.TestCase):
    def test_no_cross_tenant_token_leak(self):
        self.assertFalse(hasattr(main, "_last_active_token"))
        with main._cdse_token_lock:
            main._cdse_token_cache["fake_tenant"] = {
                "token": "secret-tenant-token",
                "expires_at": 9999999999.0,
            }
        try:
            # Without server credentials or explicit bearer/headers, must raise 401
            orig_u, orig_p = main.CDSE_SERVER_USERNAME, main.CDSE_SERVER_PASSWORD
            orig_id, orig_sec = main.CDSE_SERVER_CLIENT_ID, main.CDSE_SERVER_CLIENT_SECRET
            main.CDSE_SERVER_USERNAME = ""
            main.CDSE_SERVER_PASSWORD = ""
            main.CDSE_SERVER_CLIENT_ID = ""
            main.CDSE_SERVER_CLIENT_SECRET = ""
            try:
                with self.assertRaises(HTTPException) as ctx:
                    main.get_cdse_token()
                self.assertEqual(ctx.exception.status_code, 401)
                status = asyncio.run(main.credentials_status())
                self.assertFalse(status["has_active_session"])
                self.assertFalse(status["configured"])
            finally:
                main.CDSE_SERVER_USERNAME = orig_u
                main.CDSE_SERVER_PASSWORD = orig_p
                main.CDSE_SERVER_CLIENT_ID = orig_id
                main.CDSE_SERVER_CLIENT_SECRET = orig_sec
        finally:
            with main._cdse_token_lock:
                main._cdse_token_cache.pop("fake_tenant", None)

    def test_wetlands_metadata_is_lightweight(self):
        data = asyncio.run(main.list_wetlands_metadata())
        self.assertGreater(len(data), 1000)
        self.assertNotIn("geometry", data[0])

    def test_macrozone_inference_and_weights(self):
        self.assertEqual(infer_chile_macrozone(lat=-22.5), "norte_altoandino")
        self.assertEqual(infer_chile_macrozone(region="Región de Magallanes"), "austral_patagonico")
        self.assertEqual(infer_chile_macrozone(region="Región de Los Ríos"), "sur_templado")
        analysis = {
            "Hydrology": {"stats": {"current": 0.35, "trend_delta": 0.02}},
            "Vegetation": {"stats": {"current": 0.45, "trend_delta": 0.01}},
            "WaterQuality": {"stats": {"current": 0.05, "trend_delta": 0.0}},
            "SoilVegetation": {"stats": {"current": 0.40, "trend_delta": 0.0}},
            "AlgaeBloom": {"stats": {"current": 0.005, "trend_delta": 0.0}},
            "WaterRatio": {"stats": {"current": 1.30, "trend_delta": 0.0}},
        }
        res_norte = calculate_consolidated_wetland_status(analysis, macrozone="norte_altoandino")
        self.assertEqual(res_norte["macrozone"], "norte_altoandino")
        self.assertEqual(res_norte["dimensions"]["vegetation"]["weight"], 45)

    def test_sar_parsing(self):
        class FakeResp:
            status_code = 200
            def json(self):
                return {
                    "data": [{
                        "interval": {"from": "2025-06-01T00:00:00Z"},
                        "outputs": {
                            "VV_DB": {"bands": {"B0": {"stats": {"sampleCount": 500, "percentiles": {"50.0": -16.2}}}}},
                            "VH_DB": {"bands": {"B0": {"stats": {"sampleCount": 500, "percentiles": {"50.0": -22.5}}}}},
                            "WATER_SAR": {"bands": {"B0": {"stats": {"sampleCount": 500, "mean": 0.42}}}},
                        },
                    }]
                }
        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResp()

        out = analyze_sentinel1_sar(FakeSession(), {"type": "Polygon", "coordinates": []}, "2025-05-01", "2025-06-30", "tok")
        self.assertEqual(out["observations"], 1)
        self.assertEqual(out["summary"]["latest_water_pct"], 42.0)

    def test_docx_report_includes_audit_trail(self):
        from docx import Document
        buf = main.generate_wetland_report(
            "Humedal Test",
            {"region": "Los Ríos", "code": "TEST-1", "bbox": [-73.25, -39.85, -73.24, -39.84]},
            {
                "audit_trail": {
                    "geometry_sha256": "abc123def456",
                    "area_ha": 12.5,
                    "area_km2": 0.125,
                    "macrozone": "sur_templado",
                    "macrozone_label": "Macrozona Sur / Templada Lluviosa",
                    "mean_cloud_free_pct": 87.4,
                    "generated_at_utc": "2026-11-17T20:00:00Z",
                }
            },
            "2025-01-01",
            "2025-12-31",
            token=None,
        )
        doc = Document(buf)
        all_text = "\n".join(p.text for p in doc.paragraphs)
        table_text = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        self.assertIn("Anexo de Trazabilidad Técnica y Auditoría", all_text)
        self.assertIn("abc123def456", table_text)
        self.assertIn("12.50 ha", table_text)


if __name__ == "__main__":
    unittest.main()
