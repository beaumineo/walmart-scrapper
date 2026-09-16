from __future__ import annotations

import csv
import json
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORES_CSV = DATA_DIR / "walmart_stores.csv"
STORE_GEO_CACHE = DATA_DIR / "store_geocode_cache.json"


def _db_path() -> Path:
    try:
        from config import writable_data_dir

        return writable_data_dir() / "deals.db"
    except Exception:
        return DATA_DIR / "deals.db"


DB_PATH = _db_path()


def get_db() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            product_id TEXT,
            title TEXT NOT NULL,
            brand TEXT,
            category TEXT,
            current_price REAL,
            list_price REAL,
            url TEXT,
            image_url TEXT,
            raw_json TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(store_id, product_id, scraped_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            zip TEXT,
            mode TEXT NOT NULL,
            item_count INTEGER DEFAULT 0,
            deal_count INTEGER DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            notes TEXT
        )
        """
    )
    conn.commit()
    return conn


def parse_store_address(raw: str) -> Dict[str, str]:
    text = (raw or "").replace("\xa0", " ").replace("\u00a0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    city = ""
    state = ""
    street = text
    m = re.search(
        r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s*(?P<zip>\d{5})?$",
        text,
    )
    if m:
        street = m.group("street").strip()
        city = m.group("city").strip()
        state = m.group("state").strip()
    else:
        m2 = re.search(r",\s*([A-Z]{2})\s*(\d{5})?\s*$", text)
        if m2:
            state = m2.group(1)
            street = text[: m2.start()].strip(" ,")
            if "," in street:
                street, city = [p.strip() for p in street.rsplit(",", 1)]
    return {"street": street, "city": city, "state": state}


def _geo_cache_paths() -> List[Path]:
    paths: List[Path] = []
    try:
        from config import writable_data_dir

        paths.append(writable_data_dir() / "store_geocode_cache.json")
    except Exception:
        pass
    paths.append(DATA_DIR / "store_geocode_cache.json")
    return paths


def _load_geo_cache() -> Dict[str, Dict[str, Any]]:
    for path in _geo_cache_paths():
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _save_geo_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    path = _geo_cache_paths()[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f)


def geocode_store_address(
    store_id: str,
    street: str,
    city: str,
    state: str,
    postal: str,
    cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, float]]:
    """Geocode a store street address. Cached on disk by store_id."""
    own_cache = cache is None
    cache = _load_geo_cache() if own_cache else cache
    if store_id in cache and cache[store_id].get("lat") is not None:
        return {"lat": float(cache[store_id]["lat"]), "lon": float(cache[store_id]["lon"])}

    query = ", ".join(
        p for p in [street, city, state, postal, "United States"] if p
    )
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={
                "User-Agent": "HiddenClearances-WalmartMVP/0.2 (store-distance accuracy)"
            },
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        time.sleep(0.9)  # Nominatim polite rate limit
        if not data:
            cache[store_id] = {"lat": None, "lon": None, "q": query}
            if own_cache:
                _save_geo_cache(cache)
            return None
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        cache[store_id] = {"lat": lat, "lon": lon, "q": query}
        if own_cache:
            _save_geo_cache(cache)
        return {"lat": lat, "lon": lon}
    except Exception:
        return None


@lru_cache(maxsize=1)
def load_official_stores() -> List[Dict[str, Any]]:
    """Load Walmart store list (real store IDs).

    Base coordinates start as the store ZIP centroid (fast). Precise street
    geocodes are applied later in find_official_stores_near when available.
    """
    from walmart_core import load_zips

    if not STORES_CSV.exists():
        return []

    zips = load_zips()
    geo = _load_geo_cache()
    out: List[Dict[str, Any]] = []
    with STORES_CSV.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = str(row.get("storeId") or "").strip()
            postal = str(row.get("postalCode") or "").strip().zfill(5)
            if not sid or len(postal) != 5:
                continue
            loc = zips.get(postal)
            if not loc:
                continue
            addr = parse_store_address(row.get("address") or "")
            street_l = (addr["street"] or "").lower()
            name = (
                "Walmart Neighborhood Market"
                if "neighborhood" in street_l
                else "Walmart Supercenter"
            )

            lat = loc["lat"]
            lon = loc["lon"]
            precision = "zip_centroid"
            cached = geo.get(sid)
            if cached and cached.get("lat") is not None:
                lat = float(cached["lat"])
                lon = float(cached["lon"])
                precision = "street_geocode"

            out.append(
                {
                    "store_id": sid,
                    "name": name,
                    "address": addr["street"]
                    or (row.get("address") or "").replace("\xa0", " "),
                    "city": addr["city"] or loc.get("city") or "",
                    "state": addr["state"] or loc.get("state") or "",
                    "zip": postal,
                    "lat": lat,
                    "lon": lon,
                    "coord_precision": precision,
                    "source": "walmart_store_list",
                }
            )
    return out


def find_official_stores_near(
    lat: float,
    lon: float,
    radius_miles: float,
    limit: int,
    refine_geocode: bool = True,
    refine_limit: int = 40,
) -> List[Dict[str, Any]]:
    """Find nearby Walmart stores within radius_miles (Walmart-style coverage).

    Returns up to `limit` stores inside the radius. Street geocodes are preferred
    for distance accuracy, but ZIP-centroid stores are still included so we do
    not under-count vs Walmart's store finder.
    """
    from collections import Counter

    from walmart_core import haversine_miles

    all_stores = load_official_stores()

    # Detect collapsed/bad ZIP centroids (many different ZIPs share one lat/lon)
    coord_counts = Counter(
        (round(float(s["lat"]), 5), round(float(s["lon"]), 5))
        for s in all_stores
        if s.get("coord_precision") != "street_geocode"
    )
    bad_coords = {c for c, n in coord_counts.items() if n >= 30}

    # Wider net first so ZIP-centroid error does not drop real nearby stores
    rough_radius = radius_miles + 25
    rough: List[Dict[str, Any]] = []
    for s in all_stores:
        item = dict(s)
        key = (round(float(item["lat"]), 5), round(float(item["lon"]), 5))
        if (
            item.get("coord_precision") != "street_geocode"
            and key in bad_coords
        ):
            # Untrusted centroid — keep as candidate only if store ZIP matches
            # a plausible nearby postal via distance after optional refine later.
            item["distance_miles"] = 9999.0
            item["coord_untrusted"] = True
        else:
            item["distance_miles"] = round(
                haversine_miles(lat, lon, item["lat"], item["lon"]), 2
            )
            item["coord_untrusted"] = False
        if item["distance_miles"] <= rough_radius or item.get("coord_untrusted"):
            rough.append(item)

    # Prefer refining nearby / untrusted candidates so more stores get real pins
    if refine_geocode:
        cache = _load_geo_cache()
        changed = False
        # Sort candidates: already close first, then untrusted
        refine_targets = sorted(
            rough,
            key=lambda x: (
                0 if x.get("coord_precision") == "street_geocode" else 1,
                0 if not x.get("coord_untrusted") else 1,
                x["distance_miles"],
            ),
        )
        refined = 0
        for item in refine_targets:
            if item.get("coord_precision") == "street_geocode":
                continue
            if refined >= refine_limit:
                break
            # Only spend geocode budget on likely-nearby or untrusted rows
            if (not item.get("coord_untrusted")) and item["distance_miles"] > radius_miles + 10:
                continue
            precise = geocode_store_address(
                item["store_id"],
                item.get("address") or "",
                item.get("city") or "",
                item.get("state") or "",
                item.get("zip") or "",
                cache=cache,
            )
            refined += 1
            changed = True
            if precise:
                item["lat"] = precise["lat"]
                item["lon"] = precise["lon"]
                item["coord_precision"] = "street_geocode"
                item["coord_untrusted"] = False
                item["distance_miles"] = round(
                    haversine_miles(lat, lon, precise["lat"], precise["lon"]), 2
                )
        if changed:
            _save_geo_cache(cache)
            load_official_stores.cache_clear()

    # Final: everything truly inside the requested radius
    scored = []
    for s in rough:
        if s.get("coord_untrusted"):
            continue
        if s["distance_miles"] <= radius_miles:
            scored.append(s)

    scored.sort(
        key=lambda x: (
            0 if x.get("coord_precision") == "street_geocode" else 1,
            x["distance_miles"],
        )
    )
    return scored[:limit]
