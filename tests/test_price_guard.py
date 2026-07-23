import unittest
from types import SimpleNamespace

from src.price_guard import BatchPriceGuardError, guard_payloads


def p(box, ns, code="M5"):
    return SimpleNamespace(name="test", code=code, new_price_1=box, new_price_2=ns)


class PriceGuardTests(unittest.TestCase):
    def test_valid_pokemon(self):
        accepted, rejected = guard_payloads("pokemon", [p(10000, 7500)])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_one_invalid_is_rejected_but_batch_continues(self):
        items = [p(10000, 10500)] + [p(10000, 7500, str(i)) for i in range(20)]
        accepted, rejected = guard_payloads("pokemon", items)
        self.assertEqual(len(accepted), 20)
        self.assertEqual(len(rejected), 1)

    def test_three_invalid_stop_batch(self):
        items = [p(10000, 10500, str(i)) for i in range(3)] + [p(10000, 7500, str(i)) for i in range(60)]
        with self.assertRaises(BatchPriceGuardError):
            guard_payloads("pokemon", items)

    def test_five_percent_stops_batch(self):
        items = [p(10000, 10500)] + [p(10000, 7500, str(i)) for i in range(19)]
        with self.assertRaises(BatchPriceGuardError):
            guard_payloads("pokemon", items)

    def test_non_pokemon_price2_is_not_ns(self):
        accepted, rejected = guard_payloads("onepiece", [p(10000, 120000)])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
