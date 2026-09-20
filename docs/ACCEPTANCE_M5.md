# Milestone 5 — Final Acceptance Sign-off

**Date:** 2026-09-21  
**Module version:** `1.0.0` (`milestone: 5`)  
**Production:** https://walmart-scrapper-production.up.railway.app

## Milestone 0 checklist

| # | Criterion | Result |
|---|-----------|--------|
| 1 | Any US ZIP → nearby Walmart stores | PASS — tested CA/NY/TX/GA/IL/WA |
| 2 | Select store by id / name / address | PASS — `/api/stores` + UI picker |
| 3 | Store-scoped reports (no shared national list) | PASS — clone-list check |
| 4 | Ranked deals with `why_deal` + tunable thresholds | PASS — M3 engine + `/api/deals/config` |
| 5 | Live path with commercial backend (Oxylabs) | PASS — `live_ready` + live pulls |
| 6 | Agreed test ZIPs: 90210, 10001, 75201, 30301, 90810 | PASS |

## Milestones delivered

| M | Deliverable | Status |
|---|-------------|--------|
| 0 | Requirements | Done — `docs/requirements.md` |
| 1 | Location API | Done — `/api/stores` |
| 2 | Store-scoped prices | Done — collector + `data/pulls/` |
| 3 | Deal engine | Done — `why_deal`, `rank_score`, thresholds |
| 4 | API + handoff | Done — `docs/API.md`, `docs/HANDOFF.md`, examples, optional `API_KEY` |
| 5 | Final QA | Done — `scripts/m5_acceptance.py` + this sign-off |

## How to re-run QA

```powershell
cd D:\E\Working_now\walmart
$env:PYTHONPATH="D:\E\Working_now\walmart\app"
$env:WALMART_COLLECT_INLINE="1"
python scripts\m5_acceptance.py
```

Machine-readable output: `docs/ACCEPTANCE_M5.json`.

## Client acceptance

Production-ready for Hidden Clearances integration:

1. Open production URL → ZIP → store → Find deals  
2. Or call `/api/stores` then `/api/deals` (see `docs/HANDOFF.md`)  
3. Confirm `/health` shows `"milestone": 5` and `"live_ready": true`

**Sign-off:** Milestone 5 complete pending client review of the hosted URL.
