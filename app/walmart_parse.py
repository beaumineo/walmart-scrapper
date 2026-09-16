"""
Walmart __NEXT_DATA__ parsing + bot-challenge detection.

Patterns adapted from:
- triposat/walmart-price-monitor (challenge markers, priceDisplayCodes, offer types)
- ChocoData-com/walmart-product-scraper (parse-first, then decide block)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# Soft challenge returns HTTP 200. Narrow markers only (triposat).
# Do NOT use "perimeterx" / "px-captcha" alone — Walmart CSP lists those on real pages.
CHALLENGE_MARKERS = (
    "activate and hold the button",  # Akamai Press & Hold UI
    "<title>robot or human",         # title tag on challenge page only
)

BLOCKED_PATH = "/blocked"

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def is_challenge_html(html: str, final_url: str = "") -> bool:
    # Hard block: redirect landed on /blocked
    if final_url and BLOCKED_PATH in final_url:
        return True
    body = (html or "").lower()
    for marker in CHALLENGE_MARKERS:
        if marker in body:
            return True
    return False


def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    m = NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _coerce_price(node: Any) -> Optional[float]:
    if node is None:
        return None
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, str):
        try:
            return float(node.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
    if isinstance(node, dict):
        if "price" in node:
            return _coerce_price(node.get("price"))
        for k in ("amount", "value", "minPrice", "currentPrice"):
            if k in node:
                return _coerce_price(node.get(k))
    return None


def extract_offer_type(price_display_codes: Any) -> Optional[str]:
    if not isinstance(price_display_codes, dict):
        return None
    for key in ("rollback", "clearance", "reducedPrice"):
        if price_display_codes.get(key):
            return key
    return None


def _as_dict(node: Any) -> Dict[str, Any]:
    return node if isinstance(node, dict) else {}


def _text_blob(*parts: Any) -> str:
    bits: List[str] = []
    for p in parts:
        if p is None:
            continue
        if isinstance(p, str):
            bits.append(p)
        elif isinstance(p, (int, float, bool)):
            bits.append(str(p))
        elif isinstance(p, dict):
            bits.extend(str(v) for v in p.values() if isinstance(v, (str, int, float, bool)))
        elif isinstance(p, list):
            for v in p:
                if isinstance(v, (str, int, float, bool)):
                    bits.append(str(v))
                elif isinstance(v, dict):
                    bits.extend(
                        str(x) for x in v.values() if isinstance(x, (str, int, float, bool))
                    )
    return " ".join(bits).lower()


def extract_fulfillment_flags(node: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    """
    Infer in-store / online signals from a Walmart product tile/node.
    Returns in_store / online as True/False/None (unknown).
    """
    fulfill = _as_dict(node.get("fulfillmentOptions") or node.get("fulfillment"))
    summary = _as_dict(node.get("fulfillmentSummary"))
    speed = _as_dict(node.get("fulfillmentSpeed"))
    badge = node.get("fulfillmentBadge") or node.get("fulfillmentType") or ""
    label = node.get("fulfillmentLabel") or node.get("fulfillmentTitle") or ""

    blob = _text_blob(
        badge,
        label,
        fulfill,
        summary,
        speed,
        node.get("availabilityStatusV2"),
        node.get("availabilityStatus"),
        node.get("availability"),
        node.get("pickupOption"),
        node.get("shippingOption"),
    )

    in_store: Optional[bool] = None
    online: Optional[bool] = None

    store_keys = (
        "in-store",
        "in store",
        "pickup",
        "pick up",
        "store only",
        "fulfillment_method:in-store",
        "store pickup",
    )
    if any(k in blob for k in store_keys):
        in_store = True
    if "online only" in blob or "shipping only" in blob:
        online = True
        if in_store is None:
            in_store = False
    elif any(k in blob for k in ("shipping", "ship to home", "delivery", "sold and shipped")):
        online = True

    for key in (
        "availableForPickup",
        "isAvailableForPickup",
        "pickupAvailable",
        "storeAvailable",
    ):
        if key in node:
            in_store = bool(node.get(key))
            break

    nested_opts = fulfill.get("fulfillmentOptions")
    if isinstance(nested_opts, list):
        for opt in nested_opts:
            if not isinstance(opt, dict):
                continue
            typ = str(opt.get("type") or opt.get("fulfillmentType") or "").upper()
            if typ in ("PICKUP", "STORE", "IN_STORE", "INSTORE"):
                in_store = True
            if typ in ("SHIPPING", "DELIVERY", "SHIP"):
                online = True

    seller = str(node.get("sellerName") or node.get("seller") or "").strip().lower()
    seller_type = str(node.get("sellerType") or "").strip().upper()
    # Marketplace 3P listings are not Walmart shelf / in-store clearance
    if seller_type in ("EXTERNAL", "MARKETPLACE", "THIRD_PARTY", "3P"):
        if in_store is None:
            in_store = False
        online = True if online is None else online
    elif (
        seller
        and seller not in ("walmart", "walmart.com", "walmart stores")
        and "walmart" not in seller
    ):
        if in_store is not True:
            in_store = False
            online = True if online is None else online

    return {"in_store": in_store, "online": online}


def is_likely_instore_product(item: Dict[str, Any], *, require_signal: bool = False) -> bool:
    """Keep shelf/pickup items; drop clear online-marketplace junk."""
    if item.get("in_store") is True:
        return True
    if item.get("in_store") is False:
        return False

    seller = str(item.get("seller_name") or "").strip().lower()
    seller_type = str(item.get("seller_type") or "").strip().upper()
    if seller_type in ("EXTERNAL", "MARKETPLACE", "THIRD_PARTY", "3P"):
        return False
    if (
        seller
        and seller not in ("walmart", "walmart.com", "walmart stores")
        and "walmart" not in seller
    ):
        return False

    source = str(item.get("collection_source") or "")
    if source in ("store_search", "store_page_search"):
        return True
    if require_signal:
        return False
    return source.startswith("store_bound") or not source


def normalize_product_node(node: Dict[str, Any], query: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Normalize a Walmart product-like node into a flat dict."""
    if not isinstance(node, dict):
        return None
    title = node.get("name") or node.get("title") or node.get("productName")
    if not title:
        return None

    price_info = node.get("priceInfo") if isinstance(node.get("priceInfo"), dict) else {}
    current = (
        _coerce_price(price_info.get("currentPrice"))
        or _coerce_price(node.get("currentPrice"))
        or _coerce_price(price_info.get("linePrice"))
        or _coerce_price(node.get("price"))
    )
    if current is None:
        return None

    was = _coerce_price(price_info.get("wasPrice")) or _coerce_price(node.get("wasPrice"))
    list_price = (
        _coerce_price(price_info.get("listPrice"))
        or _coerce_price(node.get("listPrice"))
        or was
    )
    if was is not None and was <= current:
        was = None
    if list_price is not None and list_price <= current:
        list_price = was

    unit_obj = price_info.get("unitPrice") if isinstance(price_info.get("unitPrice"), dict) else {}
    unit_price_display = unit_obj.get("priceString") if unit_obj else None

    offer_type = extract_offer_type(price_info.get("priceDisplayCodes"))
    is_reduced = bool(price_info.get("isPriceReduced")) or bool(offer_type)

    event_attrs = node.get("eventAttributes") if isinstance(node.get("eventAttributes"), dict) else {}
    is_price_event = bool(event_attrs.get("priceFlip") or event_attrs.get("specialBuy"))

    avail_v2 = node.get("availabilityStatusV2") if isinstance(node.get("availabilityStatusV2"), dict) else {}
    availability = avail_v2.get("display") or node.get("availabilityStatus") or node.get("availability")
    availability_code = node.get("availabilityStatus")

    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("displayName")

    pid = str(
        node.get("usItemId")
        or node.get("id")
        or node.get("itemId")
        or node.get("productId")
        or title
    )

    canonical = node.get("canonicalUrl") or node.get("url") or ""
    if canonical and not str(canonical).startswith("http"):
        canonical = "https://www.walmart.com" + str(canonical)

    image = None
    img = node.get("imageInfo") if isinstance(node.get("imageInfo"), dict) else {}
    if img:
        image = img.get("thumbnailUrl") or img.get("url")
        if not image:
            all_imgs = img.get("allImages") or []
            if all_imgs and isinstance(all_imgs[0], dict):
                image = all_imgs[0].get("url")

    category = node.get("departmentName") or node.get("category") or "General"
    if not isinstance(category, str):
        category = "General"

    rating_value = node.get("averageRating")
    rating = f"{rating_value:.1f}" if isinstance(rating_value, (int, float)) else None
    review_count_value = node.get("numberOfReviews")
    review_count = int(review_count_value) if isinstance(review_count_value, (int, float)) else None

    fulfill_flags = extract_fulfillment_flags(node)

    return {
        "product_id": pid,
        "title": str(title).strip(),
        "brand": brand if isinstance(brand, str) else None,
        "category": category,
        "current_price": round(float(current), 2),
        "list_price": round(float(list_price), 2) if list_price else None,
        "was_price": round(float(was), 2) if was else None,
        "unit_price_display": unit_price_display,
        "offer_type": offer_type,
        "is_reduced": is_reduced,
        "is_price_event": is_price_event,
        "seller_name": node.get("sellerName"),
        "seller_type": node.get("sellerType"),
        "availability": availability if isinstance(availability, str) else None,
        "availability_code": availability_code if isinstance(availability_code, str) else None,
        "in_store": fulfill_flags["in_store"],
        "online": fulfill_flags["online"],
        "url": str(canonical) if canonical else f"https://www.walmart.com/ip/{pid}",
        "image_url": image,
        "rating": rating,
        "review_count": review_count,
        "query": query,
    }


