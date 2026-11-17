import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("wetland_monitor")


def normalize_wetland_key(s: str) -> str:
    """Normalize string by removing accents/diacritics and non-alphanumeric chars."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", s.lower())


class WetlandCatalogueDB:
    """SQLite-backed wetland catalogue to avoid holding 23MB+ of GeoJSON polygons in RAM."""

    def __init__(self, json_path: Path, db_path: Path):
        self.json_path = json_path
        self.db_path = db_path
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_ready(self) -> None:
        if self._initialized and self.db_path.exists():
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        needs_build = not self.db_path.exists()
        if not needs_build and self.json_path.exists():
            if self.json_path.stat().st_mtime > self.db_path.stat().st_mtime:
                needs_build = True

        if needs_build and self.json_path.exists():
            self._build_from_json()
        self._initialized = True

    def _build_from_json(self) -> None:
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        tmp_db = self.db_path.with_suffix(".db.tmp")
        if tmp_db.exists():
            tmp_db.unlink()

        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                """
                CREATE TABLE wetlands (
                    id TEXT,
                    name TEXT,
                    code TEXT,
                    region TEXT,
                    bbox_json TEXT,
                    geometry_json TEXT,
                    norm_name TEXT,
                    norm_code TEXT
                )
                """
            )
            rows = []
            for w in data:
                name = str(w.get("name") or "")
                code = str(w.get("code") or "")
                rows.append(
                    (
                        str(w.get("id") or ""),
                        name,
                        code,
                        str(w.get("region") or ""),
                        json.dumps(w.get("bbox"), ensure_ascii=False),
                        json.dumps(w.get("geometry"), ensure_ascii=False) if w.get("geometry") else None,
                        normalize_wetland_key(name),
                        normalize_wetland_key(code),
                    )
                )
            conn.executemany(
                "INSERT INTO wetlands VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.execute("CREATE INDEX idx_wetlands_norm_name ON wetlands(norm_name)")
            conn.execute("CREATE INDEX idx_wetlands_norm_code ON wetlands(norm_code)")
            conn.execute("CREATE INDEX idx_wetlands_region ON wetlands(region)")
            conn.commit()
        finally:
            conn.close()

        os.replace(tmp_db, self.db_path)
        logger.info(f"Indexed {len(data)} wetlands into SQLite catalogue ({self.db_path.name})")

    def list_metadata(self, region: Optional[str] = None) -> List[Dict[str, Any]]:
        self.ensure_ready()
        if not self.db_path.exists():
            return []
        conn = self._connect()
        try:
            if region:
                cur = conn.execute(
                    "SELECT id, name, code, region, bbox_json FROM wetlands WHERE LOWER(region) LIKE ?",
                    (f"%{region.strip().lower()}%",),
                )
            else:
                cur = conn.execute("SELECT id, name, code, region, bbox_json FROM wetlands")
            out = []
            for row in cur:
                out.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "code": row["code"],
                        "region": row["region"],
                        "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                    }
                )
            return out
        finally:
            conn.close()

    def get_geometry(self, name_or_code: str) -> Optional[dict]:
        self.ensure_ready()
        if not name_or_code or not self.db_path.exists():
            return None
        raw = str(name_or_code).strip()
        norm = normalize_wetland_key(raw)
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT geometry_json FROM wetlands
                WHERE LOWER(name) = LOWER(?) OR LOWER(code) = LOWER(?) OR LOWER(id) = LOWER(?)
                   OR norm_name = ? OR norm_code = ?
                LIMIT 1
                """,
                (raw, raw, raw, norm, norm),
            )
            row = cur.fetchone()
            if row and row["geometry_json"]:
                return json.loads(row["geometry_json"])

            if len(norm) >= 3:
                cur = conn.execute(
                    """
                    SELECT geometry_json FROM wetlands
                    WHERE norm_name LIKE ? OR norm_code LIKE ?
                    LIMIT 1
                    """,
                    (f"%{norm}%", f"%{norm}%"),
                )
                row = cur.fetchone()
                if row and row["geometry_json"]:
                    return json.loads(row["geometry_json"])
            return None
        finally:
            conn.close()


def prune_disk_cache(cache_dir: Path, max_files: int = 400, max_age_days: int = 14) -> int:
    """Enforce retention policy (TTL + max file count) on disk cache directories."""
    if not cache_dir.exists():
        return 0
    now = time.time()
    cutoff = now - (max_age_days * 86400)
    entries = []
    removed = 0
    for p in cache_dir.iterdir():
        if not p.is_file() or p.suffix == ".db" or p.name.endswith(".tmp"):
            continue
        try:
            st = p.stat()
            if st.st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
            else:
                entries.append((st.st_mtime, p))
        except Exception:
            continue

    if len(entries) > max_files:
        entries.sort(key=lambda x: x[0])  # oldest first
        for _, p in entries[: len(entries) - max_files]:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
    return removed
