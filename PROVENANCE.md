# Provenance

## Where this code came from

INVARA was written inside a private working repository (internally: WIE) and
extracted here on 2026-08-17. It has been dogfooded on that repository's own
development since 2026-08-16 — every contract cited in the README is a real
task from that work, not a fixture.

The extraction moved six files and rewrote one import:

```
src/wie/verify/{__init__,__main__,contract,runner,store}.py  →  src/invara/
src/wie/chain.py                                             →  src/invara/chain.py
from .. import chain                                         →  from . import chain
```

Two test classes were deliberately left behind rather than carried over. They
assert facts about the private repository — that a particular frozen
resolution path is untouched, and that several of its engines share one
hash-chain implementation. They are meaningful there and meaningless here.
Everything else in the suite came across unchanged and passes.

## What is *not* in this repository, and will not be

- **No verdict data.** `.runtime/verify.db` is not published. The verdict
  history quoted in the README is quoted, not shipped.
- **No sealed contracts from the private repository.** Their `intent` and
  `reason` fields describe commercial work and name real companies.
- **No ledgers, no lead data, no customer data, no credentials.**
- **No dependency whose license or provenance is unrecorded.** The package
  declares zero runtime dependencies and imports only the standard library,
  which is the simplest possible answer to that question.

## Third-party code

None at runtime. `hashlib`, `json`, `sqlite3`, `subprocess`, `argparse`,
`pathlib`, `dataclasses` and `datetime` are the entire import surface.
`pytest` is a development dependency only.

## Secret redaction

Nothing in this package reads credentials, and nothing writes an environment
variable to disk. The one place where a secret could reach storage in the
originating repository — a URL carrying an API key in its query string — is
outside INVARA and is masked at that boundary there.

If you seal a contract whose completion command contains a secret, **that
command string is stored verbatim in your own `verify.db`**, because a
contract that could be edited after sealing would not be a contract. Put
secrets in the environment, not in the command.
