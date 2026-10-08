# Walmart Deal Finder — Phase 2

**Full-store inventory scan (DealHawk-style coverage)**

---

## Scope

Backend + hosted module that builds **store-wide inventory** for selected Walmart store(s), continuously refreshes prices/stock (via Oxylabs, to handle Walmart bot protection), and surfaces **markdown / hidden-clearance style deals** for the store the user picks — for Hidden Clearances.

**Phase 1 (done):** ZIP → nearby store → search-based clearance/rollback + pickup verify → deal report.  
**Phase 2 (this):** Catalog-wide scan per store → inventory DB → scheduled refresh → much higher deal recall (closer to “every item” / IG-style deep clearance finds).

**Out of scope unless added later:** aisle GPS, community feed, Target/DG/other retailers, or guaranteeing 100% of every viral shelf-sticker-only find.

**Oxylabs / proxy usage:** billed **pass-through at cost** (or cost + 15–20%) on top of build fees — scales with # of stores × refresh speed.

---

## Suggested project fee

**Build total: $6,000** (fixed)  
**Deposit (Milestone 0): $1,800 (30%)**  
**Monthly after launch (optional retainer):** $800–$1,500 ops/monitoring + Oxylabs pass-through  

*(Adjust store count / refresh cadence before kickoff if needed.)*

---

## Milestone 0: Kickoff and Specs — $1,800 (deposit 30%)

- Confirm Phase 2 deal definition (e.g. % off list, clearance flags, was/now gap, pickup-required)
- Agree how many stores (1 / 3 / 5+) and refresh cadence (1× daily / several× day / aggressive)
- Agree monthly Oxylabs ceiling / hard budget cap
- Agree API additions (scan status, deals-from-DB, optional webhooks)
- Agree success criteria (pilot store/ZIP, target deal volume, cross-store lists must differ)
- Shared Discord/Slack + email for updates
- **Deliverable:** 1-page Phase 2 requirements both sides approve

---

## Milestone 1: Inventory Backbone — $1,200 ✅

- Database: `inv_stores`, `inv_skus`, `inv_store_inventory`, `inv_scan_runs`, `inv_scan_jobs`
- Scan orchestrator: job queue, workers, rate limits, idempotent runs (`app/inventory_scan.py`)
- Seed SKU universe from clearance + department waves
- APIs: `POST/GET /api/inventory/scans`, `GET /api/inventory/stores/{store_id}`
- **Deliverable:** Pilot `#5686` seed scan writes inventory to DB; status visible via API  
  *(verified: 5 queries → 255 SKUs, 93 markdown candidates)*

---

## Milestone 2: Full-Store Collection Waves — $1,200

- Wave A: clearance / rollback / special buy / clearance-{department}
- Wave B: top departments (electronics, toys, home, grocery, apparel, kitchen, outdoor, …)
- Wave C: pagination / long-tail SKUs
- Wave D: re-check known SKUs for price/stock deltas (silent markdowns)
- Oxylabs `walmart_search` + `walmart_product` (store_id + ZIP) behind bot protection
- Basic reliability: retries, 401/429 handling, clear errors
- **Deliverable:** Sample full-ish scan for 1–2 agreed test stores; SKU coverage report

---

## Milestone 3: Store-Accurate Stock + Anti-Clone — $800

- Product-level pickup verify at **selected store only** (fix Phase 1: search flags lie)
- Never show national “unconfirmed” clones as in-store deals
- Walmart-seller rules; drop marketplace 3P junk that repeats every ZIP
- Log overlap metrics (store A vs store B)
- **Deliverable:** Deals for store A are not a copy of store B; pilot comparison doc

---

## Milestone 4: Deal Engine at Scale + API Handoff — $600

- Extend deal scoring/filter (markdown %, clearance/hidden clearance, rank, tunable thresholds)
- Price history: detect drops vs prior scan
- Fast path: `/api/deals` reads inventory DB for selected store; optional `refresh=1` priority re-scan
- Docs: auth, request/response examples, rate limits, Railway/env knobs
- **Deliverable:** Documented API + example responses for Hidden Clearances integration

---

## Milestone 5: Always-On Refresh, QA, Acceptance — $400 (final)

- Schedulers: e.g. full wave nightly + hot departments every few hours (per agreed cadence)
- Ops signals: last scan age, deal count, daily API burn; alert on Oxylabs auth failure
- UI polish: “Scanned X ago · N items · M deals ≥ filter %”
- Optional: webhook/alert when deep markdown (≥70%) appears at user’s stores
- Test across agreed US regions/stores; fix issues inside agreed scope
- Final acceptance against Milestone 0 checklist
- **Deliverable:** Production-ready Phase 2 module on Railway

---

## Payment schedule (summary)

| Milestone | Amount |
|-----------|--------|
| M0 Kickoff / Specs (deposit) | $1,800 |
| M1 Inventory Backbone | $1,200 |
| M2 Full-Store Collection | $1,200 |
| M3 Store-Accurate Stock | $800 |
| M4 Deal Engine + API | $600 |
| M5 Always-On + QA | $400 |
| **Total build** | **$6,000** |

**Not included in build total:** Oxylabs/proxy invoices (pass-through), extra stores beyond pilot (tune + burn), multi-retailer.

---

## Timeline

- **1-store pilot:** ~4–6 weeks after M0 sign-off  
- **Extra stores:** mostly ops + Oxylabs cost (+ light tuning), not a full rewrite  

---

## Success criteria (acceptance examples)

To lock in M0 (edit numbers together):

1. Pilot store (e.g. `#5686` / ZIP `90210`) after full scan: **≥300 deals ≥20%** with confirmed pickup (varies by store inventory)  
2. Second store comparison: top-50 SKU overlap **&lt; 40%** (not identical national lists)  
3. UI/API shows **last scanned** time and deal counts for the **selected** store only  
4. Health endpoint shows Oxylabs OK + last scan age  

---

## One-liner (Discord)

> Phase 2 = always-on full-store scanner for your selected Walmarts (catalog waves + pickup-accurate prices) so we can surface DealHawk-style deep clearance—not just search hits. ~$6k build over 4–6 weeks for a 1-store pilot; Oxylabs usage is monthly pass-through based on stores × refresh speed.
