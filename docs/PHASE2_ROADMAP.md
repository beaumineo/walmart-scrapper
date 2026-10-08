# Walmart Deal Finder — Phase 2

**Full-store inventory scan (DealHawk-style coverage)**

---

## Scope

Backend + hosted module that builds **store-wide inventory** for selected Walmart stores, continuously refreshes prices/stock via Oxylabs (bot protection), and surfaces **markdown / hidden-clearance** deals for the store the user picks.

**Phase 1 (done):** ZIP → nearby store → search-based clearance/rollback → pickup verify → deal report.

**Phase 2:** Catalog-wide scan per store → inventory DB → scheduled refresh → much higher deal recall (closer to “every item” / IG-style deep clearance).

**Oxylabs:** monthly **pass-through** based on stores × refresh speed (not included in build fee).

---

## Milestone map (client doc)

| Client milestone | Name | Status |
|------------------|------|--------|
| **1** | Kickoff and Specs (deposit 30%) | Pending client sign-off |
| **2** | Inventory Backbone | **Done in code** (SQLite now; Supabase optional next) |
| **3** | Full-Store Collection Waves | Next |
| **4** | Store-Accurate Stock and Anti-Clone | Pending |
| **5** | Deal Engine at Scale and API Handoff | Pending |
| **6** | Always-On Refresh, QA, Acceptance | Pending |

> Note: An earlier internal draft numbered Inventory Backbone as “M1”. This doc matches **your** Phase 2 client milestones (Kickoff = M1, Backbone = M2).

---

## Milestone 1: Kickoff and Specs (deposit 30%)

- Confirm Phase 2 deal definition (% off list, clearance flags, was/now gap, pickup required)
- Agree how many stores (1 / 3 / 5+) and refresh cadence
- Agree monthly Oxylabs ceiling and hard budget cap
- Agree API additions (scan status, deals-from-DB, optional webhooks)
- Agree success criteria (pilot store/ZIP, target deal volume, cross-store lists must differ)
- Shared Discord / Slack / Telegram / email for updates

**Deliverable:** 1-page Phase 2 requirements both sides approve

---

## Milestone 2: Inventory Backbone ✅ (implemented)

- Database: stores, SKUs, per-store inventory (price, was/list, pickup, OOS, updated_at), scan runs  
  - **Shipped:** SQLite tables `inv_*` in `deals.db` (`app/inventory_db.py`)  
  - **Your note:** Supabase — not wired yet; can migrate in a follow-up if you want hosted Postgres
- Scan orchestrator: job queue, workers, rate limits, idempotent runs (`app/inventory_scan.py`)
- Seed SKU universe from deep clearance + department waves
- `scan_status` API: progress, SKU count, last success

**APIs**
- `POST /api/inventory/scans`
- `GET /api/inventory/scans/{id}`
- `GET /api/inventory/scans`
- `GET /api/inventory/stores/{store_id}`

**Deliverable:** One pilot store scanned into DB; status visible via API  
**Verified:** `#5686` / `90210` — 5 seed queries → **255 SKUs**, 93 markdown candidates

```bash
set PYTHONPATH=app
python scripts/run_inventory_scan.py --store-id 5686 --zip 90210 --max-queries 8 --sync
```

---

## Milestone 3: Full-Store Collection Waves

- Wave A: clearance / rollback / special buy / clearance-{department}
- Wave B: top departments (electronics, toys, home, grocery, apparel, kitchen, outdoor, …)
- Wave C: pagination / long-tail SKUs
- Wave D: re-check known SKUs for price/stock deltas (silent markdowns)
- Oxylabs `walmart_search` + `walmart_product` (store_id + ZIP) behind bot protection
- Basic reliability: retries, 401/429 handling, clear errors

**Deliverable:** Sample full-ish scan for 1–2 agreed test stores; SKU coverage report

---

## Milestone 4: Store-Accurate Stock and Anti-Clone

- Product-level pickup verify at selected store only
- Never show national “unconfirmed” clones as in-store deals
- Walmart-seller rules; drop marketplace 3P junk that repeats every ZIP
- Log overlap metrics (store A vs store B)

**Deliverable:** Deals for store A are not a copy of store B; pilot comparison doc

---

## Milestone 5: Deal Engine at Scale and API Handoff

- Extend deal scoring/filter (markdown %, clearance/hidden clearance, rank, tunable thresholds)
- Price history: detect drops vs prior scan
- Fast path: `/api/deals` reads inventory DB for selected store; optional `refresh=1` priority re-scan
- Docs: auth, request/response examples, rate limits, Railway/env knobs

**Deliverable:** Documented API + deals served from inventory DB (fast path)  
*(Note: your draft accidentally repeated M4’s deliverable here — corrected above.)*

---

## Milestone 6: Always-On Refresh, QA, Acceptance

- Schedulers: full wave nightly + hot departments every few hours (per agreed cadence)
- Ops signals: last scan age, deal count, daily API burn; alert on Oxylabs auth failure
- UI polish: “Scanned X ago · N items · M deals ≥ filter %”
- Optional: webhook/alert when deep markdown (≥70%) appears at user’s stores
- Test across agreed US regions/stores; fix issues inside agreed scope
- Final acceptance against Milestone 1 checklist

**Deliverable:** Production-ready Phase 2 module on Railway

---

## One-liner

> Phase 2 = always-on full-store scanner for selected Walmarts (catalog waves + pickup-accurate prices) so we can surface DealHawk-style deep clearance — not just search hits. Oxylabs usage is monthly pass-through based on stores × refresh speed.
