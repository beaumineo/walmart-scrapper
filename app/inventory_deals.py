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
from inventory_db import get_inventory_db, get_store, upsert_inventory_items


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
) -> Dict[str, Any]:
    """
    Score inventory rows into deals with hard anti-clone defaults:
    require_pickup + walmart_seller_only + drop unverified deep markdowns.
    """
    products = list_inventory_products(store_id, pickup_only=False, limit=limit)
    thr = DealThresholds.from_env()
    # M4 hard rules — never surface national clones as in-store
    thr.require_pickup = True
    thr.prefer_pickup = True
    thr.drop_unverified_deep_markdown = True
    thr.walmart_seller_only = True
    if min_discount_pct is not None:
        thr.min_discount_pct = float(min_discount_pct)

    deals = detect_deals(str(store_id), products, thresholds=thr)
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
        "min_discount_pct": thr.min_discount_pct,
        "rules": {
            "require_pickup": True,
            "walmart_seller_only": True,
            "drop_unverified_deep_markdown": True,
        },
        "deals": deals,
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
