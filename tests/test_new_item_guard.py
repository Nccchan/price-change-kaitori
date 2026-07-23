import unittest

from src.models import CardItem, ComparisonResult
from src.price_comparator import PriceComparator


class NewItemGuardTests(unittest.TestCase):
    def test_blank_code_new_item_is_not_added(self):
        result = ComparisonResult(
            name="アドバンスパック", code="",
            competitor_price_1=10000, competitor_price_2=None,
            current_price_1=None, current_price_2=None,
            required_margin_1=200, required_margin_2=1000,
            is_new=True,
        )
        payloads = PriceComparator().build_update_payloads(
            "dragonball", [result], [], next_available_row=10
        )
        self.assertEqual(payloads, [])


if __name__ == "__main__":
    unittest.main()
