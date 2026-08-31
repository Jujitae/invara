# INVARA local execution handbook

## Product boundary

INVARA independently evaluates declared constraints from observable file bytes,
command results, and recorded provenance.  It does not prove general software
correctness, authorize work, or accept an agent's claim that work is done.
`BLOCK` and `UNVERIFIABLE` are valid fail-closed outcomes, not conditions to
weaken away.

## Local architecture and canonical sources

- `src/invara/`: contract sealing, observation, verdict, replay, and hash-chain
  implementation.
- `tests/test_invara.py`: refusal-first executable specification.
- `README.md`: user-facing product contract and first-verdict flow.
- `PROVENANCE.md`, `PRIVACY.md`, and `SECURITY.md`: provenance, local-only data,
  and threat-model commitments.
- `plugin/`: bundled MCP/stdio surface; its Python package and license files
  must stay byte-aligned with `src/invara/` and the root license files.
- Company orchestration, portfolio priority, role routing, and main integration
  authority live in `migaryos-hq`; this repository does not restate them.

## Invariants and known traps

- Observable evidence and deterministic verdicts are authoritative;
  self-report is not completion evidence.  An agent may request a verdict but
  cannot give itself one.
- Preserve sealed-contract, protected-path, evidence, hash-chain, and replay
  semantics.  Do not turn a refusal, broken check, or missing executable into
  green; retain fail-closed `UNVERIFIABLE`/`BLOCK` behavior.
- The buyer is a non-coder: the MCP/stdio entry point and usable first-verdict
  experience are product surfaces, not optional demos.
- Keep the zero-runtime-dependency and local-only/no-network claims true.
  Contracts run user-named commands with user permissions and can store their
  command strings; never put secrets in a contract.
- Windows encoding, Store Python aliases, CRLF digest changes, and plugin-copy
  drift are tested product behavior.  Do not paper over them with platform-
  specific bypasses.

## Commands and Definition of Done

```powershell
pip install pytest
pip install -e .
pytest -q
git diff --check
```

For a completed local change: the full suite passes, the plugin bundle remains
aligned where applicable, `git diff --check` is clean, protected-path and
fail-closed semantics remain intact, and a fresh independent verifier has
issued the candidate verdict.  Do not submit externally, handle credentials,
spend money, publish, or deploy from this repository without separately
delegated authority.
