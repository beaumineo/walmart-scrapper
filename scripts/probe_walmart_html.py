import json
import re
import urllib.request

req = urllib.request.Request(
    "https://www.walmart.com/store/finder?location=90210",
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    },
)
html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
print("html_len", len(html))
print("has_px", "px-captcha" in html.lower() or "PerimeterX" in html)
print("store_mentions", len(re.findall(r"store", html, re.I)))

m = re.search(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
)
if m:
    raw = m.group(1)
    print("NEXT_DATA len", len(raw))
    data = json.loads(raw)
    print("keys", list(data.keys())[:20])
    print(json.dumps(data, indent=2)[:2000])
else:
    print("NO_NEXT_DATA")
    print(html[:2000])

# Also try common API path seen in older walmart apps
candidates = [
    "https://www.walmart.com/orchestra/home/graphql",
    "https://www.walmart.com/store/electrode/api/finder?singleLineAddr=90210&distance=50",
    "https://www.walmart.com/store/finder/electrode/api/stores?singleLineAddr=90210%2C%20CA&distance=25",
]
for url in candidates:
    try:
        r = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://www.walmart.com/store/finder?location=90210",
            },
        )
        resp = urllib.request.urlopen(r, timeout=20)
        body = resp.read()
        print("OK", resp.status, url, "len", len(body), body[:200])
    except Exception as e:
        code = getattr(getattr(e, "code", None), "real", None) or getattr(e, "code", None)
        print("FAIL", code, url, type(e).__name__, e)
