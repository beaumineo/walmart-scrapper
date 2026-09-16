"""Probe whether a real browser can load Walmart search/store pages."""
from __future__ import annotations

import json
import re
import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    url = "https://www.walmart.com/search?q=clearance&facet=fulfillment_method%3AIn-store"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        title = page.title()
        html = page.content()
        print("title:", title)
        print("len:", len(html))
        print("robot:", "Robot or human" in html or "px-captcha" in html.lower())
        # try __NEXT_DATA__
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if m:
            data = json.loads(m.group(1))
            print("NEXT_DATA keys:", list(data.keys())[:10])
            text = json.dumps(data)
            print("has priceInfo:", "priceInfo" in text or "currentPrice" in text)
            print("snippet:", text[:800])
        else:
            # look for product cards
            cards = page.locator("[data-testid='item-stack'], [data-item-id], a[href*='/ip/']")
            print("card-like count:", cards.count())
            print("body text sample:", page.inner_text("body")[:500])
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERR", type(e).__name__, e)
        sys.exit(1)
