"""Persistence and the receipt, apart from the rules."""

import datetime
import os
import sqlite3
import uuid

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, customer TEXT, total REAL, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS order_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, order_row_id INTEGER, sku TEXT, qty INTEGER, line_total REAL)",
)


def new_order_identity():
    return str(uuid.uuid4()), datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_order(db_path, order, order_id, created_at, total, lines):
    con = sqlite3.connect(db_path)
    try:
        for statement in SCHEMA:
            con.execute(statement)
        cur = con.execute(
            "INSERT INTO orders (order_id, customer, total, created_at) VALUES (?, ?, ?, ?)",
            (order_id, order.get("customer", {}).get("name", ""), total, created_at),
        )
        row_id = cur.lastrowid
        con.executemany(
            "INSERT INTO order_lines (order_row_id, sku, qty, line_total) VALUES (?, ?, ?, ?)",
            [(row_id, line["sku"], line["qty"], line["line_total"]) for line in lines],
        )
        con.commit()
    finally:
        con.close()


def write_receipt(path, order_id, created_at, total, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as receipt:
        receipt.write("ORDER " + order_id + "\n")
        receipt.write("AT " + created_at + "\n")
        for line in lines:
            receipt.write("%s x%d = %.2f\n" % (line["sku"], line["qty"], line["line_total"]))
        receipt.write("TOTAL %.2f\n" % total)
