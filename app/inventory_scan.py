"""
Phase 2 inventory scan orchestrator.

Milestone 2: seed backbone
Milestone 3: full-store waves A–D (search + product recheck)
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from inventory_db import (
    coverage_report,
    create_scan_run,
    enqueue_scan_jobs,
    get_scan_run,
    get_store,
    inventory_counts,
    list_queued_jobs,
    list_scan_runs,
    list_store_product_ids,
    scan_job_stats,
    update_scan_job,
    update_scan_run,
    upsert_inventory_items,
    upsert_store,
)

# --- Wave definitions (Milestone 3) ---

WAVE_A_QUERIES: Tuple[str, ...] = (
    "clearance",
    "rollback",
    "hidden clearance",
    "special buy",
    "markdown",
    "clearance 50%",
    "clearance 70%",
    '"clearance"',
    "clearance electronics",
    "clearance toys",
    "clearance home",
    "clearance kitchen",
    "clearance furniture",
    "clearance apparel",
    "clearance grocery",
    "clearance baby",
    "clearance outdoor",
    "clearance tools",
    "clearance sports",
    "clearance appliances",
    "clearance cleaning",
    "clearance patio",
    "rollback electronics",
    "rollback home",
)

WAVE_B_QUERIES: Tuple[str, ...] = (
    "electronics",
    "toys",
    "home",
    "grocery",
    "apparel",
    "kitchen",
    "outdoor",
    "furniture",
    "baby",
    "sports",
    "appliances",
    "tools",
    "beauty",
    "pets",
    "automotive",
    "office",
    "health",
    "tv",
    "headphones",
    "vacuum",
)

RECHECK_PREFIX = "__recheck__:"

_lock = threading.Lock()
_active_threads: Dict[int, threading.Thread] = {}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_for_queries(
    queries: List[str],
    pages: int,
) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for q in queries:
        for page in range(1, pages + 1):
            jobs.append({"query": q, "page": page})
    return jobs


def build_wave_jobs(
    wave: str,
    store_id: str,
    *,
    pages_per_query: Optional[int] = None,
    max_queries: Optional[int] = None,
    recheck_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build idempotent jobs for wave: seed|A|B|C|D|full."""
    w = (wave or "seed").strip().lower()
    pages = max(
        1,
        min(
            4,
            pages_per_query
            if pages_per_query is not None
            else _env_int("INVENTORY_PAGES_PER_QUERY", 1),
        ),
    )
    max_q = max(
        1,
        max_queries
        if max_queries is not None
        else _env_int("INVENTORY_MAX_QUERIES", 24),
    )
    recheck_n = max(
        0,
        recheck_limit
        if recheck_limit is not None
        else _env_int("INVENTORY_RECHECK_MAX", 40),
    )

    jobs: List[Dict[str, Any]] = []

    if w in ("seed", "a", "wave_a", "wave-a"):
        # Seed / Wave A: clearance-heavy (seed defaults to 1 page unless pages set)
        seed_pages = 1 if (w == "seed" and pages_per_query is None) else pages
        jobs.extend(_jobs_for_queries(list(WAVE_A_QUERIES)[:max_q], seed_pages))

    elif w in ("b", "wave_b", "wave-b"):
        q = list(WAVE_B_QUERIES)[:max_q]
        jobs.extend(_jobs_for_queries(q, pages))

    elif w in ("c", "wave_c", "wave-c"):
        # Pagination / long-tail: deeper pages on A+B
        combo = list(dict.fromkeys(list(WAVE_A_QUERIES) + list(WAVE_B_QUERIES)))[:max_q]
        deep_pages = max(pages, 2)
        jobs.extend(_jobs_for_queries(combo, deep_pages))

    elif w in ("d", "wave_d", "wave-d", "recheck"):
        pids = list_store_product_ids(store_id, limit=recheck_n, prefer_markdown=True)
        for pid in pids:
            jobs.append({"query": f"{RECHECK_PREFIX}{pid}", "page": 1})

    elif w in ("full", "abcd", "all"):
        # A + B + extra pages (C) + D recheck, with budget caps
        a_cap = max(8, max_q // 2)
        b_cap = max(6, max_q - a_cap)
        jobs.extend(_jobs_for_queries(list(WAVE_A_QUERIES)[:a_cap], max(1, pages)))
        jobs.extend(_jobs_for_queries(list(WAVE_B_QUERIES)[:b_cap], max(1, pages)))
        # C: page 2+ for top clearance queries
        for q in list(WAVE_A_QUERIES)[: min(8, a_cap)]:
            for page in range(2, max(2, pages) + 1):
                jobs.append({"query": q, "page": page})
        pids = list_store_product_ids(store_id, limit=recheck_n, prefer_markdown=True)
        for pid in pids:
            jobs.append({"query": f"{RECHECK_PREFIX}{pid}", "page": 1})
    else:
        raise ValueError(
            f"Unknown wave={wave!r}. Use seed, A, B, C, D, or full."
        )

    # De-dupe while preserving order
    seen = set()
    out: List[Dict[str, Any]] = []
    for job in jobs:
        key = (str(job.get("query") or ""), int(job.get("page") or 1))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append({"query": key[0], "page": key[1]})
    return out


# Back-compat alias
def build_seed_jobs(
    pages_per_query: Optional[int] = None,
    max_queries: Optional[int] = None,
) -> List[Dict[str, Any]]:
    return build_wave_jobs(
        "seed",
        store_id="_",
        pages_per_query=pages_per_query or 1,
        max_queries=max_queries,
        recheck_limit=0,
    )


def _fetch_with_retries(do_request, *, retries: int = 2, label: str = "oxylabs"):
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return do_request()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            auth = "auth failed" in msg or "401" in msg
            rate = "rate limited" in msg or "429" in msg
            if auth:
                raise
            if attempt >= retries:
                break
            sleep_s = (1.5 * (attempt + 1)) if rate else (0.8 * (attempt + 1))
            time.sleep(sleep_s)
    raise RuntimeError(f"{label} failed after retries: {last_err}")


def _fetch_inventory_page(
    query: str,
    store_id: str,
    postal_code: Optional[str],
    page: int,
) -> Dict[str, Any]:
    """Search page for inventory — keep all mapped products (SKU universe)."""
    from config import get_collector_config
    from oxylabs_collector import _extract_results, _map_item, oxylabs_enabled

    cfg = get_collector_config()
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
        "start_page": max(1, int(page or 1)),
    }
    if len(postal) == 5:
        payload["delivery_zip"] = postal

    timeout = max(45, min(90, int(cfg.timeout_sec) + 35))

    def _once():
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
            raise RuntimeError(f"Oxylabs http={resp.status_code}: {resp.text[:240]}")
        return resp

    resp = _fetch_with_retries(_once, retries=_env_int("INVENTORY_FETCH_RETRIES", 2))
    data = resp.json() if resp.content else {}
    rows, location = _extract_results(data if isinstance(data, dict) else {})
    products: List[Dict[str, Any]] = []
    for raw in rows[: max(1, int(cfg.max_per_query))]:
        mapped = _map_item(raw, query=query, store_id=str(store_id))
        if mapped:
            products.append(mapped)

    return {
        "ok": True,
        "products": products,
        "raw_count": len(rows),
        "location": location,
    }


