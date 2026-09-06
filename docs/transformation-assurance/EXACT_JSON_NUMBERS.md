# Exact JSON observation numbers (B1-2)

The JSON adapter preserves the decimal value of accepted JSON numbers. Integer
syntax remains an integer. Fraction or exponent syntax remains a distinct numeric
category: `1.0` and `1e0` are equivalent, but exact comparison still distinguishes
them from integer `1`. Different nonzero decimal values never become equal merely
because Python's binary float would overflow, underflow or round them together.

The adapter parses fraction/exponent tokens exactly, retaining an ordinary float
only when its JSON spelling round-trips to the same decimal value. Other values
use `decimal.Decimal` internally. Canonical evidence and package JSON encode them
as **unquoted JSON numbers**, with deterministic scientific notation. No number
tags or reserved object keys are used; strings, objects, arrays, booleans and null
keep their existing meaning. Object whitespace and number exponent/trailing-zero
spellings are not observable differences under this value-based adapter.

Existing zero behavior is retained: `-0` is integer zero; `-0.0` and `0.0` compare
equal in the fractional category, although their raw digests can differ. The
declared `float_edges` policy can canonicalize negative zero as before.

For an exact Decimal operand, an accepted numeric tolerance compares the exact
decimal difference against its absolute bound, or against relative bound times
the larger magnitude. Native operands and policy bounds use their JSON decimal
spelling. Decimal precision is derived from the operands, not the ambient context.
The relative ratio printed in diagnostics can be rounded to 32 significant digits;
division of that ratio never decides whether a tolerance passes. Existing
native-only arithmetic is unchanged.

New JSON observations reject `NaN`, `Infinity` and `-Infinity` as non-JSON constants.
Fraction/exponent tokens beyond 10,000 coefficient digits or absolute exponent
10,000 are also rejected with parse diagnostics. These are unobserved values,
not equality evidence. Integers retain the existing parser's exactness and limits.
New observations also reject duplicate object member names after JSON escape
decoding, at every nesting level. This prevents last-member-wins decoding from
discarding behavior before evidence capture. Ordinary object member order remains
non-observable. Historical artifact readers retain their existing behavior; older
captures cannot recover object members that an older adapter already discarded.
Artifact readers allow up to four times the decimal limit to accommodate exact
arithmetic diagnostics from accepted inputs. The canonical_json policy uses the
same bounded lossless fractional parser; its pre-existing constant behavior is
unchanged.

Python API consumers can encounter Decimal in observation or result dictionaries.
Use `invara.exact_json.dumps` and `invara.exact_json.loads` to serialize and reload
these values without loss. Do not use `default=str` for evidence: it changes JSON
numeric types into strings. Standard JSON consumers must also choose a lossless
number parser when reading numbers outside their native numeric range.

Historical native-number records retain their canonical bytes and hashes. Legacy
NaN/Infinity artifacts remain readable; nothing rewrites or strengthens their
claims. Historical observations cannot recover numeric lexemes discarded by an
older adapter. A historical package inspection is not evidence that B1-2 was
absent from the original capture; recapture is required for that assurance.
