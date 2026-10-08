#!/usr/bin/env python3
"""Phase 2 M1 — run a pilot inventory seed scan into the DB.

Usage (from repo root):
  set PYTHONPATH=app
  python scripts/run_inventory_scan.py --store-id 5686 --zip 90210 --max-queries 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# Load .env if present
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 2 M1 inventory seed scan")
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--zip", default=None)
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--sync", action="store_true", help="Run in foreground (no thread)")
    args = parser.parse_args()

    from inventory_scan import get_scan_status, start_inventory_scan

    result = start_inventory_scan(
        store_id=args.store_id,
        zip_code=args.zip,
        wave="seed",
        store_meta={"store_id": args.store_id, "zip": args.zip},
        max_queries=args.max_queries,
        pages_per_query=args.pages,
        background=not args.sync,
    )
    print(json.dumps(result, indent=2, default=str))
    if not result.get("ok"):
        return 1

    scan_id = result["scan"]["scan_id"]
    if args.sync:
        return 0 if result["scan"].get("status") == "completed" else 2

    # Poll until done
    for _ in range(180):
        time.sleep(2)
        st = get_scan_status(scan_id)
        print(
            f"status={st['status']} progress={st['progress_pct']}% "
            f"jobs={st['jobs_done']}/{st['jobs_total']} "
            f"inventory={st['inventory_count']} api={st['api_calls']}"
        )
        if st["status"] in ("completed", "failed"):
            print(json.dumps(st, indent=2, default=str))
            return 0 if st["status"] == "completed" else 2
    print("timeout waiting for scan")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
