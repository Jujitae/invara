"""Order intake: read, price, store, report. Nothing else."""

import json
import os
import sys

from pricing import total_of
from storage import new_order_identity, save_order, write_receipt


def tags_for(order, total, order_id):
    tags = {
        "priority" if total >= 100 else "standard",
        "coupon" if order.get("coupon") else "no-coupon",
        order.get("customer", {}).get("tier", "none"),
    }
    return sorted(tags, key=lambda t: hash(t + order_id))


def main():
    order = json.load(sys.stdin)
    workspace = os.environ.get("INVARA_WORKSPACE", ".")
    try:
        total, lines = total_of(order)
    except ValueError as error:
        sys.stderr.write("invalid order: " + str(error) + "\n")
        sys.exit(2)
    order_id, created_at = new_order_identity()
    save_order(os.path.join(workspace, "shop.db"), order, order_id, created_at, total, lines)
    write_receipt(os.path.join(workspace, "out", "receipt.txt"), order_id, created_at, total, lines)
    print(
        json.dumps(
            {
                "order_id": order_id,
                "created_at": created_at,
                "customer": order.get("customer", {}).get("name", ""),
                "total": total,
                "lines": lines,
                "tags": tags_for(order, total, order_id),
            }
        )
    )


if __name__ == "__main__":
    main()
