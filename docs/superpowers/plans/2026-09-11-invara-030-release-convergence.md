# INVARA 0.3.0 Release Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge the verified INVARA Agent-Native V1 public/core candidate, official discovery surfaces, documentation, downloadable artifacts, and landing page on one exact `0.3.0` release generation before any real-money E2E purchase.

**Architecture:** Keep the existing zero-runtime-dependency INVARA Free verifier and its plugin/MCP byte-alignment contract. Keep the existing separate `invara-pro` V1 package at `0.1.0`, but declare compatibility with `invara>=0.3,<0.4`. Use the existing Paddle/MIGARYOS signed-entitlement implementation unchanged; this release lane does not charge, refund, or grant a real customer entitlement.

**Tech Stack:** Python 3.12+, setuptools/wheel, pytest, GitHub Actions, GitHub CLI, PyPI trusted publishing, MCP Registry publisher, static MIGARYOS landing site, existing local Windows Pro candidate and evidence records.

**Spec:** User-provided `/goal Complete INVARA 0.3.0 Release Convergence ...` in this task.

## Global Constraints

- Preserve the existing Agent-Native V1 implementation and Free verification invariants; patch only release identity, compatibility metadata, stale official copy, and release automation required for convergence.
- Treat repository state, published metadata, authenticated provider state, and executable evidence as authoritative in that order; record reconciliation when public state is stale.
- Keep `invara-pro` separate and unpublished unless its existing distribution state proves otherwise. Never make its internal version `0.3.0` for cosmetic reasons.
- Never expose payment details to an agent, issue Pro from checkout-open/draft/success-page claims, or perform a real Paddle payment, refund, customer entitlement test, or customer outreach.
- Preserve historical 0.1.x/0.2.x evidence as historical. Change only current public instructions and current release automation; do not rewrite historical BLOCK records.
- Keep the current Paddle checkout tab open and untouched for the later explicitly authorized human payment test.
- Do not use Claude or Fable. The independent review must be a separate Codex/Astra context with no publication authority.

## Task 1: Freeze the baseline inventory and reconciliation evidence

- [ ] Reconfirm the exact source candidates, branches, commits, remotes, dirty files, and worktree boundaries for `work/invara-030`, `work/invara-local-billing-v1`, the primary `invara` checkout, and the static landing-site checkout.
- [ ] Recheck PyPI `invara`, GitHub tags/releases/assets, MCP Registry records, official landing/docs/install URLs, downloadable bundles, Paddle-facing copy, Gumroad public listing state, and Safety/downstream index state without printing secrets.
- [ ] Classify every inspected surface as `CURRENT_0_3_0`, `STALE_0_2_1`, `LEGACY_RETIRE`, `DOWNSTREAM_AUTO_INDEX`, or `NOT_APPLICABLE`; explicitly mark the current public PyPI/GitHub/MCP/landing split and preserve the old 0.2.1 hashes and identities as reconciliation evidence.
- [ ] Store a local, non-secret inventory record under the release evidence directory, including observed URLs, timestamps, HTTP/status results, source SHA identities, and exact reasons a downstream mirror is not manually released.

## Task 2: Make the core candidate's current public release metadata coherent

- [ ] Add release-identity regression assertions for current README, AI-agent quickstart, intent quickstart, plugin README, MCP descriptor, and release workflows so current instructions cannot continue to name public 0.2.1.
- [ ] Run the new assertions first and capture the expected failing result before changing the stale current copy.
- [ ] Update current public install/discovery instructions to pin `invara==0.3.0`, point to the `v0.3.0` quickstart, and describe the public Agent-Native install/Free-ready path. Leave historical evidence and synthetic version fixtures unchanged.
- [ ] Update the PyPI and MCP Registry workflows from the old sealed 0.2.1 identities to exact 0.3.0 release inputs, while retaining tag/source/tree/artifact/hash validation and OIDC/trusted publication boundaries.
- [ ] Verify core source, plugin source, manifests, server descriptor, package scripts, and plugin byte equality still identify the same 0.3.0 generation.

## Task 3: Bind Pro to the supported core compatibility range

- [ ] Add a Pro metadata regression test that requires `invara>=0.3,<0.4`, rejects a 0.2.x-only or unbounded dependency, and preserves Pro `0.1.0`.
- [ ] Run that test red before modifying Pro metadata.
- [ ] Change only the Pro dependency/readme compatibility statements needed to express the supported `invara 0.3.x` range. Do not alter the billing, entitlement, lifecycle, or verifier implementation.
- [ ] Run the Pro unit/regression suite and inspect the built wheel METADATA to prove 0.2.x cannot satisfy the Pro package.

## Task 4: Synchronize the official landing and legacy commerce presentation

