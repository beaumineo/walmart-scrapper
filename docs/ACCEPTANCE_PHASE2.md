# Phase 2 acceptance checklist (Milestone 6)

Accept when all boxes pass on Railway (or local with Oxylabs).

## Milestone 1 — Kickoff

- [ ] Deal definition agreed (%, clearance flags, pickup required)
- [ ] Store count / refresh cadence agreed
- [x] Discord channel connected (`DISCORD_WEBHOOK_URL`)
- [x] API additions: scan status, deals-from-DB, webhooks/alerts

## Milestone 2 — Inventory backbone

- [x] `inv_*` tables + scan orchestrator
- [x] `POST/GET /api/inventory/scans`, `GET /api/inventory/stores/{id}`
- [x] Pilot store seeded (e.g. `#5686`)

## Milestone 3 — Full-store waves

- [x] Waves A–D + `full`
- [x] Coverage report API/CLI
- [x] Retries / 401–429 handling

## Milestone 4 — Anti-clone

- [x] Pickup verify + Walmart-seller deals only
- [x] Overlap metrics; lists not identical
- [x] Pilot doc `docs/PHASE2_M4_PILOT.md`

## Milestone 5 — Deal engine / handoff

- [x] `/api/deals?mode=auto|inventory` fast path
- [x] Price-drop vs prior scan
- [x] `docs/HANDOFF.md` + `docs/API.md`

## Milestone 6 — Always-on / QA

- [x] Scheduler (`INVENTORY_SCHEDULER_ENABLED=1`, watch list, full + hot intervals)
- [x] Ops: `GET /api/ops` (scan age, deals, daily API burn, auth alert)
- [x] UI: “Scanned X ago · N items · M deals ≥ filter %”
- [x] Discord deep-markdown alerts (≥70%)
- [ ] Client smoke across agreed regions/stores
- [ ] Final sign-off

## Railway env (production)

```env
OXYLABS_USERNAME=...
OXYLABS_PASSWORD=...
DISCORD_WEBHOOK_URL=...   # do not commit
INVENTORY_SCHEDULER_ENABLED=1
INVENTORY_WATCH_STORES=5686:90210,5930:90210
INVENTORY_FULL_INTERVAL_HOURS=24
INVENTORY_HOT_INTERVAL_HOURS=4
INVENTORY_FULL_WAVE=full
INVENTORY_HOT_WAVE=A
DEAL_REQUIRE_PICKUP=1
DEAL_WALMART_SELLER_ONLY=1
```

## Smoke commands

```bash
curl "$BASE/health"
curl "$BASE/api/ops"
curl "$BASE/api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=inventory"
curl -X POST "$BASE/api/ops/deep-alerts/check"
```
