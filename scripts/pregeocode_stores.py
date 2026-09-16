"""Pre-geocode Walmart store street addresses into the local cache.

Usage:
  python scripts/pregeocode_stores.py --limit 100
  python scripts/pregeocode_stores.py --zip 90810 --radius 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from store_db import (  # noqa: E402
    find_official_stores_near,
    geocode_store_address,
    load_official_stores,
    _load_geo_cache,
    _save_geo_cache,
)
from walmart_core import resolve_zip  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--zip", type=str, default="")
    parser.add_argument("--radius", type=float, default=40.0)
    args = parser.parse_args()

    cache = _load_geo_cache()
    targets = []

    if args.zip:
        loc = resolve_zip(args.zip)
        targets = find_official_stores_near(
            loc["lat"],
            loc["lon"],
            args.radius,
            limit=args.limit,
            refine_geocode=False,
        )
    else:
        for s in load_official_stores():
            if s.get("coord_precision") != "street_geocode":
                targets.append(s)
            if len(targets) >= args.limit:
                break

    print(f"geocoding up to {len(targets)} stores...")
    ok = 0
    fail = 0
    for i, s in enumerate(targets, 1):
        sid = str(s["store_id"])
        if cache.get(sid, {}).get("lat") is not None:
            ok += 1
            continue
        precise = geocode_store_address(
            sid,
            s.get("address") or "",
            s.get("city") or "",
            s.get("state") or "",
            s.get("zip") or "",
            cache=cache,
        )
        if precise:
            ok += 1
            print(f"[{i}/{len(targets)}] OK #{sid} {s.get('address')}")
        else:
            fail += 1
            print(f"[{i}/{len(targets)}] FAIL #{sid} {s.get('address')}")
        if i % 10 == 0:
            _save_geo_cache(cache)

    _save_geo_cache(cache)
    print(f"done ok={ok} fail={fail} cache={len(cache)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
