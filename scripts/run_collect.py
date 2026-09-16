"""CLI helper: run live Walmart collection in its own process (Playwright-safe)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from collector import collect_store_products  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "mode": "error", "products": [], "notes": "store_id required"}))
        return 1
    store_id = sys.argv[1]
    result = collect_store_products(store_id)
    # LiveProduct dataclasses are not JSON serializable
    products = []
    for p in result.get("products") or []:
        products.append(
            {
                "product_id": p.product_id,
                "title": p.title,
                "brand": p.brand,
                "category": p.category,
                "current_price": p.current_price,
                "list_price": p.list_price,
                "url": p.url,
                "image_url": p.image_url,
            }
        )
    out = {
        "ok": result.get("ok"),
        "mode": result.get("mode"),
        "notes": result.get("notes"),
        "scraped_at": result.get("scraped_at"),
        "products": products,
    }
    print(json.dumps(out))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
