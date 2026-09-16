"""
Milestone 2+ price collection orchestrator.

Client engines (WALMART_COLLECT_ENGINE=auto|client):
  oxylabs → scraperapi → unlocker → curl   (no browser)

Browser (uc / playwright) only if WALMART_ALLOW_BROWSER=1 — not for client.
"""
from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import PULLS_DIR, get_collector_config
from walmart_parse import looks_like_national_duplicate, overlaps_other_store_cache


DEFAULT_QUERIES = ["clearance", "rollback", "special buy"]

_LIVE_COOLDOWN_UNTIL = 0.0
_LIVE_COOLDOWN_SECONDS = 45 * 60  # longer cool-down after a challenge


def live_on_cooldown() -> bool:
    return time.time() < _LIVE_COOLDOWN_UNTIL


def _mark_live_blocked() -> None:
    global _LIVE_COOLDOWN_UNTIL
    _LIVE_COOLDOWN_UNTIL = time.time() + _LIVE_COOLDOWN_SECONDS


def clear_live_cooldown() -> None:
    global _LIVE_COOLDOWN_UNTIL
    _LIVE_COOLDOWN_UNTIL = 0.0


@dataclass
class PriceItem:
    product_id: str
    title: str
    brand: Optional[str] = None
    category: str = "General"
    current_price: Optional[float] = None
    list_price: Optional[float] = None
    was_price: Optional[float] = None
    unit_price_display: Optional[str] = None
    offer_type: Optional[str] = None
    is_reduced: bool = False
    is_price_event: bool = False
    seller_name: Optional[str] = None
    seller_type: Optional[str] = None
    availability: Optional[str] = None
    availability_code: Optional[str] = None
    in_store: Optional[bool] = None
    online: Optional[bool] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    rating: Optional[str] = None
    review_count: Optional[int] = None
    query: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PricePull:
    store_id: str
    zip: Optional[str]
    ok: bool
    mode: str
    products: List[PriceItem] = field(default_factory=list)
    notes: str = ""
    proxy_used: bool = False
    attempts: int = 1
    scraped_at: str = ""
    queries: List[str] = field(default_factory=list)
    engine: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "store_id": self.store_id,
            "zip": self.zip,
            "ok": self.ok,
            "mode": self.mode,
            "engine": self.engine,
            "product_count": len(self.products),
            "products": [p.to_dict() for p in self.products],
            "notes": self.notes,
            "proxy_used": self.proxy_used,
            "attempts": self.attempts,
            "scraped_at": self.scraped_at or _now(),
            "queries": self.queries,
            "summary": _summarize_products(self.products),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict_to_item(d: Dict[str, Any]) -> PriceItem:
    return PriceItem(
        product_id=str(d.get("product_id") or ""),
        title=str(d.get("title") or ""),
        brand=d.get("brand"),
        category=str(d.get("category") or "General"),
        current_price=d.get("current_price"),
        list_price=d.get("list_price"),
        was_price=d.get("was_price"),
        unit_price_display=d.get("unit_price_display"),
        offer_type=d.get("offer_type"),
        is_reduced=bool(d.get("is_reduced")),
        is_price_event=bool(d.get("is_price_event")),
        seller_name=d.get("seller_name"),
        seller_type=d.get("seller_type"),
        availability=d.get("availability"),
        availability_code=d.get("availability_code"),
        in_store=d.get("in_store"),
        online=d.get("online"),
        url=d.get("url"),
        image_url=d.get("image_url"),
        rating=d.get("rating"),
        review_count=d.get("review_count"),
        query=d.get("query"),
    )


def _summarize_products(products: List[PriceItem]) -> Dict[str, Any]:
    with_was = sum(
        1
        for p in products
        if p.was_price or (p.list_price and p.current_price and p.list_price > p.current_price)
    )
    return {
        "total": len(products),
        "with_was_or_list": with_was,
        "with_offer_type": sum(1 for p in products if p.offer_type),
        "with_availability": sum(1 for p in products if p.availability),
        "avg_current_price": round(
            sum(p.current_price for p in products if p.current_price) / max(1, len(products)),
            2,
        )
        if products
        else 0,
    }


