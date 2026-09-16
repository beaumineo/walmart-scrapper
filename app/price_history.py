"""Price history persistence (triposat-style time series, SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from store_db import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_price_history_schema() -> None:
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            product_id TEXT NOT NULL,
            title TEXT,
            current_price REAL,
            was_price REAL,
            list_price REAL,
            offer_type TEXT,
            availability TEXT,
            raw_json TEXT,
            scraped_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_hist_prod ON price_history(store_id, product_id, scraped_at)"
    )
    conn.commit()
    conn.close()


def save_price_history(
    store_id: Optional[str],
    products: List[Dict[str, Any]],
    scraped_at: Optional[str] = None,
) -> int:
    ensure_price_history_schema()
    scraped_at = scraped_at or _now()
    conn = get_db()
    n = 0
    for p in products:
        conn.execute(
            """
            INSERT INTO price_history (
                store_id, product_id, title, current_price, was_price, list_price,
                offer_type, availability, raw_json, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_id,
                str(p.get("product_id") or ""),
                p.get("title"),
                p.get("current_price"),
                p.get("was_price"),
                p.get("list_price"),
                p.get("offer_type"),
                p.get("availability"),
                json.dumps(p),
                scraped_at,
            ),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def recent_prices(product_id: str, store_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_price_history_schema()
    conn = get_db()
    if store_id:
        rows = conn.execute(
            """
            SELECT * FROM price_history
            WHERE product_id = ? AND store_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (product_id, store_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM price_history
            WHERE product_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (product_id, limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    conn.close()
    return out
