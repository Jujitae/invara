"""The existing test suite of the ugly shop. It passes. It does not test the boundary.

Run from this directory: python -m unittest test_app
"""

import unittest

import app


class Quote(unittest.TestCase):
    def test_no_discount_below_ten(self):
        total, lines = app.quote({"customer": {"tier": "gold"}, "items": [{"sku": "a", "qty": 9, "price": 2.0}]})
        self.assertEqual(total, 18.0)
        self.assertEqual(lines[0]["line_total"], 18.0)

    def test_gold_discount_on_bulk(self):
        total, _ = app.quote({"customer": {"tier": "gold"}, "items": [{"sku": "a", "qty": 20, "price": 2.0}]})
        self.assertEqual(total, 36.0)

    def test_coupon_needs_fifty(self):
        total, _ = app.quote({"customer": {"tier": "none"}, "items": [{"sku": "a", "qty": 1, "price": 60.0}], "coupon": "SAVE5"})
        self.assertEqual(total, 55.0)
        total, _ = app.quote({"customer": {"tier": "none"}, "items": [{"sku": "a", "qty": 1, "price": 40.0}], "coupon": "SAVE5"})
        self.assertEqual(total, 40.0)

    def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            app.quote({"items": [{"sku": "a", "qty": 0, "price": 1.0}]})


if __name__ == "__main__":
    unittest.main()