def save_pull(pull: PricePull) -> Path:
    PULLS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PULLS_DIR / f"pull_{pull.store_id}_{stamp}.json"

    # Never promote national clearance clones as this store's "latest"
    is_national = bool(pull.ok and pull.products and looks_like_national_duplicate(pull.products))
    clone_of = None
    if pull.ok and pull.products and not is_national:
        clone_of = overlaps_other_store_cache(pull.store_id, pull.products)
    notes = pull.notes or ""
    if is_national or clone_of:
        pull.ok = False
        pull.mode = "national_duplicate"
        reason = (
            f"rejected: identical product list as store {clone_of} (not store-scoped)"
            if clone_of
            else "rejected: national clearance list (same across stores)"
        )
        pull.notes = ((notes + "; ") if notes else "") + reason
        # Do not keep cloned products around for accidental UI use
        pull.products = []
        # Poisoned peer cache must die too — otherwise UI keeps showing the clone
        for doomed in {str(pull.store_id), str(clone_of) if clone_of else ""}:
            if not doomed:
                continue
            peer = PULLS_DIR / f"latest_{doomed}.json"
            if peer.exists():
                try:
                    peer.unlink()
                except Exception:
                    pass

    payload = pull.to_dict()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Only refresh store latest on real store-scoped live success
    store_scoped = any(
        m in (pull.notes or "")
        for m in (
            "store_search",
            "store_page_search",
            "instore_only=1",
            "oxylabs ok",
            "scraperapi html ok",
            "unlocker ok",
        )
    ) and "national_duplicate" not in (pull.notes or "") and "source=deals" not in (
        pull.notes or ""
    )

    if pull.ok and pull.products and pull.mode == "live" and store_scoped and not is_national:
        (PULLS_DIR / f"latest_{pull.store_id}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        try:
            from price_history import save_price_history

            save_price_history(
                store_id=pull.store_id,
                products=[p.to_dict() for p in pull.products],
                scraped_at=pull.scraped_at or _now(),
            )
        except Exception:
            pass
    else:
        # Drop a poisoned latest_* so the UI cannot keep serving clones
        latest = PULLS_DIR / f"latest_{pull.store_id}.json"
        if latest.exists() and (is_national or clone_of):
            try:
                latest.unlink()
            except Exception:
                pass
    return path


def load_cached_live_pull(store_id: Optional[str] = None) -> Optional[PricePull]:
    """Return last successful *store-specific* live pull for THIS store only.

    Rejects:
    - national /shop/deals/clearance caches (identical across stores)
    - pulls without store_search / store_page_search markers
    """
    if not store_id:
        return None
    sid = str(store_id)
    candidates: List[Path] = [PULLS_DIR / f"latest_{sid}.json"]
    if PULLS_DIR.exists():
        pulls = sorted(
            PULLS_DIR.glob(f"pull_{sid}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(pulls[:20])

    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not payload.get("ok"):
            continue
        if str(payload.get("store_id") or "") != sid:
            continue
        notes = str(payload.get("notes") or "")
        # Must be a true store search — deals hub is the same list every store
        if "store_search" not in notes and "store_page_search" not in notes:
            continue
        if "source=deals" in notes or "store_bound_deals" in notes:
            continue
        if "national_duplicate" in notes:
            continue
        products = [_dict_to_item(p) for p in (payload.get("products") or [])]
        if not products:
            continue
        if looks_like_national_duplicate(products):
            continue
        # Reject caches that are clones of another store (UC national bug)
        if overlaps_other_store_cache(sid, products):
            continue
        return PricePull(
            store_id=sid,
            zip=payload.get("zip"),
            ok=True,
            mode="live",
            products=products,
            notes=f"cached from {path.name}; {notes[:120]}",
            proxy_used=bool(payload.get("proxy_used")),
            attempts=int(payload.get("attempts") or 1),
            scraped_at=payload.get("scraped_at") or "",
            queries=list(payload.get("queries") or []),
            engine=str(payload.get("engine") or "cache"),
        )
    return None


def cache_age_seconds(pull: Optional[PricePull]) -> Optional[float]:
    if not pull or not pull.scraped_at:
        return None
    try:
        ts = pull.scraped_at.replace("Z", "+00:00")
        scraped = datetime.fromisoformat(ts)
        if scraped.tzinfo is None:
            scraped = scraped.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - scraped).total_seconds())
    except Exception:
        return None


def collect_store_prices(
    store_id: str,
    zip_code: Optional[str] = None,
    queries: Optional[List[str]] = None,
    force: bool = False,
) -> PricePull:
    import os
    import subprocess

    cfg = get_collector_config()
    queries = queries or list(cfg.queries) or DEFAULT_QUERIES

    if (not force) and live_on_cooldown() and os.environ.get("WALMART_COLLECT_INLINE") != "1":
        return PricePull(
            store_id=str(store_id),
            zip=zip_code,
            ok=False,
            mode="blocked",
            notes="collector on cooldown after recent bot check",
            proxy_used=cfg.proxy_enabled,
            scraped_at=_now(),
            queries=queries,
            engine="cooldown",
        )

    # Hosted (Railway) and Docker images: always collect in-process.
    # Subprocess needs scripts/run_price_pull.py which may be absent in the image.
    on_railway = bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_price_pull.py"
    use_inline = (
        os.environ.get("WALMART_COLLECT_INLINE", "").strip() == "1"
        or on_railway
        or not script.is_file()
    )

    if not use_inline:
        try:
            cmd = [sys.executable, str(script), str(store_id)]
            if zip_code:
                cmd.extend(["--zip", str(zip_code)])
            if force:
                cmd.append("--force")
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(
                    150,
                    int(cfg.timeout_sec)
                    * max(1, len(queries))
                    * (cfg.max_retries + 1)
                    + 60,
                ),
                env={**os.environ, "WALMART_COLLECT_INLINE": "1"},
            )
            lines = (proc.stdout or "").strip().splitlines()
            payload = json.loads(lines[-1]) if lines else {}
            products = [_dict_to_item(p) for p in (payload.get("products") or [])]
            mode = payload.get("mode") or ("live" if products else "empty")
            if mode == "blocked":
                _mark_live_blocked()
            # If subprocess printed an error and no products, fall through to inline
            notes = (payload.get("notes") or proc.stderr or "")[:400]
            if products or payload.get("ok"):
                pull = PricePull(
                    store_id=str(store_id),
                    zip=zip_code or payload.get("zip"),
                    ok=bool(payload.get("ok")),
                    mode=mode,
                    products=products,
                    notes=notes,
                    proxy_used=bool(payload.get("proxy_used")),
                    attempts=int(payload.get("attempts") or 1),
                    scraped_at=payload.get("scraped_at") or _now(),
                    queries=payload.get("queries") or queries,
                    engine=str(payload.get("engine") or ""),
                )
                save_pull(pull)
                return pull
            if "No such file" in notes or "can't open file" in notes:
                pass  # fall through to inline
            else:
                pull = PricePull(
                    store_id=str(store_id),
                    zip=zip_code or payload.get("zip"),
                    ok=False,
                    mode=mode,
                    products=[],
                    notes=notes,
                    proxy_used=bool(payload.get("proxy_used")),
                    attempts=int(payload.get("attempts") or 1),
                    scraped_at=payload.get("scraped_at") or _now(),
                    queries=payload.get("queries") or queries,
                    engine=str(payload.get("engine") or ""),
                )
                save_pull(pull)
                return pull
        except Exception:
            pass

    return _collect_inline(store_id=store_id, zip_code=zip_code, queries=queries)


