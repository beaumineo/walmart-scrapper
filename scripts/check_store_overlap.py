import json
import sys
from pathlib import Path

sys.path.insert(0, "app")
from walmart_core import build_report

pulls = Path("data/pulls")
for sid in ["3650", "5930", "542"]:
    files = sorted(pulls.glob(f"pull_{sid}_*.json"), reverse=True)
    latest = pulls / f"latest_{sid}.json"
    print("===", sid, "n_files", len(files), "latest", latest.exists())
    path = latest if latest.exists() else (files[0] if files else None)
    if not path:
        continue
    d = json.loads(path.read_text(encoding="utf-8"))
    ids = [p.get("product_id") for p in (d.get("products") or []) if p.get("product_id")]
    print(path.name, "ok", d.get("ok"), "engine", d.get("engine"), "n", len(ids))
    print(" first_ids", ids[:10])

sets = {}
for sid, z in [("5930", "90210"), ("3650", "90210"), ("542", "70310")]:
    r = build_report(z, sid, prefer_live=False)
    titles = tuple(sorted((d.get("title") or "")[:40] for d in (r.get("deals") or [])[:12]))
    pids = tuple(sorted(str(d.get("product_id") or "") for d in (r.get("deals") or [])[:20]))
    sets[sid] = (pids, titles)
    meta = r.get("meta") or {}
    print(
        "REPORT",
        sid,
        "mode",
        meta.get("data_mode"),
        "deals",
        len(r.get("deals") or []),
        "notes",
        str(meta.get("collector_notes") or "")[:120],
    )
    for t in titles[:5]:
        print("  -", t)

sids = list(sets)
for i, a in enumerate(sids):
    for b in sids[i + 1 :]:
        pa, ta = sets[a]
        pb, tb = sets[b]
        ov = len(set(pa) & set(pb)) / max(len(set(pa) | set(pb)), 1)
        print(f"overlap {a}-{b}: {ov:.0%} shared_ids={len(set(pa)&set(pb))}")
