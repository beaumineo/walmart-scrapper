# Integration Handoff (Milestone 4)

For the Hidden Clearances team wiring this module into the site.

## What you get

| Piece | Where |
|-------|--------|
| Live demo UI | `https://walmart-scrapper-production.up.railway.app/` |
| OpenAPI | `/docs` |
| Written API | `docs/API.md` |
| Example JSON | `docs/examples/` |
| Acceptance (M5) | `docs/ACCEPTANCE_M5.md` |

## Wire-up (minimal)

```js
const BASE = "https://walmart-scrapper-production.up.railway.app";
const headers = { /* "X-API-Key": process.env.WALMART_API_KEY */ };

const stores = await fetch(`${BASE}/api/stores?zip=${zip}&limit=20`, { headers })
  .then(r => r.json());

const storeId = stores.stores[0].store_id;

const report = await fetch(
  `${BASE}/api/deals?zip=${zip}&store_id=${storeId}&min_discount_pct=20&mode=live`,
  { headers, signal: AbortSignal.timeout(120000) }
).then(r => r.json());

if (!report.meta?.live_ok) {
  // show report.meta.user_error — do not invent deals
}
```

## Env vars your host already needs

Required for live:

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
```

## Guarantees

- Reports are **store-scoped** (no national clearance hub).
- Different stores near the same ZIP return **different** deal sets.
- `min_discount_pct` is a hard floor (offer flags cannot bypass it).
- Failed live pulls never fall back to another store’s catalog.

## Not included (per contract)

- Full Hidden Clearances website UI
- Other retailers
- Ongoing scrape maintenance after delivery (retainer if needed)

## Support contact

Ship questions against `GET /health` (`version`, `live_ready`, `backends`) plus the failing request URL/params.
