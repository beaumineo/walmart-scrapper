#!/usr/bin/env python3
"""Phase 2 M4 — product pickup verify + optional overlap report.

Usage:
  set PYTHONPATH=app
  python scripts/run_inventory_verify.py --store-id 5686 --max-verify 20
  python scripts/run_inventory_verify.py --overlap 5686 5930
  python scripts/run_inventory_verify.py --store-id 5686 --deals
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="M4 pickup verify / overlap / deals")
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--zip", default=None)
    parser.add_argument("--max-verify", type=int, default=20)
    parser.add_argument("--deals", action="store_true")
    parser.add_argument(
        "--overlap",
        nargs=2,
        metavar=("STORE_A", "STORE_B"),
        help="Compare two stores",
    )
    parser.add_argument("--notify", action="store_true", help="Also post to Discord if configured")
    args = parser.parse_args()

    if args.overlap:
        from inventory_deals import compare_store_overlap

        report = compare_store_overlap(args.overlap[0], args.overlap[1])
        print(json.dumps(report, indent=2, default=str))
        if args.notify:
            from discord_notify import notify_overlap_report

            print("discord:", notify_overlap_report(report))
        return 0 if report.get("anti_clone_ok") else 2

    if not args.store_id:
        parser.error("--store-id required unless --overlap")

    if args.deals:
        from inventory_deals import deals_from_inventory

        result = deals_from_inventory(args.store_id)
        slim = dict(result)
        slim["deals"] = (result.get("deals") or [])[:15]
        print(json.dumps(slim, indent=2, default=str))
        return 0

    from inventory_deals import deals_from_inventory, verify_store_pickup

    before = deals_from_inventory(args.store_id)
    print(
        f"before: inventory={before['inventory_count']} "
        f"pickup={before['pickup_confirmed']} deals={before['deal_count']}"
    )
    result = verify_store_pickup(
        args.store_id,
        zip_code=args.zip,
        max_verify=args.max_verify,
    )
    print(json.dumps(result, indent=2, default=str))
    after = deals_from_inventory(args.store_id)
    print(
        f"after: inventory={after['inventory_count']} "
        f"pickup={after['pickup_confirmed']} deals={after['deal_count']}"
    )
    if args.notify:
        from discord_notify import notify

        notify(
            f"🔍 Pickup verify store `{args.store_id}`: "
            f"checked {result.get('checked')} → "
            f"+pickup_true {result.get('pickup_true')} · "
            f"deals {before['deal_count']}→{after['deal_count']}"
        )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
