"""
Milestone 3 — Deal Detection Engine.

Scores and filters markdown / clearance / hidden-clearance style deals,
ranks best deals for a store, and explains *why* each row is a deal.

Thresholds are tunable via env or DealThresholds so Hidden Clearances
can adjust later without code changes:
  DEAL_MIN_DISCOUNT_PCT=20
  DEAL_HIDDEN_CLEARANCE_PCT=70
  DEAL_CLEARANCE_PCT=40
  DEAL_MARKDOWN_PCT=20
  DEAL_INCLUDE_SHELF=1
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class DealThresholds:
    """Tunable deal rules (M3 deliverable)."""

    min_discount_pct: float = 20.0
    hidden_clearance_pct: float = 70.0
    clearance_pct: float = 40.0
    markdown_pct: float = 20.0
    include_shelf: bool = False  # deals only — no plain shelf rows
    prefer_offer_flags: bool = True
    drop_online_only: bool = True
    drop_out_of_stock: bool = False
    # Only keep items available for pickup at THIS store (kills marketplace clones).
    require_pickup: bool = True
    prefer_pickup: bool = True
    drop_unverified_deep_markdown: bool = True
    # Marketplace 3P sellers repeat the same "clearance" across every ZIP.
    walmart_seller_only: bool = True
    max_deals: int = 2000
    deals_only: bool = True  # drop minor_drop / shelf from output

    @classmethod
    def from_env(cls, overrides: Optional[Dict[str, Any]] = None) -> "DealThresholds":
        def _f(key: str, default: float) -> float:
            raw = os.environ.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _b(key: str, default: bool) -> bool:
            raw = os.environ.get(key)
            if raw is None or raw == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        t = cls(
            min_discount_pct=_f("DEAL_MIN_DISCOUNT_PCT", 20.0),
            hidden_clearance_pct=_f("DEAL_HIDDEN_CLEARANCE_PCT", 70.0),
            clearance_pct=_f("DEAL_CLEARANCE_PCT", 40.0),
            markdown_pct=_f("DEAL_MARKDOWN_PCT", 20.0),
            include_shelf=_b("DEAL_INCLUDE_SHELF", False),
            prefer_offer_flags=_b("DEAL_PREFER_OFFER_FLAGS", True),
            drop_online_only=_b("DEAL_DROP_ONLINE_ONLY", True),
            drop_out_of_stock=_b("DEAL_DROP_OOS", False),
            require_pickup=_b("DEAL_REQUIRE_PICKUP", True),
            prefer_pickup=_b("DEAL_PREFER_PICKUP", True),
            drop_unverified_deep_markdown=_b("DEAL_DROP_UNVERIFIED_DEEP", True),
            walmart_seller_only=_b("DEAL_WALMART_SELLER_ONLY", True),
            max_deals=int(_f("DEAL_MAX_DEALS", 2000)),
            deals_only=_b("DEAL_DEALS_ONLY", True),
        )
        if overrides:
            for k, v in overrides.items():
                if hasattr(t, k) and v is not None:
                    setattr(t, k, v)
        return t

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_deal(
    *,
    discount_pct: float,
    offer_type: Optional[str],
    is_price_event: bool,
    thresholds: DealThresholds,
) -> tuple[str, str, List[str]]:
    """Return (deal_type, confidence, why_reasons)."""
    why: List[str] = []
    ot = (offer_type or "").lower().strip()

    if thresholds.prefer_offer_flags and ot == "rollback":
        why.append("Walmart rollback flag")
        return "rollback", "high", why
    if thresholds.prefer_offer_flags and ot == "clearance":
        why.append("Walmart clearance flag")
        if discount_pct >= thresholds.hidden_clearance_pct:
            why.append(f"{discount_pct:.0f}% off list (hidden-clearance depth)")
            return "hidden_clearance", "high", why
        return "clearance", "high", why
    if thresholds.prefer_offer_flags and ot == "reducedprice":
        why.append("Walmart reduced-price flag")

    if discount_pct >= thresholds.hidden_clearance_pct:
        why.append(f"{discount_pct:.0f}% below list/was (>={thresholds.hidden_clearance_pct:.0f}% hidden clearance)")
        return "hidden_clearance", "high", why
    if discount_pct >= thresholds.clearance_pct:
        why.append(f"{discount_pct:.0f}% below list/was (>={thresholds.clearance_pct:.0f}% clearance)")
        return "clearance", "high", why
    if discount_pct >= thresholds.markdown_pct:
        why.append(f"{discount_pct:.0f}% markdown vs list/was")
        conf = "high" if discount_pct >= 30 else "medium"
        return "markdown", conf, why
    if is_price_event:
        why.append("Price-event / special-buy signal")
        return "special_buy", "medium", why
    if discount_pct > 0:
        why.append(f"Small drop ({discount_pct:.0f}%) — below deal threshold")
        return "minor_drop", "low", why
    why.append("Shelf price at selected store (no was/list compare)")
    return "shelf", "medium", why


def rank_score(deal: Dict[str, Any]) -> float:
    """Higher = better deal for ranking."""
    pct = float(deal.get("discount_pct") or 0)
    savings = float(deal.get("savings") or 0)
    conf = {"high": 1.0, "medium": 0.6, "low": 0.25}.get(
        str(deal.get("confidence") or "medium"), 0.5
    )
    type_boost = {
        "hidden_clearance": 25,
        "clearance": 15,
        "rollback": 18,
        "special_buy": 10,
        "markdown": 8,
        "shelf": 1,
        "minor_drop": 0,
    }.get(str(deal.get("deal_type") or ""), 0)
    stock_boost = 0.0
    if deal.get("out_of_stock") is True:
        stock_boost = -50.0
    elif deal.get("pickup_available") is True:
        stock_boost = 15.0
    elif deal.get("in_stock") is True:
        stock_boost = 5.0
    return pct * 2.0 + min(savings, 80) * 0.35 + type_boost + conf * 5 + stock_boost


def score_product(
    store_id: str,
    product: Any,
    thresholds: DealThresholds,
) -> Optional[Dict[str, Any]]:
    """Score one live PriceItem or product dict into a deal row (or None to drop)."""
    if hasattr(product, "to_dict"):
        p = product.to_dict()
    elif isinstance(product, dict):
        p = product
    else:
        return None

    if thresholds.drop_online_only and p.get("in_store") is False:
        return None
    if thresholds.drop_out_of_stock and (
        p.get("out_of_stock") is True or p.get("in_stock") is False
    ):
        return None
    if thresholds.require_pickup and p.get("pickup_available") is not True:
        return None
    if thresholds.walmart_seller_only:
        seller = str(p.get("seller_name") or "").strip().lower()
        if seller and "walmart" not in seller:
            return None

    current = _num(p.get("current_price"))
    if current is None or current <= 0:
        return None

    was = _num(p.get("was_price"))
    list_price = _num(p.get("list_price"))
    compare = None
    if was and was > current:
        compare = was
    elif list_price and list_price > current:
        compare = list_price

    offer_type = p.get("offer_type")
    is_event = bool(p.get("is_price_event"))
    pid = str(p.get("product_id") or "")

    if compare:
        savings = round(compare - current, 2)
        pct = round((savings / compare) * 100, 1)
        # Hard floor: only show deals at/above the user's threshold.
        if pct < thresholds.min_discount_pct:
            return None
        # National marketplace junk often has huge % off but no store pickup flag,
        # and repeats across every ZIP — drop those so store lists diverge.
        if (
            thresholds.drop_unverified_deep_markdown
            and p.get("pickup_available") is not True
            and pct >= 65.0
        ):
            return None
        deal_type, confidence, why = classify_deal(
            discount_pct=pct,
            offer_type=offer_type,
            is_price_event=is_event,
            thresholds=thresholds,
        )
        if deal_type in ("shelf", "minor_drop"):
            if not thresholds.include_shelf:
                return None
        why_text = "; ".join(why)
        if savings > 0:
            why_text += f"; save ${savings:.2f}"
        row = {
            "deal_id": f"live-{store_id}-{pid}",
            "store_id": store_id,
            "product_id": pid,
            "title": p.get("title"),
            "brand": p.get("brand"),
            "category": p.get("category") or "General",
            "current_price": round(current, 2),
            "list_price": round(float(compare), 2),
            "discount_pct": pct,
            "savings": savings,
            "deal_type": deal_type,
            "offer_type": offer_type,
            "confidence": confidence,
            "url": p.get("url"),
            "image_url": p.get("image_url"),
            "in_store": p.get("pickup_available") is True,
            "availability": p.get("availability"),
            "in_stock": p.get("in_stock"),
            "out_of_stock": p.get("out_of_stock"),
            "stock_status": p.get("stock_status") or p.get("availability"),
            "pickup_available": p.get("pickup_available"),
            "delivery_available": p.get("delivery_available"),
            "shipping_available": p.get("shipping_available"),
            "seller_name": p.get("seller_name"),
            "seller_type": p.get("seller_type"),
            "is_price_event": is_event,
            "why_deal": why_text,
            "notes": why_text,
        }
    else:
        # No list/was price → cannot prove discount % vs the slider. Skip.
        return None

    row["rank_score"] = round(rank_score(row), 2)
    return row


def detect_deals(
    store_id: str,
    products: Sequence[Any],
    thresholds: Optional[DealThresholds] = None,
    min_discount_pct: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """M3 entry: score, filter, rank products into deal rows."""
    thr = thresholds or DealThresholds.from_env()
    if min_discount_pct is not None:
        thr.min_discount_pct = float(min_discount_pct)

    deals: List[Dict[str, Any]] = []
    for p in products:
        row = score_product(store_id, p, thr)
        if not row:
            continue
        if thr.deals_only and str(row.get("deal_type") or "") in (
            "shelf",
            "minor_drop",
        ):
            continue
        pct = float(row.get("discount_pct") or 0)
        if pct < thr.min_discount_pct:
            continue
        # Require a real list/was compare price (no zero-% placeholder rows).
        if not row.get("list_price"):
            continue
        deals.append(row)

    deals.sort(
        key=lambda d: (
            # Prefer confirmed pickup so each store's top list looks local.
            0
            if (not thr.prefer_pickup) or d.get("pickup_available") is True
            else 1,
            -float(d.get("rank_score") or 0),
            -float(d.get("discount_pct") or 0),
            -float(d.get("savings") or 0),
            str(d.get("title") or ""),
        )
    )
    # Soft cap only — return whatever the store actually produced up to max_deals.
    cap = max(1, int(thr.max_deals))
    return deals[:cap]


def enrich_sample_deal(deal: Dict[str, Any], thresholds: DealThresholds) -> Dict[str, Any]:
    """Add why_deal / rank_score to sample Deal dicts."""
    pct = float(deal.get("discount_pct") or 0)
    deal_type, confidence, why = classify_deal(
        discount_pct=pct,
        offer_type=deal.get("offer_type"),
        is_price_event=False,
        thresholds=thresholds,
    )
    # Keep sample deal_type if already set more specifically
    if not deal.get("deal_type") or deal.get("deal_type") == "markdown":
        deal["deal_type"] = deal_type
    deal["confidence"] = deal.get("confidence") or confidence
    savings = float(deal.get("savings") or 0)
    why_text = "; ".join(why)
    if savings > 0:
        why_text += f"; save ${savings:.2f}"
    if deal.get("notes") and deal["notes"] not in why_text:
        why_text = f"{deal['notes']}; {why_text}"
    deal["why_deal"] = why_text
    deal["notes"] = why_text
    deal["rank_score"] = round(rank_score(deal), 2)
    return deal
