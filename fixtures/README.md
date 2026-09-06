# Proving fixtures

Self-contained synthetic examples for the transformation assurance core. The
public fixture tests and sample-report generator drive them through the real
workflow; they are safe demonstration inputs, not customer data.

| Fixture | Directory | What it proves |
|---|---|---|
| A — ugly but working | `ugly_shop/` | raw runs differ by benign noise (fresh UUID, wall-clock timestamp, hash-ordered tags, autoincrement keys); the declared policies remove exactly that noise; the normalization audit stays visible; the existing tests pass |
| B — valid refactor | `clean_shop/` | pricing written once, storage and receipt separated; measured duplicate windows and largest-module size fall; behaviour preserved within the envelope; accepted by the governor |
| C — cleaner but wrong | `wrong_shop/` | identical to B except the bulk discount starts above ten units instead of at ten; the existing tests still pass; the counterexample search finds and minimizes an order of exactly ten; the unit is rejected and rolled back without touching the accepted state |

A note on B, kept because it is the point of the exercise: the first
version of the clean refactor computed the total with `sum(...)`, which is
the integer `0` for an empty order where the original prints `0.0`. The
recorded corpus never held an empty order and the existing tests did not
care, so it went unnoticed until the comparison began to distinguish
`int` from `float` under exact comparison: the
search then minimized every wrong-shop divergence down to the empty order.
Both refactors now start the sum from `0.0`. A JSON consumer that reads
`0` and `0.0` differently would have seen that change; INVARA did.
| D — normalization abuse | (tests only) | blanket ignore, root ignore and overbroad tolerance are refused; a policy added after a divergence pins the verdict below PASS |
| E — finite domain | `finite/` | eighty inputs enumerated in full: `target_ok.py` is proved equivalent, `target_bad.py` yields the minimal diverging member |

`manifests/shop.json` and `manifests/finite.json` are the Equivalence
Manifests the tests and sample generator load (they substitute the running interpreter for the
`python` command so the proof does not depend on `PATH`).

Every application writes only under `$INVARA_WORKSPACE`, the fresh
directory INVARA creates for each run.
