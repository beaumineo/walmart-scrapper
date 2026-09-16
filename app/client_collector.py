"""
Client-facing Walmart in-store collector (no browser).

Shipped path:
  Oxylabs (store_id + delivery_zip) → ScraperAPI HTML → Bright Data Unlocker

ISP/curl alone is NOT enough for Walmart (Akamai /blocked) and must not be
treated as a ready live backend — that produced long hangs then empty errors.

Env (set ONE):
  OXYLABS_USERNAME + OXYLABS_PASSWORD
  SCRAPERAPI_KEY
  BRIGHTDATA_API_KEY + BRIGHTDATA_UNLOCKER_ZONE
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import CollectorConfig, get_collector_config


def _is_brightdata_isp_only(cfg: CollectorConfig) -> bool:
    """True when the only proxies look like Bright Data ISP (known Walmart-blocked)."""
    if not cfg.proxies:
        return False
    for p in cfg.proxies:
        user = (p.user or "").lower()
        if "brd-customer" in user and "isp" in user:
            return True
        if "brd-customer" in user and "unlocker" not in user and "unblocker" not in user:
            return True
    return False


def client_backends_status(cfg: Optional[CollectorConfig] = None) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    from oxylabs_collector import oxylabs_enabled
    from scraperapi_collector import scraperapi_enabled
    from unlocker_collector import unlocker_enabled

    status = {
        "oxylabs": oxylabs_enabled(cfg),
        "scraperapi": scraperapi_enabled(cfg),
        "unlocker": unlocker_enabled(cfg),
        "curl_proxy": bool(cfg.proxy_enabled),
        "brightdata_isp_blocked": _is_brightdata_isp_only(cfg)
        and not (
            oxylabs_enabled(cfg) or scraperapi_enabled(cfg) or unlocker_enabled(cfg)
        ),
    }
    status["reliable"] = bool(
        status["oxylabs"] or status["scraperapi"] or status["unlocker"]
    )
    return status


def client_live_ready(cfg: Optional[CollectorConfig] = None) -> bool:
    """True only when a Walmart-capable commercial backend is configured."""
    status = client_backends_status(cfg)
    return bool(status["reliable"])


def setup_required_message(store_id: Optional[str] = None) -> str:
    sid = f" store #{store_id}" if store_id else ""
    return (
        f"Cannot load live in-store deals{sid}: Walmart blocks ISP proxies (Akamai). "
        "Add one commercial backend to .env, then retry — "
        "OXYLABS_USERNAME + OXYLABS_PASSWORD (best for store-scoped), "
        "or SCRAPERAPI_KEY, "
        "or BRIGHTDATA_API_KEY + BRIGHTDATA_UNLOCKER_ZONE. "
        "Browser scraping is disabled for the client."
    )


def collect_store_for_client(
    store_id: str,
    *,
    postal_code: Optional[str] = None,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
) -> Dict[str, Any]:
    """
    Collect store-scoped products without a browser.

    Returns the same shape as other collectors:
      {ok, mode, products, notes, proxy_used, attempts, engine}
    """
    cfg = cfg or get_collector_config()
    queries = queries or list(cfg.queries)
    notes: List[str] = []
    status = client_backends_status(cfg)

    if not status["reliable"]:
        return {
            "ok": False,
            "mode": "setup_required",
            "products": [],
            "notes": setup_required_message(store_id),
            "proxy_used": bool(cfg.proxy_enabled),
            "attempts": 0,
            "engine": "client",
        }

    # 1) Oxylabs — only backend with first-class store_id + delivery_zip
    if status["oxylabs"]:
        from oxylabs_collector import collect_store_via_oxylabs

        result = collect_store_via_oxylabs(
            store_id, queries=queries, cfg=cfg, postal_code=postal_code
        )
        if result.get("ok") and result.get("products"):
            return result
        notes.append(result.get("notes") or "oxylabs empty")
    else:
        notes.append("oxylabs skipped (not configured)")

    # 2) ScraperAPI HTML only (structured search has no store pin → national)
    if status["scraperapi"]:
        from scraperapi_collector import collect_store_via_scraperapi

        result = collect_store_via_scraperapi(
            store_id, queries=queries, cfg=cfg, postal_code=postal_code
        )
        if result.get("ok") and result.get("products"):
            return result
        notes.append(result.get("notes") or "scraperapi empty")
    else:
        notes.append("scraperapi skipped (not configured)")

    # 3) Bright Data Web Unlocker
    if status["unlocker"]:
        from unlocker_collector import collect_store_via_unlocker

        result = collect_store_via_unlocker(store_id, queries=queries, cfg=cfg)
        if result.get("ok") and result.get("products"):
            return result
        notes.append(result.get("notes") or "unlocker empty")
    else:
        notes.append("unlocker skipped (not configured)")

    # Optional last resort: curl only if not known-blocked ISP-only
    if status["curl_proxy"] and not status["brightdata_isp_blocked"]:
        from http_collector import collect_store_via_curl

        result = collect_store_via_curl(
            store_id, queries=queries, cfg=cfg, postal_code=postal_code
        )
        if result.get("ok") and result.get("products"):
            return result
        notes.append(result.get("notes") or f"curl mode={result.get('mode')}")
    elif status["brightdata_isp_blocked"]:
        notes.append("curl skipped (Bright Data ISP is Akamai-blocked on Walmart)")

    return {
        "ok": False,
        "mode": "error",
        "products": [],
        "notes": "; ".join(n for n in notes if n)
        + "; commercial backend configured but returned no store-scoped products",
        "proxy_used": bool(cfg.proxy_enabled),
        "attempts": 1,
        "engine": "client",
    }
