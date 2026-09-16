from __future__ import annotations

"""Scan persistence helpers for deal reports."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from store_db import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_scan(
    store_id: str,
    zip_code: str,
    mode: str,
    deals: List[Dict[str, Any]],
    products_count: int = 0,
    notes: str = "",
) -> int:
    conn = get_db()
    started = _now()
    cur = conn.execute(
        """
        INSERT INTO scans (store_id, zip, mode, item_count, deal_count, started_at, finished_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            store_id,
            zip_code,
            mode,
            products_count or len(deals),
            len(deals),
            started,
            started,
            notes[:500] if notes else "",
        ),
    )
    scan_id = int(cur.lastrowid)

    for d in deals:
        conn.execute(
            """
            INSERT INTO products (
                store_id, product_id, title, brand, category,
                current_price, list_price, url, image_url, raw_json, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_id,
                str(d.get("deal_id") or d.get("product_id") or ""),
                d.get("title") or "",
                d.get("brand"),
                d.get("category"),
                d.get("current_price"),
                d.get("list_price"),
                d.get("url"),
                d.get("image_url"),
                json.dumps(d),
                started,
            ),
        )
    conn.commit()
    conn.close()
    return scan_id


def list_scans(store_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db()
    if store_id:
        rows = conn.execute(
            """
            SELECT id, store_id, zip, mode, item_count, deal_count, started_at, finished_at, notes
            FROM scans WHERE store_id = ? ORDER BY id DESC LIMIT ?
            """,
            (store_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, store_id, zip, mode, item_count, deal_count, started_at, finished_at, notes
            FROM scans ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = [dict(r) for r in rows]
    conn.close()
    return out


def get_scan_deals(scan_id: int) -> Dict[str, Any]:
    conn = get_db()
    scan = conn.execute(
        "SELECT * FROM scans WHERE id = ?", (scan_id,)
    ).fetchone()
    if not scan:
        conn.close()
        raise ValueError(f"Scan {scan_id} not found")
    # products saved around same timestamp
    rows = conn.execute(
        """
        SELECT title, brand, category, current_price, list_price, url, image_url, raw_json, scraped_at
        FROM products
        WHERE store_id = ? AND scraped_at = ?
        ORDER BY id ASC
        """,
        (scan["store_id"], scan["started_at"]),
    ).fetchall()
    deals = []
    for r in rows:
        if r["raw_json"]:
            try:
                deals.append(json.loads(r["raw_json"]))
                continue
            except Exception:
                pass
        deals.append(dict(r))
    out = {"scan": dict(scan), "deals": deals}
    conn.close()
    return out
