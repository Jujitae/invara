# Normalization semantics

Comparison is exact by default. A *policy* relaxes it at the paths its
selectors match, on source and target alike, and every application is an
*action* in the audit log stored beside the normalized record. Raw
observations are deep-copied before anything happens; the normalized value
is a separate, separately digested record that names the raw digest and the
policy-set digest it was derived under. Implementation:
`src/invara/assurance/normalize.py`; comparators in `compare.py`.

JSON fraction/exponent values and their tolerance arithmetic follow the
[exact JSON number contract](EXACT_JSON_NUMBERS.md), including lossless
serialization and the distinction between integer and fractional syntax.

## Paths and selectors

An observation is a JSON tree keyed by probe id: `/out/value/orders/0/id`,
`/db/tables/orders/rows/3/total`, `/files/entries/out~1receipt.txt/text`
(RFC 6901 escaping: `~1` is `/`, `~0` is `~`). A selector names a set of
paths: `*` matches exactly one segment, `**` any number including none.
Selectors are matched against normalized paths; when an unordered sort
moves an element, the divergence record carries both the normalized path
and the raw path each side's value came from. A selector that names a
numeric index under a list that is compared unordered
(`/out/value/0/v` beside an `unordered_multiset` at `/out/value`) is
refused as `unstable_index_selector`: after the sort that index names
whichever element happened to land there. Use `*`.

## Policy kinds

| kind | phase | effect | params |
|---|---|---|---|
| `exact` | — | documentary; comparison is exact anyway | |
| `canonical_json` | value | a string leaf is parsed as JSON and compared structurally; an unparseable string, or one with duplicate keys, stays a string and the action says so; the normalized record lists `parsed_roots`, and a divergence inside a parsed value reports the raw text it came from | |
| `ordered_sequence` | — | documentary; lists are compared in order by default | |
| `unordered_set` | structure | the list is sorted by the canonical JSON of each element (generated identifiers masked for the sort) and deduplicated on the exact element, so two records that differ only by a generated identifier are two records | |
| `unordered_multiset` | structure | sorted, duplicates kept | |
| `numeric_abs_tolerance` | compare | numbers compare equal when `|a-b| <= abs`; anything not numeric on both sides is a divergence; refused at freeze and at amendment when `abs` is at least every baseline value it applies to (`overbroad_tolerance`) | `abs` > 0 |
| `numeric_rel_tolerance` | compare | equal when `|a-b| / max(|a|,|b|) <= rel` | `0 < rel < 1` |
| `float_edges` | value | NaN becomes `<nan>`, ±inf become `<inf>`/`<-inf>`, `-0.0` becomes `0.0` | `nan_equal`, `negative_zero_equal` (default true) |
| `timestamp` | value or compare | an ISO 8601 instant with a real calendar date (or an epoch number only with `epoch: true`) becomes `<timestamp>`; instants inside text are replaced inline; with `tolerance_s` the value is kept and instants compare within the bound; a plain number under a tolerance is never a timestamp | `tolerance_s` from 0 to 366 days, `epoch` |
| `generated_id` | identity | values matching the pattern map to `<id:N>` in order of first appearance along the policy's primary selector, then its extra `paths`; identifiers inside text are replaced inline; the same raw value maps to the same placeholder everywhere the policy looks | `pattern` (`uuid`, `int`, `hex`, `any`, `{"regex": ...}`), `group` |
| `path_canonical` | value | declared roots are replaced by their tokens and the path after a token is written with `/` | `roots: [{"token", "path"}]` |
| `redact` | value | the value becomes `<redacted>` on both sides; not a comparable leaf | `replacement` |
| `ignore` | structure | the key or element is removed | |
| `line_endings` | value | `\r\n` and `\r` become `\n` | `to: "lf"` |
| `stable_map` | value | whole-value translation is one declared lookup; `in_text` replaces matches once against the original text, longest key first, independent of map insertion order, and never reprocesses replacement text | `map`, `in_text` |

Every policy whose selector matches a path leaves an action, even when it
had nothing to change (`matched; nothing to change`), so the audit log
says which policies looked at a value and not only which altered it.
Under exact comparison an `int` and a `float` are different types
(`1` vs `1.0` diverges); a tolerance compares mixed numbers by value. A
tolerance that cannot apply to a pair of values (two nulls, two strings)
leaves the values to decide for themselves, exactly; a NaN or an infinity
under a tolerance is a divergence, since no bound absorbs it.

Order of application: the built-in reserved-text escape, then value-local
policies and `ignore` during one bottom-up walk; unordered sorts as each list is finished (sort keys mask
generated identifiers so a fresh id does not decide the order); generated
identity assignment as a final pass in canonical order — dict keys sorted,
list index order — selector by selector. Tolerance policies do not change
values; the comparator applies them at compare time and records every
application, within or beyond the bound.

## The built-in policies

Two normalizations are applied on INVARA's own authority and still logged
as actions under their own ids.

