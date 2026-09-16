import copy
import importlib.util
from pathlib import Path

import pytest

from src import supabase_writer as writer

spec = importlib.util.spec_from_file_location("special_follow", Path(__file__).parents[1] / "scripts/special_follow_kaitori.py")
special = importlib.util.module_from_spec(spec)
spec.loader.exec_module(special)


@pytest.fixture
def bundle():
    name = "30th CELEBRATION プレミアムデッキセット エーフィ・ブラッキー"
    product = dict(id="p1", sku="PKM-M6A-DECK", category="Pokemon", product_type="DECK",
                   canonical_unit="BOX", homura_ref=name, is_active=True, kaitori_hidden=False,
                   kaitori_prep=True)
    contract = dict(product_id="p1", source="homura", status="active", kind="manual",
                    unit="BOX", external_ref=name, unit_verified=True)
    candidate = dict(name=name, price=16000, products=[dict(id="p1", sku=product["sku"])])
    return product, [contract], [candidate]


def test_deck_identifier_is_not_a_price_unit(bundle):
    item, reason = special.validate_target(*bundle)
    assert item["price"] == 16000 and reason is None


@pytest.mark.parametrize("price", [0, None, True, False, "16,000", "bad", -1, 1.5])
def test_invalid_price_blocks(bundle, price):
    bundle[2][0]["price"] = price
    assert special.validate_target(*bundle)[1] == "invalid_observed_price"


@pytest.mark.parametrize("field,value", [("unit", "NS"), ("status", "paused"),
    ("unit_verified", False), ("source", "macho"), ("kind", "auto"), ("derive_rule", "multiply")])
def test_contract_must_still_authorize_exact_source_and_lane(bundle, field, value):
    bundle[1][0][field] = value
    assert special.validate_target(*bundle)[1] == "source_contract_invalid"


def test_verified_unit_without_current_listing_is_not_success(bundle):
    assert special.validate_target(bundle[0], bundle[1], [])[1] == "listing_unconfirmed"


def test_two_prices_or_two_products_are_ambiguous(bundle):
    assert special.validate_target(bundle[0], bundle[1], bundle[2] * 2)[1] == "ambiguous_listing"
    bundle[2][0]["products"].append(dict(id="p2"))
    assert special.validate_target(*bundle)[1] == "ambiguous_product_match"


def test_source_ref_must_match_product_ref(bundle):
    bundle[1][0]["external_ref"] = "another set"
    assert special.validate_target(*bundle)[1] == "source_ref_difference"


def test_rule_uses_active_offset_and_rejects_mid_cycle_change(monkeypatch):
    monkeypatch.setattr(special, "_rest", lambda _: [{"version": 7, "rule_json": {
        "kaitori": {"categories": {"PKM": {"enabled": True, "source": "homura", "box_offset": 350}}}}}])
    monkeypatch.delenv("RULE_VERSION", raising=False)
    assert special.active_box_rule() == (350, 7)
    monkeypatch.setenv("RULE_VERSION", "6")
    with pytest.raises(ValueError, match="rule_version_changed"):
        special.active_box_rule()


@pytest.mark.parametrize("rules", [[], [{}, {}], [{"version": 1, "rule_json": {"kaitori": {
    "categories": {"PKM": {"enabled": True, "source": "homura", "box_offset": True}}}}}]])
def test_invalid_or_missing_rule_never_falls_back_to_200(monkeypatch, rules):
    monkeypatch.setattr(special, "_rest", lambda _: rules)
    with pytest.raises(ValueError):
        special.active_box_rule()


def test_manual_read_failure_is_closed_only_for_strict_caller(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("offline")
    monkeypatch.setattr(writer, "_rest", fail)
    rows = [dict(product_id="p1", unit="BOX", value=100)]
    with pytest.raises(RuntimeError, match="manual_decision_read_failed"):
        writer._hold_same_day_manual(rows, strict=True)
    assert writer._hold_same_day_manual(rows) == (rows, [])


@pytest.fixture
def runtime(monkeypatch, bundle):
    product, contracts, candidates = copy.deepcopy(bundle)
    monkeypatch.setattr(special, "TARGETS", {product["sku"]: {}})
    monkeypatch.setattr(special, "active_box_rule", lambda: (200, 6))
    monkeypatch.setattr(special, "_conf", lambda: ("https://example.invalid", "fixture", None, None))
    monkeypatch.setattr(special.HomuraFetcher, "resolve_other_items", lambda *args: candidates)
    monkeypatch.setattr(special, "_latest_kaitori_prices", lambda _: {("p1", "BOX"): (14200, "today")})
    monkeypatch.setattr(special, "_hold_same_day_manual", lambda rows, **kw: (rows, []))
    monkeypatch.setattr(special, "_drop_unchanged_today", lambda rows: (rows, 0))
    writes, reads = [], []
    def rest(path, method="GET", body=None):
        if method != "GET":
            writes.append((path, body))
            return []
        if path.startswith("products?"): return [product]
        if path.startswith("external_price_sources?"): return contracts
        raise AssertionError(path)
    monkeypatch.setattr(special, "_rest", rest)
    monkeypatch.setattr(special, "read_back", lambda *args: (reads.append(args) or ("PASS", [])))
    monkeypatch.setattr(special, "notify_if_new", lambda *args: pytest.fail("unexpected notification"))
    return writes, reads, candidates


def test_dry_run_is_read_only_and_complete(runtime, tmp_path):
    import json
    report = tmp_path / "check.json"
    assert special.run(False, report) == 0
    data = json.loads(report.read_text())
    assert data["results"][0]["proposed_price"] == 16200
    assert data["publication_checked"] is False
    assert runtime[0] == runtime[1] == []


def test_batch_write_and_shared_readback(runtime):
    assert special.run(True) == 0
    assert len(runtime[0]) == len(runtime[1]) == 1
    assert runtime[0][0][1][0]["value"] == 16200


def test_not_listed_is_nonzero_and_does_not_write(runtime):
    runtime[2].clear()
    assert special.run(True) == 2
    assert runtime[0] == runtime[1] == []


def test_manual_decision_protected_without_notification(runtime, monkeypatch):
    monkeypatch.setattr(special, "_hold_same_day_manual", lambda rows, **kw: ([], [dict(manual=15000)]))
    assert special.run(True) == 0
    assert runtime[0] == []


def test_unchanged_does_not_insert_but_is_read_back(runtime, monkeypatch):
    monkeypatch.setattr(special, "_drop_unchanged_today", lambda rows: ([], len(rows)))
    assert special.run(True) == 0
    assert runtime[0] == [] and len(runtime[1]) == 1


def test_readback_mismatch_is_nonzero(runtime, monkeypatch):
    monkeypatch.setattr(special, "read_back", lambda *args: ("FAIL", [{}]))
    assert special.run(True) == 2
