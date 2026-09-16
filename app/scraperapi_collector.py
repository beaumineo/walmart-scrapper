"""
ScraperAPI Walmart collector.

Two modes:
1) URL + ultra_premium/premium — fetch store-bound search HTML, parse locally
   (preserves was/list prices for deals).
2) Structured /structured/walmart/search — JSON items fallback (often no was_price).

Docs: https://docs.scraperapi.com/structured-data-endpoints/e-commerce/walmart/
Env: SCRAPERAPI_KEY
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from config import CollectorConfig, get_collector_config
from http_collector import build_store_cookie_header, _candidate_urls
from walmart_parse import (
    is_challenge_html,
    is_likely_instore_product,
    parse_products_from_html,
)


def scraperapi_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    return bool(cfg.scraperapi_key)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_url_html(
    url: str,
    cfg: CollectorConfig,
    cookie_header: Optional[str] = None,
) -> Dict[str, Any]:
    import requests

    params: Dict[str, Any] = {
        "api_key": cfg.scraperapi_key,
        "url": url,
        "country_code": "us",
    }
    if cfg.scraperapi_ultra:
        params["ultra_premium"] = "true"
    else:
        params["premium"] = "true"
        params["render"] = "true"

    headers = {}
    if cookie_header:
        headers["Cookie"] = cookie_header
        params["keep_headers"] = "true"

    timeout = max(90, int(cfg.timeout_sec) + 60)
    resp = requests.get(
        "https://api.scraperapi.com/",
        params=params,
        headers=headers or None,
        timeout=timeout,
    )
    html = resp.text or ""
    if resp.status_code == 401:
        raise RuntimeError("ScraperAPI auth failed (check SCRAPERAPI_KEY)")
    if resp.status_code == 429:
        raise RuntimeError("ScraperAPI rate limited / out of credits")
    if resp.status_code >= 400:
        raise RuntimeError(f"ScraperAPI http={resp.status_code}: {html[:240]}")
    if is_challenge_html(html, url):
        raise RuntimeError("ScraperAPI still returned Walmart challenge HTML")
    return {"ok": True, "html": html, "status": resp.status_code}


def _fetch_structured_search(query: str, cfg: CollectorConfig) -> List[Dict[str, Any]]:
    import requests

    params = {
        "api_key": cfg.scraperapi_key,
        "query": query,
        "country_code": "us",
        "tld": "com",
        "page": "1",
    }
    timeout = max(60, int(cfg.timeout_sec) + 30)
    resp = requests.get(
        "https://api.scraperapi.com/structured/walmart/search",
        params=params,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"ScraperAPI structured http={resp.status_code}: {resp.text[:240]}")
    data = resp.json()
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items[: cfg.max_per_query]:
        if not isinstance(it, dict):
            continue
        price = _num(it.get("price"))
        url = it.get("url") or ""
        pid = str(it.get("id") or "")
        if not pid and isinstance(url, str) and "/ip/" in url:
            pid = url.rstrip("/").split("/")[-1].split("?")[0]
        title = str(it.get("name") or "").strip()
        if not title:
            continue
        out.append(
            {
                "product_id": pid or title[:40],
                "title": title,
                "current_price": price,
                "list_price": None,
                "was_price": None,
                "seller_name": it.get("seller"),
                "availability": it.get("availability"),
                "url": url,
                "image_url": it.get("image"),
                "in_store": True,
                "online": True,
                "query": query,
                "collection_source": "scraperapi_structured",
            }
        )
    return out


def collect_store_via_scraperapi(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not scraperapi_enabled(cfg):
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "scraperapi not configured (set SCRAPERAPI_KEY)",
            "proxy_used": False,
            "attempts": 0,
            "engine": "scraperapi",
        }

    queries = queries or list(cfg.queries)
    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0
    cookie = build_store_cookie_header(str(store_id), postal_code)

    for q in queries:
        got = False
        # Prefer store-bound HTML (was_price for deals)
        for url, source in _candidate_urls(str(store_id), q)[:2]:
            attempts += 1
            try:
                time.sleep(max(0.4, cfg.min_delay_sec * 0.25))
                result = _fetch_url_html(url, cfg, cookie_header=cookie)
                products = parse_products_from_html(
                    result["html"], query=q, limit=cfg.max_per_query
                )
                kept: List[Dict[str, Any]] = []
                for p in products:
                    p["store_id"] = str(store_id)
                    p["collection_source"] = f"scraperapi_{source}"
                    if p.get("in_store") is None:
                        p["in_store"] = True
                    if not is_likely_instore_product(p):
                        continue
                    kept.append(p)
                if kept:
                    notes.append(
                        f"scraperapi html ok source={source} store={store_id} "
                        f"query={q} n={len(kept)}"
                    )
                    for p in kept:
                        pid = p.get("product_id")
                        if not pid or pid in seen:
                            continue
                        seen.add(pid)
                        all_products.append(p)
                    got = True
                    break
                notes.append(f"scraperapi html empty source={source} query={q}")
            except Exception as e:
                notes.append(
                    f"scraperapi html error source={source}: {type(e).__name__}: {e}"
                )

        if got:
            continue

        # Do NOT use structured search here — it has no store_id and returns the
        # same national catalog for every store (the exact client-facing bug).
        notes.append(
            f"scraperapi skipped structured fallback for query={q} "
            "(no store pin; would clone across stores)"
        )

    return {
        "ok": bool(all_products),
        "mode": "live" if all_products else "empty",
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": True,
        "attempts": attempts or 1,
        "engine": "scraperapi",
    }
