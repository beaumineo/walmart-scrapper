from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure `app/` is on sys.path (Vercel loads app/main.py from repo root)
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api_guard import enforce_rate_limit, require_api_key
from walmart_core import build_report, find_stores_near_zip, load_zips

app = FastAPI(
    title="Walmart In-Store Deal Finder (Hidden Clearances)",
    description=(
        "Integration API for Hidden Clearances.\n\n"
        "**Flow:** `GET /api/stores?zip=` → pick a store → "
        "`GET /api/deals?zip=&store_id=&min_discount_pct=`.\n\n"
        "Live pulls are store-scoped (Oxylabs). Docs: `/docs`, `docs/API.md`, `docs/HANDOFF.md`."
    ),
    version="1.5.0",
    contact={"name": "Hidden Clearances Walmart module"},
)

_cors = [
    o.strip()
    for o in (os.environ.get("CORS_ALLOW_ORIGINS") or "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors or ["*"],
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
    # Milestone 6 — always-on refresh (no-op unless INVENTORY_SCHEDULER_ENABLED=1)
    try:
        from inventory_scheduler import start_scheduler

        start_scheduler()
    except Exception:
        pass


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["ops"])
def health():
    from config import get_collector_config
    from client_collector import client_backends_status, client_live_ready
    from api_guard import configured_api_key

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
    backends = client_backends_status(cfg)
    return {
        "ok": True,
        "zip_count": len(zips),
        "official_store_count": store_count,
        "street_geocode_count": geo_ok,
        "proxy_enabled": cfg.proxy_enabled,
        "version": "1.5.0",
        "milestone": 5,
        "phase2_milestone": 6,
        "milestones_complete": [0, 1, 2, 3, 4, 5],
        "proxy_count": len(cfg.proxies),
        "engine": cfg.collect_engine,
        "live_ready": client_live_ready(cfg),
        "backends": backends,
        "deal_thresholds": deal_thr,
        "auth_required": bool(configured_api_key()),
        "deploy": {
            "vercel": bool(os.environ.get("VERCEL")),
            "railway": bool(
                os.environ.get("RAILWAY_ENVIRONMENT")
                or os.environ.get("RAILWAY_PROJECT_ID")
            ),
            "collect_inline": (
                os.environ.get("WALMART_COLLECT_INLINE", "") == "1"
                or bool(
                    os.environ.get("RAILWAY_ENVIRONMENT")
                    or os.environ.get("RAILWAY_PROJECT_ID")
                )
            ),
            "build": "1.5.0-phase2-m6-always-on",
        },
    }


@app.get("/api/backends", tags=["ops"], dependencies=[Depends(require_api_key)])
def api_backends():
    """Which live collectors are configured (no secrets returned)."""
    from client_collector import client_backends_status, client_live_ready, setup_required_message

    status = client_backends_status()
    hosted = bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("VERCEL")
    )
    return {
        "ready": client_live_ready(),
        "backends": status,
        "setup_message": None if client_live_ready() else setup_required_message(),
        "preferred": ["oxylabs", "scraperapi", "unlocker"],
        "hosted": hosted,
        "allow_browser_config": not hosted,
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


@app.post("/api/backends/configure", tags=["ops"], dependencies=[Depends(require_api_key)])
def api_backends_configure(body: LiveBackendConfig):
    """
    Save a commercial live backend into .env (local/dev only).
    On Railway, set Variables in the dashboard instead.
    """
    from config import ENV_PATH
    from client_collector import client_backends_status, client_live_ready

    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
        raise HTTPException(
            status_code=400,
            detail=(
                "This host is Railway. Set OXYLABS_USERNAME and OXYLABS_PASSWORD "
                "in the Variables tab (not in the browser), then redeploy."
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

    try:
        _upsert_env(ENV_PATH, updates)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not write .env: {e}") from e

    # Apply immediately in this process
    for k, v in updates.items():
        os.environ[k] = v

    return {
        "ok": True,
        "saved_keys": sorted(updates.keys()),
        "ready": client_live_ready(),
        "backends": client_backends_status(),
    }


@app.get(
    "/api/stores",
    tags=["integration"],
    dependencies=[Depends(require_api_key)],
    summary="ZIP → nearby Walmart stores",
)
def api_stores(
    request: Request,
    response: Response,
    zip: str = Query(..., min_length=3, max_length=10, description="US ZIP code"),
    radius_miles: float = Query(50, ge=1, le=100),
    limit: int = Query(100, ge=1, le=200),
):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
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


@app.get("/api/prices", tags=["collector"], dependencies=[Depends(require_api_key)])
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


@app.get("/api/item/{item_id}", tags=["collector"], dependencies=[Depends(require_api_key)])
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


@app.get("/api/history/{product_id}", tags=["collector"], dependencies=[Depends(require_api_key)])
def api_history(
    product_id: str,
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from price_history import recent_prices

    rows = recent_prices(product_id=product_id, store_id=store_id, limit=limit)
    return {"product_id": product_id, "store_id": store_id, "count": len(rows), "history": rows}


@app.get("/api/deals/config", tags=["integration"], dependencies=[Depends(require_api_key)])
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


@app.get(
    "/api/deals",
    tags=["integration"],
    dependencies=[Depends(require_api_key)],
    summary="Store → ranked in-store deals report",
)
def api_deals(
    request: Request,
    response: Response,
    zip: str = Query(..., min_length=3, max_length=10),
    store_id: str = Query(...),
    radius_miles: float = Query(50, ge=1, le=100),
    min_discount_pct: float = Query(20, ge=0, le=95),
    mode: str = Query(
        "auto",
        pattern="^(auto|live|inventory)$",
        description="auto=inventory DB when scanned, else live; inventory=DB only; live=Oxylabs pull",
    ),
    refresh: int = Query(
        0,
        ge=0,
        le=1,
        description="1 = queue priority inventory recheck (fast path) or force live pull",
    ),
):
    """
    In-store deal report for the selected Walmart store.

    Milestone 5 fast path: `mode=auto|inventory` serves deals from the inventory DB
    (pickup-confirmed + Walmart seller). `refresh=1` queues a background recheck and
    still returns current DB deals immediately.

    `mode=live` runs a full Oxylabs store sweep (often 30–90s — client timeout ≥ 180s).
    """
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v

    use_inventory = mode in ("auto", "inventory")
    if use_inventory:
        try:
            from inventory_deals import build_inventory_deal_report, store_has_inventory

            if mode == "inventory" or store_has_inventory(store_id):
                return build_inventory_deal_report(
                    zip_code=zip,
                    store_id=store_id,
                    min_discount_pct=min_discount_pct,
                    refresh=bool(refresh),
                    radius_miles=radius_miles,
                )
            if mode == "inventory":
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No inventory for store {store_id}. "
                        "Run POST /api/inventory/scans first."
                    ),
                )
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            if mode == "inventory":
                raise HTTPException(
                    status_code=502, detail=f"Inventory deals failed: {e}"
                ) from e
            # auto: fall through to live

    try:
        return build_report(
            zip_code=zip,
            store_id=store_id,
            radius_miles=radius_miles,
            min_discount_pct=min_discount_pct,
            prefer_live=True,
            force_refresh=bool(refresh),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Deal report failed: {e}") from e


@app.get("/api/report", tags=["integration"], dependencies=[Depends(require_api_key)])
def api_report(
    request: Request,
    response: Response,
    zip: str = Query(...),
    store_id: Optional[str] = Query(None),
    radius_miles: float = Query(50, ge=1, le=100),
    min_discount_pct: float = Query(20, ge=0, le=95),
):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
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


@app.get("/api/scans", tags=["ops"], dependencies=[Depends(require_api_key)])
def api_scans(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from scan_store import list_scans

    scans = list_scans(store_id=store_id, limit=limit)
    return {"count": len(scans), "scans": scans}


@app.get("/api/scans/{scan_id}", tags=["ops"], dependencies=[Depends(require_api_key)])
def api_scan_detail(scan_id: int):
    from scan_store import get_scan_deals

    try:
        return get_scan_deals(scan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# --- Phase 2 / Milestone 3: inventory waves A–D ---


class InventoryScanRequest(BaseModel):
    store_id: str = Field(..., min_length=1, max_length=32)
    zip: Optional[str] = Field(None, min_length=3, max_length=10)
    wave: str = Field(
        "seed",
        max_length=32,
        description="seed|A|B|C|D|full — see PHASE2_ROADMAP Milestone 3",
    )
    max_queries: Optional[int] = Field(None, ge=1, le=80)
    pages_per_query: Optional[int] = Field(None, ge=1, le=4)
    recheck_limit: Optional[int] = Field(
        None, ge=0, le=200, description="Wave D / full: max SKUs to recheck via product API"
    )
    background: bool = True


@app.post(
    "/api/inventory/scans",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Start an inventory scan (seed / wave A–D / full)",
)
def api_inventory_scan_start(body: InventoryScanRequest, request: Request, response: Response):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
    from inventory_scan import start_inventory_scan
    from walmart_core import find_stores_near_zip

    store_meta = None
    zip_code = body.zip
    if zip_code:
        try:
            _loc, stores = find_stores_near_zip(zip_code, radius_miles=50, limit=50)
            match = next((s for s in stores if str(s.store_id) == str(body.store_id)), None)
            if match:
                store_meta = match.to_dict() if hasattr(match, "to_dict") else dict(match.__dict__)
                zip_code = store_meta.get("zip") or zip_code
        except Exception:
            store_meta = {"store_id": body.store_id, "zip": zip_code}

    try:
        result = start_inventory_scan(
            store_id=body.store_id,
            zip_code=zip_code,
            wave=body.wave or "seed",
            store_meta=store_meta,
            max_queries=body.max_queries,
            pages_per_query=body.pages_per_query,
            recheck_limit=body.recheck_limit,
            background=body.background,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Inventory scan failed: {e}") from e

    if not result.get("ok") and result.get("error") == "scan_already_running":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "scan_already_running",
                "scan": result.get("scan"),
            },
        )
    return result


@app.get(
    "/api/inventory/scans",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="List inventory scan runs",
)
def api_inventory_scans(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from inventory_scan import list_inventory_scans

    scans = list_inventory_scans(store_id=store_id, limit=limit)
    return {"count": len(scans), "scans": scans}


@app.get(
    "/api/inventory/scans/{scan_id}",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Inventory scan status (progress, SKU count, last update)",
)
def api_inventory_scan_status(scan_id: int):
    from inventory_scan import get_scan_status

    try:
        return get_scan_status(scan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get(
    "/api/inventory/stores/{store_id}",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Store inventory summary (SKU count, last successful scan)",
)
def api_inventory_store_status(store_id: str):
    from inventory_scan import get_store_inventory_status

    return get_store_inventory_status(store_id)


@app.get(
    "/api/inventory/stores/{store_id}/coverage",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="SKU coverage report (Milestone 3 deliverable)",
)
def api_inventory_coverage(store_id: str):
    from inventory_scan import get_coverage_report

    return get_coverage_report(store_id)


@app.get(
    "/api/inventory/stores/{store_id}/deals",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Deals from inventory DB (pickup + Walmart-seller only)",
)
def api_inventory_deals(
    store_id: str,
    min_discount_pct: float = Query(20, ge=0, le=95),
    limit: int = Query(200, ge=1, le=2000),
):
    from inventory_deals import deals_from_inventory

    result = deals_from_inventory(store_id, min_discount_pct=min_discount_pct)
    deals = result.get("deals") or []
    result["deals"] = deals[:limit]
    result["returned"] = len(result["deals"])
    return result


class InventoryVerifyRequest(BaseModel):
    store_id: str = Field(..., min_length=1, max_length=32)
    zip: Optional[str] = Field(None, min_length=3, max_length=10)
    max_verify: Optional[int] = Field(40, ge=1, le=200)


@app.post(
    "/api/inventory/verify",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Product-level pickup verify for inventory markdowns (M4)",
)
def api_inventory_verify(body: InventoryVerifyRequest, request: Request, response: Response):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
    from inventory_deals import verify_store_pickup

    try:
        return verify_store_pickup(
            body.store_id,
            zip_code=body.zip,
            max_verify=body.max_verify,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Pickup verify failed: {e}") from e


@app.get(
    "/api/inventory/overlap",
    tags=["phase2"],
    dependencies=[Depends(require_api_key)],
    summary="Store A vs B overlap metrics (anti-clone)",
)
def api_inventory_overlap(
    store_a: str = Query(...),
    store_b: str = Query(...),
    min_discount_pct: float = Query(20, ge=0, le=95),
):
    from inventory_deals import compare_store_overlap

    return compare_store_overlap(
        store_a, store_b, min_discount_pct=min_discount_pct
    )


@app.get(
    "/api/ops",
    tags=["ops"],
    dependencies=[Depends(require_api_key)],
    summary="Phase 2 ops dashboard (scan age, deals, API burn, scheduler)",
)
def api_ops_status():
    from ops_status import build_ops_status

    return build_ops_status()


@app.post(
    "/api/ops/scheduler/tick",
    tags=["ops"],
    dependencies=[Depends(require_api_key)],
    summary="Run one scheduler tick now (full/hot due waves)",
)
def api_ops_scheduler_tick(request: Request, response: Response):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
    from inventory_scheduler import run_scheduler_once

    return {"ok": True, "scheduler": run_scheduler_once()}


@app.post(
    "/api/ops/deep-alerts/check",
    tags=["ops"],
    dependencies=[Depends(require_api_key)],
    summary="Scan watched stores for ≥70% markdown and Discord-alert new ones",
)
def api_ops_deep_alerts(request: Request, response: Response):
    for k, v in enforce_rate_limit(request).items():
        response.headers[k] = v
    from deep_alerts import check_and_alert_deep_markdowns

    return check_and_alert_deep_markdowns()
