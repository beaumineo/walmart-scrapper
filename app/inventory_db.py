"""
Phase 2 / Milestone 1 — Inventory database.

Tables:
  inv_stores        — watched Walmart stores
  inv_skus          — global product catalog
  inv_store_inventory — per-store price/stock snapshot
  inv_scan_runs     — scan orchestrator runs
  inv_scan_jobs     — per-query jobs inside a run (idempotent)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _db_path() -> Path:
    try:
        from config import writable_data_dir

        return writable_data_dir() / "deals.db"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data" / "deals.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_inventory_db() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inv_stores (
            store_id TEXT PRIMARY KEY,
            name TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            lat REAL,
            lon REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inv_skus (
            product_id TEXT PRIMARY KEY,
            title TEXT,
            brand TEXT,
            category TEXT,
            url TEXT,
            image_url TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inv_store_inventory (
            store_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            current_price REAL,
            list_price REAL,
            was_price REAL,
            pickup_available INTEGER,
            out_of_stock INTEGER,
            in_store INTEGER,
            seller_name TEXT,
            offer_type TEXT,
            availability TEXT,
            stock_status TEXT,
            query TEXT,
            collection_source TEXT,
            raw_json TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (store_id, product_id)
        );

        CREATE INDEX IF NOT EXISTS idx_inv_inventory_store
            ON inv_store_inventory(store_id);
        CREATE INDEX IF NOT EXISTS idx_inv_inventory_updated
            ON inv_store_inventory(store_id, updated_at);

        CREATE TABLE IF NOT EXISTS inv_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            zip TEXT,
            status TEXT NOT NULL,
            wave TEXT NOT NULL DEFAULT 'seed',
            jobs_total INTEGER NOT NULL DEFAULT 0,
            jobs_done INTEGER NOT NULL DEFAULT 0,
            jobs_failed INTEGER NOT NULL DEFAULT 0,
            sku_count INTEGER NOT NULL DEFAULT 0,
            inventory_count INTEGER NOT NULL DEFAULT 0,
            api_calls INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            error TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_inv_scan_runs_store
            ON inv_scan_runs(store_id, id DESC);

        CREATE TABLE IF NOT EXISTS inv_scan_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            store_id TEXT NOT NULL,
            query TEXT NOT NULL,
            page INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            products_found INTEGER NOT NULL DEFAULT 0,
            products_upserted INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(scan_id, query, page)
        );

        CREATE INDEX IF NOT EXISTS idx_inv_scan_jobs_scan
            ON inv_scan_jobs(scan_id, status);
        """
    )
    conn.commit()


