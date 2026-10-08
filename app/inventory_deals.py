"""
Phase 2 Milestone 4 — store-accurate deals from inventory + anti-clone metrics.

- Deals only from pickup-confirmed, Walmart-seller inventory rows
- Batch product pickup verify for unverified markdowns
- Store A vs B overlap report (SKU / pickup / deal sets)
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

from deal_engine import DealThresholds, detect_deals
from inventory_db import (
    get_inventory_db,
    get_store,
    inventory_counts,
    prior_prices_map,
    upsert_inventory_items,
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def list_inventory_products(
    store_id: str,
    *,
    pickup_only: bool = False,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Flatten inv_store_inventory (+ sku title) into product dicts for deal_engine."""
    sid = str(store_id)
    lim = max(1, min(20000, int(limit)))
    conn = get_inventory_db()
    try:
        where = "i.store_id = ?"
        args: List[Any] = [sid]
        if pickup_only:
            where += " AND i.pickup_available = 1 AND COALESCE(i.out_of_stock, 0) = 0"
        rows = conn.execute(
            f"""
            SELECT
              i.store_id, i.product_id, i.current_price, i.list_price, i.was_price,
              i.pickup_available, i.out_of_stock, i.in_store, i.seller_name,
              i.offer_type, i.availability, i.stock_status, i.query,
              i.collection_source,
              s.title, s.brand, s.category, s.url, s.image_url
            FROM inv_store_inventory i
            LEFT JOIN inv_skus s ON s.product_id = i.product_id
            WHERE {where}
            ORDER BY i.updated_at DESC
            LIMIT ?
            """,
            (*args, lim),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            pickup = r["pickup_available"]
            oos = r["out_of_stock"]
            out.append(
                {
                    "store_id": r["store_id"],
                    "product_id": r["product_id"],
                    "title": r["title"] or r["product_id"],
                    "brand": r["brand"],
                    "category": r["category"] or "General",
                    "url": r["url"],
                    "image_url": r["image_url"],
                    "current_price": r["current_price"],
                    "list_price": r["list_price"],
                    "was_price": r["was_price"],
                    "pickup_available": True
                    if pickup == 1
                    else False
                    if pickup == 0
                    else None,
                    "out_of_stock": True if oos == 1 else False if oos == 0 else None,
                    "in_store": True if r["in_store"] == 1 else False if r["in_store"] == 0 else None,
                    "seller_name": r["seller_name"],
                    "offer_type": r["offer_type"],
                    "availability": r["availability"],
                    "stock_status": r["stock_status"],
                    "query": r["query"],
                    "collection_source": r["collection_source"],
                }
            )
        return out
    finally:
        conn.close()


def deals_from_inventory(
    store_id: str,
    *,
    min_discount_pct: Optional[float] = None,
    limit: int = 5000,
    coverage: bool = False,
) -> Dict[str, Any]:
    """
    Score inventory rows into deals.

    Default (coverage=False): hard anti-clone rules —
      require_pickup + walmart_seller_only + drop unverified deep markdowns.
      Fewer, cleaner, pickup-confirmed deals.

    coverage=True (DealHawk-style volume): show every discounted item with a
      stock badge instead of dropping. Does NOT require confirmed pickup and
      keeps marketplace sellers (flagged). Much higher volume.
    """
    products = list_inventory_products(store_id, pickup_only=False, limit=limit)
    thr = DealThresholds.from_env()
    if coverage:
        # Volume mode — surface all real markdowns, badge the stock status.
        thr.require_pickup = False
        thr.prefer_pickup = True
        thr.drop_unverified_deep_markdown = False
        thr.walmart_seller_only = False
        thr.drop_online_only = False
    else:
        # Clean mode — never surface national clones as in-store.
        thr.require_pickup = True
        thr.prefer_pickup = True
        thr.drop_unverified_deep_markdown = True
        thr.walmart_seller_only = True
    if min_discount_pct is not None:
        thr.min_discount_pct = float(min_discount_pct)

    deals = detect_deals(str(store_id), products, thresholds=thr)
    priors = prior_prices_map(
        str(store_id), [str(d.get("product_id")) for d in deals]
    )
    price_drops = 0
    for d in deals:
        pid = str(d.get("product_id") or "")
        prior = priors.get(pid) or {}
        prior_price = prior.get("prior_price")
        cur = d.get("current_price")
        d["prior_price"] = prior_price
        d["prior_scraped_at"] = prior.get("prior_scraped_at")
        d["price_dropped"] = False
        d["drop_amount"] = None
        try:
            if prior_price is not None and cur is not None:
                pp = float(prior_price)
                cc = float(cur)
                if pp - cc >= 0.01:
                    drop = round(pp - cc, 2)
                    d["price_dropped"] = True
                    d["drop_amount"] = drop
                    price_drops += 1
                    why = str(d.get("why_deal") or "")
                    bit = f"price dropped ${drop:.2f} since last scan"
                    d["why_deal"] = f"{why}; {bit}" if why else bit
                    # Boost rank for fresh drops
                    d["rank_score"] = round(float(d.get("rank_score") or 0) + 12.0, 2)
        except (TypeError, ValueError):
            pass

    deals.sort(
        key=lambda x: (
            0 if x.get("price_dropped") else 1,
            -float(x.get("rank_score") or 0),
            -float(x.get("discount_pct") or 0),
        )
    )

    pickup_n = sum(1 for p in products if p.get("pickup_available") is True)
    unverified = sum(1 for p in products if p.get("pickup_available") is not True)
    third_party = 0
    for p in products:
        seller = str(p.get("seller_name") or "").strip().lower()
        if seller and "walmart" not in seller:
            third_party += 1

    return {
        "store_id": str(store_id),
        "inventory_count": len(products),
        "pickup_confirmed": pickup_n,
        "unverified_or_ship": unverified,
        "third_party_seller_rows": third_party,
        "deal_count": len(deals),
        "price_drop_count": price_drops,
        "min_discount_pct": thr.min_discount_pct,
        "coverage_mode": bool(coverage),
        "rules": {
            "require_pickup": thr.require_pickup,
            "walmart_seller_only": thr.walmart_seller_only,
            "drop_unverified_deep_markdown": thr.drop_unverified_deep_markdown,
        },
        "thresholds": thr.to_dict(),
        "deals": deals,
        "last_inventory_update": inventory_counts(str(store_id)).get(
            "last_inventory_update"
        ),
    }


def _product_id_set(products: List[Dict[str, Any]]) -> Set[str]:
    return {str(p["product_id"]) for p in products if p.get("product_id")}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    return round(100.0 * len(a & b) / max(1, len(a | b)), 1)


def compare_store_overlap(
    store_a: str,
    store_b: str,
    *,
    min_discount_pct: float = 20.0,
) -> Dict[str, Any]:
    """Overlap metrics for Milestone 4 anti-clone deliverable."""
    a = str(store_a)
    b = str(store_b)
    prods_a = list_inventory_products(a)
    prods_b = list_inventory_products(b)

    all_a = _product_id_set(prods_a)
    all_b = _product_id_set(prods_b)
    pickup_a = _product_id_set(
        [p for p in prods_a if p.get("pickup_available") is True]
    )
    pickup_b = _product_id_set(
        [p for p in prods_b if p.get("pickup_available") is True]
    )

    deals_a = deals_from_inventory(a, min_discount_pct=min_discount_pct)
    deals_b = deals_from_inventory(b, min_discount_pct=min_discount_pct)
    deal_ids_a = {str(d["product_id"]) for d in deals_a["deals"] if d.get("product_id")}
    deal_ids_b = {str(d["product_id"]) for d in deals_b["deals"] if d.get("product_id")}

    shared_all = sorted(all_a & all_b)
    shared_pickup = sorted(pickup_a & pickup_b)
    shared_deals = sorted(deal_ids_a & deal_ids_b)
    exclusive_a = sorted(deal_ids_a - deal_ids_b)
    exclusive_b = sorted(deal_ids_b - deal_ids_a)
    deal_ov = _jaccard(deal_ids_a, deal_ids_b)
    identical = deal_ids_a == deal_ids_b and len(deal_ids_a) > 0
    # National rollbacks can legitimately appear at many stores when pickup is real.
    # Clone failure = identical deal sets (or empty exclusives while both have deals).
    anti_ok = (not identical) and (
        len(exclusive_a) > 0 or len(exclusive_b) > 0 or not deal_ids_a or not deal_ids_b
    )

    return {
        "store_a": a,
        "store_b": b,
        "sku_count_a": len(all_a),
        "sku_count_b": len(all_b),
        "sku_overlap_pct": _jaccard(all_a, all_b),
        "sku_shared": len(shared_all),
        "pickup_count_a": len(pickup_a),
        "pickup_count_b": len(pickup_b),
        "pickup_overlap_pct": _jaccard(pickup_a, pickup_b),
        "pickup_shared": len(shared_pickup),
        "deal_count_a": len(deal_ids_a),
        "deal_count_b": len(deal_ids_b),
        "deal_overlap_pct": deal_ov,
        "deal_shared": len(shared_deals),
        "deal_exclusive_a": len(exclusive_a),
        "deal_exclusive_b": len(exclusive_b),
        "deal_shared_ids_sample": shared_deals[:20],
        "deal_exclusive_a_sample": exclusive_a[:10],
        "deal_exclusive_b_sample": exclusive_b[:10],
        "lists_identical": identical,
        "anti_clone_ok": anti_ok,
        "notes": (
            "Deals require pickup-confirmed + Walmart seller. "
            "Shared national rollbacks are OK if both stores have pickup; "
            "clone failure = identical lists with no exclusives."
        ),
    }


def candidates_for_pickup_verify(
    store_id: str,
    *,
    limit: int = 40,
    min_markdown_pct: float = 10.0,
) -> List[Dict[str, Any]]:
    """Markdown rows not yet pickup=true (prefer Walmart sellers)."""
    products = list_inventory_products(store_id)
    need: List[Dict[str, Any]] = []
    for p in products:
        if p.get("pickup_available") is True:
            continue
        cur = p.get("current_price")
        was = p.get("was_price") or p.get("list_price")
        try:
            cur_f = float(cur) if cur is not None else None
            was_f = float(was) if was is not None else None
        except (TypeError, ValueError):
            continue
        if not cur_f or not was_f or was_f <= cur_f:
            continue
        pct = (was_f - cur_f) / was_f * 100.0
        if pct < min_markdown_pct:
            continue
        seller = str(p.get("seller_name") or "").lower()
        if seller and "walmart" not in seller:
            continue
        p["_markdown_pct"] = pct
        need.append(p)
    need.sort(key=lambda x: float(x.get("_markdown_pct") or 0), reverse=True)
    return need[: max(0, int(limit))]


def verify_store_pickup(
    store_id: str,
    *,
    zip_code: Optional[str] = None,
    max_verify: Optional[int] = None,
    workers: Optional[int] = None,
) -> Dict[str, Any]:
    """Product-level pickup verify for inventory markdowns (writes back to DB)."""
    from config import get_collector_config
    from oxylabs_collector import fetch_walmart_product, oxylabs_enabled

    sid = str(store_id)
    cfg = get_collector_config()
    if not oxylabs_enabled(cfg):
        raise RuntimeError("Oxylabs not configured")

    postal = zip_code
    if not postal:
        st = get_store(sid)
        postal = (st or {}).get("zip")

    budget = max(
        0,
        max_verify
        if max_verify is not None
        else _env_int("INVENTORY_VERIFY_MAX", 40),
    )
    workers_n = max(
        1,
        min(
            8,
            workers
            if workers is not None
            else _env_int("INVENTORY_PARALLEL_WORKERS", 3),
        ),
    )
    need = candidates_for_pickup_verify(sid, limit=budget)
    if not need:
        return {
            "ok": True,
            "store_id": sid,
            "checked": 0,
            "updated": 0,
            "pickup_true": 0,
            "pickup_false": 0,
            "failed": 0,
            "notes": "nothing to verify",
        }

    updated_rows: List[Dict[str, Any]] = []
    pickup_true = 0
    pickup_false = 0
    failed = 0
    delay = float(os.environ.get("INVENTORY_JOB_DELAY_SEC") or 0.25)

    def _one(p: Dict[str, Any]) -> Dict[str, Any]:
        if delay:
            time.sleep(delay)
        pid = str(p["product_id"])
        try:
            info = fetch_walmart_product(pid, sid, postal, cfg)
            return {"pid": pid, "base": p, "info": info, "error": None}
        except Exception as e:
            return {
                "pid": pid,
                "base": p,
                "info": None,
                "error": f"{type(e).__name__}: {e}",
            }

    with ThreadPoolExecutor(max_workers=min(workers_n, len(need))) as pool:
        futs = [pool.submit(_one, p) for p in need]
        for fut in as_completed(futs):
            hit = fut.result()
            info = hit.get("info") or {}
            base = hit["base"]
            if hit.get("error") or not info.get("ok"):
                failed += 1
                continue
            pickup = info.get("pickup_available")
            oos = bool(info.get("out_of_stock"))
            if pickup is True:
                pickup_true += 1
            elif pickup is False:
                pickup_false += 1
            row = {
                "product_id": hit["pid"],
                "title": base.get("title") or hit["pid"],
                "brand": base.get("brand"),
                "category": base.get("category"),
                "url": base.get("url"),
                "image_url": base.get("image_url"),
                "current_price": info.get("current_price") or base.get("current_price"),
                "list_price": info.get("was_price") or base.get("list_price"),
                "was_price": info.get("was_price") or base.get("was_price"),
                "pickup_available": pickup,
                "out_of_stock": oos,
                "in_store": True if pickup is True else False if pickup is False else None,
                "seller_name": info.get("seller_name") or base.get("seller_name"),
                "offer_type": base.get("offer_type"),
                "availability": (
                    "Out of stock"
                    if oos
                    else "In stock · pickup"
                    if pickup is True
                    else "Ship only (not in-store)"
                    if pickup is False
                    else "Store stock unconfirmed"
                ),
                "stock_status": (
                    "Out of stock"
                    if oos
                    else "In stock"
                    if pickup is True
                    else "Ship only"
                    if pickup is False
                    else "Unconfirmed"
                ),
                "query": base.get("query") or "m4_verify",
                "collection_source": "oxylabs_walmart_product",
                "store_id": sid,
            }
            updated_rows.append(row)

    upserted = upsert_inventory_items(sid, updated_rows) if updated_rows else 0
    return {
        "ok": True,
        "store_id": sid,
        "zip": postal,
        "checked": len(need),
        "updated": upserted,
        "pickup_true": pickup_true,
        "pickup_false": pickup_false,
        "failed": failed,
        "unresolved": len(need) - pickup_true - pickup_false - failed,
    }


def store_has_inventory(store_id: str, *, min_skus: int = 1) -> bool:
    counts = inventory_counts(str(store_id))
    return int(counts.get("inventory_count") or 0) >= int(min_skus)


def queue_priority_refresh(
    store_id: str,
    zip_code: Optional[str] = None,
    *,
    recheck_limit: int = 20,
) -> Dict[str, Any]:
    """Background Wave D recheck + light verify (refresh=1 on fast path)."""
    import threading

    sid = str(store_id)
    postal = zip_code
    if not postal:
        st = get_store(sid)
        postal = (st or {}).get("zip")

    started: Dict[str, Any] = {"verify": False, "scan": None}

    def _bg() -> None:
        try:
            verify_store_pickup(sid, zip_code=postal, max_verify=min(20, recheck_limit))
        except Exception:
            pass
        try:
            from inventory_scan import start_inventory_scan

            start_inventory_scan(
                store_id=sid,
                zip_code=postal,
                wave="D",
                recheck_limit=recheck_limit,
                background=True,
            )
        except Exception:
            pass

    threading.Thread(target=_bg, name=f"inv-refresh-{sid}", daemon=True).start()
    started["verify"] = True
    started["queued"] = True
    return started


def build_inventory_deal_report(
    zip_code: str,
    store_id: str,
    *,
    store: Optional[Any] = None,
    location: Optional[Dict[str, Any]] = None,
    min_discount_pct: float = 20.0,
    refresh: bool = False,
    radius_miles: float = 50.0,
    coverage: bool = False,
) -> Dict[str, Any]:
    """
    Milestone 5 fast path — deal report shaped like build_report, sourced from inventory DB.
    """
    import time
    from urllib.parse import quote

    sid = str(store_id)
    loc = location
    store_obj = store
    if store_obj is None or loc is None:
        from walmart_core import find_stores_near_zip

        loc2, stores = find_stores_near_zip(zip_code, radius_miles=radius_miles)
        loc = loc or loc2
        if store_obj is None:
            store_obj = next((s for s in stores if str(s.store_id) == sid), None)
        if store_obj is None:
            raise ValueError(
                f"Store '{store_id}' not found for ZIP {zip_code}. Call /stores first."
            )

    refresh_meta: Optional[Dict[str, Any]] = None
    if refresh:
        refresh_meta = queue_priority_refresh(
            sid,
            zip_code=getattr(store_obj, "zip", None) or zip_code,
        )

    inv = deals_from_inventory(
        sid, min_discount_pct=min_discount_pct, coverage=coverage
    )
    deals = list(inv.get("deals") or [])
    for d in deals:
        if not d.get("url"):
            pid = d.get("product_id")
            q = quote(str(d.get("title") or "walmart"))
            d["url"] = (
                f"https://www.walmart.com/ip/{pid}"
                if pid and str(pid).isdigit()
                else f"https://www.walmart.com/search?q={q}"
            )
        d["min_discount_pct"] = float(min_discount_pct)
        d["source"] = "inventory_db"

    store_dict = (
        store_obj.to_dict()
        if hasattr(store_obj, "to_dict")
        else dict(getattr(store_obj, "__dict__", {}) or {})
    )
    counts = inventory_counts(sid)
    age_sec = None
    last_upd = counts.get("last_inventory_update")
    if last_upd:
        try:
            from datetime import datetime, timezone

            ts = datetime.fromisoformat(str(last_upd).replace("Z", "+00:00"))
            age_sec = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
        except Exception:
            age_sec = None

    note = (
        "Coverage mode: all in-store markdowns with stock badges (DealHawk-style volume)."
        if coverage
        else "Fast path: pickup-confirmed + Walmart-seller deals from inventory DB. "
        "Use coverage=1 for full volume, refresh=1 to queue a recheck."
    )
    return {
        "zip": (loc or {}).get("zip") or zip_code,
        "location": {
            "city": (loc or {}).get("city"),
            "state": (loc or {}).get("state"),
            "lat": (loc or {}).get("lat"),
            "lon": (loc or {}).get("lon"),
        },
        "store": store_dict,
        "summary": {
            "deal_count": len(deals),
            "avg_discount_pct": round(
                sum(float(d.get("discount_pct") or 0) for d in deals) / len(deals), 1
            )
            if deals
            else 0,
            "max_discount_pct": max(
                (float(d.get("discount_pct") or 0) for d in deals), default=0
            ),
            "total_savings_if_bought_all": round(
                sum(float(d.get("savings") or 0) for d in deals), 2
            ),
            "min_discount_pct": min_discount_pct,
            "price_drop_count": int(inv.get("price_drop_count") or 0),
            "inventory_count": int(inv.get("inventory_count") or 0),
            "pickup_confirmed": int(inv.get("pickup_confirmed") or 0),
            "coverage_mode": bool(coverage),
        },
        "deals": deals,
        "meta": {
            "data_mode": "inventory",
            "live_ok": True,
            "user_error": None,
            "store_source": store_dict.get("source") or "inventory",
            "collector_notes": (
                f"inventory_db skus={inv.get('inventory_count')} "
                f"pickup={inv.get('pickup_confirmed')} "
                f"drops={inv.get('price_drop_count')}"
            ),
            "live_product_count": int(inv.get("pickup_confirmed") or 0),
            "cache_age_sec": age_sec,
            "scan_id": None,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note,
            "deal_thresholds": inv.get("thresholds") or {},
            "milestone": 5,
            "phase2_milestone": 5,
            "source": "inventory_db",
            "last_inventory_update": last_upd,
            "refresh_queued": bool(refresh_meta),
            "refresh": refresh_meta,
            "rules": inv.get("rules"),
        },
    }
