"""Pricing rules, written once.

Fixture B: the valid refactor. The two copies of the loop in the ugly
application are one function here, the thresholds have names, and nothing
in this module touches a file, a database or the clock.
"""

TIER_DISCOUNT = {"gold": 0.10, "silver": 0.05}
DISCOUNT_MIN_QTY = 10
COUPON_CODE = "SAVE5"
COUPON_VALUE = 5
COUPON_MIN_TOTAL = 50


def price_line(item, tier):
    qty = int(item["qty"])
    price = float(item["price"])
    if qty <= 0:
        raise ValueError("qty must be positive: " + str(item.get("sku")))
    line = qty * price
    if qty >= DISCOUNT_MIN_QTY and tier in TIER_DISCOUNT:
        line = line - line * TIER_DISCOUNT[tier]
    return {"sku": item["sku"], "qty": qty, "line_total": round(line, 2)}


def total_of(order):
    """(total, lines) for an order, or ValueError for an invalid one."""

    tier = order.get("customer", {}).get("tier", "none")
    lines = [price_line(item, tier) for item in order.get("items", [])]
    # start from 0.0: an empty order totals 0.0 (a float), as the original printed it
    total = sum((line["line_total"] for line in lines), 0.0)
    if order.get("coupon") == COUPON_CODE and total >= COUPON_MIN_TOTAL:
        total -= COUPON_VALUE
    return round(total, 2), lines