def _pull_from_result(
    store_id: str,
    zip_code: Optional[str],
    queries: List[str],
    result: Dict[str, Any],
    engine_name: str,
    prior_notes: Optional[List[str]] = None,
) -> PricePull:
    notes = list(prior_notes or [])
    if result.get("notes"):
        notes.append(str(result["notes"]))
    products = [_dict_to_item(p) for p in (result.get("products") or [])]
    ok = bool(result.get("ok") and products)
    mode = str(result.get("mode") or ("live" if ok else "empty"))
    if ok:
        mode = "live"
    return PricePull(
        store_id=str(store_id),
        zip=zip_code,
        ok=ok,
        mode=mode if ok else mode,
        products=products if ok else [],
        notes="; ".join(n for n in notes if n),
        proxy_used=bool(result.get("proxy_used")),
        attempts=int(result.get("attempts") or 1),
        scraped_at=_now(),
        queries=queries,
        engine=engine_name,
    )


def _collect_inline(
    store_id: str,
    zip_code: Optional[str],
    queries: List[str],
) -> PricePull:
    """
    Client-safe order (auto / client):
      oxylabs → scraperapi → web_unlocker → curl_cffi

    Browser engines (uc / playwright) are opt-in only via WALMART_ALLOW_BROWSER=1.
    They previously returned the same national catalog for every store.
    """
    cfg = get_collector_config()
    engine = (cfg.collect_engine or "auto").lower()
    if engine == "client":
        engine = "auto"
    notes: List[str] = []
    allow_browser = bool(getattr(cfg, "allow_browser", False))

    # Preferred path: non-browser client module
    if engine in ("auto", "oxylabs", "scraperapi", "unlocker", "curl"):
        try:
            from client_collector import collect_store_for_client

            if engine == "auto":
                result = collect_store_for_client(
                    store_id, postal_code=zip_code, queries=queries, cfg=cfg
                )
                pull = _pull_from_result(
                    store_id,
                    zip_code,
                    queries,
                    result,
                    str(result.get("engine") or "client"),
                    prior_notes=notes,
                )
                # Cross-store clone check happens inside save_pull
                save_pull(pull)
                if pull.ok:
                    return pull
                notes.append(pull.notes or "client module failed")
            else:
                # Forced single engine still goes through vendor helpers below
                pass
        except Exception as e:
            notes.append(f"client module error: {type(e).__name__}: {e}")

    def _try_vendor(name: str, enabled: bool, collect_fn) -> Optional[PricePull]:
        if engine not in ("auto", name):
            return None
        # When auto already ran client module, skip re-running the same vendors
        if engine == "auto":
            return None
        if not enabled:
            pull = PricePull(
                store_id=str(store_id),
                zip=zip_code,
                ok=False,
                mode="error",
                notes=f"{name} not configured",
                proxy_used=False,
                scraped_at=_now(),
                queries=queries,
                engine=name,
            )
            save_pull(pull)
            return pull
        try:
            result = collect_fn()
            pull = _pull_from_result(
                store_id, zip_code, queries, result, name, prior_notes=notes
            )
            save_pull(pull)
            return pull
        except Exception as e:
            notes.append(f"{name} error: {type(e).__name__}: {e}")
            pull = PricePull(
                store_id=str(store_id),
                zip=zip_code,
                ok=False,
                mode="error",
                notes="; ".join(notes),
                proxy_used=False,
                scraped_at=_now(),
                queries=queries,
                engine=name,
            )
            save_pull(pull)
            return pull

    if engine == "oxylabs":
        from oxylabs_collector import collect_store_via_oxylabs, oxylabs_enabled

        return _try_vendor(
            "oxylabs",
            oxylabs_enabled(cfg),
            lambda: collect_store_via_oxylabs(
                store_id, queries=queries, cfg=cfg, postal_code=zip_code
            ),
        )  # type: ignore[return-value]

    if engine == "scraperapi":
        from scraperapi_collector import collect_store_via_scraperapi, scraperapi_enabled

        return _try_vendor(
            "scraperapi",
            scraperapi_enabled(cfg),
            lambda: collect_store_via_scraperapi(
                store_id, queries=queries, cfg=cfg, postal_code=zip_code
            ),
        )  # type: ignore[return-value]

    if engine == "unlocker":
        from unlocker_collector import collect_store_via_unlocker, unlocker_enabled

        hit = _try_vendor(
            "unlocker",
            unlocker_enabled(cfg),
            lambda: collect_store_via_unlocker(store_id, queries=queries, cfg=cfg),
        )
        if hit is not None:
            hit.engine = "web_unlocker"
        return hit  # type: ignore[return-value]

    if engine == "curl":
        from http_collector import collect_store_via_curl

        result = collect_store_via_curl(
            store_id, queries=queries, cfg=cfg, postal_code=zip_code
        )
        pull = _pull_from_result(
            store_id, zip_code, queries, result, "curl_cffi", prior_notes=notes
        )
        save_pull(pull)
        return pull

    # Opt-in browser engines only (local debugging — not for client)
    if allow_browser and engine in ("auto", "uc"):
        try:
            from uc_collector import collect_store_via_uc, uc_enabled

            if uc_enabled(cfg):
                result = collect_store_via_uc(
                    store_id, queries=queries, cfg=cfg, postal_code=zip_code
                )
                pull = _pull_from_result(
                    store_id, zip_code, queries, result, "uc", prior_notes=notes
                )
                save_pull(pull)
                if pull.ok or engine == "uc":
                    return pull
                notes.append(pull.notes or "uc failed")
        except Exception as e:
            notes.append(f"uc error: {type(e).__name__}: {e}")

    if allow_browser and engine in ("auto", "playwright"):
        pw = _collect_playwright(store_id, zip_code, queries, prior_notes=notes)
        save_pull(pw)
        return pw

    pull = PricePull(
        store_id=str(store_id),
        zip=zip_code,
        ok=False,
        mode="error",
        notes="; ".join(notes)
        or "no live backend — set OXYLABS_USERNAME/PASSWORD or SCRAPERAPI_KEY",
        proxy_used=cfg.proxy_enabled,
        scraped_at=_now(),
        queries=queries,
        engine=engine,
    )
    save_pull(pull)
    return pull


