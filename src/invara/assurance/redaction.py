"""Secret redaction for captured text, evidence and reports.

A system under observation may print a credential, and a report may quote
the observation. Neither may carry it. The rules here are narrow and
named: well-known key shapes, ``name=value`` credential assignments, bearer
tokens and private-key blocks. Ordinary prose that merely contains the word
"password" is left alone, because a redactor that eats the evidence is a
different kind of failure.

**Redaction is confidentiality, not equivalence.** Every token carries a
fingerprint of the value it replaced — sixteen hex digits of its SHA-256 —
so the same secret redacts to the same token and two different secrets
redact to two different tokens. A comparison over redacted evidence
therefore still sees a changed secret as a divergence, while the evidence
persists only the fingerprint. Declared ``redact`` *policies* in the
manifest are a different thing: they are explicit normalization, logged as
actions and subject to every abuse protection, and they do erase the value.

Applied at capture time to both sides of a comparison and again to every
report.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

__all__ = ["fingerprint", "looks_secret", "redact_text", "redact_value"]


def fingerprint(value: str) -> str:
    """Sixteen hex digits of the SHA-256 of the value: enough to compare, not to recover."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _token(kind: str, value: str) -> str:
    return f"<redacted:{kind}#{fingerprint(value)}>"


_RULES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        lambda m: _token("private-key", m.group(0)),
    ),
    (
        re.compile(
            r"(?i)\b(access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key"
            r"|api[_-]?key|access[_-]?key|password|passwd|pwd|secret|token)"
            r"(\"?\s*[=:]\s*\"?)(?!<redacted:)([^\s,;\"'`]+)"
        ),
        lambda m: m.group(1) + m.group(2) + _token("credential", m.group(3)),
    ),
    (re.compile(r"(?i)\b(bearer)\s+(?!<redacted:)([A-Za-z0-9._~+/-]{16,}=*)"), lambda m: m.group(1) + " " + _token("bearer-token", m.group(2))),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), lambda m: _token("aws-access-key", m.group(0))),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), lambda m: _token("github-token", m.group(0))),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), lambda m: _token("slack-token", m.group(0))),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), lambda m: _token("api-key", m.group(0))),
)


def redact_text(text: str) -> tuple[str, int]:
    """The text with every recognised secret replaced by a fingerprinted token, and how many were."""

    total = 0
    for pattern, replacement in _RULES:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def looks_secret(text: str) -> bool:
    """Whether any rule would fire on this text — the manifest's gate against storing a secret."""

    return any(pattern.search(text) for pattern, _ in _RULES)


_SECRET_KEY_RE = re.compile(r"(?i)^(?:.*[_-])?(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credentials?)$")


def redact_value(value: Any) -> tuple[Any, int]:
    """Walk a JSON-shaped value; strings are redacted, everything else copied.

    A string under a key that names a secret (``password``, ``access_token``,
    ``api_key`` ...) is redacted whole, whatever it looks like: a parsed JSON
    field or a database column has no keyword inside the value to match.
    """

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        total = 0
        for key, item in value.items():
            if isinstance(key, str) and isinstance(item, str) and item and not item.startswith("<redacted:") and _SECRET_KEY_RE.match(key):
                out[key] = _token("credential", item)
                total += 1
                continue
            out[key], count = redact_value(item)
            total += count
        return out, total
    if isinstance(value, list):
        items: list[Any] = []
        total = 0
        for item in value:
            redacted, count = redact_value(item)
            items.append(redacted)
            total += count
        return items, total
    return value, 0
