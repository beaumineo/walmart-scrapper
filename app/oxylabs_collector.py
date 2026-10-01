"""
Oxylabs Web Scraper API — Walmart search with store localization.

Uses source=walmart_search + delivery_zip + store_id + fulfillment_type=pickup
so results are store-scoped (not national deals hub).

Docs: https://developers.oxylabs.io/api-targets/e-commerce/walmart/search
Env: OXYLABS_USERNAME / OXYLABS_PASSWORD
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Sequence

from config import CollectorConfig, get_collector_config
from walmart_parse import is_likely_instore_product


def oxylabs_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    if not (cfg.oxylabs_username and cfg.oxylabs_password):
        return False
    mode = (os.environ.get("OXYLABS_MODE") or "auto").strip().lower()
    # Trial Web Unblocker accounts do not authenticate on realtime Scraper API.
    if mode in ("unblocker", "web_unblocker"):
        return False
    return True


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stock_fields(raw: Dict[str, Any], general: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Walmart search/product stock + fulfillment signals.

    Important: many category search rows return pickup/delivery/shipping all
    false even when the item is NOT out of stock. Only trust an explicit
    out_of_stock=true flag for OOS — do not infer OOS from empty fulfillment.
    """
    fulfillment = raw.get("fulfillment") if isinstance(raw.get("fulfillment"), dict) else {}

    explicit = general.get("out_of_stock")
    if explicit is None:
        explicit = raw.get("out_of_stock")
    if explicit is None:
        explicit = fulfillment.get("out_of_stock")
    oos = bool(explicit) if explicit is not None else False

    pickup = fulfillment.get("pickup")
    delivery = fulfillment.get("delivery")
    shipping = fulfillment.get("shipping")

    if oos:
        in_stock = False
        availability = "Out of stock"
        stock_status = "Out of stock"
    elif pickup is True:
        in_stock = True
        availability = "In stock · pickup"
        stock_status = "In stock"
    elif delivery is True or shipping is True:
        # Online/marketplace ship — NOT an in-store pickup deal.
        in_stock = True
        availability = "Ship only (not in-store)"
        stock_status = "Ship only"
    else:
        in_stock = False
        availability = "Store stock unconfirmed"
        stock_status = "Unconfirmed"

    return {
        "availability": availability,
        "in_stock": in_stock,
        "out_of_stock": oos,
        "pickup_available": True if pickup is True else False if pickup is False else None,
        "delivery_available": bool(delivery) if delivery is not None else None,
        "shipping_available": bool(shipping) if shipping is not None else None,
        "stock_status": stock_status,
    }


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
        or price_obj.get("price_was")
        or raw.get("price_strikethrough")
    )
    url = general.get("url") or raw.get("url") or ""
    if url and isinstance(url, str) and url.startswith("/"):
        url = "https://www.walmart.com" + url
    image = general.get("image") or raw.get("image")

    is_reduced = bool(was and current and was > current)
    section = str(general.get("section_title") or raw.get("section_title") or "").strip()
    badge = str(general.get("badge") or "").strip().lower()
    offer_type = _infer_offer_type(query=query, section_title=section, badge=badge)
    seller_name = seller.get("name") or raw.get("seller_name")
    is_walmart = bool(seller_name) and "walmart" in str(seller_name).lower()
    stock = _stock_fields(raw, general)
    # NEVER invent in-store from seller alone — that floods every ZIP with the
    # same national Walmart.com markdowns (Frigidaire/Sterilite clones).
    in_store: Optional[bool]
    if stock["pickup_available"] is True:
        in_store = True
    elif stock["pickup_available"] is False:
        in_store = False
    else:
        in_store = None

    item: Dict[str, Any] = {
        "product_id": pid or title[:40],
        "title": title or pid,
        "brand": raw.get("brand"),
        "category": "General",
        "current_price": current,
        "list_price": was,
        "was_price": was,
        "offer_type": offer_type,
        "is_reduced": is_reduced,
        "is_price_event": bool(is_reduced or offer_type),
        "seller_name": seller_name,
        "availability": stock["availability"],
        "availability_code": stock["stock_status"],
        "in_stock": stock["in_stock"],
        "out_of_stock": stock["out_of_stock"],
        "pickup_available": stock["pickup_available"],
        "delivery_available": stock["delivery_available"],
        "shipping_available": stock["shipping_available"],
        "stock_status": stock["stock_status"],
        "in_store": in_store,
        "online": True,
        "url": url or None,
        "image_url": image,
        "query": query,
        "section_title": section or None,
        "store_id": str(store_id),
        "collection_source": "oxylabs_walmart_search",
        "is_walmart_seller": is_walmart,
    }
    return item


