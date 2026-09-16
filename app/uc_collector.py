"""
Local collector via undetected-chromedriver (Akamai-resistant browser).

Best for local/dev when commercial APIs are not configured.
Not suitable for Vercel/serverless (no Chrome).

Repo: https://github.com/ultrafunkamsterdam/undetected-chromedriver
Env: WALMART_UC_ENABLED=1, WALMART_UC_HEADLESS=1
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import CollectorConfig, ProxyConfig, get_collector_config, is_serverless
from http_collector import _candidate_urls, build_store_cookie_header
from walmart_parse import (
    is_challenge_html,
    is_likely_instore_product,
    parse_products_from_html,
)


def uc_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    if is_serverless():
        return False
    return bool(cfg.uc_enabled)


def _chrome_major() -> Optional[int]:
    try:
        out = subprocess.check_output(
            [
                "reg",
                "query",
                r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon",
                "/v",
                "version",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _proxy_auth_extension(proxy: ProxyConfig) -> str:
    """Chrome MV2 extension that injects proxy + Proxy-Authorization (UC pattern)."""
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "WalmartProxyAuth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }
    background = f"""
var config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{
      scheme: "http",
      host: {json.dumps(proxy.host)},
      port: parseInt({json.dumps(str(proxy.port))})
    }},
    bypassList: ["localhost"]
  }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
function callbackFn(details) {{
  return {{
    authCredentials: {{
      username: {json.dumps(proxy.user)},
      password: {json.dumps(proxy.password)}
    }}
  }};
}}
chrome.webRequest.onAuthRequired.addListener(
  callbackFn,
  {{urls: ["<all_urls>"]}},
  ["blocking"]
);
"""
    tmp = Path(tempfile.mkdtemp(prefix="uc_proxy_"))
    zip_path = tmp / "proxy_auth.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("background.js", background)
    return str(zip_path)


def collect_store_via_uc(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not uc_enabled(cfg):
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "uc disabled or serverless (install undetected-chromedriver locally)",
            "proxy_used": False,
            "attempts": 0,
            "engine": "uc",
        }

    try:
        import undetected_chromedriver as uc
    except ImportError:
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "undetected-chromedriver / selenium not installed",
            "proxy_used": False,
            "attempts": 0,
            "engine": "uc",
        }

    queries = queries or list(cfg.queries)
    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0
    driver = None
    proxy_used = False

    try:
        options = uc.ChromeOptions()
        proxy = cfg.next_proxy()
        use_auth_ext = bool(proxy and proxy.user)
        # Chrome extensions (proxy auth) do not load reliably in headless.
        headless = bool(cfg.uc_headless) and not use_auth_ext
        if headless:
            options.add_argument("--headless=new")
        elif use_auth_ext and cfg.uc_headless:
            notes.append("uc forcing headed for proxy-auth extension")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1400,900")
        options.add_argument("--lang=en-US")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.page_load_strategy = "eager"

        if proxy:
            if proxy.user:
                ext = _proxy_auth_extension(proxy)
                options.add_extension(ext)
                proxy_used = True
                notes.append(f"uc auth_proxy={proxy.host}:{proxy.port}")
            else:
                options.add_argument(f"--proxy-server=http://{proxy.host}:{proxy.port}")
                proxy_used = True
                notes.append(f"uc proxy={proxy.host}:{proxy.port}")

        version_main = _chrome_major()
        kwargs: Dict[str, Any] = {"options": options, "use_subprocess": True}
        if version_main:
            kwargs["version_main"] = version_main
            notes.append(f"uc chrome_major={version_main}")

        driver = uc.Chrome(**kwargs)
        driver.set_page_load_timeout(max(55, int(cfg.timeout_sec) + 15))

        def _safe_get(url: str) -> str:
            try:
                driver.get(url)
            except Exception as e:
                # eager + timeout often still leaves usable DOM
                notes.append(f"uc nav warn: {type(e).__name__}")
            time.sleep(max(2.5, min(6.0, cfg.min_delay_sec)))
            try:
                driver.execute_script(
                    "window.scrollTo(0, Math.min(document.body.scrollHeight, 1600));"
                )
                time.sleep(1.2)
            except Exception:
                pass
            return driver.page_source or ""

        # Seed store cookies
        html0 = _safe_get("https://www.walmart.com/")
        if is_challenge_html(html0, "https://www.walmart.com/"):
            notes.append("uc challenge on homepage")
        cookie_header = build_store_cookie_header(str(store_id), postal_code)
        for part in cookie_header.split("; "):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            try:
                driver.add_cookie(
                    {
                        "name": name,
                        "value": value,
                        "domain": ".walmart.com",
                        "path": "/",
                    }
                )
            except Exception:
                pass

        for q in queries:
            got = False
            for url, source in _candidate_urls(str(store_id), q)[:2]:
                attempts += 1
                try:
                    html = _safe_get(url)
                    final_url = ""
                    try:
                        final_url = driver.current_url or url
                    except Exception:
                        final_url = url
                    if is_challenge_html(html, final_url):
                        notes.append(f"uc challenge source={source} query={q}")
                        continue
                    products = parse_products_from_html(
                        html, query=q, limit=cfg.max_per_query
                    )
                    kept: List[Dict[str, Any]] = []
                    for p in products:
                        p["store_id"] = str(store_id)
                        p["collection_source"] = f"uc_{source}"
                        if p.get("in_store") is None:
                            p["in_store"] = True
                        if not is_likely_instore_product(p):
                            continue
                        kept.append(p)
                    if kept:
                        notes.append(
                            f"uc ok source={source} store={store_id} query={q} n={len(kept)}"
                        )
                        for p in kept:
                            pid = p.get("product_id")
                            if not pid or pid in seen:
                                continue
                            seen.add(pid)
                            all_products.append(p)
                        got = True
                        break
                    notes.append(
                        f"uc empty/filter source={source} query={q} raw={len(products)}"
                    )
                except Exception as e:
                    notes.append(f"uc error source={source}: {type(e).__name__}: {e}")
            if not got:
                notes.append(f"uc no products query={q}")

    except Exception as e:
        notes.append(f"uc fatal: {type(e).__name__}: {e}")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    blocked = any("challenge" in n.lower() for n in notes) and not all_products
    return {
        "ok": bool(all_products),
        "mode": "blocked" if blocked else ("live" if all_products else "empty"),
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": proxy_used,
        "attempts": attempts or 1,
        "engine": "uc",
    }
