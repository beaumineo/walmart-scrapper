from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure `app/` is on sys.path (Vercel loads app/main.py from repo root)
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from walmart_core import build_report, find_stores_near_zip, load_zips

app = FastAPI(
    title="Walmart In-Store Deal Finder (Hidden Clearances)",
    description=(
        "ZIP → nearby store → live in-store markdown / hidden-clearance deals "
        "for that Walmart only. No demo catalogs. No national online-only lists."
    ),
    version="0.9.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _warmup() -> None:
    load_zips()
    try:
        from store_db import load_official_stores, get_db

        load_official_stores()
        get_db().close()
    except Exception:
        pass


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    from config import get_collector_config

    zips = load_zips()
    store_count = 0
    geo_ok = 0
    try:
        from store_db import load_official_stores, _load_geo_cache

        store_count = len(load_official_stores())
        geo = _load_geo_cache()
        geo_ok = sum(1 for v in geo.values() if v.get("lat") is not None)
    except Exception:
        pass
    cfg = get_collector_config()
    deal_thr = {}
    try:
        from deal_engine import DealThresholds

        deal_thr = DealThresholds.from_env().to_dict()
    except Exception:
        pass
    backends = {}
    live_ready = False
    try:
        from client_collector import client_backends_status, client_live_ready

        backends = client_backends_status(cfg)
        live_ready = client_live_ready(cfg)
    except Exception:
        pass
    return {
        "ok": True,
        "zip_count": len(zips),
        "official_store_count": store_count,
        "street_geocode_count": geo_ok,
        "proxy_enabled": cfg.proxy_enabled,
        "version": "0.9.2",
        "milestone": 3,
        "milestones_complete": [1, 2, 3],
        "proxy_count": len(cfg.proxies),
        "engine": cfg.collect_engine,
        "deal_thresholds": deal_thr,
        "live_ready": live_ready,
        "backends": backends,
        "deploy": {
            "vercel": bool(os.environ.get("VERCEL")),
            "railway": bool(
                os.environ.get("RAILWAY_ENVIRONMENT")
                or os.environ.get("RAILWAY_PROJECT_ID")
            ),
            "reliable_demo": True,
        },
    }


@app.get("/api/backends")
def api_backends():
    """Which live collectors are configured (no secrets returned)."""
    from client_collector import client_backends_status, client_live_ready, setup_required_message

    status = client_backends_status()
    ready = client_live_ready()
    hosted = bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("VERCEL")
    )
    msg = None
    if not ready:
        msg = (
            "Oxylabs is not active in this running service yet. "
            "In Railway → Variables, click Apply changes / Deploy, "
            "wait for the new deployment to finish, then refresh."
            if hosted
            else setup_required_message()
        )
    return {
        "ready": ready,
        "backends": status,
        "setup_message": msg,
        "hosted": hosted,
        "allow_inline_configure": not hosted,
        "preferred": ["oxylabs", "scraperapi", "unlocker"],
    }


class LiveBackendConfig(BaseModel):
    scraperapi_key: Optional[str] = Field(None, description="ScraperAPI key")
    oxylabs_username: Optional[str] = Field(None)
    oxylabs_password: Optional[str] = Field(None)
    brightdata_api_key: Optional[str] = Field(None)
    brightdata_unlocker_zone: Optional[str] = Field(None, description="e.g. web_unlocker1")


def _upsert_env(path: Path, updates: Dict[str, str]) -> None:
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    keys = set(updates)
    out: list[str] = []
    seen = set()
    for line in lines:
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            k = raw.split("=", 1)[0].strip()
            if k in keys:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


@app.post("/api/backends/configure")
def api_backends_configure(body: LiveBackendConfig):
    """
    Local/dev only: save a commercial backend into .env and process env.
    On Railway/Vercel, set Variables in the host dashboard (this endpoint is blocked).
    """
    from config import ENV_PATH
    from client_collector import client_backends_status, client_live_ready

    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID") or os.environ.get("VERCEL"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Do not paste API keys in the public UI on hosted deploys. "
                "Set OXYLABS_USERNAME / OXYLABS_PASSWORD in Railway Variables, "
                "then click Apply changes / Deploy."
            ),
        )

    updates: Dict[str, str] = {}
    if body.scraperapi_key and body.scraperapi_key.strip():
        updates["SCRAPERAPI_KEY"] = body.scraperapi_key.strip()
        updates["SCRAPERAPI_ULTRA"] = "1"
    if body.oxylabs_username and body.oxylabs_password:
        updates["OXYLABS_USERNAME"] = body.oxylabs_username.strip()
        updates["OXYLABS_PASSWORD"] = body.oxylabs_password.strip()
    if body.brightdata_api_key and body.brightdata_unlocker_zone:
        updates["BRIGHTDATA_API_KEY"] = body.brightdata_api_key.strip()
        updates["BRIGHTDATA_UNLOCKER_ZONE"] = body.brightdata_unlocker_zone.strip()

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="Provide scraperapi_key, or oxylabs_username+password, "
            "or brightdata_api_key+unlocker_zone",
        )

    env_saved = False
    try:
        _upsert_env(ENV_PATH, updates)
        env_saved = True
    except Exception:
        # Still apply in-process for this local session
        pass

    for k, v in updates.items():
        os.environ[k] = v

    return {
        "ok": True,
        "saved_keys": sorted(updates.keys()),
        "env_file_saved": env_saved,
        "ready": client_live_ready(),
        "backends": client_backends_status(),
    }