def upsert_store(store: Dict[str, Any]) -> None:
    sid = str(store.get("store_id") or "").strip()
    if not sid:
        return
    now = _now()
    conn = get_inventory_db()
    try:
        conn.execute(
            """
            INSERT INTO inv_stores (
                store_id, name, address, city, state, zip, lat, lon, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id) DO UPDATE SET
                name=COALESCE(excluded.name, inv_stores.name),
                address=COALESCE(excluded.address, inv_stores.address),
                city=COALESCE(excluded.city, inv_stores.city),
                state=COALESCE(excluded.state, inv_stores.state),
                zip=COALESCE(excluded.zip, inv_stores.zip),
                lat=COALESCE(excluded.lat, inv_stores.lat),
                lon=COALESCE(excluded.lon, inv_stores.lon),
                updated_at=excluded.updated_at
            """,
            (
                sid,
                store.get("name"),
                store.get("address"),
                store.get("city"),
                store.get("state"),
                store.get("zip"),
                store.get("lat"),
                store.get("lon"),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_store(store_id: str) -> Optional[Dict[str, Any]]:
    conn = get_inventory_db()
    try:
        row = conn.execute(
            "SELECT * FROM inv_stores WHERE store_id = ?",
            (str(store_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_inventory_items(
    store_id: str,
    products: List[Dict[str, Any]],
) -> int:
    """Upsert SKUs + store inventory rows. Returns number of inventory upserts."""
    sid = str(store_id)
    now = _now()
    if not products:
        return 0
    conn = get_inventory_db()
    n = 0
    try:
        for p in products:
            pid = str(p.get("product_id") or "").strip()
            if not pid:
                continue
            title = p.get("title") or pid
            conn.execute(
                """
                INSERT INTO inv_skus (
                    product_id, title, brand, category, url, image_url,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    title=COALESCE(excluded.title, inv_skus.title),
                    brand=COALESCE(excluded.brand, inv_skus.brand),
                    category=COALESCE(excluded.category, inv_skus.category),
                    url=COALESCE(excluded.url, inv_skus.url),
                    image_url=COALESCE(excluded.image_url, inv_skus.image_url),
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    pid,
                    title,
                    p.get("brand"),
                    p.get("category") or "General",
                    p.get("url"),
                    p.get("image_url"),
                    now,
                    now,
                ),
            )

            def _tri(v: Any) -> Optional[int]:
                if v is True:
                    return 1
                if v is False:
                    return 0
                return None

            conn.execute(
                """
                INSERT INTO inv_store_inventory (
                    store_id, product_id, current_price, list_price, was_price,
                    pickup_available, out_of_stock, in_store, seller_name, offer_type,
                    availability, stock_status, query, collection_source, raw_json,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, product_id) DO UPDATE SET
                    current_price=COALESCE(excluded.current_price, inv_store_inventory.current_price),
                    list_price=COALESCE(excluded.list_price, inv_store_inventory.list_price),
                    was_price=COALESCE(excluded.was_price, inv_store_inventory.was_price),
                    pickup_available=COALESCE(excluded.pickup_available, inv_store_inventory.pickup_available),
                    out_of_stock=COALESCE(excluded.out_of_stock, inv_store_inventory.out_of_stock),
                    in_store=COALESCE(excluded.in_store, inv_store_inventory.in_store),
                    seller_name=COALESCE(excluded.seller_name, inv_store_inventory.seller_name),
                    offer_type=COALESCE(excluded.offer_type, inv_store_inventory.offer_type),
                    availability=COALESCE(excluded.availability, inv_store_inventory.availability),
                    stock_status=COALESCE(excluded.stock_status, inv_store_inventory.stock_status),
                    query=COALESCE(excluded.query, inv_store_inventory.query),
                    collection_source=COALESCE(excluded.collection_source, inv_store_inventory.collection_source),
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    sid,
                    pid,
                    p.get("current_price"),
                    p.get("list_price") or p.get("was_price"),
                    p.get("was_price") or p.get("list_price"),
                    _tri(p.get("pickup_available")),
                    _tri(p.get("out_of_stock")),
                    _tri(p.get("in_store")),
                    p.get("seller_name"),
                    p.get("offer_type"),
                    p.get("availability"),
                    p.get("stock_status"),
                    p.get("query"),
                    p.get("collection_source"),
                    json.dumps(p, default=str)[:8000],
                    now,
                    now,
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def create_scan_run(
    store_id: str,
    zip_code: Optional[str],
    wave: str,
    jobs_total: int,
) -> int:
    conn = get_inventory_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO inv_scan_runs (
                store_id, zip, status, wave, jobs_total, jobs_done, jobs_failed,
                sku_count, inventory_count, api_calls, notes, created_at
            ) VALUES (?, ?, 'queued', ?, ?, 0, 0, 0, 0, 0, '', ?)
            """,
            (str(store_id), zip_code, wave, int(jobs_total), _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def enqueue_scan_jobs(scan_id: int, store_id: str, jobs: List[Dict[str, Any]]) -> int:
    """Insert jobs; skip duplicates (UNIQUE scan_id+query+page). Returns inserted count."""
    conn = get_inventory_db()
    inserted = 0
    try:
        for job in jobs:
            q = str(job.get("query") or "").strip()
            page = int(job.get("page") or 1)
            if not q:
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO inv_scan_jobs (
                        scan_id, store_id, query, page, status
                    ) VALUES (?, ?, ?, ?, 'queued')
                    """,
                    (int(scan_id), str(store_id), q, page),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Idempotent: already enqueued for this scan
                continue
        conn.commit()
    finally:
        conn.close()
    return inserted


def update_scan_run(scan_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status",
        "jobs_total",
        "jobs_done",
        "jobs_failed",
        "sku_count",
        "inventory_count",
        "api_calls",
        "notes",
        "error",
        "started_at",
        "finished_at",
    }
    sets = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(int(scan_id))
    conn = get_inventory_db()
    try:
        conn.execute(
            f"UPDATE inv_scan_runs SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def update_scan_job(job_id: int, **fields: Any) -> None:
    allowed = {
        "status",
        "products_found",
        "products_upserted",
        "error",
        "started_at",
        "finished_at",
    }
    sets = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(int(job_id))
    conn = get_inventory_db()
    try:
        conn.execute(
            f"UPDATE inv_scan_jobs SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def list_queued_jobs(scan_id: int) -> List[Dict[str, Any]]:
    conn = get_inventory_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM inv_scan_jobs
            WHERE scan_id = ? AND status = 'queued'
            ORDER BY id ASC
            """,
            (int(scan_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_scan_run(scan_id: int) -> Optional[Dict[str, Any]]:
    conn = get_inventory_db()
    try:
        row = conn.execute(
            "SELECT * FROM inv_scan_runs WHERE id = ?", (int(scan_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_scan_runs(
    store_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    conn = get_inventory_db()
    try:
        if store_id:
            rows = conn.execute(
                """
                SELECT * FROM inv_scan_runs
                WHERE store_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (str(store_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM inv_scan_runs
                ORDER BY id DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def inventory_counts(store_id: str) -> Dict[str, Any]:
    sid = str(store_id)
    conn = get_inventory_db()
    try:
        inv = conn.execute(
            "SELECT COUNT(*) AS n FROM inv_store_inventory WHERE store_id = ?",
            (sid,),
        ).fetchone()
        with_was = conn.execute(
            """
            SELECT COUNT(*) AS n FROM inv_store_inventory
            WHERE store_id = ?
              AND was_price IS NOT NULL
              AND current_price IS NOT NULL
              AND was_price > current_price
            """,
            (sid,),
        ).fetchone()
        pickup = conn.execute(
            """
            SELECT COUNT(*) AS n FROM inv_store_inventory
            WHERE store_id = ? AND pickup_available = 1
            """,
            (sid,),
        ).fetchone()
        last = conn.execute(
            """
            SELECT MAX(updated_at) AS ts FROM inv_store_inventory WHERE store_id = ?
            """,
            (sid,),
        ).fetchone()
        last_ok = conn.execute(
            """
            SELECT id, status, finished_at, inventory_count, sku_count, wave
            FROM inv_scan_runs
            WHERE store_id = ? AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (sid,),
        ).fetchone()
        active = conn.execute(
            """
            SELECT id, status, jobs_done, jobs_total, started_at
            FROM inv_scan_runs
            WHERE store_id = ? AND status IN ('queued', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (sid,),
        ).fetchone()
        by_query = conn.execute(
            """
            SELECT COALESCE(query, '(unknown)') AS q, COUNT(*) AS n
            FROM inv_store_inventory
            WHERE store_id = ?
            GROUP BY COALESCE(query, '(unknown)')
            ORDER BY n DESC
            LIMIT 30
            """,
            (sid,),
        ).fetchall()
        return {
            "store_id": sid,
            "inventory_count": int(inv["n"] if inv else 0),
            "markdown_candidates": int(with_was["n"] if with_was else 0),
            "pickup_true_count": int(pickup["n"] if pickup else 0),
            "last_inventory_update": last["ts"] if last else None,
            "last_successful_scan": dict(last_ok) if last_ok else None,
            "active_scan": dict(active) if active else None,
            "top_queries": [{"query": r["q"], "count": int(r["n"])} for r in by_query],
        }
    finally:
        conn.close()


def list_store_product_ids(
    store_id: str,
    *,
    limit: int = 100,
    prefer_markdown: bool = True,
) -> List[str]:
    """Product IDs for wave D re-check (prefer items with was/list first)."""
    sid = str(store_id)
    lim = max(1, min(500, int(limit)))
    conn = get_inventory_db()
    try:
        if prefer_markdown:
            rows = conn.execute(
                """
                SELECT product_id FROM inv_store_inventory
                WHERE store_id = ?
                ORDER BY
                  CASE
                    WHEN was_price IS NOT NULL AND current_price IS NOT NULL
                         AND was_price > current_price THEN 0
                    ELSE 1
                  END,
                  updated_at ASC
                LIMIT ?
                """,
                (sid, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT product_id FROM inv_store_inventory
                WHERE store_id = ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (sid, lim),
            ).fetchall()
        return [str(r["product_id"]) for r in rows if r["product_id"]]
    finally:
        conn.close()


def coverage_report(store_id: str) -> Dict[str, Any]:
    """SKU coverage summary for Milestone 3 deliverable."""
    counts = inventory_counts(store_id)
    sid = str(store_id)
    conn = get_inventory_db()
    try:
        sources = conn.execute(
            """
            SELECT COALESCE(collection_source, '(unknown)') AS src, COUNT(*) AS n
            FROM inv_store_inventory
            WHERE store_id = ?
            GROUP BY COALESCE(collection_source, '(unknown)')
            ORDER BY n DESC
            """,
            (sid,),
        ).fetchall()
        deep = conn.execute(
            """
            SELECT COUNT(*) AS n FROM inv_store_inventory
            WHERE store_id = ?
              AND was_price IS NOT NULL AND current_price IS NOT NULL
              AND was_price > current_price
              AND ((was_price - current_price) * 100.0 / was_price) >= 40
            """,
            (sid,),
        ).fetchone()
        scans = conn.execute(
            """
            SELECT id, wave, status, inventory_count, api_calls, started_at, finished_at
            FROM inv_scan_runs
            WHERE store_id = ?
            ORDER BY id DESC LIMIT 10
            """,
            (sid,),
        ).fetchall()
        return {
            "store_id": sid,
            "inventory_count": counts["inventory_count"],
            "markdown_candidates": counts["markdown_candidates"],
            "markdown_ge40_pct": int(deep["n"] if deep else 0),
            "pickup_true_count": counts["pickup_true_count"],
            "last_inventory_update": counts["last_inventory_update"],
            "last_successful_scan": counts["last_successful_scan"],
            "by_collection_source": [
                {"source": r["src"], "count": int(r["n"])} for r in sources
            ],
            "top_queries": counts.get("top_queries") or [],
            "recent_scans": [dict(r) for r in scans],
        }
    finally:
        conn.close()


def scan_job_stats(scan_id: int) -> Dict[str, int]:
    conn = get_inventory_db()
    try:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM inv_scan_jobs
            WHERE scan_id = ? GROUP BY status
            """,
            (int(scan_id),),
        ).fetchall()
        out = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out
    finally:
        conn.close()
