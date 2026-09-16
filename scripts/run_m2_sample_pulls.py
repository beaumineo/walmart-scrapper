"""
Milestone 2+3: sample pulls for agreed test ZIPs/stores.

- Tries live collection when PROXIES is set
- Always writes a store-unique sample deal report (mode=sample) so deliverables
  are not blocked by Walmart bot checks
- Verifies product/title overlap across stores stays low (same-list regression)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from config import PULLS_DIR, get_collector_config  # noqa: E402
from walmart_core import build_report, find_stores_near_zip  # noqa: E402

TEST_ZIPS = [
    ("90210", "Beverly Hills / LA"),
    ("90810", "Long Beach"),
    ("10001", "New York"),
    ("75201", "Dallas"),
    ("30301", "Atlanta"),
]


def main() -> int:
    cfg = get_collector_config()
    print(f"proxy_enabled={cfg.proxy_enabled} proxies={len(cfg.proxies)}")
    PULLS_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    title_sets = {}

    for zip_code, label in TEST_ZIPS:
        print(f"\n=== {zip_code} ({label}) ===")
        try:
            loc, stores = find_stores_near_zip(zip_code, radius_miles=25, limit=3)
        except Exception as e:
            results.append({"zip": zip_code, "label": label, "ok": False, "error": str(e)})
            continue
        if not stores:
            results.append({"zip": zip_code, "label": label, "ok": False, "error": "no stores"})
            continue

        store = stores[0]
        print(f"store #{store.store_id} {store.name} · {store.address}")

        # M3 sample report (always works; store-unique catalog)
        report = build_report(
            zip_code=zip_code,
            store_id=store.store_id,
            prefer_live=False,
            min_discount_pct=20,
        )
        deals = report.get("deals") or []
        titles = {d.get("title") for d in deals if d.get("title")}
        title_sets[store.store_id] = titles

        out = {
            "milestone": "2+3",
            "zip": zip_code,
            "label": label,
            "store": report.get("store"),
            "summary": report.get("summary"),
            "meta": report.get("meta"),
            "deals": deals,
        }
        path = PULLS_DIR / f"m2_sample_{zip_code}_{store.store_id}.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"sample deals={len(deals)} file={path.name}")
        if deals:
            top_why = (deals[0].get("why_deal") or "")[:80]
            print(f"  top: {deals[0].get('title')} | {top_why}")

        # Optional live attempt only when explicitly requested
        live_info = {"attempted": False}
        if cfg.proxy_enabled and "--live" in sys.argv:
            live_info["attempted"] = True
            try:
                live = build_report(
                    zip_code=zip_code,
                    store_id=store.store_id,
                    prefer_live=True,
                    min_discount_pct=20,
                )
                live_info["data_mode"] = live.get("meta", {}).get("data_mode")
                live_info["deal_count"] = len(live.get("deals") or [])
                live_info["live_ok"] = live.get("meta", {}).get("live_ok")
            except Exception as e:
                live_info["error"] = str(e)

        results.append(
            {
                "zip": zip_code,
                "label": label,
                "store_id": store.store_id,
                "store_address": store.address,
                "sample_deal_count": len(deals),
                "sample_file": str(path),
                "live": live_info,
                "ok": True,
            }
        )

    # Overlap check (same-list regression)
    sids = list(title_sets)
    overlaps = []
    for i, a in enumerate(sids):
        for b in sids[i + 1 :]:
            sa, sb = title_sets[a], title_sets[b]
            if not sa or not sb:
                continue
            ov = len(sa & sb) / max(len(sa), len(sb))
            overlaps.append({"a": a, "b": b, "overlap_pct": round(ov * 100, 1)})
            print(f"overlap store {a} vs {b}: {ov:.0%}")

    summary_path = PULLS_DIR / "m2_sample_summary.json"
    payload = {
        "milestone": "2+3",
        "proxy_enabled": cfg.proxy_enabled,
        "proxy_count": len(cfg.proxies),
        "results": results,
        "title_overlaps": overlaps,
        "max_overlap_pct": max((o["overlap_pct"] for o in overlaps), default=0),
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {summary_path}")
    print(f"max_title_overlap_pct={payload['max_overlap_pct']}")
    if payload["max_overlap_pct"] > 55:
        print("WARNING: sample overlap still high — check catalog partitioning")
        return 1
    print("OK: store sample lists are sufficiently distinct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
