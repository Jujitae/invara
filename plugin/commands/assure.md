---
description: Decide whether an existing AFTER behaves the same as its BEFORE under a declared definition of equivalence — corpus comparison, counterexample search and finite-domain proof, with a plain-language report (이미 바뀐 코드가 예전과 같은지 확인)
---

The user has a BEFORE and an AFTER — two directories, two commits checked
out side by side, a legacy system and its migration — and wants to know
whether the AFTER still does what the BEFORE did. You declare how the two
are observed; INVARA decides. Quote its words exactly.

## 0. The tool

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src"        # bash / zsh
```

```powershell
$env:PYTHONPATH = "${CLAUDE_PLUGIN_ROOT}/src"        # PowerShell
```

Every step is `python -m invara assure <step> ... --db .runtime/verify.db`.
Runs are local and offline; a request to any host but loopback makes a run
`UNVERIFIABLE`.

## 1. Declare the two systems and how they are observed

Write the Equivalence Manifest (`.invara/assure.manifest.json`). It names
both systems, how an input reaches them, which probes observe them, which
policies relax exact comparison and why, and which claims are mandatory.

```json
{
  "schema_version": "invara.assurance.manifest/1",
  "session_id": "migration-2026-09-03",
  "source_system": {"id": "before", "kind": "process", "command": ["python", "legacy.py"], "root": "$SOURCE_ROOT"},
  "target_system": {"id": "after", "kind": "process", "command": ["python", "-m", "newapp"], "root": "$TARGET_ROOT"},
  "input_domain": {"kind": "corpus", "delivery": "stdin_json", "corpus": [{"id": "c1", "input": {"...": "..."}}]},
  "probes": [
    {"id": "cli", "adapter": "process", "capture": ["stderr"], "mandatory": true},
    {"id": "out", "adapter": "json", "source": "stdout", "mandatory": true}
  ],
  "policies": [],
  "claims": [
    {"id": "corpus", "kind": "corpus_equivalence", "mandatory": true},
    {"id": "search", "kind": "counterexample_search", "mandatory": true, "params": {"runs": 100, "seconds": 300, "seed": 1}}
  ]
}
```

When the whole input space is small and enumerable, declare it and ask for
a proof instead of a search:

```json
"input_domain": {"kind": "finite", "delivery": "stdin_json", "finite": {"parameters": {"zone": {"range": [1, 5]}, "express": [false, true]}}},
"claims": [{"id": "domain", "kind": "finite_domain_proof", "mandatory": true}]
```

## 2. Run the protocol

One command does the whole protocol and prints the three questions first:

```bash
python -m invara assure run --manifest .invara/assure.manifest.json --source-root <BEFORE dir> --target-root <AFTER dir> --md .invara/report.md --json .invara/report.json --db .runtime/verify.db
```

It creates the session, characterizes, freezes, evaluates every claim the
manifest declares, computes the verdict and writes the report. If the
report lists volatile values without a policy, or blind spots you did not
intend, amend the manifest (below) and run the same command again: only
what the amendment invalidated is re-evaluated. Run it again with the same
`--source-root` and `--target-root`: the session is bound to them, other
or absent directories are refused (`roots_mismatch`, `root_missing`), and a
different AFTER directory is a new session id. The step-by-step form is
the same protocol, one command at a time:

```bash
python -m invara assure init --manifest .invara/assure.manifest.json --source-root <BEFORE dir> --target-root <AFTER dir> --db .runtime/verify.db
python -m invara assure characterize <session-id> --runs 3 --db .runtime/verify.db
```

Characterize runs only the BEFORE, once per session. Every *volatile* path
it lists differs between runs of the unchanged program; INVARA proposes a
policy kind for each and marks it inferred. Put the policies you agree
with, with reasons, into an amendment file and apply it (the stability
claim is re-derived from the stored runs; characterize is not run again):

```bash
python -m invara assure amend <session-id> --amendment .invara/amend-1.json --db .runtime/verify.db
python -m invara assure freeze <session-id> --db .runtime/verify.db
python -m invara assure compare <session-id> --db .runtime/verify.db
python -m invara assure search <session-id> --db .runtime/verify.db
python -m invara assure prove <session-id> --db .runtime/verify.db        # only for a finite domain
python -m invara assure performance <session-id> --db .runtime/verify.db  # only with a performance_envelope claim
python -m invara assure verify <session-id> --db .runtime/verify.db
python -m invara assure report <session-id> --md .invara/report.md --json .invara/report.json --db .runtime/verify.db
python -m invara assure complete <session-id> --db .runtime/verify.db
```

Performance is preserved only when a `performance_envelope` claim was
declared and `performance` measured it under its protocol; otherwise the
report says performance was NOT VERIFIED, and so do you.

Exit codes: a claim that `DIVERGED` exits 1, one that is `UNVERIFIABLE`
exits 2, a refused step exits 3. `verify --require PASS` exits 1 unless the
final verdict is exactly `PASS`, which is how a sealed INVARA contract can
gate on this session.

## 3. What the words mean

- `PROVED_WITHIN_DECLARED_DOMAIN` — every member of a finite domain was
  compared and matched. The only claim that uses the word proved.
- `PRESERVED_WITHIN_ENVELOPE` — every recorded input matched under the
  declared policies. Says nothing about inputs outside the corpus.
- `NO_DIVERGENCE_FOUND` — the bounded search found no diverging input. Not
  a proof; say so to the user.
- `DIVERGED` — an input on which the two systems differ is on record, with
  the path, both raw values, both normalized values and the policy in force.
  A search also records the smallest input that reproduces it.
- `UNVERIFIABLE` — something could not run or could not be compared. Not a
  pass; it cannot be turned into one by re-running until it goes away.
- `HUMAN_REVIEW` — machine claims hold and a person must decide: a declared
  review item, a volatile dimension nobody accepted a policy for, or a
  policy that was added after a divergence had been seen.

The final verdict is one of `PASS`, `BLOCK`, `UNVERIFIABLE`, `HUMAN_REVIEW`,
and it names the rule that decided it.

## 4. When it diverges

Do not reach for a policy. First show the user the divergence in plain
words: which input, which observed value, before and after. A difference
is either behaviour the user wants kept (then the AFTER is wrong), or noise
they are willing to declare away with a reason (then an amendment with
that reason is the honest record). An amendment that covers a path where a
divergence was already recorded pins the session to `HUMAN_REVIEW`; that
is by design, and you tell the user why.

## 5. Report

Show the three questions and their answers first, verbatim: 전이랑 같아? /
이상한 점 있어? / 모르면 솔직히 말해!. Then the six sections — 기능 유지 / 성능 유지 / 정리 완료 항목 / 되돌린 변경 /
확인하지 못한 영역 / 다음에 사람이 볼 것 — verbatim and first. The
technical report below them is for whoever must check the evidence:
manifest and baseline digests, policies, coverage by kind of knowledge,
every divergence and counterexample, provenance, limitations.
