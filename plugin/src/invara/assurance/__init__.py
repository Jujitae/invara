"""Transformation assurance: did the behaviour survive the change?

The verifier in the parent package answers one question about one task —
did the work keep the promises it declared. This subpackage answers a
harder one about a *transformation*: given a system before and a system
after, under an explicit, content-addressed definition of equivalence,
what stayed the same, what diverged, what could not be observed, and may
an individual repair be kept.

The trust architecture is the parent's, unchanged. The transformer — a
coding agent, a migration tool, a person — is untrusted and replaceable.
Nothing it says is an input; every claim here is computed from raw
observation records, a frozen baseline and a manifest whose digest is part
of every result. There is no field in which a repairer can mark its own
work accepted, and there is no model, service or network call anywhere on
the path.

Pure modules (``paths``, ``manifest``, ``normalize``, ``compare``,
``claims``, ``session``, ``report``, ``redaction``) never reach for the
process, the filesystem, the clock or the network; a test holds them to
that the way the parent package's core is held. The impure modules —
``evidence``, ``adapters``, ``execute``, ``engine``, ``search``, ``proof``,
``analysis``, ``governor``, ``cli`` — are the adapters around that core.
"""

from __future__ import annotations

from .records import COMPARISON_VERSION, FREEZE_VERSION, NORMALIZED_VERSION, OBSERVATION_VERSION

__all__ = ["COMPARISON_VERSION", "FREEZE_VERSION", "NORMALIZED_VERSION", "OBSERVATION_VERSION"]
