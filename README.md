# Walmart Deal Finder (Hidden Clearances)

Backend module: **ZIP → stores → store-scoped prices → ranked deals**.

| Milestone | Status |
|-----------|--------|
| M0 Specs | Done — `docs/requirements.md` |
| M1 Location | Done — `/api/stores` |
| M2 Prices | Done — store-scoped collector |
| M3 Deal engine | Done — `why_deal`, thresholds |

## Same-list bug (fixed)

National `/shop/deals/clearance` returned the same products for every store. That hub is disabled; sample catalogs are partitioned by `store_id`.

## Local run

```powershell
cd d:\E\Working_now\walmart
pip install -r requirements.txt
# optional live scraping extras:
# pip install -r requirements-collector.txt
copy .env.example .env
cd app
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Deploy to Vercel (client demo)

Vercel runs this as a FastAPI serverless app (`app/main.py`).

**Reliable by default:** on Vercel, `mode=auto` serves **store-unique sample deals** (no proxy, no Walmart bot timeouts). Live scraping needs `PROXIES` + `WALMART_FORCE_LIVE=1` and often still fails on short serverless limits — keep sample for client demos.

```bash
# from repo root
npm i -g vercel   # once
vercel login
vercel            # preview
vercel --prod     # production URL for the client
```

Or connect the GitHub repo in the Vercel dashboard → Import → Root Directory = repo root → Deploy.

### Env vars (optional)

| Var | Purpose |
|-----|---------|
| `PROXIES` | Bright Data etc. for live (local / forced) |
| `WALMART_FORCE_LIVE=1` | Attempt live on Vercel (not recommended for demos) |
| `DEAL_MIN_DISCOUNT_PCT` | M3 threshold (default 20) |

### Client test checklist

1. Open the Vercel URL  
2. ZIP `90210` → Find stores → pick store A → Generate report  
3. Pick store B → Generate report → **lists should differ**  
4. Each deal shows **why_deal**

## APIs

| Endpoint | Notes |
|----------|--------|
| `GET /api/stores?zip=` | M1 |
| `GET /api/deals?zip=&store_id=&mode=sample\|auto\|live` | M2+M3 |
| `GET /api/deals/config` | Thresholds |
| `GET /health` | `milestone: 3` |
