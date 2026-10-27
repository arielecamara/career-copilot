"""ESCO names used only as synonyms of the same concept.

A tool, method, or technology is never matched through a related concept.
The index is a local JSON list of ``{"id": "...", "labels": ["...", ...]}``.
An empty index means two terms match only when they are the same name.
"""

from __future__ import annotations

import json
from pathlib import Path

_LABEL_TO_ID: dict[str, str] = {}
_ID_TO_LABELS: dict[str, tuple[str, ...]] = {}


def _fold(label: str) -> str:
    return " ".join(label.casefold().split())


def load_index(path: Path | None = None) -> None:
    """Replace the in-memory name index. A missing file leaves it empty."""

    global _LABEL_TO_ID, _ID_TO_LABELS
    _LABEL_TO_ID = {}
    _ID_TO_LABELS = {}
    target = path
    if target is None:
        target = Path(__file__).with_name("data") / "esco_labels.json"
    if not target.is_file():
        return
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return
    labels_for: dict[str, list[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        concept_id = str(item.get("id", "")).strip()
        labels = item.get("labels")
        if not concept_id or not isinstance(labels, list):
            continue
        kept = [" ".join(str(label).split()) for label in labels if str(label).strip()]
        if not kept:
            continue
        labels_for[concept_id] = kept
        for label in kept:
            _LABEL_TO_ID.setdefault(_fold(label), concept_id)
    _ID_TO_LABELS = {key: tuple(value) for key, value in labels_for.items()}


def concept_id(term: str) -> str:
    return _LABEL_TO_ID.get(_fold(term), "")


def labels_for(term: str) -> tuple[str, ...]:
    concept = concept_id(term)
    if not concept:
        return (term,)
    return _ID_TO_LABELS.get(concept, (term,))


def same_concept(left: str, right: str) -> bool:
    left_id = concept_id(left)
    right_id = concept_id(right)
    return bool(left_id) and left_id == right_id


load_index()
