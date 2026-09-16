# Walmart Deal Finder — Requirements (Milestone 0)

**Product:** Backend module for [Hidden Clearances](https://www.hiddenclearances.com/)  
**Scope:** ZIP → nearby Walmart stores → store-scoped prices → ranked markdown / clearance deals  
**Fee / timeline:** $2,000 fixed · ~2–3 weeks (completion-based)

---

## Deal definition

A **deal** is a product at the **selected store** where one or more apply:

| Rule | Default | Meaning |
|------|---------|---------|
| Markdown % | ≥ 20% | `(list_or_was − current) / list_or_was` |
| Clearance | ≥ 40% or Walmart `clearance` flag | Strong cut |
| Hidden clearance | ≥ 70% or deep clearance flag | “Hidden clearance” style |
| Rollback | Walmart `rollback` flag | Counted even if % is lower |
| Shelf (optional) | `DEAL_INCLUDE_SHELF=1` | Live shelf price without was/list |

Tunable via env / `GET /api/deals/config` (`DEAL_MIN_DISCOUNT_PCT`, `DEAL_HIDDEN_CLEARANCE_PCT`, …).

Each deal row includes **`why_deal`** (human-readable reason) and **`rank_score`**.

---

## API output (agreed shape)

### `GET /api/stores?zip=90210`
Nearby stores: `store_id`, `name`, `address`, `city`, `state`, `zip`, `distance_miles`, …

### `GET /api/deals?zip=90210&store_id=XXXX&min_discount_pct=20&mode=auto|live|sample`
```json
{
  "zip": "90210",
  "store": { "store_id": "...", "name": "...", "address": "..." },
  "summary": { "deal_count": 0, "avg_discount_pct": 0, "max_discount_pct": 0, "total_savings_if_bought_all": 0 },
  "deals": [
    {
      "deal_id": "...",
      "title": "...",
      "current_price": 0,
      "list_price": 0,
      "discount_pct": 0,
      "savings": 0,
      "deal_type": "hidden_clearance|clearance|rollback|markdown|shelf",
      "confidence": "high|medium|low",
      "why_deal": "…",
      "rank_score": 0,
      "url": "https://www.walmart.com/...",
      "in_store": true
    }
  ],
  "meta": { "data_mode": "live|live_cached|sample|failed", "live_ok": false, "deal_thresholds": {}, "milestone": 3 }
}
```

### Also
- `GET /api/prices?store_id=&zip=` — raw M2 price pull  
- `GET /api/deals/config` — M3 thresholds  
- `GET /health` — readiness + `milestone: 3`

---

## Success criteria

| # | Criterion |
|---|-----------|
| 1 | Any US ZIP returns nearby Walmart stores (official list preferred) |
| 2 | User can select a store by ID / name / address |
| 3 | Report is **store-scoped** — stores must **not** share one national clearance list |
| 4 | Deals ranked with `why_deal` and tunable % thresholds |
| 5 | Sample / demo mode works without proxy (`mode=sample`); live needs `PROXIES` |
| 6 | Test ZIPs: `90210`, `10001`, `75201`, `30301`, `90810` |

---

## Milestone map

| M | Payment | Deliverable |
|---|---------|-------------|
| 0 Kickoff | $600 | This requirements doc |
| 1 Location | $400 | `/api/stores` + UI store picker |
| 2 Prices | $400 | Store-scoped collection + pulls under `data/pulls/` |
| 3 Deal engine | $300 | Scoring, ranking, `why_deal`, thresholds |

---

## Known constraint (live)

Walmart blocks many automated requests. Live pulls require a residential/ISP proxy (e.g. Bright Data). The app **never** falls back to the national `/shop/deals/clearance` hub for store reports — that was the root cause of identical product lists across stores.
