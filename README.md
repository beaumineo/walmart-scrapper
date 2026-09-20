# Walmart Deal Finder (Hidden Clearances)

Backend module: **ZIP → stores → store-scoped prices → ranked deals**.

| Milestone | Status |
|-----------|--------|
| M0 Specs | Done — `docs/requirements.md` |
| M1 Location | Done — `/api/stores` |
| M2 Prices | Done — store-scoped collector |
| M3 Deal engine | Done — `why_deal`, thresholds |
| M4 API + handoff | Done — `docs/API.md`, `docs/HANDOFF.md` |
| M5 Final QA | Done — `docs/ACCEPTANCE_M5.md` |

## Production

https://walmart-scrapper-production.up.railway.app  
Health: `/health` (`milestone: 5`) · Swagger: `/docs`

## Local run

```powershell
cd d:\E\Working_now\walmart
pip install -r requirements.txt
copy .env.example .env
# set OXYLABS_USERNAME / OXYLABS_PASSWORD
cd app
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## Client integration

See **`docs/HANDOFF.md`** and **`docs/API.md`**.

```text
GET /api/stores?zip=90210
GET /api/deals?zip=90210&store_id=5686&min_discount_pct=20&mode=live
```

Use a client timeout ≥ 120s for live deals.

## Env (important)

| Var | Purpose |
|-----|---------|
| `OXYLABS_USERNAME` / `OXYLABS_PASSWORD` | Live store-scoped pulls |
| `WALMART_COLLECT_ENGINE=auto` | Collector order |
| `API_KEY` | Optional; require `X-API-Key` on `/api/*` |
| `CORS_ALLOW_ORIGINS` | Comma-separated origins (default `*`) |
| `RATE_LIMIT_PER_MIN` | Default 60; `0` disables |
| `DEAL_MIN_DISCOUNT_PCT` | M3 slider floor (default 20) |

## Acceptance

```powershell
$env:PYTHONPATH="d:\E\Working_now\walmart\app"
python scripts\m5_acceptance.py
```
