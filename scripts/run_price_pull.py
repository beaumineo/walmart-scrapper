"""CLI: run one Milestone 2 price pull (Playwright main-thread safe)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from collector import collect_store_prices  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("store_id")
    parser.add_argument("--zip", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    pull = collect_store_prices(
        store_id=args.store_id,
        zip_code=args.zip or None,
        force=args.force,
    )
    print(json.dumps(pull.to_dict()))
    return 0 if pull.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
