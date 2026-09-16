from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ZIP_PATH = DATA_DIR / "us_zips.json"


def _cache_path(name: str) -> Path:
    """Writable cache on serverless (/tmp); repo data/ locally."""
    try:
        from config import writable_data_dir

        return writable_data_dir() / name
    except Exception:
        return DATA_DIR / name


ZIP_CACHE_PATH = DATA_DIR / "zip_geocode_cache.json"  # default; resolved at use
STORE_CACHE_PATH = DATA_DIR / "store_cache.json"

EARTH_MI = 3958.8


@dataclass
class Store:
    store_id: str
    name: str
    address: str
    city: str
    state: str
    zip: str
    lat: float
    lon: float
    distance_miles: float
    phone: Optional[str] = None
    source: str = "openstreetmap"
    coord_precision: str = "unknown"
    store_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Deal:
    deal_id: str
    store_id: str
    title: str
    brand: Optional[str]
    category: str
    current_price: float
    list_price: float
    discount_pct: float
    savings: float
    deal_type: str
    confidence: str
    url: Optional[str]
    image_url: Optional[str]
    in_store: bool = True
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return EARTH_MI * 2 * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def load_zips() -> Dict[str, Dict[str, Any]]:
    import json

    with ZIP_PATH.open(encoding="utf-8") as f:
        rows = json.load(f)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            lat = float(row.get("latitude") or "")
            lon = float(row.get("longitude") or "")
        except (TypeError, ValueError):
            continue
        z = str(row["zip_code"]).zfill(5)
        out[z] = {
            "zip": z,
            "lat": lat,
            "lon": lon,
            "city": row.get("city") or "",
            "state": row.get("state") or "",
            "county": row.get("county") or "",
        }
    return out


