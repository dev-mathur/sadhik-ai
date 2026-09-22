"""Restricted-term copy linter.

Sadhik never tells a customer it is "compliant", "certified" or "audit-ready":
it reports whether submitted data is consistent or inconsistent with a
requirement. This module finds those words in any product copy.

The one exemption is the mandated footer (FR 8.3), matched by EXACT string.
"""

from __future__ import annotations

import re

DISCLAIMER = "This is an analysis of submitted data. It is not an audit opinion or an attestation."

RESTRICTED_TERMS: tuple[str, ...] = (
    "compliant",
    "non-compliant",
    "certified",
    "audit-ready",
    "DCAA-approved",
    "attest",
    "attestation",
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:non[-\s]?|in[-\s]?)?compliant\b", re.IGNORECASE),
    re.compile(r"\bcertified\b", re.IGNORECASE),
    re.compile(r"\baudit[-\s]?ready\b", re.IGNORECASE),
    re.compile(r"\bDCAA[-\s]?approved\b", re.IGNORECASE),
    re.compile(r"\battest\w*", re.IGNORECASE),
)


def find_restricted(text: str) -> list[str]:
    """Restricted words/phrases in `text`, in order of appearance (case-insensitive).
    Every exact occurrence of DISCLAIMER is removed first; nothing else is exempt."""
    cleaned = text.replace(DISCLAIMER, " ")
    hits: list[tuple[int, str]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(cleaned):
            hits.append((m.start(), m.group(0)))
    hits.sort()
    return [h for _, h in hits]
