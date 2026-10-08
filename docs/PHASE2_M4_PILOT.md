# Phase 2 Milestone 4 — Pilot comparison (anti-clone)

**Date:** 2026-10-08  
**Goal:** Deals for store A are not a copy of store B; only pickup-confirmed Walmart-seller inventory.

## Rules enforced

| Rule | Behavior |
|------|----------|
| Require pickup | `pickup_available=true` only |
| Walmart seller | Drop marketplace 3P sellers |
| Unverified deep markdown | Never shown as in-store deals |
| Product verify | `walmart_product` recheck writes pickup back to inventory |

## Pilot stores

| Store | ZIP | Inventory | Pickup confirmed | Deals (≥20% off) |
|-------|-----|-----------|------------------|------------------|
| `#5686` | 90210 | 287 | 83 (after verify) | 26 |
| `#5930` | 90210 | 250 | 71 | 22 |
| `#542` | 70310 | 258 | 79 | 26 |

Pickup verify sample: `#5686` checked 15 unverified markdowns → **15 pickup_true** (deals 18→26).

## Overlap

| Pair | Deal overlap | Exclusive A / B | Identical? | anti_clone_ok |
|------|--------------|-----------------|------------|---------------|
| 5686 vs 5930 (same ZIP area) | 45.5% | 11 / 7 | no | **yes** |
| 5686 vs 542 (90210 vs 70310) | 62.5% | 6 / 6 | no | **yes** |

**Note:** National rollbacks (KitchenAid, Nespresso, etc.) can correctly appear at many stores when pickup is real. That is **not** the clone bug. The clone bug was shipping/unconfirmed online markdowns repeated for every ZIP.

## How to reproduce

```bash
set PYTHONPATH=app
python scripts/run_inventory_verify.py --store-id 5686 --max-verify 20
python scripts/run_inventory_verify.py --overlap 5686 5930
python scripts/run_inventory_verify.py --store-id 5686 --deals
```

APIs: `GET /api/inventory/stores/{id}/deals`, `POST /api/inventory/verify`, `GET /api/inventory/overlap`.