`builtin:reserved` runs first on string leaves: raw placeholders (`<id:N>`,
`<timestamp>`, `<redacted>`, `<nan>`, `<inf>`, `<-inf>`) and `$WORKSPACE` /
`$ROOT` tokens are prefixed with NUL (`\u0000` in JSON). Every raw NUL is
doubled, including one already preceding a reserved token. For example,
raw `<id:1>` becomes `\u0000<id:1>`, whereas raw `\u0000<id:1>` becomes
`\u0000\u0000\u0000<id:1>` (shown in JSON escape notation). This uniquely
decodable encoding applies inline and inside arbitrary nested wrappers;
it has no left-boundary exemption for repeated dollars. Ordinary angle
brackets and dollar signs are unchanged. Generated placeholders and path
tokens are inserted afterwards without an escape prefix. The same raw
literal on both sides still compares equal, while raw text cannot forge
a generated placeholder. Raw evidence is unchanged. Normalized
representations and digests containing these characters differ from the
former one-level literal-wrapper encoding; regenerate comparisons under
the repaired normalizer rather than mixing normalized outputs from both
versions.

`builtin:paths`: the per-run workspace path becomes `$WORKSPACE` and the
resolved system root becomes `$ROOT` where either appears **at a path
boundary** in a string (not as a bare substring of a longer name), with
only the path that follows the token written with forward slashes. Both
are directories INVARA chose; their spelling is not behaviour. A traceback
that names `C:\ws\before\app.py` on one side and `C:\ws\after\app.py`
on the other therefore compares equal, while a traceback that names a
different line does not. A declared `path_canonical` policy on the same
text is credited for its own replacements, the built-in for its own.

## Relationship-preserving identity

With `generated_id` at `/out/value/orders/*/id` and `paths: ["/out/value/lines/*/order_id"]`,
a source where orders `[A, B]` are referenced by lines `[A, A, B]` normalizes
to `[<id:1>, <id:2>]` and `[<id:1>, <id:1>, <id:2>]`. A target with orders
`[C, B]` and lines `[C, B, B]` normalizes to `[<id:1>, <id:2>]` and
`[<id:1>, <id:2>, <id:2>]` — a divergence at the second line, because the
relationship changed even though every id is "just generated". Primary
selector first, extra paths after, so identity is defined where it is
generated and checked where it is referenced.

When an unordered list holds elements that are identical apart from their
generated identifiers, the sort cannot tell them apart; the normalized
record carries an `ambiguities` note and the correspondence is by position.

## Refusals

At validation:

- an `ignore` whose selector is the root, or has no concrete segment
  (`/*`, `/**`, `/*/**`), or names all of a mandatory probe (`/out`, `/out/**`);
- a `generated_id`, `redact`, `timestamp` or `stable_map` policy without a
  concrete segment (`blanket_policy`): these kinds replace values, and a
  wildcard-only selector would replace everything;
- an exclusion at the root or without a concrete segment
  (`root_exclusion`, `blanket_exclusion`), or covering all of a mandatory
  probe (`probe_exclusion`);
- a numeric index under an unordered list (`unstable_index_selector`), in
  a policy or an exclusion;
- a tolerance with a non-positive or non-finite bound, a relative bound of
  1.0 or more, or a timestamp tolerance above 366 days (`overbroad_tolerance`);
- a non-exact policy without a reason; an unknown kind; unknown params;
- a policy with `origin: inferred` and `accepted: true`;
- a manifest with no mandatory claim that compares the target
  (`no_comparison_claim`: `baseline_stability` alone is not assurance) or
  with two claims of one kind (`duplicate_claim_kind`).

At freeze, and again at every amendment against the stored baseline:

- a policy set under which a mandatory probe has no comparable leaf left
  (`normalization_erases_signal`). Placeholders `<redacted...>` and
  `<timestamp>` do not count. Identifier placeholders count only through a
  relationship: a `<id:N>` that appears more than once, or beside another
  comparable value; a probe reduced to distinct identifiers and nothing
  else would match any target that prints as many distinct values. An
  observation that was empty before normalization (no files written, no
  rows) is evidence of emptiness and is not refused;
- an absolute tolerance at least as large as every baseline value it
  applies to (`overbroad_tolerance`).

At compare time, a mandatory probe emptied on both sides is
`unverifiable`, never equal; emptied on one side only, it is a divergence.

After a divergence, an amendment is not refused but it is flagged and the
session's verdict can no longer be `PASS` when it:

- adds or changes a policy whose selector covers a recorded divergence
  path, an ancestor of one, or the raw path a sorted element came from;
- adds or changes an exclusion covering one of those paths;
- removes or demotes a mandatory claim, or removes a human-review item.

Divergences in informational probes and under exclusions are remembered
for this purpose too (`informational_divergence_paths`).

## Stability and proposals

Characterizing with more than one run compares the runs of the unchanged
source. Every path that differs is a *volatile path*; for each, INVARA
proposes a kind — `generated_id` for UUIDs, `timestamp` for instants,
`unordered_multiset` for the same elements in another order, and
`human_decision` for anything it cannot classify. Proposals are recorded
`origin: inferred, accepted: false` and have no effect until a person or an
agent, by name, puts them in the manifest through an amendment with a
reason. A volatile path that no accepted policy covers is reported and
holds the verdict at `HUMAN_REVIEW`. Whether a policy *covers* a path is
decided empirically: the stored runs are re-normalized under the policies
in force and the paths that still differ are uncovered
(`uncovered_volatile_empirical`); a tolerance policy, which rewrites
nothing, covers a path when every run's value lies within its bound of the
first run's. A policy of the wrong kind on the right selector (a
`line_endings` policy over a UUID) covers nothing.
Characterize runs once per session; after an amendment the stability
claim is re-derived from the stored runs under the new policies.