def _fetch_product_recheck(
    product_id: str,
    store_id: str,
    postal_code: Optional[str],
) -> Dict[str, Any]:
    """Wave D: walmart_product refresh for price/stock deltas."""
    from config import get_collector_config
    from oxylabs_collector import fetch_walmart_product, oxylabs_enabled

    cfg = get_collector_config()
    if not oxylabs_enabled(cfg):
        raise RuntimeError("Oxylabs not configured")

    def _once():
        info = fetch_walmart_product(product_id, store_id, postal_code, cfg)
        if not info.get("ok"):
            raise RuntimeError(f"product lookup empty for {product_id}")
        return info

    info = _fetch_with_retries(
        _once,
        retries=_env_int("INVENTORY_FETCH_RETRIES", 2),
        label="oxylabs_product",
    )
    pickup = info.get("pickup_available")
    oos = bool(info.get("out_of_stock"))
    product = {
        "product_id": str(product_id),
        "title": str(product_id),
        "current_price": info.get("current_price"),
        "list_price": info.get("was_price"),
        "was_price": info.get("was_price"),
        "pickup_available": pickup,
        "out_of_stock": oos,
        "in_store": True if pickup is True else False if pickup is False else None,
        "seller_name": info.get("seller_name"),
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
        "query": "wave_d_recheck",
        "collection_source": "oxylabs_walmart_product",
        "store_id": str(store_id),
    }
    return {"ok": True, "products": [product], "raw_count": 1}


