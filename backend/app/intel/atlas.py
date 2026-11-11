"""HVO / Renewable Diesel & SAF plant atlas (legacy embedded hvo_saf_global_plant_atlas.html).

The legacy served a third-party HTML file that loaded d3/topojson/datamaps from a CDN. The plant list (94 plants,
"snapshot compiled Jul 2026") now lives in intel.atlas_plants, seeded from data/atlas_plants.json, and the page is
rendered natively. Admins can replace the dataset with a JSON file of the same shape.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from app.core.db import get_engine
from app.core.errors import ApiError

SEED = Path(__file__).parent / "data" / "atlas_plants.json"
FIELDS = ["name", "company", "country", "region", "lat", "lon", "status", "type", "kt", "safkt", "quoted",
          "startup", "note", "est"]
SNAPSHOT_KEY = "atlas.snapshot"


def _validate(plants: list) -> list[dict]:
    if not isinstance(plants, list) or not plants:
        raise ApiError(422, "bad_atlas", "Expected a non-empty list of plants.")
    out = []
    for i, p in enumerate(plants):
        try:
            row = {k: p.get(k) for k in FIELDS}
            row["name"], row["country"] = str(row["name"]).strip(), str(row["country"]).strip()
            row["lat"], row["lon"], row["kt"] = float(row["lat"]), float(row["lon"]), float(row["kt"])
            row["safkt"] = None if row["safkt"] in (None, "") else float(row["safkt"])
            row["est"] = bool(row["est"])
            for k in ("company", "region", "quoted", "startup", "note"):
                row[k] = "" if row[k] is None else str(row[k])
            if row["status"] not in ("op", "con", "ann", "can") or row["type"] not in ("standalone", "copro"):
                raise ValueError("status must be op|con|ann|can and type standalone|copro")
            if not row["name"] or not (-90 <= row["lat"] <= 90 and -180 <= row["lon"] <= 180):
                raise ValueError("name, lat and lon are required")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ApiError(422, "bad_atlas", f"Plant #{i + 1}: {exc}") from exc
        out.append(row)
    return out


def replace(doc: dict) -> int:
    plants = _validate(doc.get("plants") if isinstance(doc, dict) else doc)
    snapshot = (doc.get("snapshot") if isinstance(doc, dict) else None) or ""
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM intel.atlas_plants"))
        c.execute(text(f"INSERT INTO intel.atlas_plants ({', '.join(FIELDS)}) "
                       f"VALUES ({', '.join(':' + k for k in FIELDS)})"), plants)
        c.execute(text("INSERT INTO ops.app_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                       "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
                  {"k": SNAPSHOT_KEY, "v": json.dumps(snapshot)})
    return len(plants)


def seed_if_empty() -> int:
    with get_engine().connect() as c:
        if c.execute(text("SELECT EXISTS (SELECT 1 FROM intel.atlas_plants)")).scalar():
            return 0
    return replace(json.loads(SEED.read_text(encoding="utf-8")))


def payload() -> dict:
    with get_engine().connect() as c:
        rows = c.execute(text(f"SELECT {', '.join(FIELDS)} FROM intel.atlas_plants ORDER BY kt DESC, name")).mappings().all()
        snap = c.execute(text("SELECT value FROM ops.app_settings WHERE key = :k"), {"k": SNAPSHOT_KEY}).scalar()
    plants = [{**r, "lat": float(r["lat"]), "lon": float(r["lon"]), "kt": float(r["kt"]),
               "safkt": None if r["safkt"] is None else float(r["safkt"])} for r in rows]
    return {"snapshot": snap or None, "plants": plants}