def _infer_offer_type(*, query: str, section_title: str, badge: str) -> Optional[str]:
    blob = f"{query} {section_title} {badge}".lower()
    if "rollback" in blob:
        return "rollback"
    if "clearance" in blob:
        return "clearance"
    if "special buy" in blob or "specialbuy" in blob:
        return "specialbuy"
    if "reduced" in blob or "markdown" in blob:
        return "reducedprice"
    return None


# Store-scoped sweep: lead with queries that return pickup=true + was/list,
# then category clearance (kept only when pickup is confirmed at this store).
DEFAULT_QUERIES = (
    "clearance",
    "rollback",
    "hidden clearance",
    '"clearance"',
    "clearance 50%",
    "clearance 70%",
    "markdown",
    "special buy",
    "clearance kitchen",
    "clearance toys",
    "clearance electronics",
    "clearance home",
    "clearance furniture",
    "clearance tools",
    "rollback electronics",
    "clearance outdoor",
    "clearance baby",
    "clearance apparel",
    "clearance grocery",
    "clearance sports",
    "clearance appliances",
    "rollback home",
    "clearance patio",
    "reduced price",
    "clearance cleaning",
)

# Extra pages for high-yield queries (Oxylabs start_page).
DEEP_PAGE_QUERIES = {
    "clearance",
    "rollback",
    "hidden clearance",
    "clearance kitchen",
    "clearance toys",
    "clearance electronics",
    "clearance home",
    "clearance furniture",
    "clearance tools",
    "rollback electronics",
    "clearance 50%",
    "clearance 70%",
}
# Second page for solid category queries (budget-friendly).
MID_PAGE_QUERIES = {
    '"clearance"',
    "markdown",
    "special buy",
    "clearance outdoor",
    "clearance baby",
    "clearance apparel",
    "clearance grocery",
    "clearance sports",
    "clearance appliances",
    "rollback home",
    "clearance patio",
    "clearance cleaning",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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
    start_page: int = 1,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not oxylabs_enabled(cfg):
        raise RuntimeError("Oxylabs not configured")

    import requests

    postal = "".join(c for c in str(postal_code or "") if c.isdigit()).zfill(5)[:5]
    page = max(1, int(start_page or 1))
    payload: Dict[str, Any] = {
        "source": "walmart_search",
        "query": query,
        "parse": True,
        "domain": "com",
        "store_id": str(store_id),
        "fulfillment_type": "pickup",
        "start_page": page,
    }
    if len(postal) == 5:
        payload["delivery_zip"] = postal

    timeout = max(45, min(90, int(cfg.timeout_sec) + 35))
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
        "start_page": page,
    }