def start_inventory_scan(
    store_id: str,
    zip_code: Optional[str] = None,
    *,
    wave: str = "seed",
    store_meta: Optional[Dict[str, Any]] = None,
    pages_per_query: Optional[int] = None,
    max_queries: Optional[int] = None,
    recheck_limit: Optional[int] = None,
    background: bool = True,
) -> Dict[str, Any]:
    """Create a scan run + jobs and optionally start a background worker."""
    sid = str(store_id).strip()
    if not sid:
        raise ValueError("store_id required")

    active = inventory_counts(sid).get("active_scan")
    if active:
        return {
            "ok": False,
            "error": "scan_already_running",
            "scan": get_scan_status(int(active["id"])),
        }

    if store_meta:
        meta = dict(store_meta)
        meta["store_id"] = sid
        if zip_code and not meta.get("zip"):
            meta["zip"] = zip_code
        upsert_store(meta)
    else:
        upsert_store({"store_id": sid, "zip": zip_code})

    if not zip_code:
        existing = get_store(sid)
        if existing and existing.get("zip"):
            zip_code = str(existing["zip"])

    jobs = build_wave_jobs(
        wave,
        sid,
        pages_per_query=pages_per_query,
        max_queries=max_queries,
        recheck_limit=recheck_limit,
    )
    if not jobs:
        raise ValueError(
            f"No jobs for wave={wave!r}. For wave D, seed the store first (wave A/full)."
        )

    scan_id = create_scan_run(sid, zip_code, wave=wave, jobs_total=len(jobs))
    inserted = enqueue_scan_jobs(scan_id, sid, jobs)
    update_scan_run(
        scan_id,
        jobs_total=inserted,
        notes=f"enqueued={inserted} wave={wave}",
    )

    if background:
        t = threading.Thread(
            target=run_inventory_scan,
            args=(scan_id,),
            kwargs={"postal_code": zip_code},
            name=f"inv-scan-{scan_id}",
            daemon=True,
        )
        with _lock:
            _active_threads[scan_id] = t
        t.start()
    else:
        run_inventory_scan(scan_id, postal_code=zip_code)

    return {"ok": True, "scan": get_scan_status(scan_id)}


def run_inventory_scan(scan_id: int, postal_code: Optional[str] = None) -> Dict[str, Any]:
    """Execute queued jobs for a scan run (blocking)."""
    run = get_scan_run(scan_id)
    if not run:
        raise ValueError(f"scan {scan_id} not found")

    sid = str(run["store_id"])
    zip_code = postal_code or run.get("zip")
    workers = max(1, min(8, _env_int("INVENTORY_PARALLEL_WORKERS", 3)))
    delay = max(0.0, _env_float("INVENTORY_JOB_DELAY_SEC", 0.4))

    update_scan_run(
        scan_id,
        status="running",
        started_at=run.get("started_at") or _now(),
    )

    jobs = list_queued_jobs(scan_id)
    done = int(run.get("jobs_done") or 0)
    failed = int(run.get("jobs_failed") or 0)
    api_calls = int(run.get("api_calls") or 0)
    notes: List[str] = []

    def _one(job: Dict[str, Any]) -> Dict[str, Any]:
        jid = int(job["id"])
        q = str(job["query"])
        page = int(job["page"] or 1)
        update_scan_job(jid, status="running", started_at=_now())
        if delay:
            time.sleep(delay)
        try:
            if q.startswith(RECHECK_PREFIX):
                pid = q[len(RECHECK_PREFIX) :]
                result = _fetch_product_recheck(pid, sid, zip_code)
                label = f"recheck:{pid}"
            else:
                result = _fetch_inventory_page(q, sid, zip_code, page)
                label = f"{q} p={page}"
            products = result.get("products") or []
            upserted = upsert_inventory_items(sid, products)
            update_scan_job(
                jid,
                status="completed",
                products_found=len(products),
                products_upserted=upserted,
                finished_at=_now(),
            )
            return {
                "ok": True,
                "job_id": jid,
                "label": label,
                "found": len(products),
                "upserted": upserted,
            }
        except Exception as e:
            update_scan_job(
                jid,
                status="failed",
                error=f"{type(e).__name__}: {e}"[:500],
                finished_at=_now(),
            )
            return {
                "ok": False,
                "job_id": jid,
                "label": q,
                "error": f"{type(e).__name__}: {e}",
            }

    auth_fail = False
    try:
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(jobs) or 1))) as pool:
            futs = [pool.submit(_one, job) for job in jobs]
            for fut in as_completed(futs):
                hit = fut.result()
                api_calls += 1
                if hit.get("ok"):
                    done += 1
                    notes.append(
                        f"ok {hit.get('label')} n={hit.get('found')} up={hit.get('upserted')}"
                    )
                else:
                    failed += 1
                    err = str(hit.get("error") or "")
                    notes.append(f"fail {hit.get('label')}: {err[:120]}")
                    if "auth failed" in err.lower() or "401" in err:
                        auth_fail = True
                        break

                counts = inventory_counts(sid)
                update_scan_run(
                    scan_id,
                    jobs_done=done,
                    jobs_failed=failed,
                    api_calls=api_calls,
                    inventory_count=counts["inventory_count"],
                    sku_count=counts["inventory_count"],
                    notes="; ".join(notes[-12:]),
                )

        counts = inventory_counts(sid)
        status = "failed" if (auth_fail or (done == 0 and failed > 0)) else "completed"
        update_scan_run(
            scan_id,
            status=status,
            jobs_done=done,
            jobs_failed=failed,
            api_calls=api_calls,
            inventory_count=counts["inventory_count"],
            sku_count=counts["inventory_count"],
            finished_at=_now(),
            error="oxylabs_auth_failed" if auth_fail else None,
            notes="; ".join(notes[-20:]),
        )
    except Exception as e:
        update_scan_run(
            scan_id,
            status="failed",
            error=f"{type(e).__name__}: {e}"[:500],
            finished_at=_now(),
        )
    finally:
        with _lock:
            _active_threads.pop(int(scan_id), None)

    final = get_scan_status(scan_id)
    try:
        from discord_notify import notify_scan_finished

        notify_scan_finished(final)
    except Exception:
        pass
    return final


