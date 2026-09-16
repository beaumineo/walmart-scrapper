"""
HTTP collector — store-bound Walmart pulls (triposat retry pattern).

  curl_cffi Chrome TLS
  → detect challenge /blocked /429 /5xx
  → rotate to a FRESH proxy session identity
  → retry

Store binding (critical):
  Incomplete cookies → national clearance (same products/prices every store).
  We set ACID + locGuestData + locDataV3 + assortmentStoreId with the
  store's nodeId and postal code (SerpAPI / Walmart guest-location shape).

Prefer /shop/deals/* with store cookies (less blocked than /search).
Never use naked national deals without store cookies for store reports.
"""
from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from config import CollectorConfig, ProxyConfig, get_collector_config
from walmart_parse import (
    is_challenge_html,
    is_likely_instore_product,
    looks_like_national_duplicate,
    parse_products_from_html,
)


class RetryableError(Exception):
    """Transient failure — retry with the next proxy/session (triposat)."""


ChallengeError = RetryableError

# Queries that map to Walmart deals hub slugs (more reliable than /search).
_DEAL_SLUGS = {
    "clearance": "clearance",
    "rollback": "rollback",
    "rollbacks": "rollback",
    "markdown": "clearance",
    "markdowns": "clearance",
    "special buy": "special-buys",
    "special buys": "special-buys",
    "special-buy": "special-buys",
}


def deals_url_for_query(query: str) -> Optional[str]:
    """National deals hub URL (compat). Prefer store-bound URLs for reports."""
    slug = _DEAL_SLUGS.get((query or "").strip().lower())
    if not slug:
        return None
    return f"https://www.walmart.com/shop/deals/{slug}"


def _proxy_label(proxy: Optional[ProxyConfig]) -> str:
    if not proxy:
        return "direct"
    user = proxy.user or ""
    if "-session-" in user:
        return "session-" + user.split("-session-")[-1][:12]
    return f"{proxy.host}:{proxy.port}"


def _next_identity(cfg: CollectorConfig) -> Optional[ProxyConfig]:
    if not cfg.proxies:
        return None
    base = random.choice(cfg.proxies)
    if cfg.rotate_session or len(cfg.proxies) == 1:
        return base.with_session()
    return base


def build_store_cookie_header(
    store_id: str,
    postal_code: Optional[str] = None,
) -> str:
    """Pin requests to a store so assortment/prices are not national defaults."""
    sid = str(store_id)
    postal = "".join(c for c in str(postal_code or "90210") if c.isdigit()).zfill(5)[:5]
    if len(postal) != 5:
        postal = "90210"

    ts_ms = int(time.time() * 1000)
    acid = str(uuid.uuid4())
    loc_obj: Dict[str, Any] = {
        "intent": "SHIPPING",
        "storeIntent": "PICKUP",
        "mergeFlag": True,
        "pickup": {"nodeId": sid, "timestamp": ts_ms},
        "postalCode": {"base": postal, "timestamp": ts_ms},
        "validateKey": f"prod:v2:{acid}",
    }
    raw = json.dumps(loc_obj, separators=(",", ":"))
    # Walmart expects urlsafe base64 for locGuestData / locDataV3
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    return (
        f"ACID={acid}; "
        f"hasACID=true; "
        f"hasLocData=1; "
        f"assortmentStoreId={sid}; "
        f"locDataV3={b64}; "
        f"locGuestData={b64}"
    )


def _fetch_html(
    url: str,
    cfg: CollectorConfig,
    proxy: Optional[ProxyConfig] = None,
    store_id: Optional[str] = None,
    postal_code: Optional[str] = None,
    referer: Optional[str] = None,
) -> str:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:
        raise RuntimeError("curl_cffi not installed; pip install curl_cffi") from e

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        headers["Referer"] = referer
    if store_id:
        headers["Cookie"] = build_store_cookie_header(str(store_id), postal_code)

    kwargs: Dict[str, Any] = {
        "timeout": cfg.timeout_sec,
        "impersonate": "chrome",
        "headers": headers,
    }
    if proxy is not None:
        kwargs["proxy"] = proxy.url

    response = curl_requests.get(url, **kwargs)
    final_url = str(getattr(response, "url", "") or "")

    if response.status_code == 404:
        return ""

    if response.status_code == 429:
        raise RetryableError("rate limited 429")
    if 500 <= response.status_code < 600:
        raise RetryableError(f"server {response.status_code}")
    if "/blocked" in final_url:
        raise RetryableError("Akamai hard block (/blocked)")

    html = response.text or ""
    if is_challenge_html(html, final_url=final_url):
        raise RetryableError("bot challenge")

    if response.status_code != 200:
        raise Exception(f"permanent http {response.status_code}")

    return html


