# Assurance producer identity

Assurance sessions bind the verifier at creation, before any baseline capture.
`producer_identity` is part of the hashed `created` event, the reduced session,
and the report's technical provenance. Package version 4 also names its digest
in the index and hashed export event. An old session/package without this
contract cannot be resumed or accepted as an identified producer.

The identity is derived from the actual imported `invara` package, not the
first installed distribution called `invara`. It contains:

- A sorted SHA-256 manifest of every shipped Python module, including the
  assurance implementation, kernel, CLI and MCP launcher, plus the aggregate
  manifest digest. Missing required modules and mixed package paths are refused.
- The actual imported module paths and source `__version__`. Loaded Python
  function code is checked against compiled source; its aggregate also binds
  continuity across a session. Standard dataclass-generated methods are
  distinguished from handwritten verifier source.
- Distribution version, metadata location/digest, console entrypoints and
  direct-install metadata when that distribution owns the loaded package.
  Unrelated installed metadata is labelled separately and never supplies the
  producer version. Conflicting attributable versions/entrypoints are refused.
- Python executable bytes and the invoked module or wrapper path/bytes, when
  available. A changed wrapper is refused on session continuation.
- The enclosing verifier checkout's commit, tree and observed clean state when
  Git can establish them. These describe the verifier, separately from the
  source/target repository in the assurance report. Git is optional for an
  installed distribution or source archive; missing Git is recorded as null.

Workflow steps check this identity before using a session and again when
recording their outcome. A source, import-root, function, attributable metadata
or launcher change requires a new session. Changes to an enclosing checkout's
unrelated files do not by themselves change verifier identity; the module
manifest still checks every verifier source file.

Package inspection validates the embedded contract without opening paths
supplied by the archive. It checks consistency between creation, export, index
and report and reports `producer_identity` separately from `inspector_identity`.
The same implementation relocated to a different directory can inspect a
package. A different source manifest or runtime function digest cannot close
that package's replay: `identity_match` and `ok` are false. The inspector is
checked again after replay to detect a change during inspection.

These records are unsigned local provenance, not a vendor signature or remote
attestation. They establish which local implementation is claimed and detect
deployment/import drift. They cannot defeat an owner who controls the Python
process, replaces the identity checker, or consistently rewrites the complete
evidence chain and its external receipt. Python's standard library, operating
system and execution environment remain trusted; this is not an SBOM or an
attestation of every transitive platform dependency. The legacy kernel contract
and replay format are unchanged; this contract applies to assurance sessions
and assurance package replay.