def get_scan_status(scan_id: int) -> Dict[str, Any]:
    run = get_scan_run(scan_id)
    if not run:
        raise ValueError(f"scan {scan_id} not found")
    jobs = scan_job_stats(scan_id)
    store = inventory_counts(str(run["store_id"]))
    total = int(run.get("jobs_total") or 0)
    finished = int(jobs.get("completed") or 0) + int(jobs.get("failed") or 0)
    progress = round(100.0 * finished / total, 1) if total else 0.0
    return {
        "scan_id": int(run["id"]),
        "store_id": run["store_id"],
        "zip": run.get("zip"),
        "status": run["status"],
        "wave": run.get("wave"),
        "progress_pct": progress,
        "jobs_total": total,
        "jobs_done": int(run.get("jobs_done") or 0),
        "jobs_failed": int(run.get("jobs_failed") or 0),
        "job_status": jobs,
        "sku_count": int(run.get("sku_count") or 0),
        "inventory_count": int(run.get("inventory_count") or 0),
        "api_calls": int(run.get("api_calls") or 0),
        "notes": run.get("notes"),
        "error": run.get("error"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "created_at": run.get("created_at"),
        "store_inventory": {
            "inventory_count": store["inventory_count"],
            "markdown_candidates": store["markdown_candidates"],
            "pickup_true_count": store["pickup_true_count"],
            "last_inventory_update": store["last_inventory_update"],
        },
    }


def list_inventory_scans(
    store_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    rows = list_scan_runs(store_id=store_id, limit=limit)
    out = []
    for r in rows:
        out.append(
            {
                "scan_id": r["id"],
                "store_id": r["store_id"],
                "zip": r.get("zip"),
                "status": r["status"],
                "wave": r.get("wave"),
                "jobs_total": r.get("jobs_total"),
                "jobs_done": r.get("jobs_done"),
                "jobs_failed": r.get("jobs_failed"),
                "inventory_count": r.get("inventory_count"),
                "sku_count": r.get("sku_count"),
                "api_calls": r.get("api_calls"),
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
                "created_at": r.get("created_at"),
                "error": r.get("error"),
            }
        )
    return out


def get_store_inventory_status(store_id: str) -> Dict[str, Any]:
    return inventory_counts(str(store_id))


def get_coverage_report(store_id: str) -> Dict[str, Any]:
    return coverage_report(str(store_id))
