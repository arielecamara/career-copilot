"""Ownership verbs from SPEC.md. Do not add verbs here to make a claim stronger."""

from __future__ import annotations

import re

# Phrase first so "responsible for" is available to the earliest-match scan.
OWNERSHIP_VERBS: tuple[str, ...] = (
    "responsible for",
    "architected",
    "owned",
    "led",
)

_VERB_PATTERNS = tuple(
    (verb, re.compile(r"(?<!\w)" + re.escape(verb) + r"(?!\w)", re.IGNORECASE))
    for verb in OWNERSHIP_VERBS
)


def find_ownership_verb(text: str) -> str | None:
    """Return the ownership verb as written, or None when the excerpt has none."""

    found: str | None = None
    found_at: int | None = None
    for _verb, pattern in _VERB_PATTERNS:
        match = pattern.search(text)
        if match and (found_at is None or match.start() < found_at):
            found = match.group(0)
            found_at = match.start()
    return found


def verb_appears(verb: str, text: str) -> bool:
    pattern = re.compile(r"(?<!\w)" + re.escape(verb) + r"(?!\w)", re.IGNORECASE)
    return pattern.search(text) is not None