def _load_zip_cache() -> Dict[str, Dict[str, Any]]:
    import json

    path = _cache_path("zip_geocode_cache.json")
    # Prefer writable cache; fall back to bundled if present
    for candidate in (path, DATA_DIR / "zip_geocode_cache.json"):
        if not candidate.exists():
            continue
        try:
            with candidate.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _save_zip_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    import json

    path = _cache_path("zip_geocode_cache.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f)


def resolve_zip(zip_code: str) -> Dict[str, Any]:
    z = "".join(c for c in zip_code.strip() if c.isdigit()).zfill(5)
    if len(z) != 5:
        raise ValueError("ZIP must be 5 digits")

    # Prefer accurate Nominatim geocode (cached). Local ZIP DB is fallback only —
    # some public ZIP files have incorrect centroids (e.g. 90210).
    cache = _load_zip_cache()
    if z in cache:
        return cache[z]

    loc = nominatim_geocode_zip(z)
    if loc:
        # merge city/state from local DB when available
        local = load_zips().get(z)
        if local:
            loc["city"] = loc.get("city") or local.get("city") or ""
            loc["state"] = loc.get("state") or local.get("state") or ""
            loc["county"] = loc.get("county") or local.get("county") or ""
        cache[z] = loc
        _save_zip_cache(cache)
        return loc

    zips = load_zips()
    if z in zips:
        return zips[z]
    raise ValueError(f"Unknown ZIP: {z}")


def nominatim_geocode_zip(zip_code: str) -> Optional[Dict[str, Any]]:
    params = {
        "postalcode": zip_code,
        "country": "US",
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    try:
        data = _nominatim_get("/search", params)
    except Exception:
        return None
    if not data:
        return None
    item = data[0]
    addr = item.get("address") or {}
    return {
        "zip": zip_code,
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "city": addr.get("city") or addr.get("town") or addr.get("village") or "",
        "state": _state_abbr(addr.get("state") or ""),
        "county": (addr.get("county") or "").replace(" County", ""),
    }


def _nominatim_get(path: str, params: Dict[str, Any]) -> Any:
    headers = {
        "User-Agent": "HiddenClearances-WalmartMVP/0.1 (deal-finder demo; local)",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=30.0, headers=headers) as client:
        r = client.get(f"https://nominatim.openstreetmap.org{path}", params=params)
        r.raise_for_status()
        # be polite
        time.sleep(0.8)
        return r.json()


_STORE_MEMO: Dict[str, Tuple[Dict[str, Any], List[Store]]] = {}


def find_stores_near_zip(zip_code: str, radius_miles: float = 50.0, limit: int = 100) -> Tuple[Dict[str, Any], List[Store]]:
    loc = resolve_zip(zip_code)
    memo_key = f"{loc['zip']}|{radius_miles}|{limit}|v4"
    if memo_key in _STORE_MEMO:
        return _STORE_MEMO[memo_key]

    lat, lon = loc["lat"], loc["lon"]
    stores: List[Store] = []

    # 1) Prefer official Walmart store IDs (~4.6k US stores)
    try:
        from store_db import find_official_stores_near

        # Fast path for UI/API: use cached coords only (no Nominatim during request).
        # Pre-geocode via scripts/pregeocode_stores.py for better distances.
        official = find_official_stores_near(
            lat, lon, radius_miles, limit, refine_geocode=False
        )
        for item in official:
            sid = str(item["store_id"])
            city_slug = (item.get("city") or "store").lower().replace(" ", "-")
            state_slug = (item.get("state") or "").lower()
            stores.append(
                Store(
                    store_id=sid,
                    name=item["name"],
                    address=item["address"],
                    city=item["city"],
                    state=item["state"],
                    zip=item["zip"],
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                    distance_miles=float(item["distance_miles"]),
                    source="walmart_store_list",
                    coord_precision=item.get("coord_precision") or "zip_centroid",
                    store_url=f"https://www.walmart.com/store/{sid}-{city_slug}-{state_slug}",
                )
            )
    except Exception:
        stores = []

    # 2) If official list missing/empty, fall back to OpenStreetMap
    if not stores:
        dlat = radius_miles / 69.0
        dlon = radius_miles / max(49.0, abs(math.cos(math.radians(lat)) * 69.0))
        params = {
            "q": "Walmart",
            "format": "json",
            "addressdetails": 1,
            "extratags": 1,
            "limit": min(max(limit * 2, 50), 50),
            "countrycodes": "us",
            "viewbox": f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}",
            "bounded": 1,
        }
        try:
            results = _nominatim_get("/search", params) or []
        except Exception:
            results = []

        seen = set()
        for item in results:
            name = (item.get("display_name") or "").split(",")[0].strip()
            display = (item.get("display_name") or "").lower()
            brand = ((item.get("extratags") or {}).get("brand") or "").lower()
            if "walmart" not in name.lower() and "wal-mart" not in display:
                if "walmart" not in brand and "wal-mart" not in brand:
                    continue

            slat = float(item["lat"])
            slon = float(item["lon"])
            dist = haversine_miles(lat, lon, slat, slon)
            if dist > radius_miles:
                continue

            addr = item.get("address") or {}
            city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or ""
            state = addr.get("state") or ""
            postal = (addr.get("postcode") or "").split(";")[0][:5]
            road = addr.get("road") or addr.get("suburb") or ""
            house = addr.get("house_number") or ""
            street = f"{house} {road}".strip()

            osm_id = str(item.get("osm_id") or item.get("place_id"))
            ref = (item.get("extratags") or {}).get("ref") or (item.get("extratags") or {}).get("store_number")
            store_id = str(ref) if ref else f"osm-{osm_id}"

            key = (round(slat, 4), round(slon, 4))
            if key in seen:
                continue
            seen.add(key)

            nice_name = name if "walmart" in name.lower() else "Walmart"
            if "neighborhood" in (item.get("type") or ""):
                continue

            stores.append(
                Store(
                    store_id=store_id,
                    name=nice_name,
                    address=street or (item.get("display_name") or "").split(",")[0],
                    city=city,
                    state=_state_abbr(state),
                    zip=postal or loc["zip"],
                    lat=slat,
                    lon=slon,
                    distance_miles=round(dist, 2),
                    phone=(item.get("extratags") or {}).get("phone"),
                    source="openstreetmap",
                )
            )

        stores.sort(key=lambda s: s.distance_miles)
        stores = stores[:limit]

    if not stores:
        stores = _fallback_stores(loc, radius_miles, limit)

    _STORE_MEMO[memo_key] = (loc, stores)
    return loc, stores


def _state_abbr(state: str) -> str:
    mapping = {
        "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
        "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
        "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
        "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
        "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
        "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
        "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
        "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
        "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
        "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
    }
    if not state:
        return ""
    if len(state) == 2:
        return state.upper()
    return mapping.get(state, state)


def _fallback_stores(loc: Dict[str, Any], radius_miles: float, limit: int) -> List[Store]:
    """Deterministic placeholder stores near ZIP when OSM has no Walmart POIs."""
    rng = random.Random(int(loc["zip"]))
    out: List[Store] = []
    count = min(limit, 8)
    for i in range(count):
        bearing = rng.random() * 2 * math.pi
        dist = 1.5 + rng.random() * min(radius_miles, 18)
        # rough offset
        dlat = (dist / 69.0) * math.cos(bearing)
        dlon = (dist / 54.0) * math.sin(bearing)
        sid = f"WMT-{loc['zip']}-{i+1:02d}"
        out.append(
            Store(
                store_id=sid,
                name="Walmart Supercenter" if i % 2 == 0 else "Walmart Neighborhood Market",
                address=f"{100 + i * 37} Local Retail Dr",
                city=loc.get("city") or "Nearby",
                state=loc.get("state") or "",
                zip=loc["zip"],
                lat=round(loc["lat"] + dlat, 6),
                lon=round(loc["lon"] + dlon, 6),
                distance_miles=round(dist, 2),
                source="fallback_placeholder",
            )
        )
    out.sort(key=lambda s: s.distance_miles)
    return out


# Expanded sample catalog — partitioned by store_id so stores do NOT share lists.
CATALOG = [
    ("Ninja Air Fryer Pro 5.5 Qt", "Ninja", "Kitchen", 129.99, ["clearance", "markdown"]),
    ("Instant Pot Duo 7-in-1 6 Qt", "Instant Pot", "Kitchen", 99.99, ["markdown"]),
    ("Keurig K-Mini Coffee Maker", "Keurig", "Kitchen", 79.99, ["clearance"]),
    ("Dyson V8 Absolute Vacuum", "Dyson", "Home", 429.99, ["markdown"]),
    ("Shark Navigator Lift-Away", "Shark", "Home", 199.99, ["clearance"]),
    ("Levi's 505 Regular Jeans", "Levi's", "Apparel", 59.98, ["clearance", "hidden"]),
    ("Nike Men's Revolution 6", "Nike", "Apparel", 70.00, ["markdown"]),
    ("Adidas Essentials Hoodie", "Adidas", "Apparel", 55.00, ["clearance"]),
    ("Hanes Men's 6-Pack Tees", "Hanes", "Apparel", 24.00, ["hidden"]),
    ("Apple AirPods 4", "Apple", "Electronics", 129.00, ["markdown"]),
    ("Samsung 55\" Crystal UHD 4K", "Samsung", "Electronics", 478.00, ["clearance"]),
    ("Fire TV Stick 4K Select", "Amazon", "Electronics", 29.99, ["markdown"]),
    ("LEGO Star Wars X-Wing", "LEGO", "Toys", 59.99, ["clearance"]),
    ("Barbie Dreamhouse", "Mattel", "Toys", 199.00, ["markdown"]),
    ("Nerf Elite 2.0", "Nerf", "Toys", 24.97, ["hidden"]),
    ("Charmin Ultra Soft 18 Mega", "Charmin", "Grocery", 28.98, ["markdown"]),
    ("Tide Liquid Laundry 100 oz", "Tide", "Grocery", 18.97, ["clearance"]),
    ("Equate Ibuprofen 200ct", "Equate", "Health", 9.98, ["hidden"]),
    ("CeraVe Moisturizing Cream 19oz", "CeraVe", "Health", 19.97, ["markdown"]),
    ("Mainstays Queen Sheet Set", "Mainstays", "Home", 24.00, ["clearance", "hidden"]),
    ("Better Homes Throw Pillow", "Better Homes", "Home", 18.00, ["hidden"]),
    ("Ozark Trail 20oz Tumbler", "Ozark Trail", "Outdoor", 12.00, ["clearance"]),
    ("Coleman Sundome 4-Person Tent", "Coleman", "Outdoor", 79.00, ["markdown"]),
    ("Hyper Tough Drill Combo Kit", "Hyper Tough", "Tools", 69.00, ["clearance"]),
    ("Great Value Paper Towels 12pk", "Great Value", "Grocery", 15.98, ["markdown"]),
    ("Bissell Little Green Portable", "Bissell", "Home", 119.99, ["clearance"]),
    ("Cuisinart Griddler Deluxe", "Cuisinart", "Kitchen", 99.95, ["markdown"]),
    ("Robot Vacuum Eufy 11S", "eufy", "Home", 229.99, ["clearance", "hidden"]),
    ("Sony WH-CH720N Headphones", "Sony", "Electronics", 149.99, ["markdown"]),
    ("Logitech MX Master 3S", "Logitech", "Electronics", 99.99, ["clearance"]),
    ("Yeti Rambler 30oz", "YETI", "Outdoor", 38.00, ["markdown"]),
    ("Black+Decker Toaster Oven", "Black+Decker", "Kitchen", 69.00, ["clearance"]),
    ("Crocs Classic Clog", "Crocs", "Apparel", 49.99, ["hidden"]),
    ("Under Armour Tech Tee", "Under Armour", "Apparel", 25.00, ["markdown"]),
    ("Purina One Dry Dog Food 16.5lb", "Purina", "Pets", 29.98, ["clearance"]),
    ("Fresh Step Litter 42lb", "Fresh Step", "Pets", 24.97, ["markdown"]),
    ("Vizio 40\" Full HD Smart TV", "VIZIO", "Electronics", 168.00, ["clearance"]),
    ("Roku Streaming Stick 4K", "Roku", "Electronics", 49.00, ["markdown"]),
    ("Oster Blender Pro 1200", "Oster", "Kitchen", 59.99, ["hidden"]),
    ("Rubbermaid Storage Tote 3pk", "Rubbermaid", "Home", 22.00, ["clearance"]),
    ("Igloo Cooler 52qt", "Igloo", "Outdoor", 49.00, ["markdown"]),
    ("Craftsman 20V Drill", "Craftsman", "Tools", 89.00, ["clearance"]),
    ("Clorox Disinfecting Wipes 5pk", "Clorox", "Grocery", 14.97, ["markdown"]),
    ("Dove Body Wash 6pk", "Dove", "Health", 19.94, ["clearance"]),
    ("Neutrogena Sunscreen SPF 70", "Neutrogena", "Health", 12.97, ["hidden"]),
    ("Mainstays Soft Throw Blanket", "Mainstays", "Home", 12.00, ["clearance"]),
    ("Holiday Time String Lights", "Holiday Time", "Home", 16.00, ["markdown"]),
    ("Play-Doh Modeling Compound 10pk", "Play-Doh", "Toys", 9.00, ["clearance"]),
    ("Hot Wheels 20-Car Pack", "Hot Wheels", "Toys", 24.00, ["markdown"]),
    ("George Foreman Grill", "George Foreman", "Kitchen", 39.00, ["hidden"]),
    ("Hamilton Beach Microwave 0.7cu", "Hamilton Beach", "Kitchen", 79.00, ["clearance"]),
    ("KitchenAid Hand Mixer", "KitchenAid", "Kitchen", 59.99, ["markdown"]),
    ("Pyrex Baking Dish 3pc", "Pyrex", "Kitchen", 24.00, ["clearance"]),
    ("Contigo Water Bottle 24oz", "Contigo", "Outdoor", 16.00, ["markdown"]),
    ("Quechua Hiking Backpack 20L", "Quechua", "Outdoor", 29.00, ["clearance", "hidden"]),
    ("Wilson Tennis Balls 3pk", "Wilson", "Sports", 5.00, ["markdown"]),
    ("Spalding Basketball", "Spalding", "Sports", 19.00, ["clearance"]),
    ("Fitbit Inspire 3", "Fitbit", "Electronics", 99.95, ["markdown"]),
    ("Anker USB-C Charger 40W", "Anker", "Electronics", 25.00, ["clearance"]),
    ("Sandisk 128GB Flash Drive", "SanDisk", "Electronics", 14.00, ["hidden"]),
    ("Brother Label Maker", "Brother", "Office", 39.00, ["markdown"]),
    ("Pilot G2 Pens 12pk", "Pilot", "Office", 12.00, ["clearance"]),
    ("Mead Spiral Notebooks 6pk", "Mead", "Office", 9.00, ["markdown"]),
    ("Scotch Packaging Tape 6pk", "Scotch", "Office", 15.00, ["clearance"]),
    ("Glade Plugin Refills 5pk", "Glade", "Home", 11.00, ["markdown"]),
    ("Febreze Air Freshener 3pk", "Febreze", "Home", 9.00, ["clearance"]),
    ("Lysol Spray Twin Pack", "Lysol", "Home", 8.00, ["hidden"]),
    ("Reynolds Wrap Foil 2pk", "Reynolds", "Grocery", 10.00, ["markdown"]),
    ("Ziploc Bags Variety", "Ziploc", "Grocery", 12.00, ["clearance"]),
    ("Pedialyte Electrolyte 6pk", "Pedialyte", "Health", 14.00, ["markdown"]),
    ("Advil Liqui-Gels 200ct", "Advil", "Health", 22.00, ["clearance"]),
    ("Crest 3D Whitestrips", "Crest", "Health", 44.00, ["markdown"]),
    ("Oral-B Electric Toothbrush", "Oral-B", "Health", 49.00, ["clearance", "hidden"]),
    ("Pampers Swaddlers Size 3", "Pampers", "Baby", 32.00, ["markdown"]),
    ("Huggies Wipes 3pk", "Huggies", "Baby", 12.00, ["clearance"]),
    ("Graco Car Seat Base", "Graco", "Baby", 79.00, ["markdown"]),
    ("Fisher-Price Rock-a-Stack", "Fisher-Price", "Toys", 10.00, ["clearance"]),
    ("Melissa & Doug Puzzle", "Melissa & Doug", "Toys", 14.00, ["hidden"]),
    ("Crayola Crayon 64ct", "Crayola", "Toys", 6.00, ["markdown"]),
]


def _store_catalog_slice(store_id: str, take: int = 12) -> List[tuple]:
    """
    Deterministic low-overlap catalog slice per store.

    Rank every catalog row by hash(store_id|title) and take the top N.
    With ~80 catalog rows and take=12, stores rarely share most titles.
    """
    if not CATALOG:
        return []
    scored = [
        (hashlib.sha256(f"{store_id}|{item[0]}".encode()).hexdigest(), item)
        for item in CATALOG
    ]
    scored.sort(key=lambda x: x[0])
    return [item for _, item in scored[: min(take, len(CATALOG))]]


def generate_deals_for_store(
    store: Store,
    min_discount_pct: float = 20.0,
    limit: int = 40,
) -> List[Deal]:
    """
    Store-specific sample deals (demo / mode=sample).

    Product set is partitioned by store_id so two stores near the same ZIP
    do not show the same list.
    """
    seed = int(hashlib.sha256(store.store_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    items = _store_catalog_slice(store.store_id, take=min(14, len(CATALOG)))

    deals: List[Deal] = []
    for title, brand, category, list_price, tags in items:
        roll = rng.random()
        if "hidden" in tags or roll < 0.18:
            pct = rng.uniform(70, 88)
            deal_type = "hidden_clearance"
            confidence = "high"
        elif "clearance" in tags or roll < 0.5:
            pct = rng.uniform(40, 69)
            deal_type = "clearance"
            confidence = "high"
        elif roll < 0.85:
            pct = rng.uniform(max(min_discount_pct, 20), 39)
            deal_type = "markdown"
            confidence = "medium"
        else:
            pct = rng.uniform(5, 19)
            deal_type = "minor_drop"
            confidence = "low"

        if pct < min_discount_pct:
            continue

        current = round(list_price * (1 - pct / 100.0), 2)
        if current >= 10:
            current = (
                math.floor(current) + 0.00
                if rng.random() < 0.25
                else round(current - 0.03, 2)
            )
            current = max(0.50, current)
        # Slight store-specific price jitter so same SKU (if any) still differs
        jitter = ((seed % 17) - 8) * 0.01
        current = max(0.5, round(current + jitter, 2))
        savings = round(list_price - current, 2)
        real_pct = round((savings / list_price) * 100, 1)

        note = f"Sample deal for store #{store.store_id}"
        if deal_type == "hidden_clearance":
            note = "Deep cut vs list - hidden-clearance style candidate"
        elif "hidden" in tags:
            note = "Unadvertised-style markdown candidate"

        slug = hashlib.md5(
            f"{store.store_id}|{title}|{list_price}".encode()
        ).hexdigest()[:10]
        search_q = quote(title)
        deals.append(
            Deal(
                deal_id=f"d-{store.store_id}-{slug}",
                store_id=store.store_id,
                title=title,
                brand=brand,
                category=category,
                current_price=current,
                list_price=round(list_price, 2),
                discount_pct=real_pct,
                savings=savings,
                deal_type=deal_type,
                confidence=confidence,
                url=f"https://www.walmart.com/search?q={search_q}",
                image_url=None,
                in_store=True,
                notes=note,
            )
        )

    deals.sort(key=lambda d: (-d.discount_pct, -d.savings))
    return deals[:limit]


def build_report(
    zip_code: str,
    store_id: str,
    radius_miles: float = 50.0,
    min_discount_pct: float = 20.0,
    prefer_live: bool = True,
) -> Dict[str, Any]:
    loc, stores = find_stores_near_zip(zip_code, radius_miles=radius_miles)
    store = next((s for s in stores if s.store_id == store_id), None)
    if store is None:
        raise ValueError(f"Store '{store_id}' not found for ZIP {zip_code}. Call /stores first.")

    data_mode = "failed"
    collector_notes = ""
    deals_dicts: List[Dict[str, Any]] = []
    live_product_count = 0
    cache_age_sec: Optional[float] = None
    sid = str(store_id)

    # Live pricing requires a Walmart-capable commercial backend (not ISP-only).
    can_live = prefer_live
    try:
        from client_collector import client_live_ready, setup_required_message

        if not client_live_ready():
            can_live = False
            collector_notes = setup_required_message(sid)
            data_mode = "setup_required"
    except Exception:
        pass

    if can_live and store.source == "walmart_store_list" and not sid.startswith("osm-"):
        try:
            import os

            from collector import (
                cache_age_seconds,
                clear_live_cooldown,
                collect_store_prices,
                live_products_to_deals,
                load_cached_live_pull,
            )

            # Same-store cache ONLY — and only if the pull was for this store's ZIP
            cached = load_cached_live_pull(sid)
            cache_age_sec = cache_age_seconds(cached)
            store_zip = "".join(c for c in str(store.zip or zip_code or "") if c.isdigit())[:5]
            cached_zip = "".join(c for c in str(getattr(cached, "zip", None) or "") if c.isdigit())[:5] if cached else ""
            zip_ok = (not cached_zip) or (not store_zip) or (cached_zip == store_zip)
            same_store_cache = bool(
                cached
                and cached.products
                and str(cached.store_id) == sid
                and zip_ok
            )
            # Short TTL so ZIP/store switches don't keep serving stale catalogs
            fresh_same_store = bool(
                same_store_cache
                and cache_age_sec is not None
                and cache_age_sec < 20 * 60
            )

            pull = None
            if fresh_same_store:
                pull = cached
                data_mode = "live"
                collector_notes = (
                    f"same-store cache age={int(cache_age_sec)}s ({cached.notes})"
                )
            else:
                clear_live_cooldown()
                prev_retries = os.environ.get("WALMART_MAX_RETRIES")
                os.environ["WALMART_MAX_RETRIES"] = os.environ.get(
                    "WALMART_REPORT_MAX_RETRIES", "2"
                )
                try:
                    pull = collect_store_prices(
                        store_id=sid,
                        zip_code=store.zip or zip_code,
                        force=True,
                    )
                finally:
                    if prev_retries is None:
                        os.environ.pop("WALMART_MAX_RETRIES", None)
                    else:
                        os.environ["WALMART_MAX_RETRIES"] = prev_retries

                collector_notes = pull.notes or ""
                if pull.proxy_used:
                    collector_notes = (collector_notes + "; proxy=on").strip("; ")

                if pull.ok and pull.products:
                    data_mode = "live"
                    collector_notes = (
                        (collector_notes + "; ") if collector_notes else ""
                    ) + f"live prices ({len(pull.products)}) for store {sid}"
                elif same_store_cache:
                    # OK only when THIS store was pulled successfully before
                    pull = cached
                    data_mode = "live_cached"
                    collector_notes = (
                        (collector_notes + "; ") if collector_notes else ""
                    ) + (
                        f"refresh failed; last successful pull for store {sid} only"
                    )
                else:
                    pull = None
                    data_mode = "failed"
                    collector_notes = (
                        (collector_notes + "; ") if collector_notes else ""
                    ) + (
                        f"no live data for store {sid}; "
                        "will not reuse another store's cache"
                    )

            if pull and pull.products and str(pull.store_id) == sid:
                # Final guard: never ship a catalog that matches another store
                from walmart_parse import overlaps_other_store_cache

                clone_of = overlaps_other_store_cache(sid, pull.products)
                if clone_of:
                    data_mode = "failed"
                    collector_notes = (
                        (collector_notes + "; ") if collector_notes else ""
                    ) + (
                        f"rejected clone of store {clone_of}; "
                        "refusing to show identical list"
                    )
                    deals_dicts = []
                    live_product_count = 0
                    # Drop poisoned same-store latest
                    try:
                        from config import PULLS_DIR

                        for doomed in (sid, str(clone_of)):
                            peer = PULLS_DIR / f"latest_{doomed}.json"
                            if peer.exists():
                                peer.unlink()
                    except Exception:
                        pass
                else:
                    deals_dicts = live_products_to_deals(
                        sid,
                        pull.products,
                        min_discount_pct=min_discount_pct,
                        include_shelf=False,
                    )
                    # Belt-and-suspenders: never return under-slider rows
                    deals_dicts = [
                        d
                        for d in deals_dicts
                        if float(d.get("discount_pct") or 0) + 1e-9 >= float(min_discount_pct)
                    ]
                    live_product_count = len(pull.products)
                    for d in deals_dicts:
                        d["store_id"] = sid
                        d["min_discount_pct"] = float(min_discount_pct)
            else:
                deals_dicts = []
                if data_mode in ("live", "live_cached"):
                    data_mode = "failed"
        except Exception as e:
            data_mode = "failed"
            collector_notes = f"{type(e).__name__}: {e}"
            deals_dicts = []
            try:
                from collector import load_cached_live_pull, live_products_to_deals

                cached = load_cached_live_pull(sid)
                if cached and cached.products and str(cached.store_id) == sid:
                    deals_dicts = live_products_to_deals(
                        sid,
                        cached.products,
                        min_discount_pct=min_discount_pct,
                        include_shelf=False,
                    )
                    live_product_count = len(cached.products)
                    data_mode = "live_cached"
                    collector_notes = (
                        collector_notes
                        + f"; same-store cache after error ({cached.notes})"
                    )
            except Exception:
                pass

    # Never inject demo/sample catalogs — client needs real in-store deals only.
    thresholds_meta: Dict[str, Any] = {}
    thr = None
    try:
        from deal_engine import DealThresholds, enrich_sample_deal

        thr = DealThresholds.from_env(
            {
                "min_discount_pct": min_discount_pct,
                "include_shelf": False,
                "deals_only": True,
            }
        )
        thresholds_meta = thr.to_dict()
    except Exception:
        thr = None

    if not prefer_live and not deals_dicts:
        # Explicit non-live request with no data → failed (sample disabled)
        data_mode = "failed"
        collector_notes = (collector_notes + "; sample/demo disabled").strip("; ")

    # Enrich live rows missing why_deal
    if deals_dicts and thr is not None:
        for d in deals_dicts:
            if not d.get("why_deal"):
                enrich_sample_deal(d, thr)

    note = {
        "live": (
            "Live in-store markdown / clearance deals for the selected Walmart "
            "(not national online listings)."
        ),
        "live_cached": (
            "Fresh pull failed; showing last successful in-store deal pull "
            "for this same store only."
        ),
        "failed": (
            "Could not load in-store deals for this store. "
            "Demo catalogs and other stores' data are never shown."
        ),
        "failed_no_proxy": (
            "A Walmart-capable live backend is required "
            "(Oxylabs, ScraperAPI, or Bright Data Web Unlocker)."
        ),
        "setup_required": (
            "Live in-store deals need Oxylabs or ScraperAPI configured. "
            "Bright Data ISP alone is blocked by Walmart/Akamai."
        ),
    }.get(data_mode) or (collector_notes or data_mode)

    live_ok = data_mode in ("live", "live_cached")
    user_error = None
    if data_mode == "setup_required":
        try:
            from client_collector import setup_required_message

            user_error = setup_required_message(sid)
        except Exception:
            user_error = collector_notes or note
    elif data_mode == "failed_no_proxy":
        user_error = (
            "In-store prices need Oxylabs (OXYLABS_USERNAME/PASSWORD) "
            "or ScraperAPI (SCRAPERAPI_KEY). ISP proxies alone are blocked."
        )
    elif data_mode == "live_cached":
        age_bit = ""
        if cache_age_sec is not None:
            age_bit = f" (about {max(1, int(cache_age_sec // 60))} min old)"
        user_error = (
            f"Couldn't refresh in-store prices for this store{age_bit}. "
            "Showing the last successful results for this same store only."
        )
    elif not live_ok and data_mode != "sample":
        try:
            from client_collector import client_live_ready

            live_backend_ready = client_live_ready()
        except Exception:
            live_backend_ready = False
        if collector_notes and len(str(collector_notes).strip()) > 8:
            user_error = (
                f"Could not load deals for store #{sid}. {collector_notes}"
            )
        elif live_backend_ready:
            user_error = (
                f"Could not load deals for store #{sid}. "
                "The live data provider returned no store-scoped results. "
                "Try another store or retry in a minute."
            )
        else:
            user_error = (
                f"Could not load deals for store #{sid}. "
                "Set OXYLABS_USERNAME and OXYLABS_PASSWORD on the server, then retry."
            )
        data_mode = "failed"
        deals_dicts = []
        live_product_count = 0

    scan_id = None
    try:
        from scan_store import save_scan

        scan_id = save_scan(
            store_id=store.store_id,
            zip_code=loc["zip"],
            mode=data_mode,
            deals=deals_dicts,
            notes=collector_notes or note,
        )
    except Exception:
        scan_id = None

    # Ensure every deal row has a usable Walmart URL
    for d in deals_dicts:
        if not d.get("url"):
            q = quote(str(d.get("title") or "walmart"))
            pid = d.get("product_id")
            d["url"] = (
                f"https://www.walmart.com/ip/{pid}"
                if pid and str(pid).isdigit()
                else f"https://www.walmart.com/search?q={q}"
            )

    return {
        "zip": loc["zip"],
        "location": {
            "city": loc.get("city"),
            "state": loc.get("state"),
            "lat": loc["lat"],
            "lon": loc["lon"],
        },
        "store": store.to_dict(),
        "summary": {
            "deal_count": len(deals_dicts),
            "avg_discount_pct": round(
                sum(d["discount_pct"] for d in deals_dicts) / len(deals_dicts), 1
            )
            if deals_dicts
            else 0,
            "max_discount_pct": max((d["discount_pct"] for d in deals_dicts), default=0),
            "total_savings_if_bought_all": round(
                sum(d["savings"] for d in deals_dicts), 2
            ),
            "min_discount_pct": min_discount_pct,
        },
        "deals": deals_dicts,
        "meta": {
            "data_mode": data_mode,
            "live_ok": live_ok,
            "user_error": user_error,
            "store_source": store.source,
            "collector_notes": collector_notes,
            "live_product_count": live_product_count,
            "cache_age_sec": int(cache_age_sec) if cache_age_sec is not None else None,
            "scan_id": scan_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note,
            "deal_thresholds": thresholds_meta,
            "milestone": 3,
        },
    }