def _collect_playwright(
    store_id: str,
    zip_code: Optional[str],
    queries: List[str],
    prior_notes: Optional[List[str]] = None,
) -> PricePull:
    from walmart_parse import (
        is_challenge_html,
        is_likely_instore_product,
        parse_products_from_html,
    )
    cfg = get_collector_config()
    notes = list(prior_notes or [])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return PricePull(
            store_id=str(store_id),
            zip=zip_code,
            ok=False,
            mode="error",
            notes="; ".join(notes + ["Playwright not installed"]),
            proxy_used=cfg.proxy_enabled,
            scraped_at=_now(),
            queries=queries,
            engine="playwright",
        )

    products: List[PriceItem] = []
    seen = set()
    attempts = 0
    # Sticky ISP: one browser attempt only. Rotating sessions may retry.
    pw_attempts = max(1, cfg.max_retries if cfg.rotate_session else 1)

    for attempt in range(1, pw_attempts + 1):
        attempts = attempt
        try:
            with sync_playwright() as p:
                launch_kwargs: Dict[str, Any] = {"headless": True}
                proxy = cfg.first_playwright_proxy()
                if proxy:
                    launch_kwargs["proxy"] = proxy
                    notes.append(f"pw proxy={proxy.get('server')}")

                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                from http_collector import _candidate_urls, build_store_cookie_header

                cookie_header = build_store_cookie_header(
                    str(store_id), zip_code
                )
                pw_cookies = []
                for part in cookie_header.split("; "):
                    if "=" not in part:
                        continue
                    name, value = part.split("=", 1)
                    pw_cookies.append(
                        {
                            "name": name,
                            "value": value,
                            "domain": ".walmart.com",
                            "path": "/",
                        }
                    )
                context.add_cookies(pw_cookies)
                page = context.new_page()
                blocked = False

                for q in queries:
                    candidate_urls = _candidate_urls(str(store_id), q)
                    got_batch = False
                    for url, source in candidate_urls:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=int(cfg.timeout_sec * 1000),
                        )
                        page.wait_for_timeout(2500)
                        html = page.content()
                        final_url = page.url

                        batch = parse_products_from_html(
                            html, query=q, limit=cfg.max_per_query
                        )
                        filtered = []
                        for raw in batch:
                            raw["collection_source"] = source
                            if (
                                source in ("store_search", "store_page_search")
                                and raw.get("in_store") is None
                            ):
                                raw["in_store"] = True
                            if not is_likely_instore_product(raw):
                                continue
                            filtered.append(raw)
                        batch = filtered
                        if batch:
                            for raw in batch:
                                item = _dict_to_item(raw)
                                if item.product_id in seen:
                                    continue
                                seen.add(item.product_id)
                                products.append(item)
                            notes.append(
                                f"pw source={source} store={store_id} "
                                f"{q}:{len(batch)} instore_only=1"
                            )
                            got_batch = True
                            break

                        if is_challenge_html(html, final_url):
                            notes.append(
                                f"pw blocked source={source} query={q} attempt={attempt}"
                            )
                            blocked = True
                            break
                        notes.append(f"pw empty source={source} query={q}")

                    if blocked:
                        break
                    if not got_batch:
                        notes.append(f"pw empty query={q}")
                    page.wait_for_timeout(
                        int(random.uniform(cfg.min_delay_sec, cfg.max_delay_sec) * 1000)
                    )

                browser.close()

                if products:
                    return PricePull(
                        store_id=str(store_id),
                        zip=zip_code,
                        ok=True,
                        mode="live",
                        products=products,
                        notes="; ".join(n for n in notes if n),
                        proxy_used=cfg.proxy_enabled,
                        attempts=attempts,
                        scraped_at=_now(),
                        queries=queries,
                        engine="playwright",
                    )
                if blocked:
                    # Do not retry sticky IP
                    if (not cfg.rotate_session) or attempt >= pw_attempts:
                        return PricePull(
                            store_id=str(store_id),
                            zip=zip_code,
                            ok=False,
                            mode="blocked",
                            notes="; ".join(n for n in notes if n)
                            + ("; stopped to protect proxy IP" if not cfg.rotate_session else ""),
                            proxy_used=cfg.proxy_enabled,
                            attempts=attempts,
                            scraped_at=_now(),
                            queries=queries,
                            engine="playwright",
                        )
                    time.sleep(cfg.min_delay_sec * attempt)
                    continue
        except Exception as e:
            notes.append(f"pw attempt {attempt}: {type(e).__name__}: {e}")
            if attempt < pw_attempts:
                time.sleep(cfg.min_delay_sec * attempt)
                continue

    return PricePull(
        store_id=str(store_id),
        zip=zip_code,
        ok=False,
        mode="empty" if "empty" in ";".join(notes) else "error",
        notes="; ".join(n for n in notes if n),
        proxy_used=cfg.proxy_enabled,
        attempts=attempts,
        scraped_at=_now(),
        queries=queries,
        engine="playwright",
    )


# Back-compat
LiveProduct = PriceItem


def collect_store_products(store_id: str, **kwargs) -> Dict[str, Any]:
    pull = collect_store_prices(store_id, zip_code=kwargs.get("zip_code"), force=kwargs.get("force", False))
    return {
        "ok": pull.ok,
        "mode": pull.mode,
        "products": pull.products,
        "notes": pull.notes,
        "scraped_at": pull.scraped_at,
        "proxy_used": pull.proxy_used,
        "engine": pull.engine,
    }


def live_products_to_deals(
    store_id: str,
    products: List[PriceItem],
    min_discount_pct: float = 20.0,
    include_shelf: bool = False,
) -> List[Dict[str, Any]]:
    """Convert live pulls into ranked in-store DEAL rows (M3). Shelf-only dropped."""
    from deal_engine import DealThresholds, detect_deals

    thr = DealThresholds.from_env(
        {
            "min_discount_pct": min_discount_pct,
            "include_shelf": include_shelf,
            "deals_only": True,
            "drop_online_only": True,
        }
    )
    return detect_deals(store_id, products, thresholds=thr)
