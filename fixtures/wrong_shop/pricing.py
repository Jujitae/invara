"""Pricing rules, written once -- and one of them changed.

Fixture C: the cleaner but wrong refactor. Identical to the valid refactor
except that the bulk discount now starts *above* ten units instead of *at*
ten. The existing tests pass; a customer buying exactly ten pays more.
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
    if qty > DISCOUNT_MIN_QTY and tier in TIER_DISCOUNT:
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