def find_product_nodes(next_data: Dict[str, Any], limit: int = 80) -> List[Dict[str, Any]]:
    """
    Collect product-like nodes from search or item __NEXT_DATA__.
    Prefer nodes with usItemId + name + priceInfo (ChocoData style).
    """
    found: List[Dict[str, Any]] = []
    seen_ids = set()

    # Direct product page path
    try:
        direct = next_data["props"]["pageProps"]["initialData"]["data"]["product"]
        if isinstance(direct, dict):
            found.append(direct)
            pid = direct.get("usItemId")
            if pid:
                seen_ids.add(str(pid))
    except (KeyError, TypeError):
        pass

    queue: List[Any] = [next_data]
    seen_obj = set()
    while queue and len(found) < limit:
        node = queue.pop(0)
        if isinstance(node, dict):
            oid = id(node)
            if oid in seen_obj:
                continue
            seen_obj.add(oid)
            if (
                (node.get("usItemId") or node.get("id"))
                and (node.get("name") or node.get("title"))
                and (node.get("priceInfo") or node.get("currentPrice") is not None)
            ):
                pid = str(node.get("usItemId") or node.get("id"))
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    found.append(node)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    queue.append(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    queue.append(v)
    return found


def parse_products_from_html(
    html: str,
    query: Optional[str] = None,
    limit: int = 80,
    *,
    instore_only: bool = False,
) -> List[Dict[str, Any]]:
    nd = extract_next_data(html)
    if not nd:
        return []
    nodes = find_product_nodes(nd, limit=max(limit * 3, limit))
    out: List[Dict[str, Any]] = []
    seen = set()
    for node in nodes:
        item = normalize_product_node(node, query=query)
        if not item:
            continue
        if item["product_id"] in seen:
            continue
        if instore_only and not is_likely_instore_product(item):
            continue
        seen.add(item["product_id"])
        out.append(item)
        if len(out) >= limit:
            break
    return out


# Product IDs that appeared across every store in old national deals-hub pulls.
_NATIONAL_CLEARANCE_CORE = frozenset(
    {
        "1010276517",
        "1080807668",
        "907964471",
        "5106025363",
        "5578251950",
        "15936364385",
        "10360363078",
        "1236104786",
        "12499662768",
        "14623665243",
    }
)


def product_id_set(products: Any) -> set:
    ids = set()
    for p in products or []:
        if isinstance(p, dict):
            pid = p.get("product_id")
        else:
            pid = getattr(p, "product_id", None)
        if pid:
            ids.add(str(pid))
    return ids


def looks_like_national_duplicate(products: Any, *, min_hit: int = 4) -> bool:
    """True when the product set matches the known national clearance list."""
    ids = product_id_set(products)
    if len(ids) < 3:
        return False
    hits = len(ids & _NATIONAL_CLEARANCE_CORE)
    if hits >= min_hit:
        return True
    if hits >= 3 and hits / max(len(ids), 1) >= 0.35:
        return True
    return False


def jaccard_ids(a: Any, b: Any) -> float:
    sa = product_id_set(a)
    sb = product_id_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


def overlaps_other_store_cache(
    store_id: str,
    products: Any,
    *,
    pulls_dir: Optional[Path] = None,
    threshold: float = 0.72,
    min_ids: int = 8,
) -> Optional[str]:
    """
    Return other store_id if this product set is nearly identical to another
    store's latest or recent pull (the cross-store clone bug).
    """
    from config import PULLS_DIR

    sid = str(store_id)
    ids = product_id_set(products)
    if len(ids) < min_ids:
        return None
    root = pulls_dir or PULLS_DIR
    if not root.exists():
        return None

    candidates: List[Path] = list(root.glob("latest_*.json"))
    # Also scan recent per-store pulls so deleted latest_* cannot resurrect clones
    for path in sorted(root.glob("pull_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
        :80
    ]:
        candidates.append(path)

    seen_files = set()
    for path in candidates:
        if path in seen_files or not path.exists():
            continue
        seen_files.add(path)
        name = path.stem
        if name.startswith("latest_"):
            other = name.replace("latest_", "", 1)
        elif name.startswith("pull_"):
            # pull_{store}_{timestamp}
            parts = name.split("_")
            other = parts[1] if len(parts) >= 3 else ""
        else:
            continue
        if not other or other == sid:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("store_id") or "") not in ("", other):
            # mismatched filename/payload — still use payload store_id
            other = str(payload.get("store_id") or other)
            if other == sid:
                continue
        other_products = payload.get("products") or []
        if len(product_id_set(other_products)) < min_ids:
            continue
        if jaccard_ids(ids, other_products) >= threshold:
            return other
    return None