def fetch_walmart_product(
    product_id: str,
    store_id: str,
    postal_code: Optional[str] = None,
    cfg: Optional[CollectorConfig] = None,
) -> Dict[str, Any]:
    """Store-scoped product lookup — reliable pickup flag (search often lies)."""
    cfg = cfg or get_collector_config()
    if not oxylabs_enabled(cfg):
        raise RuntimeError("Oxylabs not configured")

    import requests

    postal = "".join(c for c in str(postal_code or "") if c.isdigit()).zfill(5)[:5]
    payload: Dict[str, Any] = {
        "source": "walmart_product",
        "product_id": str(product_id),
        "parse": True,
        "domain": "com",
        "store_id": str(store_id),
        "fulfillment_type": "pickup",
    }
    if len(postal) == 5:
        payload["delivery_zip"] = postal

    timeout = max(45, min(90, int(cfg.timeout_sec) + 35))
    resp = requests.post(
        "https://realtime.oxylabs.io/v1/queries",
        auth=(cfg.oxylabs_username, cfg.oxylabs_password),
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 401:
        raise RuntimeError("Oxylabs auth failed (check OXYLABS_USERNAME/PASSWORD)")
    if resp.status_code >= 400:
        raise RuntimeError(f"Oxylabs product http={resp.status_code}: {resp.text[:200]}")

    data = resp.json() if resp.content else {}
    results = data.get("results") if isinstance(data, dict) else None
    content = None
    if isinstance(results, list) and results and isinstance(results[0], dict):
        content = results[0].get("content")
    if not isinstance(content, dict):
        return {"ok": False, "product_id": str(product_id)}

    general = content.get("general") if isinstance(content.get("general"), dict) else {}
    price_obj = content.get("price") if isinstance(content.get("price"), dict) else {}
    fulfillment = (
        content.get("fulfillment") if isinstance(content.get("fulfillment"), dict) else {}
    )
    seller = content.get("seller") if isinstance(content.get("seller"), dict) else {}
    location = content.get("location") if isinstance(content.get("location"), dict) else {}

    pickup = fulfillment.get("pickup")
    oos = general.get("out_of_stock")
    if oos is None:
        oos = fulfillment.get("out_of_stock")

    return {
        "ok": True,
        "product_id": str(product_id),
        "current_price": _num(price_obj.get("price")),
        "was_price": _num(price_obj.get("price_strikethrough") or price_obj.get("was_price")),
        "pickup_available": True if pickup is True else False if pickup is False else None,
        "delivery_available": fulfillment.get("delivery"),
        "shipping_available": fulfillment.get("shipping"),
        "out_of_stock": bool(oos) if oos is not None else False,
        "seller_name": seller.get("name"),
        "store_id": str(location.get("store_id") or store_id),
    }


def enrich_products_store_pickup(
    products: List[Dict[str, Any]],
    store_id: str,
    postal_code: Optional[str],
    cfg: CollectorConfig,
    *,
    max_enrich: int = 40,
    workers: int = 6,
) -> tuple:
    """Verify pickup at THIS store via walmart_product for unverified markdowns.

    Search fulfillment.pickup is often false even when the store has the item.
    Product lookups are store-accurate and make lists diverge by ZIP.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _markdown_pct(p: Dict[str, Any]) -> float:
        cur = _num(p.get("current_price"))
        was = _num(p.get("was_price") or p.get("list_price"))
        if not cur or not was or was <= cur:
            return -1.0
        return (was - cur) / was * 100.0

    need: List[Dict[str, Any]] = []
    for p in products:
        if p.get("pickup_available") is True:
            continue
        if _markdown_pct(p) < 10:
            continue
        seller = str(p.get("seller_name") or "").lower()
        if seller and "walmart" not in seller:
            continue
        need.append(p)

    need.sort(key=_markdown_pct, reverse=True)
    need = need[: max(0, int(max_enrich))]
    if not need:
        return 0, 0

    updated = 0
    calls = 0

    def _one(p: Dict[str, Any]) -> Dict[str, Any]:
        pid = str(p.get("product_id") or "")
        try:
            info = fetch_walmart_product(pid, store_id, postal_code, cfg)
            return {"pid": pid, "info": info, "error": None}
        except Exception as e:
            return {"pid": pid, "info": None, "error": f"{type(e).__name__}: {e}"}

    by_id = {str(p.get("product_id")): p for p in products}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(need)))) as pool:
        futs = [pool.submit(_one, p) for p in need]
        for fut in as_completed(futs):
            calls += 1
            hit = fut.result()
            info = hit.get("info") or {}
            p = by_id.get(str(hit.get("pid")))
            if not p or not info.get("ok"):
                # Could not verify → leave unverified (deal_engine will drop).
                continue
            pickup = info.get("pickup_available")
            p["pickup_available"] = pickup
            p["delivery_available"] = info.get("delivery_available")
            p["shipping_available"] = info.get("shipping_available")
            p["out_of_stock"] = bool(info.get("out_of_stock"))
            if info.get("seller_name"):
                p["seller_name"] = info.get("seller_name")
            if info.get("current_price"):
                p["current_price"] = info.get("current_price")
            if info.get("was_price"):
                p["was_price"] = info.get("was_price")
                p["list_price"] = info.get("was_price")
            if pickup is True and not p.get("out_of_stock"):
                p["in_store"] = True
                p["in_stock"] = True
                p["availability"] = "In stock · pickup"
                p["stock_status"] = "In stock"
            elif pickup is False:
                p["in_store"] = False
                p["availability"] = "Ship only (not in-store)"
                p["stock_status"] = "Ship only"
            else:
                p["in_store"] = False
                p["availability"] = "Store stock unconfirmed"
                p["stock_status"] = "Unconfirmed"
            p["collection_source"] = "oxylabs_walmart_product"
            updated += 1

    return updated, calls


def _build_query_queue(queries: Optional[Sequence[str]], cfg: CollectorConfig) -> List[str]:
    """Prefer high-yield DEFAULT_QUERIES unless WALMART_QUERIES is set explicitly."""
    env_queries = os.environ.get("WALMART_QUERIES", "").strip()
    if queries:
        primary = list(queries)
    elif env_queries:
        primary = list(cfg.queries or [])
    else:
        # Ignore stale low-yield CollectorConfig defaults (clearance/rollback first).
        primary = list(DEFAULT_QUERIES)

    queued: List[str] = []
    seen = set()
    for q in list(primary) + list(DEFAULT_QUERIES):
        key = str(q or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        queued.append(str(q).strip())
    max_q = max(18, _env_int("WALMART_MAX_QUERIES", 24))
    return queued[:max_q]


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

    from concurrent.futures import ThreadPoolExecutor, as_completed

    queued = _build_query_queue(queries, cfg)
    pages_deep = max(1, min(4, _env_int("WALMART_PAGES_PER_QUERY", 2)))
    max_api_calls = max(20, _env_int("WALMART_MAX_API_CALLS", 28))
    max_enrich = max(0, _env_int("WALMART_ENRICH_MAX", 40))
    workers = max(1, min(12, _env_int("WALMART_PARALLEL_WORKERS", 6)))

    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0

    def _with_was_count() -> int:
        n = 0
        for p in all_products:
            cur = p.get("current_price")
            was = p.get("was_price") or p.get("list_price")
            if cur and was and float(was) > float(cur):
                n += 1
        return n

    def _ingest(batch: List[Dict[str, Any]]) -> int:
        added = 0
        for p in batch:
            pid = p.get("product_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            all_products.append(p)
            added += 1
        return added

    def _pages_for(q: str) -> int:
        q_l = q.lower()
        if q_l in DEEP_PAGE_QUERIES:
            return pages_deep
        if q_l in MID_PAGE_QUERIES:
            return min(2, pages_deep)
        return 1

    def _run_one(q: str, page: int) -> Dict[str, Any]:
        try:
            result = fetch_walmart_search(
                q,
                store_id=str(store_id),
                postal_code=postal_code,
                cfg=cfg,
                start_page=page,
            )
            return {
                "ok": True,
                "query": q,
                "page": page,
                "result": result,
                "error": None,
            }
        except Exception as e:
            return {
                "ok": False,
                "query": q,
                "page": page,
                "result": None,
                "error": f"{type(e).__name__}: {e}",
                "auth_failed": "auth failed" in str(e).lower() or "401" in str(e),
            }

    def _run_batch(jobs: List[tuple]) -> List[Dict[str, Any]]:
        """Run (query, page) jobs in parallel; preserve completion notes."""
        nonlocal attempts
        if not jobs:
            return []
        remain = max_api_calls - attempts
        if remain <= 0:
            return []
        jobs = jobs[:remain]
        attempts += len(jobs)
        out: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            futs = [pool.submit(_run_one, q, page) for q, page in jobs]
            for fut in as_completed(futs):
                out.append(fut.result())
        return out

    def _apply_hits(hits: List[Dict[str, Any]]) -> Dict[str, bool]:
        """Ingest hits; return map of query -> had_products on page 1."""
        page1_ok: Dict[str, bool] = {}
        auth_fail_count = 0
        hits_sorted = sorted(
            hits, key=lambda h: (str(h.get("query") or ""), int(h.get("page") or 1))
        )
        for hit in hits_sorted:
            q = str(hit.get("query") or "")
            page = int(hit.get("page") or 1)
            if not hit.get("ok"):
                if hit.get("auth_failed"):
                    auth_fail_count += 1
                else:
                    notes.append(
                        f"oxylabs error query={q} page={page}: {hit.get('error')}"
                    )
                if page == 1:
                    page1_ok[q] = False
                continue
            result = hit.get("result") or {}
            batch = result.get("products") or []
            loc = result.get("location") or {}
            loc_bit = ""
            if isinstance(loc, dict) and loc:
                loc_bit = (
                    f" loc_store={loc.get('store_id')} "
                    f"loc_zip={loc.get('zip_code') or loc.get('zipcode')}"
                )
            added = _ingest(batch)
            if batch:
                if page == 1:
                    page1_ok[q] = True
                notes.append(
                    f"oxylabs ok store={store_id} zip={postal_code or ''} "
                    f"query={q} page={page} n={len(batch)} added={added} "
                    f"raw={result.get('raw_count')}{loc_bit}"
                )
            else:
                if page == 1:
                    page1_ok[q] = False
                notes.append(
                    f"oxylabs empty store={store_id} query={q} page={page} "
                    f"raw={result.get('raw_count')}"
                )
        if auth_fail_count:
            notes.append(
                "oxylabs_auth_failed: OXYLABS_USERNAME/PASSWORD rejected (HTTP 401). "
                "Update credentials in Railway Variables and redeploy."
            )
            page1_ok.clear()
        return page1_ok

    t0 = time.time()
    # Wave 1: every query page 1 in parallel (fast first paint of coverage).
    wave1 = [(q, 1) for q in queued]
    page1_hits = _run_batch(wave1)
    # Fail fast: if the first wave is all auth failures, do not burn more credits.
    auth_fails = sum(1 for h in page1_hits if h.get("auth_failed"))
    if page1_hits and auth_fails == len(page1_hits):
        notes.append(
            "oxylabs_auth_failed: OXYLABS_USERNAME/PASSWORD rejected (HTTP 401). "
            "Update credentials in Railway Variables and redeploy."
        )
        notes.append(
            f"priced_markdown_candidates=0 unique_products=0 api_calls={attempts} "
            f"parallel_workers={workers} elapsed_sec={round(time.time() - t0, 1)}"
        )
        return {
            "ok": False,
            "mode": "auth_failed",
            "products": [],
            "notes": "; ".join(n for n in notes if n),
            "proxy_used": True,
            "attempts": attempts or 1,
            "engine": "oxylabs",
        }

    page1_ok = _apply_hits(page1_hits)

    # Wave 2: extra pages only for queries that returned something on page 1.
    wave2: List[tuple] = []
    for q in queued:
        pages_for_q = _pages_for(q)
        if pages_for_q <= 1:
            continue
        if not page1_ok.get(q):
            continue
        for page in range(2, pages_for_q + 1):
            wave2.append((q, page))
    if attempts < max_api_calls and wave2:
        _apply_hits(_run_batch(wave2))
    elif wave2 and attempts >= max_api_calls:
        notes.append(f"api_budget_stop max_api_calls={max_api_calls}")

    # Wave 3: product-level pickup verify — search flags lie; this makes stores diverge.
    enrich_updated = 0
    enrich_calls = 0
    if all_products and max_enrich > 0:
        enrich_updated, enrich_calls = enrich_products_store_pickup(
            all_products,
            store_id=str(store_id),
            postal_code=postal_code,
            cfg=cfg,
            max_enrich=max_enrich,
            workers=workers,
        )
        attempts += enrich_calls
        notes.append(
            f"store_pickup_enrich updated={enrich_updated} checked={enrich_calls} "
            f"budget={max_enrich}"
        )

    before = len(all_products)
    # Only keep items confirmed for pickup at THIS store after enrich.
    all_products = [
        p for p in all_products if p.get("pickup_available") is True and not p.get("out_of_stock")
    ]
    notes.append(
        f"pickup_confirmed={len(all_products)} dropped_unconfirmed={before - len(all_products)}"
    )

    elapsed = round(time.time() - t0, 1)
    notes.append(
        f"priced_markdown_candidates={_with_was_count()} "
        f"unique_products={len(all_products)} api_calls={attempts} "
        f"parallel_workers={workers} elapsed_sec={elapsed}"
    )

    return {
        "ok": bool(all_products),
        "mode": "live" if all_products else "empty",
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": True,
        "attempts": attempts or 1,
        "engine": "oxylabs",
    }
