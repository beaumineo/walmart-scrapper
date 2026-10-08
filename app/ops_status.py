"""
Phase 2 Milestone 6 — ops signals (scan age, deals, API burn, auth health).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from inventory_db import get_inventory_db, inventory_counts, list_scan_runs
from inventory_deals import deals_from_inventory


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _age_sec(ts: Optional[str]) -> Optional[int]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))


def _watched_stores() -> List[Dict[str, str]]:
    """INVENTORY_WATCH_STORES=5686:90210,5930:90210"""
    raw = os.environ.get("INVENTORY_WATCH_STORES", "").strip()
    out: List[Dict[str, str]] = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                sid, z = part.split(":", 1)
                out.append({"store_id": sid.strip(), "zip": z.strip()})
            else:
                out.append({"store_id": part, "zip": ""})
        return out
    # Fallback: stores already in inv_stores
    conn = get_inventory_db()
    try:
        rows = conn.execute(
            "SELECT store_id, zip FROM inv_stores ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        return [{"store_id": r["store_id"], "zip": r["zip"] or ""} for r in rows]
    finally:
        conn.close()


def daily_api_burn() -> Dict[str, Any]:
    """Sum inv_scan_runs.api_calls for UTC today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_inventory_db()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(api_calls), 0) AS n, COUNT(*) AS runs
            FROM inv_scan_runs
            WHERE substr(COALESCE(started_at, created_at), 1, 10) = ?
            """,
            (today,),
        ).fetchone()
        auth_fails = conn.execute(
            """
            SELECT COUNT(*) AS n FROM inv_scan_runs
            WHERE substr(COALESCE(started_at, created_at), 1, 10) = ?
              AND (
                error LIKE '%auth%' OR error LIKE '%401%'
                OR notes LIKE '%auth failed%'
              )
            """,
            (today,),
        ).fetchone()
        return {
            "utc_date": today,
            "api_calls": int(row["n"] if row else 0),
            "scan_runs": int(row["runs"] if row else 0),
            "auth_failures": int(auth_fails["n"] if auth_fails else 0),
        }
    finally:
        conn.close()


def store_ops_row(store_id: str, zip_code: str = "") -> Dict[str, Any]:
    counts = inventory_counts(store_id)
    min_pct = _env_float("DEAL_MIN_DISCOUNT_PCT", 20.0)
    deals_info: Dict[str, Any] = {}
    try:
        deals_info = deals_from_inventory(store_id, min_discount_pct=min_pct)
    except Exception as e:
        deals_info = {"error": f"{type(e).__name__}: {e}", "deal_count": 0}

    last_upd = counts.get("last_inventory_update")
    age = _age_sec(last_upd)
    stale_hours = _env_float("INVENTORY_STALE_HOURS", 30.0)
    stale = age is not None and age > stale_hours * 3600

    last_scan = counts.get("last_successful_scan") or {}
    recent = list_scan_runs(store_id=store_id, limit=1)
    last_err = None
    if recent and recent[0].get("status") == "failed":
        last_err = recent[0].get("error")

    return {
        "store_id": str(store_id),
        "zip": zip_code or (get_zip(store_id) or ""),
        "inventory_count": counts.get("inventory_count") or 0,
        "pickup_true_count": counts.get("pickup_true_count") or 0,
        "markdown_candidates": counts.get("markdown_candidates") or 0,
        "deal_count": int(deals_info.get("deal_count") or 0),
        "price_drop_count": int(deals_info.get("price_drop_count") or 0),
        "min_discount_pct": min_pct,
        "last_inventory_update": last_upd,
        "scan_age_sec": age,
        "scan_age_human": _human_age(age),
        "stale": stale,
        "active_scan": counts.get("active_scan"),
        "last_successful_scan": last_scan,
        "last_error": last_err,
    }


def get_zip(store_id: str) -> Optional[str]:
    from inventory_db import get_store

    st = get_store(store_id)
    return (st or {}).get("zip")


def _human_age(age_sec: Optional[int]) -> str:
    if age_sec is None:
        return "never"
    if age_sec < 60:
        return f"{age_sec}s ago"
    if age_sec < 3600:
        return f"{age_sec // 60}m ago"
    if age_sec < 86400:
        return f"{age_sec // 3600}h ago"
    return f"{age_sec // 86400}d ago"


def build_ops_status() -> Dict[str, Any]:
    from discord_notify import discord_configured
    from inventory_scheduler import scheduler_status

    stores = [store_ops_row(s["store_id"], s.get("zip") or "") for s in _watched_stores()]
    burn = daily_api_burn()
    auth_alert = burn["auth_failures"] > 0
    stale_n = sum(1 for s in stores if s.get("stale"))
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase2_milestone": 6,
        "discord_configured": discord_configured(),
        "scheduler": scheduler_status(),
        "daily_api_burn": burn,
        "auth_alert": auth_alert,
        "stale_store_count": stale_n,
        "store_count": len(stores),
        "stores": stores,
        "ui_hint": _ui_hint(stores),
    }


def _ui_hint(stores: List[Dict[str, Any]]) -> Optional[str]:
    if not stores:
        return "No watched stores — set INVENTORY_WATCH_STORES=5686:90210"
    s = stores[0]
    return (
        f"Scanned {s.get('scan_age_human')} | {s.get('inventory_count')} items | "
        f"{s.get('deal_count')} deals >={s.get('min_discount_pct')}%"
    )
