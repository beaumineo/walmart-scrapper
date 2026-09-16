# Host for client demo — Railway (API + UI together)

This app needs **long-running** live Walmart pulls (often 30–90s).  
Use **Railway** for the whole app (frontend + backend). Vercel alone will time out.

## 1) Create Railway project

1. Go to https://railway.app → New Project → Deploy from GitHub (or empty + Dockerfile)
2. Root directory = this repo
3. Railway will build with `Dockerfile`

## 2) Set environment variables

In Railway → Variables:

```
OXYLABS_USERNAME=your_user
OXYLABS_PASSWORD=your_pass
WALMART_COLLECT_ENGINE=auto
WALMART_QUERIES=clearance,rollback
DEAL_DEALS_ONLY=1
DEAL_INCLUDE_SHELF=0
DEAL_MIN_DISCOUNT_PCT=20
WALMART_UC_ENABLED=0
WALMART_ALLOW_BROWSER=0
```

Optional: keep `PROXIES` unset on Railway (Oxylabs is enough).

## 3) Share with client

After deploy, Railway gives a public URL like:

`https://your-service.up.railway.app`

Open that URL — the UI is served from FastAPI (`/`).

## 4) Optional: Vercel frontend later

Only if you want a separate marketing domain:

- Vercel = static `app/static`
- Set fetch base to Railway API
- Keep scraping on Railway

For the client check **now**, Railway-only is simplest.
