"""
Phase 2 / Milestone 1 — Scan orchestrator.

Enqueues idempotent search jobs, runs them with a worker pool + rate limit,
and upserts results into the inventory DB.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Tuple

from inventory_db import (
    create_scan_run,
    enqueue_scan_jobs,
    get_scan_run,
    inventory_counts,
    list_queued_jobs,
    list_scan_runs,
    scan_job_stats,
    update_scan_job,
    update_scan_run,
    upsert_inventory_items,
    upsert_store,
)

# Seed wave — clearance + department coverage (M1).
# M2 will add deeper pagination / long-tail / re-check waves.
SEED_QUERIES: Tuple[str, ...] = (
    "clearance",
    "rollback",
    "hidden clearance",
    "special buy",
    "markdown",
    "clearance 50%",
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
    "rollback electronics",
    "rollback home",
)

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


def build_seed_jobs(
    pages_per_query: Optional[int] = None,
    max_queries: Optional[int] = None,
) -> List[Dict[str, Any]]:
    pages = max(1, min(3, pages_per_query if pages_per_query is not None else _env_int("INVENTORY_PAGES_PER_QUERY", 1)))
    max_q = max(4, max_queries if max_queries is not None else _env_int("INVENTORY_MAX_QUERIES", 20))
    queries = list(SEED_QUERIES)[:max_q]
    jobs: List[Dict[str, Any]] = []
    for q in queries:
        for page in range(1, pages + 1):
            jobs.append({"query": q, "page": page})
    return jobs


def _fetch_inventory_page(
    query: str,
    store_id: str,
    postal_code: Optional[str],
    page: int,
) -> Dict[str, Any]:
    """Search page for inventory seed — keep all mapped products (SKU universe)."""
    from config import get_collector_config
    from oxylabs_collector import (
        _extract_results,
        _map_item,
        oxylabs_enabled,
    )

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

    data = resp.json() if resp.content else {}
    rows, location = _extract_results(data if isinstance(data, dict) else {})
    products: List[Dict[str, Any]] = []
    for raw in rows[: max(1, int(cfg.max_per_query))]:
        mapped = _map_item(raw, query=query, store_id=str(store_id))
        if not mapped:
            continue
        # Inventory seed keeps Walmart + unknown sellers; drop clear 3P later in M3.
        products.append(mapped)

    return {
        "ok": True,
        "products": products,
        "raw_count": len(rows),
        "location": location,
    }


def start_inventory_scan(
    store_id: str,
    zip_code: Optional[str] = None,
    *,
    wave: str = "seed",
    store_meta: Optional[Dict[str, Any]] = None,
    pages_per_query: Optional[int] = None,
    max_queries: Optional[int] = None,
    background: bool = True,
) -> Dict[str, Any]:
    """Create a scan run + jobs and optionally start a background worker."""
    sid = str(store_id).strip()
    if not sid:
        raise ValueError("store_id required")

    # One active scan per store (idempotent guard).
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

    jobs = build_seed_jobs(pages_per_query=pages_per_query, max_queries=max_queries)
    scan_id = create_scan_run(sid, zip_code, wave=wave, jobs_total=len(jobs))
    inserted = enqueue_scan_jobs(scan_id, sid, jobs)
    update_scan_run(scan_id, jobs_total=inserted, notes=f"enqueued={inserted} wave={wave}")

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

    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    update_scan_run(
        scan_id,
        status="running",
        started_at=run.get("started_at") or now_iso,
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
        job_now = datetime.now(timezone.utc).isoformat()
        update_scan_job(
            jid,
            status="running",
            started_at=job_now,
        )
        if delay:
            time.sleep(delay)
        try:
            result = _fetch_inventory_page(q, sid, zip_code, page)
            products = result.get("products") or []
            upserted = upsert_inventory_items(sid, products)
            update_scan_job(
                jid,
                status="completed",
                products_found=len(products),
                products_upserted=upserted,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            return {
                "ok": True,
                "job_id": jid,
                "query": q,
                "page": page,
                "found": len(products),
                "upserted": upserted,
                "raw": result.get("raw_count"),
            }
        except Exception as e:
            update_scan_job(
                jid,
                status="failed",
                error=f"{type(e).__name__}: {e}"[:500],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            return {
                "ok": False,
                "job_id": jid,
                "query": q,
                "page": page,
                "error": f"{type(e).__name__}: {e}",
            }

    auth_fail = False
    try:
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(jobs)))) as pool:
            futs = [pool.submit(_one, job) for job in jobs]
            for fut in as_completed(futs):
                hit = fut.result()
                api_calls += 1
                if hit.get("ok"):
                    done += 1
                    notes.append(
                        f"ok q={hit.get('query')} p={hit.get('page')} "
                        f"n={hit.get('found')} up={hit.get('upserted')}"
                    )
                else:
                    failed += 1
                    err = str(hit.get("error") or "")
                    notes.append(f"fail q={hit.get('query')}: {err[:120]}")
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
            finished_at=datetime.now(timezone.utc).isoformat(),
            error="oxylabs_auth_failed" if auth_fail else None,
            notes="; ".join(notes[-20:]),
        )
    except Exception as e:
        update_scan_run(
            scan_id,
            status="failed",
            error=f"{type(e).__name__}: {e}"[:500],
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        with _lock:
            _active_threads.pop(int(scan_id), None)

    return get_scan_status(scan_id)


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
