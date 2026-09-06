"""Order intake for a small shop. It works. Nobody wants to touch it.

Fixture A: the ugly but working application. Everything is in this file,
the pricing rules are written out twice, storage and formatting sit inside
the business logic, and the output carries a generated id, a timestamp and
a list whose order depends on that id. Its behaviour is what INVARA must
capture and defend.

Input (stdin, JSON):
    {"customer": {"name": "...", "tier": "gold|silver|none"},
     "items": [{"sku": "...", "qty": 3, "price": 9.5}, ...],
     "coupon": "SAVE5"}          (coupon optional)

Output: a JSON summary on stdout, a receipt at $INVARA_WORKSPACE/out/receipt.txt,
and two tables in $INVARA_WORKSPACE/shop.db. Exit 2 on an invalid order.
"""

import datetime
import json
import os
import sqlite3
import sys
import uuid

TIER_DISCOUNT = {"gold": 0.10, "silver": 0.05}


def quote(order):
    # NOTE: the same loop lives in commit_order below. Keep them in sync (they are not).
    total = 0.0
    lines = []
    for item in order.get("items", []):
        qty = int(item["qty"])
        price = float(item["price"])
        if qty <= 0:
            raise ValueError("qty must be positive: " + str(item.get("sku")))
        line = qty * price
        tier = order.get("customer", {}).get("tier", "none")
        if qty >= 10 and tier in TIER_DISCOUNT:
            line = line - line * TIER_DISCOUNT[tier]
        line = round(line, 2)
        lines.append({"sku": item["sku"], "qty": qty, "line_total": line})
        total += line
    if order.get("coupon") == "SAVE5" and total >= 50:
        total -= 5
    return round(total, 2), lines


def commit_order(order, db_path):
    total = 0.0
    lines = []
    for item in order.get("items", []):
        qty = int(item["qty"])
        price = float(item["price"])
        if qty <= 0:
            raise ValueError("qty must be positive: " + str(item.get("sku")))
        line = qty * price
        tier = order.get("customer", {}).get("tier", "none")
        if qty >= 10 and tier in TIER_DISCOUNT:
            line = line - line * TIER_DISCOUNT[tier]
        line = round(line, 2)
        lines.append({"sku": item["sku"], "qty": qty, "line_total": line})
        total += line
    if order.get("coupon") == "SAVE5" and total >= 50:
        total -= 5
    total = round(total, 2)
    order_id = str(uuid.uuid4())
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, customer TEXT, total REAL, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS order_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, order_row_id INTEGER, sku TEXT, qty INTEGER, line_total REAL)"
    )
    cur = con.execute(
        "INSERT INTO orders (order_id, customer, total, created_at) VALUES (?, ?, ?, ?)",
        (order_id, order.get("customer", {}).get("name", ""), total, created_at),
    )
    row_id = cur.lastrowid
    for line in lines:
        con.execute(
            "INSERT INTO order_lines (order_row_id, sku, qty, line_total) VALUES (?, ?, ?, ?)",
            (row_id, line["sku"], line["qty"], line["line_total"]),
        )
    con.commit()
    con.close()
    return order_id, created_at, total, lines


def main():
    order = json.load(sys.stdin)
    ws = os.environ.get("INVARA_WORKSPACE", ".")
    try:
        order_id, created_at, total, lines = commit_order(order, os.path.join(ws, "shop.db"))
    except ValueError as e:
        sys.stderr.write("invalid order: " + str(e) + "\n")
        sys.exit(2)
    os.makedirs(os.path.join(ws, "out"), exist_ok=True)
    f = open(os.path.join(ws, "out", "receipt.txt"), "w", encoding="utf-8")
    f.write("ORDER " + order_id + "\n")
    f.write("AT " + created_at + "\n")
    for line in lines:
        f.write("%s x%d = %.2f\n" % (line["sku"], line["qty"], line["line_total"]))
    f.write("TOTAL %.2f\n" % total)
    f.close()
    tags = set()
    tags.add("priority" if total >= 100 else "standard")
    tags.add("coupon" if order.get("coupon") else "no-coupon")
    tags.add(order.get("customer", {}).get("tier", "none"))
    tags = sorted(tags, key=lambda t: hash(t + order_id))  # "stable enough"
    print(
        json.dumps(
            {
                "order_id": order_id,
                "created_at": created_at,
                "customer": order.get("customer", {}).get("name", ""),
                "total": total,
                "lines": lines,
                "tags": tags,
            }
        )
    )


if __name__ == "__main__":
    main()
