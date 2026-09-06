"""Shipping cost, rewritten with a fuel surcharge somebody added to one cell."""

import json
import sys

TABLE = {
    zone: [round(base + weight * rate, 2) for weight in range(0, 9)]
    for zone, (base, rate) in {1: (4.0, 0.5), 2: (5.5, 0.75), 3: (7.0, 1.0), 4: (9.0, 1.5), 5: (12.0, 2.25)}.items()
}
TABLE[5][8] += 0.5  # the "remote heavy" surcharge nobody agreed to
EXPRESS_SURCHARGE = 7.5


def cost(zone, weight_class, express):
    amount = TABLE[zone][weight_class]
    return round(amount + (EXPRESS_SURCHARGE if express else 0.0), 2)


def main():
    data = json.load(sys.stdin)
    print(json.dumps({"cost": cost(int(data["zone"]), int(data["weight_class"]), bool(data["express"]))}))


if __name__ == "__main__":
    main()
