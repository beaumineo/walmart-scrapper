"""
Oxylabs Web Unblocker — store-scoped Walmart search via proxy.

Trial / Web Unblocker accounts authenticate at:
  https://user:pass@unblock.oxylabs.io:60000
(not realtime.oxylabs.io Scraper API).

Env:
  OXYLABS_USERNAME / OXYLABS_PASSWORD
  OXYLABS_MODE=unblocker
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import urllib3

from config import CollectorConfig, get_collector_config
from http_collector import _candidate_urls
from walmart_parse import (
    is_challenge_html,
    is_likely_instore_product,
    looks_like_national_duplicate,
    parse_products_from_html,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Keep this list short — each Unblocker render is ~20–45s.
DEFAULT_QUERIES = (
    "clearance",
    "rollback",
    "special buy",
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
    postal_code: Optional[str],
) -> str:
    import requests

    geo = "".join(c for c in str(postal_code or "") if c.isdigit())[:5]
    headers = {
        "x-oxylabs-render": "html",
        "x-oxylabs-geo-location": geo if len(geo) == 5 else "United States",
    }
    timeout = max(45, min(90, int(cfg.timeout_sec) + 40))
    resp = requests.get(
        url,
        proxies=_proxies(cfg),
        headers=headers,
        timeout=timeout,
        verify=False,
    )
    if resp.status_code == 401:
        raise RuntimeError("Oxylabs Unblocker auth failed (check OXYLABS_USERNAME/PASSWORD)")
    if resp.status_code == 429:
        raise RuntimeError("Oxylabs Unblocker rate limited")
    if resp.status_code >= 400:
        raise RuntimeError(f"Oxylabs Unblocker http={resp.status_code}: {resp.text[:200]}")
    html = resp.text or ""
    if is_challenge_html(html, url):
        raise RuntimeError("Oxylabs Unblocker returned Walmart challenge page")
    return html


def _build_query_queue(queries: Optional[Sequence[str]], cfg: CollectorConfig) -> List[str]:
    """Prefer a short unblocker-specific queue (HTML render is expensive)."""
    # Ignore the large scraper-api query list from env/config for unblocker speed.
    use_cfg = bool(queries)
    primary = list(queries) if use_cfg else list(DEFAULT_QUERIES)
    queued: List[str] = []
    seen = set()
    for q in list(primary) + list(DEFAULT_QUERIES):
        key = str(q or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        queued.append(str(q).strip())
    # Hard default 6 — Unblocker HTML is slow; more queries = multi-minute waits.
    return queued[: max(3, _env_int("WALMART_MAX_QUERIES", 4))]


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

    queued = _build_query_queue(queries, cfg)
    max_api_calls = max(3, _env_int("WALMART_MAX_API_CALLS", 4))
    # Trial Unblocker rate-limits hard if too many parallel renders.
    workers = max(2, min(4, _env_int("WALMART_PARALLEL_WORKERS", 4)))

    all_products: List[Dict[str, Any]] = []
    seen = set()
    notes: List[str] = []
    attempts = 0
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
            # One URL only — fallback doubles latency on Unblocker.
            url, source = _candidate_urls(sid, q)[0]
            html = _fetch_html(url, cfg=cfg, postal_code=postal_code)
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
                }
            if kept:
                return {
                    "ok": True,
                    "query": q,
                    "source": source,
                    "products": kept,
                    "raw": len(products),
                    "error": None,
                }
            return {
                "ok": False,
                "query": q,
                "error": f"empty source={source} raw={len(products)}",
                "products": [],
            }
        except Exception as e:
            return {
                "ok": False,
                "query": q,
                "error": f"{type(e).__name__}: {e}",
                "auth_failed": "auth failed" in str(e).lower(),
                "products": [],
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
        if hit.get("ok"):
            added = _ingest(hit.get("products") or [])
            notes.append(
                f"oxylabs_unblocker ok store={sid} zip={postal_code or ''} "
                f"query={q} source={hit.get('source')} n={len(hit.get('products') or [])} "
                f"added={added} raw={hit.get('raw')}"
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
