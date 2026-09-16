#!/usr/bin/env python3
"""Read-only acquisition check using the production parser/resolver and DB ledger.

This is not a publication command. It neither clears kaitori_prep nor runs the
production price updater. No DB writes, notifications, or collection batch flush.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.homura_fetcher import HomuraFetcher
from src.models import GameType
from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref
from src.supabase_writer import _rest, _conf, _latest_kaitori_prices
from special_follow_kaitori import TARGETS, validate_target


def standard_result(product, contracts, items, raw_by_category):
    """Only BOX/NS contracts; neither SKU suffix nor price proximity is evidence."""
    if len(contracts) != 1:
        return None, "source_contract_not_unique"
    contract = contracts[0]
    unit = product.get("canonical_unit")
    if (product.get("category") != "Pokemon" or unit not in {"BOX", "NS"}
            or product.get("product_type") != unit or contract.get("unit") != unit
            or contract.get("status") != "active" or contract.get("kind") != "auto"
            or contract.get("source") != "homura" or contract.get("unit_verified") is not True
            or contract.get("derive_rule") or contract.get("derive_from_sku")):
        return None, "standard_contract_invalid"
    ref = normalize_ref(contract.get("external_ref"))
    if not ref or ref != normalize_ref(product.get("homura_ref")):
        return None, "source_ref_difference"
    matches = [i for i in items if normalize_ref(i.code) == ref]
    if len(matches) != 1:
        return None, "listing_unconfirmed" if not matches else "ambiguous_listing"
    item = matches[0]
    price = item.price_1 if unit == "BOX" else item.price_2
    if type(price) is not int or price <= 0:
        return None, "invalid_observed_price"
    # _merge uses a dict and could hide two contradictory prices for one name.
    # Check the original shelf before accepting its merged value; responsive
    # desktop/mobile duplicates with the same value are harmless.
    category = 128 if unit == "BOX" else 129
    raw = [r for r in raw_by_category.get(category, []) if normalize_ref(r.get("name")) == normalize_ref(item.name)]
    if not raw or any(type(r.get("price")) is not int or r["price"] != price for r in raw):
        return None, "raw_listing_difference"
    return {"name": item.name, "price": price}, None


def check(skus):
    if not skus or len(set(skus)) != len(skus) or any(not re.fullmatch(r"[A-Z0-9-]+", s) for s in skus):
        raise ValueError("invalid_sku_scope")
    # Do not attach this one-game diagnostic to a production collection attempt:
    # recording/flush would affect live Fail-Safe evidence (F-225).
    for key in ("COLLECTION_ATTEMPT_ID", "COLLECTION_ATTEMPT_DIR"):
        os.environ.pop(key, None)
    os.environ["MASTER_FROM_LEDGER"] = "1"
    products = _rest("products?select=id,sku,name_jp,category,product_type,canonical_unit,homura_ref,"
                     "is_active,kaitori_hidden,kaitori_prep&sku=in.(" + ",".join(skus) + ")")
    if any(p.get("category") != "Pokemon" for p in products):
        raise ValueError("unsupported_category")
    ids = ",".join(p["id"] for p in products)
    sources = _rest("external_price_sources?select=*&source=eq.homura&product_id=in.(" + ids + ")") if ids else []
    latest = _latest_kaitori_prices([p["id"] for p in products])
    public = _rest("v_public_kaitori?select=product_id,unit,price&product_id=in.(" + ids + ")") if ids else []
    fetcher = HomuraFetcher()
    raw_by_category = {}
    fetch_category = fetcher._fetch_category
    def capture_category(category):
        rows = fetch_category(category)
        raw_by_category[category] = rows
        return rows
    fetcher._fetch_category = capture_category
    standard = fetcher.fetch(GameType.POKEMON).items if any(s not in TARGETS for s in skus) else []
    url, key, _, _ = _conf()
    special = fetcher.resolve_other_items(GameType.POKEMON, HomuraSupabaseResolver(url, key)) if any(s in TARGETS for s in skus) else []
    results = []
    for sku in skus:
        matches = [p for p in products if p["sku"] == sku]
        if len(matches) != 1:
            results.append({"sku": sku, "result": "blocked", "reason_code": "product_not_unique"})
            continue
        p = matches[0]
        contracts = [s for s in sources if s["product_id"] == p["id"]]
        if p.get("is_active") is not True or p.get("kaitori_hidden") is not False:
            item, reason = None, "product_unavailable"
        elif sku in TARGETS:
            candidates = [i for i in special if normalize_ref(i.get("name")) == normalize_ref(p.get("homura_ref"))]
            item, reason = validate_target(p, contracts, candidates)
        else:
            item, reason = standard_result(p, contracts, standard, raw_by_category)
        published = [v for v in public if v["product_id"] == p["id"] and v["unit"] == p["canonical_unit"]]
        results.append({"sku": sku, "product_id": p["id"], "result": "blocked" if reason else "success",
            "reason_code": reason, "product_type": p["product_type"], "canonical_unit": p["canonical_unit"],
            "source": "homura", "external_ref": p["homura_ref"], "observed_name": (item or {}).get("name"),
            "observed_price": (item or {}).get("price"), "kaitori_prep": p["kaitori_prep"],
            "latest_history_price": latest.get((p["id"], p["canonical_unit"]), (None, None))[0],
            "published_prices": [v["price"] for v in published]})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": "read_only_acquisition",
        "ok": all(r["result"] == "success" for r in results), "expected_count": len(skus),
        "success_count": sum(r["result"] == "success" for r in results), "results": results,
        "publication_authorized": False,
        "remaining_checks": ["active_rule_and_guards", "noon_run_completion", "sales_floor", "public_readback"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sku", action="append", required=True)
    ap.add_argument("--report-json", type=Path)
    args = ap.parse_args()
    try:
        report = check(args.sku)
    except Exception as exc:
        report = {"ok": False, "error_type": type(exc).__name__, "mode": "read_only_acquisition"}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report_json:
        args.report_json.write_text(output)
    print(output)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
