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
    max_deals: int = 60
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
            max_deals=int(_f("DEAL_MAX_DEALS", 60)),
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
    return pct * 2.0 + min(savings, 80) * 0.35 + type_boost + conf * 5


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
        # Hard floor: slider / min_discount_pct always wins (no offer-flag bypass)
        if pct < thresholds.min_discount_pct:
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
            "in_store": True if p.get("in_store") is None else p.get("in_store"),
            "availability": p.get("availability"),
            "seller_type": p.get("seller_type"),
            "is_price_event": is_event,
            "why_deal": why_text,
            "notes": why_text,
        }
    else:
        # No was/list price → cannot prove discount %; never pass a min-% slider
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
        # Absolute slider enforcement
        pct = float(row.get("discount_pct") or 0)
        if pct < thr.min_discount_pct:
            continue
        deals.append(row)

    deals.sort(
        key=lambda d: (
            -float(d.get("rank_score") or 0),
            -float(d.get("discount_pct") or 0),
            -float(d.get("savings") or 0),
            str(d.get("title") or ""),
        )
    )
    return deals[: max(1, int(thr.max_deals))]


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
