# Walmart Deal Finder — Requirements (Milestone 0–5)

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
| Hidden clearance | ≥ 70% or deep clearance flag | Hidden clearance depth |
| Rollback | Walmart `rollback` flag | Still subject to `min_discount_pct` floor |
| Shelf (optional) | `DEAL_INCLUDE_SHELF=1` | Live shelf price without was/list |

Tunable via env / `GET /api/deals/config`.

Each deal row includes **`why_deal`** and **`rank_score`**.

---

## API (integration)

Primary:

- `GET /api/stores?zip=90210`
- `GET /api/deals?zip=90210&store_id=XXXX&min_discount_pct=20&mode=live`

Also: `/api/deals/config`, `/api/report`, `/health`, OpenAPI at `/docs`.

Full docs: `docs/API.md` · handoff: `docs/HANDOFF.md` · examples: `docs/examples/`.

Optional auth: set `API_KEY` → require `X-API-Key` on `/api/*`.

---

## Success criteria

| # | Criterion |
|---|-----------|
| 1 | Any US ZIP returns nearby Walmart stores |
| 2 | User can select a store by ID / name / address |
| 3 | Report is store-scoped — stores must not share one national list |
| 4 | Deals ranked with `why_deal` and tunable % thresholds |
| 5 | Live pulls via Oxylabs (or ScraperAPI / Unlocker) |
| 6 | Test ZIPs: `90210`, `10001`, `75201`, `30301`, `90810` |

---

## Milestone map

| M | Payment | Deliverable | Status |
|---|---------|-------------|--------|
| 0 Kickoff | $600 | This requirements doc | Done |
| 1 Location | $400 | `/api/stores` + UI store picker | Done |
| 2 Prices | $400 | Store-scoped collection + `data/pulls/` | Done |
| 3 Deal engine | $300 | Scoring, ranking, `why_deal`, thresholds | Done |
| 4 API + handoff | $200 | Documented API + examples + handoff notes | Done |
| 5 Final QA | $100 | Multi-region acceptance + production-ready | Done |
| **Total** | **$2,000** | | |

---

## Known constraint (live)

Walmart blocks many automated requests. Live pulls use Oxylabs Walmart Search (`store_id` + `delivery_zip`). The app never falls back to the national `/shop/deals/clearance` hub for store reports.
