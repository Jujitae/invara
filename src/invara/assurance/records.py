"""Record version constants, in a module with nothing else in it.

Every JSON record this subsystem stores names its own version so a reader
can refuse a shape it does not understand instead of misreading it. The
constants live here, apart from the package ``__init__``, so the pure
modules can import them without importing the package's impure siblings.
"""

from __future__ import annotations

#: A raw observation record produced by :mod:`invara.assurance.execute`.
OBSERVATION_VERSION = "invara.assurance.observation/1"

#: A normalized record derived from a raw one under a policy set.
NORMALIZED_VERSION = "invara.assurance.normalized/1"

#: A stored comparison result.
COMPARISON_VERSION = "invara.assurance.comparison/2"

#: A stored freeze of the baseline envelope.
FREEZE_VERSION = "invara.assurance.freeze/1"

__all__ = ["COMPARISON_VERSION", "FREEZE_VERSION", "NORMALIZED_VERSION", "OBSERVATION_VERSION"]
