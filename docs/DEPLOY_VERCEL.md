# Walmart Deal Finder — Deploy notes

## Product (client)

**In-store deals only** for the selected Walmart:
ZIP → pick store → live markdown / clearance / hidden-clearance report.

- No demo / fake product catalogs
- No national online clearance hub
- M3 deal engine: % off + clearance/rollback flags + `why_deal`

## Local

```powershell
cd D:\E\Working_now\walmart
pip install -r requirements.txt
pip install -r requirements-collector.txt   # for live pulls
# set PROXIES in .env
cd app
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## Vercel

Live Walmart pulls often fail on serverless (timeouts + bot checks).
Deploy still works for store lookup; deal reports need a working proxy and
may return empty when Walmart blocks.

```bash
vercel login
vercel --prod
```

Set `PROXIES` in Vercel env for live attempts.
