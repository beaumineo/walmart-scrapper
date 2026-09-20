# API Reference (Milestone 4)

Base URL (production): `https://walmart-scrapper-production.up.railway.app`  
Interactive docs: `/docs` (Swagger) · `/redoc`

## Auth (optional)

If `API_KEY` is set on the server, every `/api/*` route requires:

```http
X-API-Key: YOUR_KEY
```

or

```http
Authorization: Bearer YOUR_KEY
```

`GET /health` and `GET /` stay public.

## Rate limits

Default: **60 requests / minute / IP** (`RATE_LIMIT_PER_MIN`).  
Set `RATE_LIMIT_PER_MIN=0` to disable. Responses include:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`

`429` when exceeded.

## Integration flow

```text
1) GET /api/stores?zip=90210
2) pick stores[].store_id
3) GET /api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=live
```

Use a **client timeout ≥ 120 seconds** for `/api/deals` (Oxylabs live pulls are often 20–90s).

---

## `GET /api/stores`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `zip` | string | required | US ZIP |
| `radius_miles` | float | 50 | 1–100 |
| `limit` | int | 100 | 1–200 |

**Example**

```bash
curl "https://HOST/api/stores?zip=90210&limit=5"
```

See `docs/examples/stores_90210.json`.

---

## `GET /api/deals`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `zip` | string | required | Must match the store lookup ZIP |
| `store_id` | string | required | From `/api/stores` |
| `min_discount_pct` | float | 20 | Hard floor on discount % |
| `radius_miles` | float | 50 | Store must be near ZIP |
| `mode` | string | `live` | `live` or `auto` (both prefer live) |

**Example**

```bash
curl --max-time 120 \
  "https://HOST/api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=live"
```

Important response fields:

| Path | Meaning |
|------|---------|
| `summary.deal_count` | Number of ranked deals |
| `deals[].why_deal` | Human reason (M3) |
| `deals[].rank_score` | Sort key (higher = better) |
| `deals[].deal_type` | `hidden_clearance` / `clearance` / `rollback` / `markdown` |
| `meta.data_mode` | `live` / `live_cached` / `failed` / `setup_required` |
| `meta.live_ok` | `true` when store-scoped live (or fresh cache) succeeded |
| `meta.deal_thresholds` | Active M3 thresholds |
| `meta.milestone` | Engine milestone stamp |

See `docs/examples/deals_90210_5686.json`.

---

## `GET /api/deals/config`

Returns tunable thresholds (`DEAL_*` env vars).

## `GET /api/report`

Convenience: same as deals, plus `nearby_stores`. If `store_id` omitted, uses nearest store.

## `GET /health`

Readiness: `live_ready`, `backends.oxylabs`, `milestone`, `auth_required`.

## Errors

| Code | When |
|------|------|
| 400 | Bad ZIP / params |
| 401 | Missing/invalid API key (only if `API_KEY` set) |
| 404 | Store not near ZIP |
| 429 | Rate limited |
| 502 | Upstream collector failure |

---

## CORS

Default `*`. Restrict with:

```env
CORS_ALLOW_ORIGINS=https://www.hiddenclearances.com,https://hiddenclearances.com
```
