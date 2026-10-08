# Integration Handoff (Phase 2 · Milestone 5)

For the Hidden Clearances team wiring this module into the site.

## What you get

| Piece | Where |
|-------|--------|
| Live demo UI | `https://walmart-scrapper-production.up.railway.app/` |
| OpenAPI | `/docs` |
| Written API | `docs/API.md` |
| Example JSON | `docs/examples/` |
| Phase 2 roadmap | `docs/PHASE2_ROADMAP.md` |
| M4 anti-clone pilot | `docs/PHASE2_M4_PILOT.md` |

## Wire-up (recommended — inventory fast path)

```js
const BASE = "https://walmart-scrapper-production.up.railway.app";
const headers = { /* "X-API-Key": process.env.WALMART_API_KEY */ };

const stores = await fetch(`${BASE}/api/stores?zip=${zip}&limit=20`, { headers })
  .then(r => r.json());

const storeId = stores.stores[0].store_id;

// Fast: reads Phase 2 inventory DB (usually <1s once the store is scanned)
const report = await fetch(
  `${BASE}/api/deals?zip=${zip}&store_id=${storeId}&min_discount_pct=20&mode=auto`,
  { headers, signal: AbortSignal.timeout(30000) }
).then(r => r.json());

// Optional: queue background pickup recheck while still returning current deals
await fetch(
  `${BASE}/api/deals?zip=${zip}&store_id=${storeId}&min_discount_pct=20&mode=inventory&refresh=1`,
  { headers }
);

if (!report.meta?.live_ok && report.meta?.data_mode !== "inventory") {
  // show report.meta.user_error — do not invent deals
}

// UI hint
const scannedAgo = report.meta?.cache_age_sec; // seconds since last inventory update
const drops = report.summary?.price_drop_count;  // fresh drops vs prior scan
```

### Mode cheat sheet

| `mode` | Behavior |
|--------|----------|
| `auto` (default) | Inventory DB if store was scanned; else live Oxylabs sweep |
| `inventory` | Inventory DB only (`404` if never scanned) |
| `live` | Full Oxylabs pull (30–90s — timeout ≥ 180s) |

`refresh=1` on inventory/auto: returns current deals immediately and queues Wave D / pickup verify in the background.

## Seed a store (ops)

```bash
# once per store (or on a schedule — Milestone 6)
curl -X POST "$BASE/api/inventory/scans" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"store_id":"5686","zip":"90210","wave":"full","max_queries":24,"background":true}'
```

## Env vars (Railway)

Required for collection / refresh:

```env
OXYLABS_USERNAME=...
OXYLABS_PASSWORD=...
WALMART_COLLECT_ENGINE=auto
```

Optional for your site:

```env
API_KEY=shared-secret-with-frontend-backend
CORS_ALLOW_ORIGINS=https://www.hiddenclearances.com
RATE_LIMIT_PER_MIN=60
DEAL_MIN_DISCOUNT_PCT=20
DEAL_HIDDEN_CLEARANCE_PCT=70
DEAL_CLEARANCE_PCT=40
DEAL_REQUIRE_PICKUP=1
DEAL_WALMART_SELLER_ONLY=1
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
INVENTORY_VERIFY_MAX=40
```

## Guarantees

- Reports are **store-scoped** (no national clearance hub as “local”).
- Inventory deals require **pickup confirmed** + **Walmart seller**.
- Different stores are not identical copies (see M4 overlap).
- `min_discount_pct` is a hard floor.
- Failed live pulls never fall back to another store’s catalog.
- Price drops vs prior inventory scan are flagged (`price_dropped`, `drop_amount`).

## Auth & rate limits

- If `API_KEY` is set: send `X-API-Key` or `Authorization: Bearer …`
- Default **60 req/min/IP** (`RATE_LIMIT_PER_MIN`); `429` when exceeded

## Not included (per contract)

- Full Hidden Clearances website UI
- Other retailers
- Ongoing scrape maintenance after delivery (retainer if needed)

## Support

Check `GET /health` (`version`, `phase2_milestone`, `live_ready`, `backends`) plus the failing URL/params.
