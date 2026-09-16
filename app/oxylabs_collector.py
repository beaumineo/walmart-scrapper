"""
Oxylabs Web Scraper API — Walmart search with store localization.

Uses source=walmart_search + delivery_zip + store_id + fulfillment_type=pickup
so results are store-scoped (not national deals hub).

Docs: https://developers.oxylabs.io/api-targets/e-commerce/walmart/search
Env: OXYLABS_USERNAME / OXYLABS_PASSWORD
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from config import CollectorConfig, get_collector_config
from walmart_parse import is_likely_instore_product


def oxylabs_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    return bool(cfg.oxylabs_username and cfg.oxylabs_password)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _map_item(raw: Dict[str, Any], query: str, store_id: str) -> Optional[Dict[str, Any]]:
    general = raw.get("general") if isinstance(raw.get("general"), dict) else {}
    price_obj = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    seller = raw.get("seller") if isinstance(raw.get("seller"), dict) else {}

    pid = str(
        general.get("product_id")
        or raw.get("product_id")
        or raw.get("us_item_id")
        or ""
    ).strip()
    title = str(general.get("title") or raw.get("title") or "").strip()
    if not pid and not title:
        return None

    current = _num(price_obj.get("price") or raw.get("price"))
    was = _num(
        price_obj.get("price_strikethrough")
        or price_obj.get("was_price")
        or raw.get("price_strikethrough")
    )
    url = general.get("url") or raw.get("url") or ""
    if url and isinstance(url, str) and url.startswith("/"):
        url = "https://www.walmart.com" + url
    image = general.get("image") or raw.get("image")

    out_of_stock = bool(general.get("out_of_stock") or raw.get("out_of_stock"))
    is_reduced = bool(was and current and was > current)

    item: Dict[str, Any] = {
        "product_id": pid or title[:40],
        "title": title or pid,
        "brand": raw.get("brand"),
        "category": "General",
        "current_price": current,
        "list_price": was,
        "was_price": was,
        "offer_type": None,
        "is_reduced": is_reduced,
        "is_price_event": is_reduced,
        "seller_name": seller.get("name") or raw.get("seller_name"),
        "availability": "Out of stock" if out_of_stock else "In stock",
        "in_store": True,
        "online": True,
        "url": url or None,
        "image_url": image,
        "query": query,
        "store_id": str(store_id),
        "collection_source": "oxylabs_walmart_search",
    }
    return item


def _extract_results(payload: Dict[str, Any]) -> tuple:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return [], {}
    content = results[0].get("content") if isinstance(results[0], dict) else None
    if not isinstance(content, dict):
        return [], {}
    rows = content.get("results") or content.get("organic") or []
    location = content.get("location") if isinstance(content.get("location"), dict) else {}
    return [r for r in rows if isinstance(r, dict)], location


def fetch_walmart_search(
    query: str,
    store_id: str,
    postal_code: Optional[str] = None,
    cfg: Optional[CollectorConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not oxylabs_enabled(cfg):
        raise RuntimeError("Oxylabs not configured")

    import requests

    postal = "".join(c for c in str(postal_code or "") if c.isdigit()).zfill(5)[:5]
    payload: Dict[str, Any] = {
        "source": "walmart_search",
        "query": query,
        "parse": True,
        "domain": "com",
        "store_id": str(store_id),
        "fulfillment_type": "pickup",
    }
    if len(postal) == 5:
        payload["delivery_zip"] = postal

    timeout = max(60, int(cfg.timeout_sec) + 30)
    resp = requests.post(
        "https://realtime.oxylabs.io/v1/queries",
        auth=(cfg.oxylabs_username, cfg.oxylabs_password),
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 401:
        raise RuntimeError("Oxylabs auth failed (check OXYLABS_USERNAME/PASSWORD)")
    if resp.status_code == 429:
        raise RuntimeError("Oxylabs rate limited")
    if resp.status_code >= 400:
        raise RuntimeError(f"Oxylabs http={resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    rows, location = _extract_results(data if isinstance(data, dict) else {})
    loc_store = str(location.get("store_id") or "").strip()
    if loc_store and loc_store != str(store_id):
        raise RuntimeError(
            f"Oxylabs location store_id={loc_store} != requested {store_id}"
        )

    products: List[Dict[str, Any]] = []
    for raw in rows[: cfg.max_per_query]:
        mapped = _map_item(raw, query=query, store_id=store_id)
        if not mapped:
            continue
        if not is_likely_instore_product(mapped):
            continue
        products.append(mapped)

    job_status = None
    if isinstance(data, dict) and data.get("results"):
        job_status = (data["results"][0] or {}).get("status_code")

    return {
        "ok": bool(products),
        "products": products,
        "status_code": job_status or resp.status_code,
        "raw_count": len(rows),
        "location": location,
    }


def collect_store_via_oxylabs(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not oxylabs_enabled(cfg):
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "oxylabs not configured (set OXYLABS_USERNAME + OXYLABS_PASSWORD)",
            "proxy_used": False,
            "attempts": 0,
            "engine": "oxylabs",
        }

    queries = queries or list(cfg.queries)
    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0

    for q in queries:
        attempts += 1
        try:
            time.sleep(max(0.5, cfg.min_delay_sec * 0.3))
            result = fetch_walmart_search(
                q, store_id=str(store_id), postal_code=postal_code, cfg=cfg
            )
            batch = result.get("products") or []
            loc = result.get("location") or {}
            loc_bit = ""
            if isinstance(loc, dict) and loc:
                loc_bit = (
                    f" loc_store={loc.get('store_id')} "
                    f"loc_zip={loc.get('zip_code')}"
                )
            if batch:
                notes.append(
                    f"oxylabs ok store={store_id} zip={postal_code or ''} "
                    f"query={q} n={len(batch)} raw={result.get('raw_count')}{loc_bit}"
                )
                for p in batch:
                    pid = p.get("product_id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    all_products.append(p)
            else:
                notes.append(
                    f"oxylabs empty store={store_id} query={q} "
                    f"raw={result.get('raw_count')}"
                )
        except Exception as e:
            notes.append(f"oxylabs error query={q}: {type(e).__name__}: {e}")

    return {
        "ok": bool(all_products),
        "mode": "live" if all_products else "empty",
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": True,
        "attempts": attempts or 1,
        "engine": "oxylabs",
    }
