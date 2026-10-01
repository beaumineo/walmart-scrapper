# Host for client demo — Railway (API + UI together)

This app needs long-running live Walmart pulls (often 45–180s with the deeper sweep).
Use Railway for the whole app (frontend + backend). Vercel alone will time out.

## 1) Create Railway project

1. Go to https://railway.app → New Project → Deploy from GitHub
2. Root directory = this repo
3. Railway builds with `Dockerfile`

## 2) Set environment variables (only these)

In Railway → Variables, set:

```
OXYLABS_USERNAME=yuto1_ezN2y
OXYLABS_PASSWORD=yutoKazuma_8
OXYLABS_MODE=scraper
OXYLABS_RENDER=0
WALMART_COLLECT_ENGINE=auto
```

(Use your current Scraper API username — the old `yutokazuma_NheH0` account now returns HTTP 401.)

For Oxylabs **Web Unblocker** trial (proxy `unblock.oxylabs.io:60000`), keep `OXYLABS_MODE=unblocker` and **`OXYLABS_RENDER=0`** (render mode can burn hundreds of MB per pull).
For Oxylabs **Scraper API** (`realtime.oxylabs.io`), set `OXYLABS_MODE=scraper`.

Optional (defaults are fine if omitted):

```
# Prefer leaving these unset so code defaults (~18 search queries + ~40 product enrich) apply
# WALMART_MAX_QUERIES=18
# WALMART_MAX_API_CALLS=24
# WALMART_ENRICH_MAX=40
# WALMART_PARALLEL_WORKERS=6
DEAL_MAX_DEALS=2000
DEAL_REQUIRE_PICKUP=1
DEAL_WALMART_SELLER_ONLY=1
DEAL_PREFER_PICKUP=1
DEAL_DROP_UNVERIFIED_DEEP=1
DEAL_DROP_UNVERIFIED_DEEP_PCT=20
DEAL_DEALS_ONLY=1
DEAL_INCLUDE_SHELF=0
DEAL_MIN_DISCOUNT_PCT=20
WALMART_UC_ENABLED=0
WALMART_ALLOW_BROWSER=0
```

If Railway still has `WALMART_MAX_QUERIES=3` or `WALMART_MAX_API_CALLS=3`, **delete those** (or raise to 20/30) so the DealHawk-scale category sweep can run.
If Railway still has `WALMART_QUERIES=clearance,rollback`, **delete that variable** so the high-yield category list is used.

Ignore Railway "Suggested Variables" for empty keys like `PROXIES`, `WALMART_TMP_DIR`, etc. You do not need to fill those.

After changing Variables, wait for redeploy (or click Redeploy).

## 3) Public URL

Settings → Networking → Generate Domain

Share: `https://your-service.up.railway.app`

Open `/` for the UI. Check `/health` — `live_ready` should be `true` and `backends.oxylabs` should be `true`.

## 4) Troubleshooting

| Symptom | Fix |
|---|---|
| UI asks for credentials | Oxylabs vars missing or not redeployed after save |
| `Application not found` | Domain unexposed or service stopped — Generate Domain again |
| Pulls fail but local works | Confirm latest deploy includes `WALMART_COLLECT_INLINE=1` (Dockerfile) |
| Suggested vars empty | Safe to ignore — not required |
