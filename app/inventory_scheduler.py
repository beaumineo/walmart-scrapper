"""
Phase 2 Milestone 6 — always-on inventory refresh scheduler.

Runs inside the web process (Railway-friendly). Controlled by env:

  INVENTORY_SCHEDULER_ENABLED=1
  INVENTORY_WATCH_STORES=5686:90210,5930:90210
  INVENTORY_FULL_INTERVAL_HOURS=24   # full / A+B style wave
  INVENTORY_HOT_INTERVAL_HOURS=4    # hot clearance / dept wave
  INVENTORY_FULL_WAVE=full
  INVENTORY_HOT_WAVE=A
  INVENTORY_SCHEDULER_MAX_QUERIES=12
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "enabled": False,
    "running": False,
    "started_at": None,
    "last_tick_at": None,
    "last_full_at": {},
    "last_hot_at": {},
    "last_error": None,
    "ticks": 0,
    "jobs_started": 0,
}
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def watched_stores() -> List[Dict[str, str]]:
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
    try:
        from inventory_db import get_inventory_db

        conn = get_inventory_db()
        try:
            rows = conn.execute(
                "SELECT store_id, zip FROM inv_stores ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
            return [
                {"store_id": str(r["store_id"]), "zip": str(r["zip"] or "")}
                for r in rows
            ]
        finally:
            conn.close()
    except Exception:
        return []


def scheduler_status() -> Dict[str, Any]:
    with _lock:
        return {
            **dict(_state),
            "watch_stores": watched_stores(),
            "full_interval_hours": _env_float("INVENTORY_FULL_INTERVAL_HOURS", 24),
            "hot_interval_hours": _env_float("INVENTORY_HOT_INTERVAL_HOURS", 4),
            "full_wave": os.environ.get("INVENTORY_FULL_WAVE", "full"),
            "hot_wave": os.environ.get("INVENTORY_HOT_WAVE", "A"),
            "enabled_env": _env_bool("INVENTORY_SCHEDULER_ENABLED", False),
        }


def _due(last_map: Dict[str, float], store_id: str, interval_hours: float) -> bool:
    last = last_map.get(store_id)
    if last is None:
        return True
    return (time.time() - last) >= max(0.25, interval_hours) * 3600


def _start_scan(store_id: str, zip_code: str, wave: str) -> Dict[str, Any]:
    from inventory_scan import start_inventory_scan

    max_q = _env_int("INVENTORY_SCHEDULER_MAX_QUERIES", 12)
    return start_inventory_scan(
        store_id=store_id,
        zip_code=zip_code or None,
        wave=wave,
        max_queries=max_q,
        pages_per_query=1,
        recheck_limit=_env_int("INVENTORY_RECHECK_MAX", 20),
        background=True,
    )


def _tick() -> None:
    stores = watched_stores()
    if not stores:
        return

    full_h = _env_float("INVENTORY_FULL_INTERVAL_HOURS", 24)
    hot_h = _env_float("INVENTORY_HOT_INTERVAL_HOURS", 4)
    full_wave = os.environ.get("INVENTORY_FULL_WAVE", "full").strip() or "full"
    hot_wave = os.environ.get("INVENTORY_HOT_WAVE", "A").strip() or "A"

    with _lock:
        _state["last_tick_at"] = datetime.now(timezone.utc).isoformat()
        _state["ticks"] = int(_state.get("ticks") or 0) + 1
        last_full = dict(_state.get("last_full_at") or {})
        last_hot = dict(_state.get("last_hot_at") or {})

    for s in stores:
        sid = s["store_id"]
        z = s.get("zip") or ""
        try:
            if _due(last_full, sid, full_h):
                result = _start_scan(sid, z, full_wave)
                with _lock:
                    _state["last_full_at"][sid] = time.time()
                    _state["jobs_started"] = int(_state.get("jobs_started") or 0) + 1
                    _state["last_error"] = None
                # Don't also hot-scan same tick
                continue
            if _due(last_hot, sid, hot_h):
                result = _start_scan(sid, z, hot_wave)
                with _lock:
                    _state["last_hot_at"][sid] = time.time()
                    _state["jobs_started"] = int(_state.get("jobs_started") or 0) + 1
                    _state["last_error"] = None
                _ = result
        except Exception as e:
            msg = f"{sid}: {type(e).__name__}: {e}"
            with _lock:
                _state["last_error"] = msg[:500]
            # Auth failures → Discord once per tick
            if "auth" in str(e).lower() or "401" in str(e):
                try:
                    from discord_notify import notify_auth_failure

                    notify_auth_failure(str(e))
                except Exception:
                    pass


def _loop() -> None:
    # First tick after a short delay so boot isn't blocked
    _stop.wait(20)
    while not _stop.is_set():
        try:
            _tick()
            try:
                from deep_alerts import check_and_alert_deep_markdowns

                check_and_alert_deep_markdowns()
            except Exception:
                pass
            try:
                from ops_status import daily_api_burn
                from discord_notify import notify_auth_failure

                burn = daily_api_burn()
                if burn.get("auth_failures"):
                    notify_auth_failure(
                        f"{burn['auth_failures']} auth failure(s) in inv_scan_runs today"
                    )
            except Exception:
                pass
        except Exception as e:
            with _lock:
                _state["last_error"] = f"tick: {type(e).__name__}: {e}"[:500]
        # Re-check due windows every ~5 minutes
        for _ in range(5):
            if _stop.is_set():
                break
            time.sleep(60)
    with _lock:
        _state["running"] = False


def start_scheduler() -> Dict[str, Any]:
    """Idempotent start when INVENTORY_SCHEDULER_ENABLED=1."""
    global _thread
    enabled = _env_bool("INVENTORY_SCHEDULER_ENABLED", False)
    with _lock:
        _state["enabled"] = enabled
        if not enabled:
            return {"ok": False, "reason": "INVENTORY_SCHEDULER_ENABLED not set"}
        if _state.get("running") and _thread and _thread.is_alive():
            return {"ok": True, "already_running": True}
        _stop.clear()
        _thread = threading.Thread(
            target=_loop, name="inv-scheduler", daemon=True
        )
        _state["running"] = True
        _state["started_at"] = datetime.now(timezone.utc).isoformat()
        _thread.start()
    return {"ok": True, "started": True}


def stop_scheduler() -> None:
    _stop.set()
    with _lock:
        _state["running"] = False


def run_scheduler_once() -> Dict[str, Any]:
    """Manual tick (ops / CLI)."""
    _tick()
    return scheduler_status()
