"""Probe clearance deals page parse via Bright Data ISP."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from config import get_collector_config  # noqa: E402
from walmart_parse import is_challenge_html, parse_products_from_html  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> None:
    cfg = get_collector_config()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=cfg.first_playwright_proxy())
        page = browser.new_context(locale="en-US").new_page()
        page.goto(
            "https://www.walmart.com/shop/deals/clearance",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(5000)
        html = page.content()
        print("challenge", is_challenge_html(html, page.url), "len", len(html))
        products = parse_products_from_html(html, query="clearance", limit=20)
        print("parsed", len(products))
        if products:
            print(json.dumps(products[0], indent=2)[:800])
        else:
            m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.S,
            )
            print("has __NEXT_DATA__", bool(m), "size", len(m.group(1)) if m else 0)
        browser.close()


if __name__ == "__main__":
    main()
