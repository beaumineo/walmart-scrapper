"""
Bright Data Web Unlocker collector.

Uses Bright Data's product to return clean HTML (their infra handles
anti-bot / CAPTCHA). This is a vendor API integration — not a DIY solver.

Auth modes (either works):
1) REST API:
   BRIGHTDATA_API_KEY=...
   BRIGHTDATA_UNLOCKER_ZONE=web_unlocker1
2) Native unlocker proxy:
   BRIGHTDATA_UNLOCKER_PROXY=brd.superproxy.io:44445:brd-customer-...-zone-web_unlocker1:PASS
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional

from config import CollectorConfig, get_collector_config
from walmart_parse import is_challenge_html, is_likely_instore_product, parse_products_from_html
from http_collector import _candidate_urls, ChallengeError, RetryableError


def unlocker_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    return bool(cfg.unlocker_api_key and cfg.unlocker_zone) or bool(cfg.unlocker_proxy)


def _sleep(cfg: CollectorConfig) -> None:
    lo = max(0.3, float(cfg.min_delay_sec) * 0.5)
    hi = max(lo, float(cfg.max_delay_sec) * 0.8)
    time.sleep(random.uniform(lo, hi))


def _fetch_via_api(url: str, cfg: CollectorConfig) -> Dict[str, Any]:
    import urllib.request

    payload = {
        "zone": cfg.unlocker_zone,
        "url": url,
        "format": "raw",
        "country": cfg.unlocker_country or "us",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.brightdata.com/request",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.unlocker_api_key}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(30, int(cfg.timeout_sec))) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200) or 200
            final_url = url
    except Exception as e:
        # urllib raises HTTPError with body sometimes
        body = ""
        status = 0
        if hasattr(e, "read"):
            try:
                body = e.read().decode("utf-8", errors="replace")  # type: ignore[attr-defined]
            except Exception:
                body = ""
        if hasattr(e, "code"):
            status = int(getattr(e, "code") or 0)
        raise RetryableError(f"unlocker api http={status}: {e}; body={body[:200]}") from e

    if is_challenge_html(html, final_url):
        raise ChallengeError("unlocker returned challenge page")

    return {
        "ok": True,
        "status": status,
        "html": html,
        "final_url": final_url,
        "error": None,
        "via": "unlocker_api",
    }


def _fetch_via_proxy(url: str, cfg: CollectorConfig) -> Dict[str, Any]:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:
        raise RuntimeError("curl_cffi not installed") from e

    proxy = cfg.unlocker_proxy
    assert proxy is not None
    response = curl_requests.get(
        url,
        proxy=proxy.url,
        timeout=max(30, int(cfg.timeout_sec)),
        impersonate="chrome124",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    html = response.text or ""
    final_url = str(getattr(response, "url", "") or url)
    if response.status_code == 429:
        raise RetryableError("unlocker proxy 429")
    if 500 <= response.status_code < 600:
        raise RetryableError(f"unlocker proxy {response.status_code}")
    if is_challenge_html(html, final_url):
        raise ChallengeError("unlocker proxy returned challenge page")
    if response.status_code != 200:
        raise RetryableError(f"unlocker proxy http {response.status_code}")
    return {
        "ok": True,
        "status": response.status_code,
        "html": html,
        "final_url": final_url,
        "error": None,
        "via": "unlocker_proxy",
    }


def fetch_html_unlocker(url: str, cfg: Optional[CollectorConfig] = None) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not unlocker_enabled(cfg):
        raise RuntimeError("Web Unlocker not configured")

    last_err = ""
    attempts = max(1, min(2, cfg.max_retries if cfg.max_retries > 1 else 1))
    for attempt in range(1, attempts + 1):
        try:
            _sleep(cfg)
            if cfg.unlocker_api_key and cfg.unlocker_zone:
                result = _fetch_via_api(url, cfg)
            else:
                result = _fetch_via_proxy(url, cfg)
            result["attempts"] = attempt
            return result
        except (ChallengeError, RetryableError) as e:
            last_err = str(e)
            if attempt < attempts:
                time.sleep(2.0 * attempt)
                continue
            raise
    raise RetryableError(last_err or "unlocker failed")


def collect_store_via_unlocker(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not unlocker_enabled(cfg):
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "unlocker not configured (need BRIGHTDATA_API_KEY+ZONE or BRIGHTDATA_UNLOCKER_PROXY)",
            "proxy_used": False,
            "attempts": 0,
            "engine": "web_unlocker",
        }

    queries = queries or list(cfg.queries)
    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0
    blocked = False

    for q in queries:
        got = False
        for url, source in _candidate_urls(str(store_id), q):
            try:
                result = fetch_html_unlocker(url, cfg=cfg)
                attempts = max(attempts, int(result.get("attempts") or 1))
                products = parse_products_from_html(
                    result["html"], query=q, limit=cfg.max_per_query
                )
                if products:
                    kept = []
                    for p in products:
                        p["store_id"] = str(store_id)
                        p["collection_source"] = source
                        if source in ("store_search", "store_page_search") and p.get("in_store") is None:
                            p["in_store"] = True
                        if not is_likely_instore_product(p):
                            continue
                        kept.append(p)
                    products = kept
                if products:
                    notes.append(
                        f"unlocker ok via={result.get('via')} source={source} "
                        f"store={store_id} query={q} n={len(products)} instore_only=1"
                    )
                    for p in products:
                        pid = p.get("product_id")
                        if not pid or pid in seen:
                            continue
                        seen.add(pid)
                        all_products.append(p)
                    got = True
                    break
                notes.append(f"unlocker empty/filtered source={source} query={q}")
            except ChallengeError as e:
                blocked = True
                notes.append(f"unlocker challenge source={source}: {e}")
                break
            except Exception as e:
                notes.append(
                    f"unlocker error source={source}: {type(e).__name__}: {e}"
                )
                break
        if blocked:
            break
        if not got:
            notes.append(f"unlocker no products query={q}")

    if blocked and not all_products:
        return {
            "ok": False,
            "mode": "blocked",
            "products": [],
            "notes": "; ".join(n for n in notes if n),
            "proxy_used": True,
            "attempts": attempts or 1,
            "engine": "web_unlocker",
        }

    return {
        "ok": bool(all_products),
        "mode": "live" if all_products else "empty",
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": True,
        "attempts": attempts or 1,
        "engine": "web_unlocker",
    }
