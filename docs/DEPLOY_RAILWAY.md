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

**Important:** after editing Variables, click **Apply changes** / **Deploy**.
Typed-but-not-applied variables are invisible to the running app.

Optional: keep `PROXIES` unset on Railway (Oxylabs is enough).

Check that credentials loaded: open `/health` — you should see `"live_ready": true` and `"backends": {"oxylabs": true, ...}`.

## 3) Public URL

Settings → Networking → **Generate Domain** → open `https://….up.railway.app`

## 4) Optional: Vercel frontend later

Only if you want a separate marketing domain:

- Vercel = static `app/static`
- Set fetch base to Railway API
- Keep scraping on Railway

For the client check **now**, Railway-only is simplest.
