"""Pre-host smoke: slider, backends, two ZIPs."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip().strip('"').strip("'")

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")


def main() -> None:
    # wait for server
    for _ in range(30):
        try:
            r = httpx.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("server not up")

    b = httpx.get(f"{BASE}/api/backends", timeout=15).json()
    print("backends", b.get("ready"), b.get("backends"))
    assert b.get("ready"), "Oxylabs/live backend not ready"

    s1 = httpx.get(f"{BASE}/api/stores", params={"zip": "70310", "limit": 5}, timeout=30).json()
    s2 = httpx.get(f"{BASE}/api/stores", params={"zip": "90210", "limit": 5}, timeout=30).json()
    sid1 = str(s1["stores"][0]["store_id"])
    sid2 = str(s2["stores"][0]["store_id"])
    print("store70310", sid1, s1["stores"][0].get("city"))
    print("store90210", sid2, s2["stores"][0].get("city"))
    assert sid1 != sid2 or s1["stores"][0].get("city") != s2["stores"][0].get("city")

    d20 = httpx.get(
        f"{BASE}/api/deals",
        params={"zip": "70310", "store_id": sid1, "min_discount_pct": 20},
        timeout=150,
    ).json()
    m20 = d20.get("meta") or {}
    deals20 = d20.get("deals") or []
    print("deals20", m20.get("data_mode"), m20.get("live_ok"), len(deals20), [x.get("discount_pct") for x in deals20[:5]])
    assert m20.get("live_ok") is True, m20.get("user_error")
    assert all(float(x.get("discount_pct") or 0) >= 20 for x in deals20)

    d70 = httpx.get(
        f"{BASE}/api/deals",
        params={"zip": "70310", "store_id": sid1, "min_discount_pct": 70},
        timeout=150,
    ).json()
    deals70 = d70.get("deals") or []
    print("deals70", d70.get("meta", {}).get("data_mode"), len(deals70))
    assert all(float(x.get("discount_pct") or 0) >= 70 for x in deals70)

    d2 = httpx.get(
        f"{BASE}/api/deals",
        params={"zip": "90210", "store_id": sid2, "min_discount_pct": 20},
        timeout=150,
    ).json()
    m2 = d2.get("meta") or {}
    deals2 = d2.get("deals") or []
    print("deals90210", sid2, m2.get("data_mode"), m2.get("live_ok"), len(deals2))
    assert m2.get("live_ok") is True, m2.get("user_error")

    ids1 = {str(x.get("product_id")) for x in deals20}
    ids2 = {str(x.get("product_id")) for x in deals2}
    if ids1 and ids2:
        ov = len(ids1 & ids2) / max(len(ids1 | ids2), 1)
        print("deal_overlap", round(ov, 3))
        assert ov < 0.85, f"deal lists too similar: {ov}"
    else:
        print("deal_overlap skipped (one side empty after filter — OK if slider strict)")

    print("SMOKE_OK")


if __name__ == "__main__":
    main()
