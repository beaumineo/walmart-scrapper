# Host for client demo — Railway (API + UI together)

This app needs long-running live Walmart pulls (often 30-90s).
Use Railway for the whole app (frontend + backend). Vercel alone will time out.

## 1) Create Railway project

1. Go to https://railway.app → New Project → Deploy from GitHub
2. Root directory = this repo
3. Railway builds with `Dockerfile`

## 2) Set environment variables (only these)

In Railway → Variables, add **only**:

```
OXYLABS_USERNAME=your_user
OXYLABS_PASSWORD=your_pass
WALMART_COLLECT_ENGINE=auto
```

Optional (defaults are fine if omitted):

```
WALMART_QUERIES=clearance,rollback
DEAL_DEALS_ONLY=1
DEAL_INCLUDE_SHELF=0
DEAL_MIN_DISCOUNT_PCT=20
WALMART_UC_ENABLED=0
WALMART_ALLOW_BROWSER=0
```

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
