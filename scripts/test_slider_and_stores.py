"""Verify min-% slider and store differentiation."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip().strip('"').strip("'")

import importlib
import config

importlib.reload(config)

from collector import _dict_to_item, live_products_to_deals
from walmart_parse import jaccard_ids, product_id_set

# 1) Slider on cached 542
path = ROOT / "data" / "pulls" / "latest_542.json"
products = []
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = [_dict_to_item(p) for p in payload.get("products") or []]

print("products", len(products))
for mn in (20, 40, 70):
    deals = live_products_to_deals("542", products, min_discount_pct=mn)
    bad = [d for d in deals if float(d.get("discount_pct") or 0) < mn]
    print(f"slider>={mn}: deals={len(deals)} under_min={len(bad)}")
    assert not bad, f"slider leak at {mn}: {bad}"

# 2) Compare two store latests if present
a = ROOT / "data" / "pulls" / "latest_542.json"
b = ROOT / "data" / "pulls" / "latest_911.json"
if a.exists() and b.exists():
    pa = json.loads(a.read_text(encoding="utf-8")).get("products") or []
    pb = json.loads(b.read_text(encoding="utf-8")).get("products") or []
    ov = jaccard_ids(pa, pb)
    print(f"overlap 542 vs 911: {ov:.1%} (ids {len(product_id_set(pa))} vs {len(product_id_set(pb))})")
    assert ov < 0.85, "stores look cloned"

print("OK")
