import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# Load .env fresh into process
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip().strip('"').strip("'")

from client_collector import client_backends_status, client_live_ready, collect_store_for_client
from collector import clear_live_cooldown, collect_store_prices, save_pull
from config import get_collector_config
from deal_engine import DealThresholds, score_product
from walmart_core import build_report

cfg = get_collector_config()
print("ready", client_live_ready(cfg))
print("backends", client_backends_status(cfg))
print("oxy_user_set", bool(cfg.oxylabs_username), "oxy_pass_set", bool(cfg.oxylabs_password))
print("user", (cfg.oxylabs_username[:6] + "…") if cfg.oxylabs_username else None)

clear_live_cooldown()
os.environ["WALMART_COLLECT_INLINE"] = "1"
os.environ["WALMART_COLLECT_ENGINE"] = "auto"

print("\n=== Oxylabs direct pull store 542 / 70360 ===")
result = collect_store_for_client("542", postal_code="70360", queries=["clearance"], cfg=cfg)
print(
    "ok",
    result.get("ok"),
    "mode",
    result.get("mode"),
    "engine",
    result.get("engine"),
    "n",
    len(result.get("products") or []),
)
print("notes", str(result.get("notes") or "")[:500])
prods = result.get("products") or []
if prods:
    for p in prods[:5]:
        print(
            "-",
            p.get("product_id"),
            "cur",
            p.get("current_price"),
            "was",
            p.get("was_price"),
            (p.get("title") or "")[:50],
        )

print("\n=== Full report ZIP 70310 store 542 ===")
clear_live_cooldown()
report = build_report("70310", "542", prefer_live=True, min_discount_pct=20)
meta = report.get("meta") or {}
print("mode", meta.get("data_mode"), "live_ok", meta.get("live_ok"), "deals", len(report.get("deals") or []))
print("user_error", meta.get("user_error"))
print("notes", str(meta.get("collector_notes") or "")[:400])
for d in (report.get("deals") or [])[:6]:
    print(
        "-",
        d.get("discount_pct"),
        d.get("current_price"),
        d.get("was_price") or d.get("list_price"),
        (d.get("title") or "")[:45],
    )
