# Bright Data ISP test log — 2026-09-14

## Proxy under test
- Host: `brd.superproxy.io:44445`
- Zone: `isp_proxy1` (dedicated US ISP)
- Observed exit: `31.105.141.41` (New York, AS6079 RCN)

## Timeline (what actually happened)

### 1) Connectivity — PASS
```
proxy_enabled True
host brd.superproxy.io port 44445
ipinfo: 31.105.141.41 / New York City / US / AS6079 RCN
```

### 2) Live product parse — PASS (before burn)
Probe: `https://www.walmart.com/shop/deals/clearance` via curl_cffi + ISP

```
status 200
len ~2,076,442
challenge False
parsed 15–20 products
example: Chaps Men's Everyday Pique Polo … current_price=15.0
```

Also confirmed with Playwright on the same URL:
```
challenge False
len ~3,061,626
parsed 20
```

### 3) Search / store paths — FAIL (even before burn)
```
/search?q=clearance&stores=2280 → Robot or human? (px-captcha)
/ip/... → Robot or human?
/store/2280 → Robot or human?
Homepage / → OK
/shop/deals/clearance → OK (until IP burned)
```

### 4) Full collector run (aggressive retries) — FAIL / burned IP
Saved pull: `data/pulls/pull_2280_20260914T140828Z.json`

```json
{
  "ok": false,
  "mode": "blocked",
  "engine": "playwright",
  "product_count": 0,
  "proxy_used": true,
  "notes": "retryable bot challenge attempt=1..3; curl mode=blocked; pw blocked query=clearance attempt=1..3"
}
```

Later pull after more retries: `data/pulls/pull_2280_20260914T141700Z.json`
- Same `mode=blocked`
- Notes show many deals+search retries on the **same** sticky ISP IP

### 5) After burn — clearance also challenged
```
status 200 len 15562
challenge True
parsed 0
title: Robot or human?
```

## Root cause
Not “ISP can’t work.”  
**One sticky ISP IP worked**, then **too many challenge retries on the same exit** flagged it.

## Fix applied in code (so this doesn’t repeat)
1. Stop immediately on bot challenge for sticky/dedicated proxies
2. Default `WALMART_MAX_RETRIES=1`, slower delays (3–8s)
3. Prefer deals hub pages; do not hammer `/search`
4. Longer live cooldown (45 min) after a block
5. Optional Bright Data session rotation: `WALMART_PROXY_ROTATE_SESSION=1`
   - Helps **residential rotating** zones
   - Does **not** magically rotate a **dedicated ISP** IP
