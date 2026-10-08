"""
Milestone 6 — Discord alerts when deep markdown (≥70%) appears at watched stores.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

from inventory_deals import deals_from_inventory
from inventory_scheduler import watched_stores


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _state_path() -> Path:
    try:
        from config import writable_data_dir

        return writable_data_dir() / "deep_alerts_sent.json"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data" / "deep_alerts_sent.json"


def _load_sent() -> Dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"day": "", "keys": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"day": "", "keys": []}


def _save_sent(day: str, keys: Set[str]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"day": day, "keys": sorted(keys)[-500:]}, indent=2),
        encoding="utf-8",
    )


def check_and_alert_deep_markdowns() -> Dict[str, Any]:
    if os.environ.get("INVENTORY_DEEP_ALERTS", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return {"ok": True, "skipped": True, "reason": "disabled"}

    threshold = _env_float(
        "INVENTORY_DEEP_ALERT_PCT",
        _env_float("DEAL_HIDDEN_CLEARANCE_PCT", 70.0),
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = _load_sent()
    sent: Set[str] = set(state.get("keys") or []) if state.get("day") == day else set()

    found: List[Dict[str, Any]] = []
    newly: List[Dict[str, Any]] = []

    for s in watched_stores():
        sid = s["store_id"]
        try:
            info = deals_from_inventory(sid, min_discount_pct=threshold)
        except Exception:
            continue
        for d in info.get("deals") or []:
            pct = float(d.get("discount_pct") or 0)
            if pct < threshold:
                continue
            if d.get("pickup_available") is not True:
                continue
            key = f"{sid}:{d.get('product_id')}"
            row = {
                "store_id": sid,
                "product_id": d.get("product_id"),
                "title": d.get("title"),
                "discount_pct": pct,
                "current_price": d.get("current_price"),
                "list_price": d.get("list_price"),
                "url": d.get("url"),
            }
            found.append(row)
            if key not in sent:
                newly.append(row)
                sent.add(key)

    if newly:
        try:
            from discord_notify import notify_deep_markdowns

            notify_deep_markdowns(newly, threshold=threshold)
        except Exception:
            pass
        _save_sent(day, sent)

    return {
        "ok": True,
        "threshold_pct": threshold,
        "found": len(found),
        "newly_alerted": len(newly),
        "sample": newly[:5],
    }