- [ ] Update the public Free landing page to the 0.3.0 agent-first install path, retain the simple human fallback, and remove current purchase-like Gumroad CTA behavior.
- [ ] If the historical Gumroad URL remains visible, mark it legacy/retired and state that existing buyer access is preserved; do not delete historical records or route new Pro sales to it.
- [ ] Verify the Pro landing page continues to show the current Paddle path, `$14.99/month`, automatic renewal until canceled, separate Codex/API costs where applicable, secure checkout handling, and no internal implementation concepts.
- [ ] Check the actual Gumroad listing without purchasing. If disabling new sales requires an unavailable seller credential or irreversible Founder-only account action, preserve the public-site retirement repair and record exactly that remaining blocker rather than claiming disabled.
- [ ] Commit and publish the static landing-site change only after the core candidate is frozen, then verify the live URL contains 0.3.0 and no stale current 0.2.1 install route.

## Task 5: Build and validate the exact release candidate from installed bytes

- [ ] Build wheel, sdist, and official plugin bundle from the exact core candidate; compute SHA-256 values and inspect package metadata, archive paths, wheel RECORD, scripts, and plugin contents.
- [ ] Freeze the exact candidate commit and artifact manifest in release evidence. Do not modify source after this freeze without creating a new candidate identity.
- [ ] Run the full core suite, plugin-alignment proof, fresh-install proof, and relevant existing assurance/replay checks.
- [ ] In a clean environment, install only the release artifact and exercise `invara list`, initialize a clean project, and reach Free-ready state without `PYTHONPATH` or local repository hints.
- [ ] Install public `invara==0.2.1` into an isolated environment, create representative existing project/history/seal state, upgrade that same environment to the reviewed 0.3.0 artifact, and verify preserved behavior plus Free operation after upgrade.
- [ ] Install the built Pro wheel against the 0.3.x core, verify compatibility and status/admission behavior locally, and ensure no Paddle charge or real entitlement is created.
- [ ] Exercise negative release gates: checkout-open/draft cannot grant Pro, no billing or activation secret appears in logs/project files/serialized evidence, and no normal path requires license/grant copy-paste or access-sync.

## Task 6: Independent exact-candidate review

- [ ] After the candidate and landing changes are frozen, create one separate Codex/Astra reviewer context with the exact source/artifact/site identities and no shared conversational assumptions.
- [ ] Require the reviewer to inspect and independently exercise release identity, public discovery/install, upgrade, Pro compatibility, publication authorization boundaries, secret isolation, historical-surface handling, and customer-friction acceptance. The reviewer must not publish or repair.
- [ ] Treat builder evidence and reviewer evidence as separate. If the reviewer finds a defect, make the smallest repair, create a new exact candidate, rerun affected trust-boundary checks, and obtain a fresh review of that new identity.

## Task 7: Publish one authorized release train

- [ ] From the independently reviewed exact commit, create and publish GitHub tag/release `v0.3.0` with only the reviewed wheel, sdist, plugin bundle, and checksums.
- [ ] Trigger the exact validated PyPI workflow and verify `pypi.org/pypi/invara/json` reports `0.3.0`, with public file hashes matching the frozen manifest.
- [ ] Trigger the exact validated MCP Registry workflow using the reviewed `server.json`; verify the official record identifies 0.3.0. Do not manually alter Safety or other auto-indexes.
- [ ] Publish the synchronized landing-site commit through its existing official path and verify live public HTML, links, install commands, and Paddle copy.
- [ ] Verify Safety/downstream indexes after the publication window. Record a stale cache as propagation lag unless official install/discovery resolves 0.2.1.

## Task 8: Final evidence and handoff

- [ ] Re-run the completion gates against public URLs and installed bytes, including a clean-context “INVARA 설치해줘.” public discovery smoke that resolves to 0.3.0 and reaches Free-ready state.
- [ ] Preserve exact source SHA, tag/release identity, artifact hashes, deployment identity, public URLs, test commands/results, independent review identity, Gumroad observation, downstream observation, and all unverified boundaries in synchronized local evidence.
- [ ] Run `agent-reach check-update` and include only the final requested fields in the Founder Office handoff.
- [ ] Report `REAL_PAYMENT_STATUS=NOT_PERFORMED`, `REVENUE=USD0`, and keep `REAL_CUSTOMER_ENTITLEMENT=UNVERIFIED` until a later explicitly authorized real-money E2E test.

## Verification Commands

Run commands from the relevant worktree and preserve raw output in the release evidence directory:

```powershell
python -m pytest -q
python -m pytest -q tests/test_release_identity.py
python -m pip wheel --no-deps . --wheel-dir .runtime/invara-dist
python scripts/verify_fresh_install.py --wheel-dir .runtime/invara-dist --plugin-root plugin
git diff --check
gh api repos/Jujitae/invara/releases/latest
Invoke-RestMethod https://pypi.org/pypi/invara/json
Invoke-RestMethod 'https://registry.modelcontextprotocol.io/v0/servers?search=invara'
```

The release is complete only when every user-specified acceptance field is backed by fresh evidence from the exact candidate and the final report distinguishes public verification from the deliberately unperformed real-money path.
