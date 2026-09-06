# Supported and unsupported domains, and the extension protocol

## Supported

- **Systems**: anything that runs as a process from an argv list and
  reads its input from stdin, an argument or a file; anything that runs as
  a local service on a loopback port and speaks HTTP. The source language
  is irrelevant: the fixtures are Python because the test suite is, and
  nothing in the observation path knows that.
- **Inputs**: a declared corpus of JSON-compatible inputs with optional
  initial state (files, SQLite); an explicitly finite cartesian domain;
  sequences of HTTP requests as operation sequences.
- **Observation**: exit codes and bounded streams; JSON parsed from a
  stream or a file; files created and removed under a root, by digest or
  text; SQLite tables by schema and rows, ordered or key-ordered; HTTP
  responses.
- **Equivalence**: exact by default; fifteen declared policy kinds; a
  relationship-preserving generated-identity mapping; bounded numeric and
  timestamp tolerances; explicit exclusions.
- **Evidence kinds**: corpus comparison (`PRESERVED_WITHIN_ENVELOPE`),
  bounded seeded search with minimized reproducers (`NO_DIVERGENCE_FOUND` /
  `DIVERGED`), exhaustive proof over a declared finite domain
  (`PROVED_WITHIN_DECLARED_DOMAIN`), repeated-run stability, measured
  wall-clock performance.
- **Governance**: git repositories with a clean tree; one unit at a time in
  disposable worktrees; crash-safe resume.
- **Structural metrics**: file and line counts, size distribution,
  duplicated line windows for any text source; dependency edges, cycles and
  public surface for Python.
- **Platforms**: Windows and Linux, Python 3.12+, CRLF and LF checkouts,
  UTF-8 and Korean text.

## Unsupported, and what the system says instead

| domain | behaviour |
|---|---|
| general semantic equivalence of arbitrary programs | never claimed; only the three evidence kinds above, each named for what it is |
| thread and process scheduling, concurrency interleavings | not observed; listed as uncontrollable in every record; a claim that depends on them cannot be evaluated |
| external services (network, third-party APIs) | outside the boundary; a request to a non-loopback host makes the run `UNVERIFIABLE`; no record/replay of external traffic is provided |
| GUIs, terminals with interaction, signals as input | not observable through the five adapters; not declared, not compared |
| behaviour over time (schedulers, clocks read by the program) | the clock is uncontrolled; timestamps are handled by declared policies; a program whose output depends on the date beyond formatting is `HUMAN_REVIEW` territory |
| non-Python structural metrics beyond duplication and size | findings for other languages are declared by the host agent and recorded as declarations, never measured |
| partial or sampled proofs | a proof is exhaustive or it is `UNVERIFIABLE` |
| resource limits beyond timeouts and capture bounds | not enforced |
| two processes driving one session | not supported; detected by the ref compare-and-swap or a stale tree, not prevented |

## Extension protocol for formal adapters

The claim model does not change when a backend does. To attach an SMT,
symbolic-execution, model-checking or translation-validation backend:

1. Implement `proof.ProofBackend` (`invara.assurance.proof-backend/1`):
   a `name`, a `version`, and `prove(finite, evaluate, *, max_members,
   max_seconds, clock) -> ProofResult`. The `evaluate` callable is the
   comparator over one input; a backend that reasons about the program
   rather than running it may ignore it, but it must still return a
   `ProofResult` whose `status` is one of `PROVED_WITHIN_DECLARED_DOMAIN`,
   `DIVERGED`, `UNVERIFIABLE`, with `domain_digest` set to the digest of
   the domain it reasoned about, `cardinality` and `members_compared`
   honest about what was covered, and a `counterexample` (id and input)
   for a divergence.
2. Register it: `proof.register_backend(MyBackend())`. Select it by name
   through `proof.prove(..., backend="my-backend")`.
3. A backend must never return `PROVED` for a domain it did not cover in
   full; `ClaimResult` refuses coverage that is not exhaustive, and a
   backend that lies about `members_compared` is a bug the tests for the
   exhaustive backend model how to catch (see `tests/assurance/test_proof.py`).
4. A backend may need a solver. That is a runtime dependency and therefore a
   separate package-release decision; a backend that shells out to an optional
   external tool must return `UNVERIFIABLE` with a reason when the tool is
   absent — the same rule every optional external tool follows here.
5. Version the protocol, not the claim model: a backend needing more from
   `prove` bumps `BACKEND_PROTOCOL_VERSION` and the CLI's `prove` step, and
   nothing in `claims.py` or the report.

The observation side extends the same way: a new adapter is a function
from the run context to a probe record, registered in `execute.run` and
validated in `manifest.Probe`, with the obligations listed in
`ADAPTERS.md`.
