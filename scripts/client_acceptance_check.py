"""Client acceptance smoke for Hidden Clearances M1-M3."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("WALMART_COLLECT_INLINE", "1")
sys.path.insert(0, os.path.dirname(__file__) if "__file__" in dir() else ".")

from fastapi.testclient import TestClient
from main import app

c = TestClient(app)


def main() -> int:
    print("=== HEALTH ===")
    h = c.get("/health").json()
    print(
        "milestone",
        h.get("milestone"),
        "complete",
        h.get("milestones_complete"),
        "live_ready",
        h.get("live_ready"),
        "oxylabs",
        (h.get("backends") or {}).get("oxylabs"),
    )

    print("=== ZIP -> stores (test ZIPs) ===")
    for z in ["90210", "10001", "75201", "30301", "90810"]:
        s = c.get("/api/stores", params={"zip": z, "limit": 5}).json()
        n = s.get("count", 0)
        first = (s.get("stores") or [None])[0]
        sid = first["store_id"] if first else None
        name = first.get("name") if first else ""
        print(f"  {z}: {n} stores | first=#{sid} {name}")
        if n < 1:
            print("FAIL: no stores")
            return 1

    print("=== store-scoped live deals ===")
    s = c.get("/api/stores", params={"zip": "90210", "limit": 8}).json()
    ids = [x["store_id"] for x in s["stores"][:2]]
    pulls = {}
    for sid in ids:
        d = c.get(
            "/api/deals",
            params={
                "zip": "90210",
                "store_id": sid,
                "min_discount_pct": 20,
                "mode": "live",
            },
        ).json()
        meta = d.get("meta") or {}
        deals = d.get("deals") or []
        pids = tuple(sorted(str(x.get("product_id")) for x in deals[:20]))
        pulls[sid] = {
            "mode": meta.get("data_mode"),
            "live_ok": meta.get("live_ok"),
            "count": (d.get("summary") or {}).get("deal_count"),
            "pids": pids,
            "why": bool(deals and deals[0].get("why_deal")),
            "rank": bool(deals and ("rank_score" in deals[0])),
            "titles": [str(x.get("title") or "")[:40] for x in deals[:3]],
        }
        print(
            f"  #{sid}: mode={pulls[sid]['mode']} live_ok={pulls[sid]['live_ok']} "
            f"deals={pulls[sid]['count']} why={pulls[sid]['why']} rank={pulls[sid]['rank']}"
        )
        print(f"    titles={pulls[sid]['titles']}")

    if len(pulls) >= 2:
        a, b = ids[0], ids[1]
        same = bool(pulls[a]["pids"]) and pulls[a]["pids"] == pulls[b]["pids"]
        print("  identical product lists across stores?", same, "(want False)")
        if same:
            print("FAIL: clone lists")
            return 1

    print("=== slider threshold ===")
    sid = ids[0]
    hi = c.get(
        "/api/deals",
        params={"zip": "90210", "store_id": sid, "min_discount_pct": 70, "mode": "live"},
    ).json()
    lo = c.get(
        "/api/deals",
        params={"zip": "90210", "store_id": sid, "min_discount_pct": 20, "mode": "live"},
    ).json()
    hi_n = (hi.get("summary") or {}).get("deal_count")
    lo_n = (lo.get("summary") or {}).get("deal_count")
    print(f"  #{sid} >=70%: {hi_n}  >=20%: {lo_n}")
    if (lo.get("meta") or {}).get("live_ok") and hi_n is not None and lo_n is not None:
        if hi_n > lo_n:
            print("FAIL: 70% returned more than 20%")
            return 1

    cfg = c.get("/api/deals/config").json()["thresholds"]
    print(
        "  thresholds",
        {
            k: cfg[k]
            for k in [
                "min_discount_pct",
                "clearance_pct",
                "hidden_clearance_pct",
                "markdown_pct",
            ]
        },
    )

    live_any = any(p["live_ok"] for p in pulls.values())
    print("=== VERDICT ===")
    print("live_ok any store:", live_any)
    print("PASS" if live_any else "FAIL: no live deals")
    return 0 if live_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
