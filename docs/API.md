# API Reference (Phase 2 · Milestone 5)

Base URL (production): `https://walmart-scrapper-production.up.railway.app`  
Interactive docs: `/docs` (Swagger) · `/redoc`  
Handoff: `docs/HANDOFF.md`

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
3) Ensure store scanned: POST /api/inventory/scans  (once / on schedule)
4) GET /api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=auto
```

- **Fast path (`auto`/`inventory`):** usually &lt;1s once inventory exists.  
- **Live path (`mode=live`):** Oxylabs sweep — client timeout ≥ **180s**.

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
| `mode` | string | `auto` | `auto` · `inventory` · `live` |
| `coverage` | int | 0 | `1` = DealHawk-style volume (all markdowns + stock badges); `0` = pickup-confirmed + Walmart-seller only |
| `refresh` | int | 0 | `1` = queue priority inventory recheck (fast path) or force live pull |

**Coverage vs clean:** `coverage=0` returns fewer, pickup-verified Walmart-seller deals. `coverage=1` returns every in-store markdown (incl. unverified/marketplace) with a `stock_status` badge — far higher volume, closer to DealHawk. Pilot `#5686`: `coverage=0` → ~29 deals, `coverage=1` → ~1,500 deals.

### `GET /api/stock`

Single-SKU stock check at a specific store.

| Query | Type | Notes |
|-------|------|-------|
| `sku` | string | Walmart product/item id |
| `store_id` | string | Store to check |
| `zip` | string | Optional; inferred from store if omitted |

Returns `pickup_available`, `out_of_stock`, `stock_status`, `current_price`, `was_price`, `discount_pct`, `seller_name`, `walmart_seller`.

```bash
curl "$BASE/api/stock?sku=20585870370&store_id=5686&zip=90210"
```

**Example (fast path)**

```bash
curl --max-time 30 \
  "https://HOST/api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=auto"
```

Important response fields:

| Path | Meaning |
|------|---------|
| `summary.deal_count` | Number of ranked deals |
| `summary.price_drop_count` | Deals cheaper than prior inventory snapshot |
| `deals[].why_deal` | Human reason |
| `deals[].rank_score` | Sort key (higher = better) |
| `deals[].deal_type` | `hidden_clearance` / `clearance` / `rollback` / `markdown` |
| `deals[].price_dropped` | `true` if current &lt; prior scan price |
| `deals[].pickup_available` | Always `true` on inventory path |
| `meta.data_mode` | `inventory` / `live` / `live_cached` / `failed` / … |
| `meta.source` | `inventory_db` on fast path |
| `meta.cache_age_sec` | Seconds since last inventory update |
| `meta.refresh_queued` | `true` when `refresh=1` kicked a background recheck |
| `meta.deal_thresholds` | Active `DEAL_*` thresholds |
| `meta.phase2_milestone` | `5` on inventory fast path |

See `docs/examples/deals_90210_5686.json`.

---

## `GET /api/deals/config`

Returns tunable thresholds (`DEAL_*` env vars).

## `GET /api/report`

Convenience: same as deals, plus `nearby_stores`. If `store_id` omitted, uses nearest store.

## Phase 2 — Inventory scans (Milestone 3)

Seed / full-store waves into SQLite, then poll status + coverage.

### `POST /api/inventory/scans`

```json
{
  "store_id": "5686",
  "zip": "90210",
  "wave": "A",
  "max_queries": 20,
  "pages_per_query": 1,
  "recheck_limit": 40,
  "background": true
}
```

`wave`: `seed` | `A` | `B` | `C` | `D` | `full`  
- **A** clearance/rollback · **B** departments · **C** deeper pages · **D** product recheck · **full** A+B+C+D  

Returns `{ ok, scan }` with `scan_id`, progress, inventory counts.  
`409` if a scan is already running for that store.

### `GET /api/inventory/scans/{scan_id}`

Progress: `status`, `progress_pct`, `jobs_done` / `jobs_total`, `inventory_count`, `sku_count`.

### `GET /api/inventory/scans?store_id=5686`

List recent inventory scan runs.

### `GET /api/inventory/stores/{store_id}`

Store summary: inventory count, markdown candidates, last successful scan.

### `GET /api/inventory/stores/{store_id}/coverage`

SKU coverage report: counts, markdown ≥40%, sources, recent waves.

### `GET /api/inventory/stores/{store_id}/deals?min_discount_pct=20`

Deals from inventory DB — **pickup confirmed + Walmart seller only** (Milestone 4 anti-clone).

### `POST /api/inventory/verify`

```json
{ "store_id": "5686", "zip": "90210", "max_verify": 40 }
```

Product-level pickup verify for unverified markdowns; writes back to inventory.

### `GET /api/inventory/overlap?store_a=5686&store_b=5930`

SKU / pickup / deal-list overlap metrics (`anti_clone_ok` when deal overlap &lt; 55%).

CLI:

```bash
set PYTHONPATH=app
python scripts/run_inventory_scan.py --store-id 5686 --zip 90210 --wave A --max-queries 12 --sync
python scripts/run_inventory_scan.py --store-id 5686 --coverage
python scripts/run_inventory_verify.py --store-id 5686 --max-verify 20
python scripts/run_inventory_verify.py --overlap 5686 5930
```

Discord updates (Milestone 1/6): set `DISCORD_WEBHOOK_URL` — scan completions, auth failures, and ≥70% deep markdowns post automatically.

### `GET /api/ops`

Ops dashboard: watched stores, scan age, deal counts, daily API burn, scheduler status.

### `POST /api/ops/scheduler/tick`

Run one scheduler pass now (starts due full/hot waves).

### `POST /api/ops/deep-alerts/check`

Find new ≥70% pickup deals on watched stores and Discord-alert them.

## `GET /health`

Readiness: `live_ready`, `backends.oxylabs`, `milestone`, `phase2_milestone`, `auth_required`.

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