def _candidate_urls(store_id: str, query: str) -> List[Tuple[str, str]]:
    """(url, source_tag) — store-specific search ONLY.

    Never use /shop/deals/* — that national hub returns the same products
    for every store (the duplicate-list bug).
    """
    q = quote(query)
    sid = quote(str(store_id))
    return [
        (
            "https://www.walmart.com/search?"
            f"q={q}&facet=fulfillment_method%3AIn-store&stores={sid}",
            "store_search",
        ),
        (
            f"https://www.walmart.com/store/{sid}/search?q={q}",
            "store_page_search",
        ),
        (
            "https://www.walmart.com/search?"
            f"q={q}&stores={sid}",
            "store_search",
        ),
    ]


def fetch_store_search(
    store_id: str,
    query: str,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Store-bound pull with challenge → rotate → retry."""
    cfg = cfg or get_collector_config()
    urls = _candidate_urls(store_id, query)
    notes: List[str] = []
    last_err = ""
    proxy_used = False
    # Cap total proxy hits: retries * first URL, then one pass on fallbacks
    max_attempts = max(1, int(cfg.max_retries))
    sid = str(store_id)
    total_budget = max_attempts + max(0, len(urls) - 1)
    used = 0

    for url_idx, (url, source) in enumerate(urls):
        attempts_here = max_attempts if url_idx == 0 else 1
        for attempt in range(1, attempts_here + 1):
            if used >= total_budget:
                break
            used += 1
            proxy = _next_identity(cfg)
            proxy_used = proxy_used or bool(proxy)
            label = _proxy_label(proxy)
            try:
                time.sleep(random.uniform(cfg.min_delay_sec, max(cfg.min_delay_sec, cfg.max_delay_sec)))
                html = _fetch_html(
                    url,
                    cfg,
                    proxy=proxy,
                    store_id=sid,
                    postal_code=postal_code,
                    referer="https://www.walmart.com/",
                )
                if not html:
                    notes.append(
                        f"empty html source={source} attempt={attempt} id={label}"
                    )
                    continue

                products = parse_products_from_html(
                    html, query=query, limit=cfg.max_per_query
                )
                kept: List[Dict[str, Any]] = []
                for p in products:
                    p["store_id"] = sid
                    p["collection_source"] = source
                    # In-store search pages are already scoped — mark as in-store
                    if source in ("store_search", "store_page_search") and p.get("in_store") is None:
                        p["in_store"] = True
                    if not is_likely_instore_product(p):
                        continue
                    kept.append(p)

                if kept:
                    if looks_like_national_duplicate(kept):
                        notes.append(
                            f"national_duplicate source={source} "
                            f"n={len(kept)} attempt={attempt} id={label}"
                        )
                        break

                    return {
                        "ok": True,
                        "mode": "live",
                        "products": kept,
                        "notes": (
                            f"curl ok source={source} store={sid} "
                            f"zip={postal_code or ''} query={query} "
                            f"n={len(kept)} instore_only=1 "
                            f"attempt={attempt} id={label}"
                        ),
                        "proxy_used": bool(proxy),
                        "attempts": used,
                        "collection_source": source,
                    }

                notes.append(
                    f"empty/instore-filter source={source} "
                    f"raw={len(products)} attempt={attempt} id={label}"
                )
                break

            except RetryableError as e:
                last_err = str(e)
                notes.append(
                    f"retryable {e} source={source} attempt={attempt} "
                    f"id={label} -> rotate"
                )
                if attempt < attempts_here:
                    time.sleep(random.uniform(3.0, 8.0))
                continue
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                notes.append(f"{last_err} attempt={attempt} id={label}")
                break

    mode = (
        "blocked"
        if any(x in last_err.lower() for x in ("challenge", "blocked", "429"))
        else "error"
    )
    return {
        "ok": False,
        "mode": mode,
        "products": [],
        "notes": "; ".join(notes) or last_err,
        "proxy_used": proxy_used or cfg.proxy_enabled,
        "attempts": used or max_attempts,
        "collection_source": "store_bound",
    }


def fetch_item(item_id: str, cfg: Optional[CollectorConfig] = None) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    url = f"https://www.walmart.com/ip/{item_id}"
    notes: List[str] = []
    max_attempts = max(1, int(cfg.max_retries))

    for attempt in range(1, max_attempts + 1):
        proxy = _next_identity(cfg)
        label = _proxy_label(proxy)
        try:
            time.sleep(random.uniform(3.0, 7.0))
            html = _fetch_html(url, cfg, proxy=proxy)
            if not html:
                return {
                    "ok": False,
                    "mode": "empty",
                    "product": None,
                    "notes": f"404 or empty item={item_id}",
                    "proxy_used": bool(proxy),
                    "attempts": attempt,
                }
            products = parse_products_from_html(html, limit=5)
            if products:
                return {
                    "ok": True,
                    "mode": "live",
                    "product": products[0],
                    "notes": f"item {item_id} ok attempt={attempt} id={label}",
                    "proxy_used": bool(proxy),
                    "attempts": attempt,
                }
            return {
                "ok": False,
                "mode": "empty",
                "product": None,
                "notes": "no product node in __NEXT_DATA__",
                "proxy_used": bool(proxy),
                "attempts": attempt,
            }
        except RetryableError as e:
            notes.append(f"{e} attempt={attempt} id={label} -> rotate")
            if attempt < max_attempts:
                time.sleep(random.uniform(3.0, 10.0))
            continue
        except Exception as e:
            notes.append(f"{type(e).__name__}: {e}")
            break

    return {
        "ok": False,
        "mode": "blocked"
        if any("challenge" in n or "blocked" in n for n in notes)
        else "error",
        "product": None,
        "notes": "; ".join(notes),
        "proxy_used": cfg.proxy_enabled,
        "attempts": max_attempts,
    }


def collect_store_via_curl(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    queries = queries or list(cfg.queries)
    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    proxy_used = False
    attempts = 0
    blocked = False
    sid = str(store_id)
    source_tag = "store_bound"

    for q in queries:
        result = fetch_store_search(
            sid, q, cfg=cfg, postal_code=postal_code
        )
        attempts = max(attempts, int(result.get("attempts") or 1))
        proxy_used = proxy_used or bool(result.get("proxy_used"))
        notes.append(result.get("notes") or "")
        if result.get("collection_source"):
            source_tag = str(result["collection_source"])
        if result.get("mode") == "blocked":
            blocked = True
            continue
        for p in result.get("products") or []:
            pid = p.get("product_id")
            if not pid or pid in seen:
                continue
            if not is_likely_instore_product(p):
                continue
            seen.add(pid)
            p["store_id"] = sid
            all_products.append(p)
        if len(queries) > 1:
            time.sleep(random.uniform(2.0, 4.0))

    if all_products:
        if looks_like_national_duplicate(all_products):
            return {
                "ok": False,
                "mode": "national_duplicate",
                "products": [],
                "notes": (
                    "; ".join(n for n in notes if n)
                    + "; rejected national clearance duplicate list"
                ).strip("; "),
                "proxy_used": proxy_used,
                "attempts": attempts,
                "engine": "curl_cffi",
                "collection_source": source_tag,
            }
        return {
            "ok": True,
            "mode": "live",
            "products": all_products,
            "notes": "; ".join(n for n in notes if n),
            "proxy_used": proxy_used,
            "attempts": attempts,
            "engine": "curl_cffi",
            "collection_source": source_tag,
        }

    if blocked:
        return {
            "ok": False,
            "mode": "blocked",
            "products": [],
            "notes": "; ".join(n for n in notes if n),
            "proxy_used": proxy_used,
            "attempts": attempts,
            "engine": "curl_cffi",
            "collection_source": source_tag,
        }

    return {
        "ok": False,
        "mode": "empty",
        "products": [],
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": proxy_used,
        "attempts": attempts,
        "engine": "curl_cffi",
        "collection_source": source_tag,
    }
