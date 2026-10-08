"""
Discord webhook notifier for Phase 2 status updates (Milestone 1 sharing channel).

Set DISCORD_WEBHOOK_URL in env. If unset, notify() is a no-op.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def discord_configured() -> bool:
    return bool(os.environ.get("DISCORD_WEBHOOK_URL", "").strip())


def notify(
    content: str,
    *,
    embeds: Optional[list] = None,
    username: str = "Hidden Clearances · Phase 2",
) -> Dict[str, Any]:
    """Post a message to the configured Discord webhook. Never raises."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return {"ok": False, "skipped": True, "reason": "DISCORD_WEBHOOK_URL not set"}

    payload: Dict[str, Any] = {
        "username": username,
        "content": (content or "")[:1900],
    }
    if embeds:
        payload["embeds"] = embeds[:10]

    try:
        import requests

        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "status": resp.status_code,
                "error": resp.text[:240],
            }
        return {"ok": True, "status": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def notify_scan_finished(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Embed for inventory scan completion."""
    status = str(scan.get("status") or "?")
    store = scan.get("store_id")
    wave = scan.get("wave")
    inv = scan.get("inventory_count")
    done = scan.get("jobs_done")
    total = scan.get("jobs_total")
    failed = scan.get("jobs_failed")
    emoji = "✅" if status == "completed" else "⚠️"
    content = (
        f"{emoji} **Inventory scan {status}** · store `{store}` · wave `{wave}`\n"
        f"SKUs: **{inv}** · jobs {done}/{total} (failed {failed})"
    )
    embed = {
        "title": f"Scan #{scan.get('scan_id')} · {status}",
        "color": 0x2ECC71 if status == "completed" else 0xE67E22,
        "fields": [
            {"name": "Store", "value": str(store), "inline": True},
            {"name": "Wave", "value": str(wave), "inline": True},
            {"name": "Inventory", "value": str(inv), "inline": True},
            {
                "name": "API calls",
                "value": str(scan.get("api_calls")),
                "inline": True,
            },
        ],
    }
    if scan.get("error"):
        embed["fields"].append(
            {"name": "Error", "value": str(scan["error"])[:500], "inline": False}
        )
    return notify(content, embeds=[embed])


def notify_overlap_report(report: Dict[str, Any]) -> Dict[str, Any]:
    a = report.get("store_a")
    b = report.get("store_b")
    deal_ov = report.get("deal_overlap_pct")
    pickup_ov = report.get("pickup_overlap_pct")
    content = (
        f"📊 **Store overlap** `{a}` vs `{b}`\n"
        f"Pickup-confirmed overlap: **{pickup_ov}%** · "
        f"Deal-list overlap: **{deal_ov}%**"
    )
    return notify(content)


def notify_milestone(text: str) -> Dict[str, Any]:
    return notify(f"📌 {text}")
