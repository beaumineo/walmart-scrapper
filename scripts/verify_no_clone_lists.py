"""Verify poisoned caches are gone and stores no longer share deal lists."""
import sys

sys.path.insert(0, "app")
from collector import load_cached_live_pull
from walmart_core import build_report

for sid in ["5930", "3650", "542"]:
    cached = load_cached_live_pull(sid)
    print(
        "cache",
        sid,
        "None" if cached is None else f"n={len(cached.products)} engine={cached.engine}",
    )

sets = {}
for sid, z in [("5930", "90210"), ("3650", "90210"), ("542", "70310")]:
    r = build_report(z, sid, prefer_live=True)
    meta = r.get("meta") or {}
    deals = r.get("deals") or []
    pids = tuple(sorted(str(d.get("product_id") or "") for d in deals))
    sets[sid] = set(pids)
    print(
        "report",
        sid,
        "mode",
        meta.get("data_mode"),
        "deals",
        len(deals),
        "err",
        (meta.get("user_error") or "")[:80],
        "notes",
        str(meta.get("collector_notes") or "")[:160],
    )

sids = list(sets)
for i, a in enumerate(sids):
    for b in sids[i + 1 :]:
        shared = sets[a] & sets[b]
        print(f"overlap {a}-{b}: shared={len(shared)} (should be 0 if empty/failed)")
