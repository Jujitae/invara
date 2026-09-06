"""Shipping cost, the original.

Fixture E: a function over an explicitly finite domain — zone 1..5,
weight class 1..8, express or not — eighty inputs in all, so equivalence
over the whole domain can be proved by running every one of them.

stdin: {"zone": 3, "weight_class": 2, "express": false}
stdout: {"cost": 12.5}
"""

import json
import sys

BASE = {1: 4.0, 2: 5.5, 3: 7.0, 4: 9.0, 5: 12.0}
RATE = {1: 0.5, 2: 0.75, 3: 1.0, 4: 1.5, 5: 2.25}
EXPRESS = 7.5


def cost(zone, weight_class, express):
    amount = BASE[zone] + weight_class * RATE[zone]
    if express:
        amount += EXPRESS
    return round(amount, 2)


def main():
    data = json.load(sys.stdin)
    print(json.dumps({"cost": cost(int(data["zone"]), int(data["weight_class"]), bool(data["express"]))}))


if __name__ == "__main__":
    main()
