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
| **1** | Kickoff and Specs (deposit 30%) | Specs open; **Discord** for updates (`DISCORD_WEBHOOK_URL`) |
| **2** | Inventory Backbone | **Done** (SQLite; Supabase optional next) |
| **3** | Full-Store Collection Waves | **Done** |
| **4** | Store-Accurate Stock and Anti-Clone | **Done** |
| **5** | Deal Engine at Scale and API Handoff | **Done** |
| **6** | Always-On Refresh, QA, Acceptance | **Done** (client smoke / sign-off open) |

> Note: An earlier internal draft numbered Inventory Backbone as “M1”. This doc matches **your** Phase 2 client milestones (Kickoff = M1, Backbone = M2).

---

## Milestone 1: Kickoff and Specs (deposit 30%)

- Confirm Phase 2 deal definition (% off list, clearance flags, was/now gap, pickup required)
- Agree how many stores (1 / 3 / 5+) and refresh cadence
- Agree monthly Oxylabs ceiling and hard budget cap
- Agree API additions (scan status, deals-from-DB, optional webhooks)
- Agree success criteria (pilot store/ZIP, target deal volume, cross-store lists must differ)
- Shared channel for updates → **Discord webhook** (`DISCORD_WEBHOOK_URL`)
  - Posts when inventory scans finish; optional overlap / verify notices
  - Paste your channel webhook into Railway / `.env` (never commit the secret)

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

## Milestone 3: Full-Store Collection Waves ✅ (implemented)

- Wave A: clearance / rollback / special buy / clearance-{department}
- Wave B: top departments (electronics, toys, home, grocery, apparel, kitchen, outdoor, …)
- Wave C: pagination / long-tail SKUs
- Wave D: re-check known SKUs for price/stock deltas (`walmart_product`)
- Oxylabs `walmart_search` + `walmart_product` (store_id + ZIP)
- Reliability: retries, hard-stop on 401, backoff on 429
- Coverage: `GET /api/inventory/stores/{id}/coverage` + CLI `--coverage`

**APIs / CLI**
- `POST /api/inventory/scans` with `wave`: `seed|A|B|C|D|full`, optional `recheck_limit`
- `GET /api/inventory/stores/{store_id}/coverage`

```bash
set PYTHONPATH=app
python scripts/run_inventory_scan.py --store-id 5686 --zip 90210 --wave A --max-queries 12 --sync
python scripts/run_inventory_scan.py --store-id 5686 --wave D --recheck-limit 20 --sync
python scripts/run_inventory_scan.py --store-id 5686 --coverage
```

**Deliverable:** Sample full-ish scan for 1–2 agreed test stores; SKU coverage report  
**Verified:** `#5686` — Wave A (+32 SKUs → **287**), Wave D product recheck ×3, coverage: 110 markdown / 38 ≥40%

---

## Milestone 4: Store-Accurate Stock and Anti-Clone ✅ (implemented)

- Product-level pickup verify at selected store only (`POST /api/inventory/verify`)
- Deals from inventory: pickup-confirmed + Walmart-seller only (`GET …/deals`)
- Never show national “unconfirmed” clones as in-store deals
- Drop marketplace 3P junk that repeats every ZIP
- Overlap metrics store A vs B (`GET /api/inventory/overlap`)

```bash
set PYTHONPATH=app
python scripts/run_inventory_verify.py --store-id 5686 --max-verify 20
python scripts/run_inventory_verify.py --store-id 5686 --deals
python scripts/run_inventory_verify.py --overlap 5686 5930
```

**Deliverable:** Deals for store A are not a copy of store B; pilot comparison doc (`docs/PHASE2_M4_PILOT.md`)

---

## Milestone 5: Deal Engine at Scale and API Handoff ✅ (implemented)

- Deal scoring/filter via `deal_engine` + inventory rules (markdown %, clearance/hidden clearance, rank, `DEAL_*` knobs)
- Price history: `inv_price_history` + `price_dropped` / `drop_amount` on deals
- Fast path: `GET /api/deals?mode=auto|inventory` reads inventory DB; `refresh=1` queues priority verify + Wave D
- Docs: `docs/API.md`, `docs/HANDOFF.md` (auth, examples, rate limits, Railway env)

```bash
# Fast path (needs prior inventory scan)
curl "http://localhost:8000/api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=inventory"
```

**Deliverable:** Documented API + deals served from inventory DB (fast path)

---

## Milestone 6: Always-On Refresh, QA, Acceptance ✅ (implemented)

- Schedulers: full wave + hot waves on `INVENTORY_WATCH_STORES` (`app/inventory_scheduler.py`)
- Ops: `GET /api/ops` — scan age, deals, daily API burn, auth alert, scheduler status
- UI: “Scanned X ago · N items · M deals ≥ filter %” (`app/static/index.html`)
- Discord: deep markdown ≥70% (`POST /api/ops/deep-alerts/check`) + auth failures
- Acceptance checklist: `docs/ACCEPTANCE_PHASE2.md`

```env
INVENTORY_SCHEDULER_ENABLED=1
INVENTORY_WATCH_STORES=5686:90210,5930:90210
INVENTORY_FULL_INTERVAL_HOURS=24
INVENTORY_HOT_INTERVAL_HOURS=4
DISCORD_WEBHOOK_URL=...   # Railway secret — never commit
```

**Deliverable:** Production-ready Phase 2 module on Railway  
**Open:** client multi-region smoke + formal sign-off

---

## Post-M6: DealHawk-parity recall (catalog crawl + coverage + stock)

Addresses client feedback ("they find 300, we find 10"):

- **Full-catalog crawl** — `wave=catalog` (alias `E`) / `mega`: broad taxonomy matrix × deep pagination.
  Pilot `#5686`: **287 → 4,696 SKUs** from a bounded 130-job crawl (full depth ≈ 650 jobs → more).
- **Coverage mode** — `GET /api/deals?...&coverage=1`: every in-store markdown with a stock badge
  (DealHawk-style volume). Pilot: **29 → ~1,500 deals**. `coverage=0` keeps the clean pickup-only list.
- **SKU stock check** — `GET /api/stock?sku=&store_id=&zip=`: pickup / OOS / price / was / seller for one item.

Tunables: `INVENTORY_CATALOG_PAGES`, `INVENTORY_CATALOG_MAX_QUERIES`, `INVENTORY_MAX_JOBS` (cost guard).

> Honest note: broad keyword crawl hugely increases recall but is not literal "every item" — a few
> specific SKUs can still sit beyond crawled pages. True 100% enumeration needs category-node/browse
> traversal (next step) and scales Oxylabs cost with store count × refresh rate.

```bash
# Deep crawl one store, then serve high-volume deals
python scripts/run_inventory_scan.py --store-id 5686 --zip 90210 --wave catalog --sync
curl "$BASE/api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=inventory&coverage=1"
curl "$BASE/api/stock?sku=20585870370&store_id=5686&zip=90210"
```

---

## One-liner

> Phase 2 = always-on full-store scanner for selected Walmarts (catalog waves + pickup-accurate prices) so we can surface DealHawk-style deep clearance — not just search hits. Oxylabs usage is monthly pass-through based on stores × refresh speed.
