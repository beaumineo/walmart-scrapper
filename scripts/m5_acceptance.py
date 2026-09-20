"""Milestone 5 — multi-region acceptance against Milestone 0 checklist."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("WALMART_COLLECT_INLINE", "1")

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

# Agreed test ZIPs (Milestone 0) + extra regions
TEST_ZIPS = [
    ("90210", "CA"),
    ("10001", "NY"),
    ("75201", "TX"),
    ("30301", "GA"),
    ("90810", "CA"),
    ("60601", "IL"),
    ("98101", "WA"),
]

c = TestClient(app)


def main() -> int:
    results = []
    t0 = time.time()

    h = c.get("/health").json()
    assert h.get("ok") is True
    assert h.get("milestone") == 5
    assert 5 in h.get("milestones_complete", [])
    results.append({"check": "health_milestone_5", "ok": True, "detail": h.get("version")})

    cfg = c.get("/api/deals/config").json()
    assert "thresholds" in cfg
    results.append({"check": "deals_config", "ok": True})

    # Criterion 1: any US ZIP → stores
    first_stores = {}
    for zip_code, region in TEST_ZIPS:
        s = c.get("/api/stores", params={"zip": zip_code, "limit": 5}).json()
        n = int(s.get("count") or 0)
        ok = n >= 1
        first = (s.get("stores") or [None])[0]
        first_stores[zip_code] = first
        results.append(
            {
                "check": "stores_for_zip",
                "zip": zip_code,
                "region": region,
                "ok": ok,
                "count": n,
                "store_id": first.get("store_id") if first else None,
            }
        )
        if not ok:
            print(json.dumps({"fail": "stores", "zip": zip_code}, indent=2))
            return 1

    # Criterion 2+3: store-scoped live deals (two CA stores) + why_deal
    z = "90210"
    s = c.get("/api/stores", params={"zip": z, "limit": 8}).json()
    ids = [x["store_id"] for x in s["stores"][:2]]
    pulls = {}
    for sid in ids:
        d = c.get(
            "/api/deals",
            params={"zip": z, "store_id": sid, "min_discount_pct": 20, "mode": "live"},
        ).json()
        deals = d.get("deals") or []
        meta = d.get("meta") or {}
        pids = tuple(sorted(str(x.get("product_id")) for x in deals[:25]))
        pulls[sid] = {
            "live_ok": meta.get("live_ok") is True,
            "mode": meta.get("data_mode"),
            "count": (d.get("summary") or {}).get("deal_count"),
            "pids": pids,
            "why": all(bool(x.get("why_deal")) for x in deals) if deals else True,
            "rank": all(("rank_score" in x) for x in deals) if deals else True,
        }
        results.append({"check": "live_deals", "store_id": sid, **pulls[sid]})

    if len(pulls) >= 2:
        a, b = ids[0], ids[1]
        same = bool(pulls[a]["pids"]) and pulls[a]["pids"] == pulls[b]["pids"]
        results.append(
            {
                "check": "no_clone_lists",
                "ok": not same,
                "store_a": a,
                "store_b": b,
            }
        )
        if same:
            print(json.dumps({"fail": "clone_lists", "results": results}, indent=2))
            return 1

    # Criterion 4: slider
    sid = ids[0]
    hi = c.get(
        "/api/deals",
        params={"zip": z, "store_id": sid, "min_discount_pct": 70, "mode": "live"},
    ).json()
    lo = c.get(
        "/api/deals",
        params={"zip": z, "store_id": sid, "min_discount_pct": 20, "mode": "live"},
    ).json()
    hi_n = int((hi.get("summary") or {}).get("deal_count") or 0)
    lo_n = int((lo.get("summary") or {}).get("deal_count") or 0)
    slider_ok = hi_n <= lo_n
    results.append(
        {
            "check": "slider_threshold",
            "ok": slider_ok,
            "store_id": sid,
            "ge_70": hi_n,
            "ge_20": lo_n,
        }
    )
    if not slider_ok:
        return 1

    live_any = any(p.get("live_ok") for p in pulls.values())
    results.append({"check": "live_backend", "ok": live_any})

    out = {
        "milestone": 5,
        "elapsed_sec": round(time.time() - t0, 1),
        "pass": live_any and all(
            r.get("ok", True) for r in results if "ok" in r
        ),
        "results": results,
    }
    out_path = ROOT / "docs" / "ACCEPTANCE_M5.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
