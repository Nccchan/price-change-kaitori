"""Network-free regression tests for optional metadata and price-path parity."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch
import uuid

from src import collection_evidence as evidence
from src.homura_fetcher import HomuraFetcher
from src.models import GameType, CardItem, CompetitorData, CompetitorType, UpdatePayload
from src.price_comparator import PriceComparator


def card(name="Alpha", code="RAW1", price="¥1,000", href="/products/1"):
    return (f'<div class="card"><div><a href="{href}">image</a><div>'
            f'<a href="{href}"><h5>{name}</h5></a><span>{code}</span></div></div>'
            f'<div><span>{price}</span></div></div>')


def response(html):
    result = MagicMock()
    result.text = html
    return result


class CollectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.attempt_id = str(uuid.uuid4())
        self.env = patch.dict(os.environ, {
            "COLLECTION_ATTEMPT_ID": self.attempt_id,
            "COLLECTION_ATTEMPT_DIR": str(self.directory),
            "COLLECTION_COLLECTOR_COMMIT": "a" * 40,
            "COLLECTION_COLLECTOR_DIRTY": "0",
            "MASTER_FROM_LEDGER": "0",
        })
        self.env.start()
        evidence._ACTIVE = None

    def tearDown(self):
        evidence._ACTIVE = None
        self.env.stop()
        self.temp.cleanup()

    def fetch(self, pages, game=GameType.POKEMON):
        fetcher = HomuraFetcher()
        fetcher.session.get = MagicMock(side_effect=[
            p if isinstance(p, BaseException) else response(p) for p in pages])
        with patch("src.homura_fetcher.get_master_data", return_value={"S1": "Alpha"}):
            result = fetcher.fetch(game)
        return fetcher, result

    def read(self, game="pokemon", suffix=""):
        return json.loads((self.directory / f"homura-{game}{suffix}.json").read_text())

    def test_pokemon_box_and_ns_are_separate_explicit_units(self):
        fetcher, result = self.fetch([card(), card(price="¥800")])
        artifact = self.read()
        self.assertEqual([(c["category_id"], c["external_unit"], c["complete"])
                          for c in artifact["categories"]], [(128, "BOX", True), (129, "NS", True)])
        self.assertEqual([(c["external_ref"], c["external_unit"], c["observed_price"])
                          for c in artifact["candidates"]], [("S1", "BOX", 1000), ("S1", "NS", 800)])
        self.assertEqual((result.items[0].price_1, result.items[0].price_2), (1000, 800))
        self.assertEqual(fetcher.session.get.call_count, 2)
        self.assertNotIn("CARTON", {c["external_unit"] for c in artifact["candidates"]})

    def test_dragonball_never_claims_unfetched_carton(self):
        fetcher, _ = self.fetch([card("FB-01 Alpha")], GameType.DRAGONBALL)
        artifact = self.read("dragonball")
        self.assertEqual(fetcher.session.get.call_count, 1)
        self.assertEqual([c["external_unit"] for c in artifact["categories"]], ["BOX"])

    def test_onepiece_carton_is_direct_category_evidence(self):
        self.fetch([card("OP-01 Alpha"), card("OP-01 Alpha", price="¥12,000")], GameType.ONEPIECE)
        self.assertEqual([(c["external_unit"], c["observed_price"])
                          for c in self.read("onepiece")["candidates"]], [("BOX", 1000), ("CARTON", 12000)])

    def test_identity_and_hash_bind_to_attempt_and_collector(self):
        self.fetch([card(), card()])
        artifact = self.read()
        self.assertEqual(artifact["attempt_id"], self.attempt_id)
        self.assertEqual(artifact["collector_version"]["commit"], "a" * 40)
        self.assertFalse(artifact["collector_version"]["dirty"])
        checksum = artifact.pop("artifact_sha256")
        self.assertEqual(checksum, hashlib.sha256(evidence._canonical(artifact)).hexdigest())
        self.assertTrue(artifact["observed_at"])
        self.assertTrue(artifact["generated_at"])

    def test_empty_json_equivalent_page_does_not_prove_absence(self):
        _, result = self.fetch(["<html>Login or changed layout</html>", card()])
        artifact = self.read()
        self.assertFalse(artifact["categories"][0]["complete"])
        self.assertEqual(artifact["categories"][0]["reason_code"], "unverified_page_structure")
        self.assertTrue(artifact["fetch_succeeded"])
        self.assertEqual(result.items[0].price_1, None)

    def test_malformed_card_means_incomplete_category_not_not_listed(self):
        self.fetch([card() + card("Broken", price="ask us", href="/products/2"), card()])
        artifact = self.read()
        self.assertFalse(artifact["categories"][0]["complete"])
        bad = next(c for c in artifact["candidates"] if c["raw_name"] == "Broken")
        self.assertFalse(bad["parse_ok"])
        self.assertEqual(bad["reason_code"], "invalid_or_missing_price")

    def test_invalid_price_prefix_and_zero_remain_invalid_observations(self):
        for bad in ("¥12abc", "¥0", "¥1,00", "¥1.5"):
            with self.subTest(bad=bad):
                child = self.directory / str(uuid.uuid4())
                child.mkdir()
                with patch.dict(os.environ, {"COLLECTION_ATTEMPT_DIR": str(child)}):
                    _, result = self.fetch([card(price=bad), card()])
                artifact = json.loads((child / "homura-pokemon.json").read_text())
                self.assertFalse(artifact["candidates"][0]["parse_ok"])
                # Existing parsing outcome is deliberately preserved.
                self.assertIsNotNone(result.items[0].price_1)

    def test_duplicate_ref_is_preserved_before_existing_merge(self):
        self.fetch([card(href="/products/1") + card(href="/products/2"), card()])
        box = [c for c in self.read()["candidates"] if c["external_unit"] == "BOX"]
        self.assertEqual(len(box), 2)
        self.assertEqual({c["external_ref"] for c in box}, {"S1"})

    def test_colliding_master_names_cannot_prove_product_identity(self):
        fetcher = HomuraFetcher()
        fetcher.session.get = MagicMock(side_effect=[response(card()), response(card())])
        with patch("src.homura_fetcher.get_master_data", return_value={"S1": "Alpha", "S2": "Alpha"}):
            result = fetcher.fetch(GameType.POKEMON)
        self.assertEqual(result.items[0].code, "S2")  # Existing merge behavior unchanged.
        self.assertTrue(all(not row["identity_resolved"] for row in self.read()["candidates"]))
        self.assertEqual({row["reason_code"] for row in self.read()["candidates"]},
                         {"ambiguous_master_name"})

    def test_identical_responsive_markup_is_deduplicated(self):
        self.fetch([card() + card(), card()])
        self.assertEqual(len([c for c in self.read()["candidates"] if c["external_unit"] == "BOX"]), 1)

    def test_pagination_complete_only_after_final_valid_page(self):
        self.fetch([card() + '<a rel="next">next</a>', card(href="/products/2"), card()])
        category = self.read()["categories"][0]
        self.assertTrue(category["complete"])
        self.assertEqual([p["terminal"] for p in category["pages"]], [False, True])

    def test_unresolved_candidate_prevents_definite_absence(self):
        self.fetch([card("Renamed product", code="unknown-barcode"), card()])
        artifact = self.read()
        self.assertFalse(artifact["categories"][0]["complete"])
        self.assertEqual(artifact["categories"][0]["reason_code"], "unresolved_candidate")
        self.assertFalse(artifact["candidates"][0]["identity_resolved"])

    def test_fetch_failure_preserves_partial_category_evidence_and_exception(self):
        with self.assertRaisesRegex(RuntimeError, "request broke"):
            self.fetch([card(), RuntimeError("request broke")])
        artifact = self.read()
        self.assertFalse(artifact["fetch_succeeded"])
        self.assertTrue(artifact["categories"][0]["complete"])
        self.assertFalse(artifact["categories"][1]["complete"])
        self.assertEqual(artifact["categories"][1]["reason_code"], "fetch_exception")
        self.assertEqual(artifact["error_type"], "RuntimeError")

    def test_no_overwrite_when_same_attempt_repeated(self):
        self.fetch([card(), card()])
        original = (self.directory / "homura-pokemon.json").read_bytes()
        self.fetch([card(price="¥5,000"), card()])
        self.assertEqual(original, (self.directory / "homura-pokemon.json").read_bytes())

    def test_metadata_write_failure_does_not_change_prices_or_http_calls(self):
        with patch.object(evidence, "_atomic_once", side_effect=OSError("disk full")):
            fetcher, result = self.fetch([card(), card(price="¥800")])
        self.assertEqual((result.items[0].price_1, result.items[0].price_2), (1000, 800))
        self.assertEqual(fetcher.session.get.call_count, 2)
        self.assertFalse((self.directory / "homura-pokemon.json").exists())

    def test_metadata_errors_preserve_original_fetch_error(self):
        with patch.object(evidence, "_atomic_once", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "request broke"):
                self.fetch([RuntimeError("request broke")])

    def test_metadata_disabled_has_no_artifact(self):
        with patch.dict(os.environ, {"COLLECTION_ATTEMPT_ID": "", "COLLECTION_ATTEMPT_DIR": ""}):
            _, result = self.fetch([card(), card()])
        self.assertEqual(len(result.items), 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_malformed_environment_is_nonblocking(self):
        with patch.dict(os.environ, {"COLLECTION_ATTEMPT_ID": "not-a-uuid"}):
            _, result = self.fetch([card(), card()])
        self.assertEqual(len(result.items), 1)

    def test_same_value_and_actual_guard_hold_do_not_change_acquisition_success(self):
        self.fetch([card(), card(price="¥800")])
        comp = PriceComparator(custom_margins={"box": 200, "carton": 200})
        data = CompetitorData(game=GameType.POKEMON, competitor=CompetitorType.HOMURA,
                              date="2026/09/11", items=[CardItem("Alpha", "S1", 1000, 800)])
        results = comp.compare("pokemon", data, [CardItem("Alpha", "S1", 1200, 10000)])
        evidence.observe_comparison("pokemon", results, comp.get_decrease_holds("pokemon", results),
                                    comp.get_increase_holds("pokemon", results), False, False)
        self.assertEqual([d["reason_code"] for d in self.read(suffix=".comparison")["decisions"]],
                         ["same_value", "guard_hold"])
        self.assertTrue(all(c["parse_ok"] for c in self.read()["candidates"]))

    def test_previous_collector_and_enabled_collector_have_identical_outputs_and_calls(self):
        # Execute the checked-out commit's untouched implementation offline.
        baseline_sha = "1d8aa8a697998b4389d5f91a9f35606629b109dd"
        previous = subprocess.check_output(
            ["git", "show", f"{baseline_sha}:src/homura_fetcher.py"], text=True)
        namespace = {"__name__": "baseline_homura"}
        exec(compile(previous, "baseline_homura", "exec"), namespace)
        namespace["get_master_data"] = lambda game: {"S1": "Alpha"}
        baseline = namespace["HomuraFetcher"]()
        pages = [card() + card(href="/products/2"), card(price="¥800")]
        baseline.session.get = MagicMock(side_effect=[response(p) for p in pages])
        old = baseline.fetch(GameType.POKEMON)
        current, new = self.fetch(pages)
        self.assertEqual(baseline.to_json(old), current.to_json(new))
        self.assertEqual(baseline.session.get.call_args_list, current.session.get.call_args_list)

    def test_price_payloads_identical_with_observation_disabled_enabled_or_broken(self):
        self.fetch([card(), card()])
        comp = PriceComparator(custom_margins={"box": 200, "carton": 200})
        data = CompetitorData(GameType.POKEMON, CompetitorType.HOMURA, "2026/09/11",
                              [CardItem("Alpha", "S1", 1000, 800)])
        current = [CardItem("Alpha", "S1", 1200, 1000)]
        results = comp.compare("pokemon", data, current)
        active = evidence._ACTIVE
        evidence._ACTIVE = None
        off = comp.build_update_payloads("pokemon", results, current, 3)
        evidence._ACTIVE = active
        on = comp.build_update_payloads("pokemon", results, current, 3)
        with patch.object(evidence, "_atomic_once", side_effect=OSError("disk full")):
            broken = comp.build_update_payloads("pokemon", results, current, 3)
        self.assertEqual(off, on)
        self.assertEqual(off, broken)

    def test_batch_guard_evidence_and_original_exception_are_preserved(self):
        from src.price_guard import BatchPriceGuardError, PriceGuardViolation
        self.fetch([card(), card()])
        comp = PriceComparator(custom_margins={"box": 200, "carton": 200})
        data = CompetitorData(GameType.POKEMON, CompetitorType.HOMURA, "2026/09/11",
                              [CardItem("Alpha", "S1", 1000, 2000)])
        current = [CardItem("Alpha", "S1", 1200, 2200)]
        results = comp.compare("pokemon", data, current)
        error = BatchPriceGuardError([PriceGuardViolation("Alpha", "S1", 1200, 2200, "NS exceeds BOX")], 1)
        with patch("src.price_guard.guard_payloads", side_effect=error):
            with self.assertRaises(BatchPriceGuardError) as caught:
                comp.build_update_payloads("pokemon", results, current, 3)
        self.assertIs(caught.exception, error)
        decision = self.read(suffix=".payloads")
        self.assertTrue(decision["batch_stopped"])
        self.assertEqual({d["guard"] for d in decision["decisions"]}, {"price_guard_batch"})

    def test_price_history_values_requests_and_manual_holds_match_baseline(self):
        from src import supabase_writer
        baseline_sha = "1d8aa8a697998b4389d5f91a9f35606629b109dd"
        namespace = {"__name__": "baseline_writer"}
        exec(compile(subprocess.check_output(
            ["git", "show", f"{baseline_sha}:src/supabase_writer.py"], text=True),
            "baseline_writer", "exec"), namespace)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 11, 3, 0, tzinfo=timezone.utc).astimezone(tz)

        products = [{"id": "pid-box", "sku": "PKM-S1-BOX", "name_jp": "Alpha", "homura_ref": "S1"},
                    {"id": "pid-ns", "sku": "PKM-S1-NS", "name_jp": "Alpha", "homura_ref": "S1"}]
        for manual in (False, True):
            with self.subTest(manual=manual):
                calls = []

                def rest(path, method="GET", body=None):
                    calls.append((path, method, json.loads(json.dumps(body))))
                    if path.startswith("products?"):
                        return products
                    if "source=eq.natsuki-decision" in path:
                        return ([{"product_id": "pid-ns", "unit": "NS", "value": 950}]
                                if manual else [])
                    return []

                def read_back(game, rows, run_id):
                    calls.append(("read_back", game, json.loads(json.dumps(rows))))

                namespace.update(_rest=rest, read_back=read_back, datetime=FixedDateTime)
                payloads = [UpdatePayload(1, "Alpha", "S1", 1200, 1000)]
                old = namespace["write_prices"]("pokemon", payloads)
                original_calls = list(calls)
                calls.clear()
                self.fetch([card(), card()])
                with patch.object(supabase_writer, "_rest", side_effect=rest), \
                     patch.object(supabase_writer, "read_back", side_effect=read_back), \
                     patch.object(supabase_writer, "datetime", FixedDateTime), \
                     patch.object(evidence, "_atomic_once", side_effect=OSError("disk full")):
                    new = supabase_writer.write_prices("pokemon", payloads)
                self.assertEqual(old, new)
                self.assertEqual(original_calls, calls)


if __name__ == "__main__":
    unittest.main()
