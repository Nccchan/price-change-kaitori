import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("homura_preflight", Path(__file__).parents[1] / "scripts/homura_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def standard():
    p = dict(id="p", sku="PKM-M6A-BOX", homura_ref="M6A", category="Pokemon", canonical_unit="BOX", product_type="BOX")
    c = dict(source="homura", external_ref="M6A", unit="BOX", status="active", kind="auto", unit_verified=True)
    items = [SimpleNamespace(code="M6A", name="30th CELEBRATION", price_1=18000, price_2=14000)]
    raw = {128: [dict(name="30th CELEBRATION", price=18000)] * 2,
           129: [dict(name="30th CELEBRATION", price=14000)] * 2}
    return p, [c], items, raw


def test_box_is_independent_from_ns(standard):
    assert preflight.standard_result(*standard)[0]["price"] == 18000


def test_ns_uses_ns_shelf_and_ns_value(standard):
    standard[0].update(sku="PKM-M6A-NS", product_type="NS", canonical_unit="NS")
    standard[1][0]["unit"] = "NS"
    assert preflight.standard_result(*standard)[0]["price"] == 14000


def test_missing_ns_never_uses_box_value(standard):
    standard[0].update(product_type="NS", canonical_unit="NS")
    standard[1][0]["unit"] = "NS"
    standard[2][0].price_2 = None
    assert preflight.standard_result(*standard)[1] == "invalid_observed_price"


def test_contradictory_mobile_desktop_values_are_not_silently_merged(standard):
    standard[3][128].append(dict(name="30th CELEBRATION", price=17000))
    assert preflight.standard_result(*standard)[1] == "raw_listing_difference"


def test_carton_cannot_inherit_ns_price(standard):
    standard[0].update(product_type="CARTON", canonical_unit="CARTON")
    standard[1][0]["unit"] = "CARTON"
    assert preflight.standard_result(*standard)[1] == "standard_contract_invalid"


@pytest.mark.parametrize("items", [[], [SimpleNamespace(code="OTHER")]])
def test_no_matching_code_is_unconfirmed_not_delisted(standard, items):
    assert preflight.standard_result(standard[0], standard[1], items, standard[3])[1] == "listing_unconfirmed"


def test_check_has_no_write_and_detaches_production_attempt(monkeypatch, standard):
    p, contracts, items, raw = standard
    p.update(is_active=True, kaitori_hidden=False, kaitori_prep=True)
    contracts[0]["product_id"] = "p"
    calls = []
    def rest(path):
        calls.append(path)
        if path.startswith("products?"): return [p]
        if path.startswith("external_price_sources?"): return contracts
        if path.startswith("v_public_kaitori?"): return []
        raise AssertionError(path)
    monkeypatch.setattr(preflight, "_rest", rest)
    monkeypatch.setattr(preflight, "_latest_kaitori_prices", lambda ids: {("p", "BOX"): (17700, "before")})
    monkeypatch.setattr(preflight, "_conf", lambda: ("https://example.invalid", "fixture", None, None))
    monkeypatch.setenv("COLLECTION_ATTEMPT_ID", "do-not-touch")
    monkeypatch.setenv("COLLECTION_ATTEMPT_DIR", "/never-write-here")
    def fetch(self, game):
        self._fetch_category(128); self._fetch_category(129)
        return SimpleNamespace(items=items)
    monkeypatch.setattr(preflight.HomuraFetcher, "fetch", fetch)
    monkeypatch.setattr(preflight.HomuraFetcher, "_fetch_category", lambda self, c: raw[c])
    result = preflight.check([p["sku"]])
    assert result["ok"] and result["publication_authorized"] is False
    assert result["results"][0]["latest_history_price"] == 17700
    assert "COLLECTION_ATTEMPT_ID" not in preflight.os.environ
    assert all(not p.startswith("rpc/") for p in calls)


def test_missing_expected_sku_is_not_a_successful_zero_row_check(monkeypatch):
    monkeypatch.setattr(preflight, "_rest", lambda _: [])
    monkeypatch.setattr(preflight, "_latest_kaitori_prices", lambda _: {})
    monkeypatch.setattr(preflight, "_conf", lambda: (None, None, None, None))
    monkeypatch.setattr(preflight.HomuraFetcher, "fetch", lambda *args: SimpleNamespace(items=[]))
    assert not preflight.check(["PKM-MISSING-BOX"])["ok"]
