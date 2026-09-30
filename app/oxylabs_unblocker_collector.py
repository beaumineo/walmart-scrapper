"""
Oxylabs Web Unblocker — store-scoped Walmart search via proxy.

Uses unblock.oxylabs.io:60000 (NOT realtime Scraper API).

Bandwidth rules:
  - Default NO browser render (x-oxylabs-render off) — render can burn 10x+ data
  - Few queries only (clearance / rollback / markdown)
  - One URL per query, store cookie + geo header for localization
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import urllib3

from config import CollectorConfig, get_collector_config
from http_collector import _candidate_urls, build_store_cookie_header
from walmart_parse import (
    is_challenge_html,
    is_likely_instore_product,
    looks_like_national_duplicate,
    parse_products_from_html,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Short list on purpose — each Unblocker hit costs time + bandwidth.
DEFAULT_QUERIES = (
    "clearance",
    "rollback",
    "markdown",
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def oxylabs_unblocker_enabled(cfg: Optional[CollectorConfig] = None) -> bool:
    cfg = cfg or get_collector_config()
    if not (cfg.oxylabs_username and cfg.oxylabs_password):
        return False
    mode = (os.environ.get("OXYLABS_MODE") or "auto").strip().lower()
    return mode in ("auto", "unblocker", "web_unblocker", "both")


def _proxies(cfg: CollectorConfig) -> Dict[str, str]:
    user = quote(str(cfg.oxylabs_username), safe="")
    password = quote(str(cfg.oxylabs_password), safe="")
    endpoint = f"http://{user}:{password}@unblock.oxylabs.io:60000"
    return {"http": endpoint, "https": endpoint}


def _fetch_html(
    url: str,
    *,
    cfg: CollectorConfig,
    store_id: str,
    postal_code: Optional[str],
) -> tuple:
    """Return (html, bytes_downloaded)."""
    import requests

    geo = "".join(c for c in str(postal_code or "") if c.isdigit())[:5]
    headers = {
        "x-oxylabs-geo-location": geo if len(geo) == 5 else "United States",
        # Pin assortment to selected store (critical for store-local lists).
        "x-oxylabs-force-cookies": "1",
        "Cookie": build_store_cookie_header(str(store_id), postal_code),
    }
    # Render is optional and VERY expensive — keep off unless explicitly enabled.
    if _env_bool("OXYLABS_RENDER", False):
        headers["x-oxylabs-render"] = "html"

    timeout = max(35, min(75, int(cfg.timeout_sec) + 25))
    resp = requests.get(
        url,
        proxies=_proxies(cfg),
        headers=headers,
        timeout=timeout,
        verify=False,
    )
    nbytes = len(resp.content or b"")
    if resp.status_code == 401:
        raise RuntimeError("Oxylabs Unblocker auth failed (check OXYLABS_USERNAME/PASSWORD)")
    if resp.status_code == 429:
        raise RuntimeError("Oxylabs Unblocker rate limited")
    if resp.status_code >= 400:
        raise RuntimeError(f"Oxylabs Unblocker http={resp.status_code}: {resp.text[:200]}")
    html = resp.text or ""
    # Soft challenge check: if we still parsed products later, keep going.
    if is_challenge_html(html, url) and "__NEXT_DATA__" not in html:
        raise RuntimeError("Oxylabs Unblocker returned Walmart challenge page")
    return html, nbytes


def _build_query_queue(queries: Optional[Sequence[str]]) -> List[str]:
    primary = list(queries) if queries else list(DEFAULT_QUERIES)
    queued: List[str] = []
    seen = set()
    for q in list(primary) + list(DEFAULT_QUERIES):
        key = str(q or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        queued.append(str(q).strip())
    # Hard cap low to protect trial bandwidth.
    return queued[: max(2, min(5, _env_int("WALMART_MAX_QUERIES", 3)))]


def collect_store_via_oxylabs_unblocker(
    store_id: str,
    queries: Optional[List[str]] = None,
    cfg: Optional[CollectorConfig] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or get_collector_config()
    if not oxylabs_unblocker_enabled(cfg):
        return {
            "ok": False,
            "mode": "error",
            "products": [],
            "notes": "oxylabs unblocker not configured",
            "proxy_used": False,
            "attempts": 0,
            "engine": "oxylabs_unblocker",
        }

    # Ignore huge scraper-api query lists from env — they explode Unblocker cost.
    queued = _build_query_queue(None if not queries else queries[:5])
    max_api_calls = max(2, min(5, _env_int("WALMART_MAX_API_CALLS", 3)))
    workers = max(1, min(3, _env_int("WALMART_PARALLEL_WORKERS", 3)))

    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0
    total_bytes = 0
    sid = str(store_id)

    def _ingest(batch: List[Dict[str, Any]]) -> int:
        added = 0
        for p in batch:
            pid = p.get("product_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            all_products.append(p)
            added += 1
        return added

    def _one(q: str) -> Dict[str, Any]:
        try:
            url, source = _candidate_urls(sid, q)[0]
            html, nbytes = _fetch_html(
                url, cfg=cfg, store_id=sid, postal_code=postal_code
            )
            products = parse_products_from_html(html, query=q, limit=cfg.max_per_query)
            kept: List[Dict[str, Any]] = []
            for p in products:
                p["store_id"] = sid
                p["collection_source"] = f"oxylabs_unblocker:{source}"
                p["query"] = q
                if p.get("in_store") is None:
                    p["in_store"] = True
                if p.get("pickup_available") is None and p.get("in_store"):
                    p["pickup_available"] = True
                if p.get("availability") is None:
                    p["availability"] = "In stock · pickup"
                if p.get("in_stock") is None:
                    p["in_stock"] = True
                if not is_likely_instore_product(p):
                    continue
                kept.append(p)
            if kept and looks_like_national_duplicate(kept):
                return {
                    "ok": False,
                    "query": q,
                    "error": f"national_duplicate source={source}",
                    "products": [],
                    "bytes": nbytes,
                }
            if kept:
                return {
                    "ok": True,
                    "query": q,
                    "source": source,
                    "products": kept,
                    "raw": len(products),
                    "bytes": nbytes,
                    "error": None,
                }
            return {
                "ok": False,
                "query": q,
                "error": f"empty source={source} raw={len(products)}",
                "products": [],
                "bytes": nbytes,
            }
        except Exception as e:
            return {
                "ok": False,
                "query": q,
                "error": f"{type(e).__name__}: {e}",
                "auth_failed": "auth failed" in str(e).lower(),
                "products": [],
                "bytes": 0,
            }

    t0 = time.time()
    jobs = queued[:max_api_calls]
    attempts = len(jobs)
    hits: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(jobs)))) as pool:
        futs = [pool.submit(_one, q) for q in jobs]
        for fut in as_completed(futs):
            hits.append(fut.result())

    auth_fails = sum(1 for h in hits if h.get("auth_failed"))
    if hits and auth_fails == len(hits):
        notes.append(
            "oxylabs_auth_failed: OXYLABS_USERNAME/PASSWORD rejected by Web Unblocker. "
            "Update credentials in Railway Variables and redeploy."
        )
        return {
            "ok": False,
            "mode": "auth_failed",
            "products": [],
            "notes": "; ".join(notes),
            "proxy_used": True,
            "attempts": attempts,
            "engine": "oxylabs_unblocker",
        }

    for hit in sorted(hits, key=lambda h: str(h.get("query") or "")):
        q = hit.get("query")
        total_bytes += int(hit.get("bytes") or 0)
        if hit.get("ok"):
            added = _ingest(hit.get("products") or [])
            notes.append(
                f"oxylabs_unblocker ok store={sid} zip={postal_code or ''} "
                f"query={q} source={hit.get('source')} n={len(hit.get('products') or [])} "
                f"added={added} raw={hit.get('raw')} bytes={hit.get('bytes')}"
            )
        else:
            notes.append(f"oxylabs_unblocker empty/error query={q}: {hit.get('error')}")

    with_was = sum(
        1
        for p in all_products
        if p.get("current_price")
        and (p.get("was_price") or p.get("list_price"))
        and float(p.get("was_price") or p.get("list_price") or 0)
        > float(p.get("current_price") or 0)
    )
    notes.append(
        f"priced_markdown_candidates={with_was} unique_products={len(all_products)} "
        f"api_calls={attempts} parallel_workers={workers} "
        f"downloaded_mb={round(total_bytes / (1024 * 1024), 2)} "
        f"render={int(_env_bool('OXYLABS_RENDER', False))} "
        f"elapsed_sec={round(time.time() - t0, 1)}"
    )

    return {
        "ok": bool(all_products),
        "mode": "live" if all_products else "empty",
        "products": all_products,
        "notes": "; ".join(n for n in notes if n),
        "proxy_used": True,
        "attempts": attempts or 1,
        "engine": "oxylabs_unblocker",
    }