@app.get("/api/stores")
def api_stores(
    zip: str = Query(..., min_length=3, max_length=10, description="US ZIP code"),
    radius_miles: float = Query(50, ge=1, le=100),
    limit: int = Query(100, ge=1, le=200),
):
    try:
        loc, stores = find_stores_near_zip(zip, radius_miles=radius_miles, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Store lookup failed: {e}") from e

    return {
        "zip": loc["zip"],
        "location": {
            "city": loc.get("city"),
            "state": loc.get("state"),
            "lat": loc["lat"],
            "lon": loc["lon"],
        },
        "radius_miles": radius_miles,
        "count": len(stores),
        "stores": [s.to_dict() for s in stores],
    }


@app.get("/api/prices")
def api_prices(
    store_id: str = Query(..., description="Walmart store ID"),
    zip: Optional[str] = Query(None),
    force: bool = Query(False, description="Bypass bot-check cooldown"),
):
    """Store-level product/price pull. Saves JSON under data/pulls/ + price history."""
    from collector import collect_store_prices
    from config import get_collector_config

    try:
        pull = collect_store_prices(store_id=store_id, zip_code=zip, force=force)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Price pull failed: {e}") from e

    payload = pull.to_dict()
    cfg = get_collector_config()
    payload["config"] = {
        "proxy_enabled": cfg.proxy_enabled,
        "proxy_count": len(cfg.proxies),
        "engine": cfg.collect_engine,
    }
    return payload


@app.get("/api/item/{item_id}")
def api_item(item_id: str):
    """Single-item price pull (triposat /ip/{id} style)."""
    from http_collector import fetch_item
    from price_history import save_price_history

    result = fetch_item(item_id)
    if result.get("ok") and result.get("product"):
        try:
            save_price_history(store_id=None, products=[result["product"]])
        except Exception:
            pass
    return result


@app.get("/api/history/{product_id}")
def api_history(
    product_id: str,
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from price_history import recent_prices

    rows = recent_prices(product_id=product_id, store_id=store_id, limit=limit)
    return {"product_id": product_id, "store_id": store_id, "count": len(rows), "history": rows}


@app.get("/api/deals/config")
def api_deals_config():
    """Milestone 3 tunable deal thresholds (also set via DEAL_* env vars)."""
    from deal_engine import DealThresholds

    return {
        "thresholds": DealThresholds.from_env().to_dict(),
        "env_keys": [
            "DEAL_MIN_DISCOUNT_PCT",
            "DEAL_HIDDEN_CLEARANCE_PCT",
            "DEAL_CLEARANCE_PCT",
            "DEAL_MARKDOWN_PCT",
            "DEAL_INCLUDE_SHELF",
            "DEAL_MAX_DEALS",
        ],
        "deal_types": [
            "hidden_clearance",
            "clearance",
            "rollback",
            "markdown",
            "special_buy",
            "shelf",
            "minor_drop",
        ],
    }


@app.get("/api/deals")
def api_deals(
    zip: str = Query(..., min_length=3, max_length=10),
    store_id: str = Query(...),
    radius_miles: float = Query(50, ge=1, le=100),
    min_discount_pct: float = Query(20, ge=0, le=95),
    mode: str = Query("live", regex="^(auto|live)$"),
):
    """
    In-store deal report for the selected Walmart store.

    Always prefers live store-scoped collection (no demo / sample catalog).
    Returns markdown / clearance / hidden-clearance deals only (M3).
    """
    prefer_live = True
    try:
        return build_report(
            zip_code=zip,
            store_id=store_id,
            radius_miles=radius_miles,
            min_discount_pct=min_discount_pct,
            prefer_live=prefer_live,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Deal report failed: {e}") from e


@app.get("/api/report")
def api_report(
    zip: str = Query(...),
    store_id: Optional[str] = Query(None),
    radius_miles: float = Query(50, ge=1, le=100),
    min_discount_pct: float = Query(20, ge=0, le=95),
):
    try:
        _loc, stores = find_stores_near_zip(zip, radius_miles=radius_miles, limit=50)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not stores:
        raise HTTPException(status_code=404, detail="No stores found near that ZIP")

    chosen = store_id or stores[0].store_id
    try:
        report = build_report(
            zip_code=zip,
            store_id=chosen,
            radius_miles=radius_miles,
            min_discount_pct=min_discount_pct,
            prefer_live=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    report["nearby_stores"] = [s.to_dict() for s in stores]
    return report


@app.get("/api/scans")
def api_scans(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from scan_store import list_scans

    scans = list_scans(store_id=store_id, limit=limit)
    return {"count": len(scans), "scans": scans}


@app.get("/api/scans/{scan_id}")
def api_scan_detail(scan_id: int):
    from scan_store import get_scan_deals

    try:
        return get_scan_deals(scan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
