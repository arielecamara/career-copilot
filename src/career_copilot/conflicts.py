"""Show disagreements between documents. Nothing in here merges or deletes atoms."""

from __future__ import annotations

import hashlib
import re

from career_copilot.models import (
    ConflictKind,
    DocumentConflict,
    EvidenceCategory,
    ExactDate,
    ProfileAtom,
)

# Used only to tell whether two documents make the same strongest claim for one skill.
_STRENGTH = {
    EvidenceCategory.mentioned_only: 0,
    EvidenceCategory.coursework: 1,
    EvidenceCategory.project: 2,
    EvidenceCategory.used_in_job: 3,
}


def detect_conflicts(atoms: list[ProfileAtom]) -> list[DocumentConflict]:
    found: dict[str, DocumentConflict] = {}
    for conflict in (
        *_role_conflicts(atoms),
        *_metric_conflicts(atoms),
        *_skill_conflicts(atoms),
    ):
        found.setdefault(conflict.conflict_id, conflict)
    return [found[key] for key in sorted(found)]


def _role_conflicts(atoms: list[ProfileAtom]) -> list[DocumentConflict]:
    headers = [atom for atom in atoms if _is_role_header(atom)]
    conflicts: list[DocumentConflict] = []
    for index, left in enumerate(headers):
        for right in headers[index + 1 :]:
            if left.source_document_id == right.source_document_id:
                continue
            relation = _employer_relation(left.employer or "", right.employer or "")
            if relation == "different":
                continue
            titles_equal = _same_text(left.title, right.title)
            if not titles_equal and not _overlaps(left, right):
                continue
            pair = _order(left, right)
            if relation == "contained":
                conflicts.append(_field_conflict(ConflictKind.employer, pair[0], pair[1], "employer"))
            if left.title and right.title and not titles_equal:
                conflicts.append(_field_conflict(ConflictKind.title, pair[0], pair[1], "title"))
            if left.start_date and right.start_date and _date_key(left) != _date_key(right):
                conflicts.append(_field_conflict(ConflictKind.dates, pair[0], pair[1], "dates"))
    return conflicts


def _metric_conflicts(atoms: list[ProfileAtom]) -> list[DocumentConflict]:
    bodies = [atom for atom in atoms if atom.metrics and not _is_role_header(atom)]
    conflicts: list[DocumentConflict] = []
    for index, left in enumerate(bodies):
        for right in bodies[index + 1 :]:
            if left.source_document_id == right.source_document_id:
                continue
            if not left.employer or not right.employer:
                continue
            if _employer_relation(left.employer, right.employer) == "different":
                continue
            if left.title and right.title and not _same_text(left.title, right.title):
                continue
            if _skeleton(left.statement) != _skeleton(right.statement):
                continue
            if set(left.metrics) == set(right.metrics):
                continue
            first, second = _order(left, right)
            conflicts.append(
                _make(
                    ConflictKind.metric,
                    first,
                    second,
                    ", ".join(first.metrics),
                    ", ".join(second.metrics),
                    (
                        f"Metric differs between {first.evidence_id} ({', '.join(first.metrics)}) "
                        f"and {second.evidence_id} ({', '.join(second.metrics)}). Both atoms are kept."
                    ),
                )
            )
    return conflicts


def _skill_conflicts(atoms: list[ProfileAtom]) -> list[DocumentConflict]:
    # skill key -> document id -> (strength, atom, surface form)
    grouped: dict[str, dict[str, tuple[int, ProfileAtom, str]]] = {}
    for atom in atoms:
        for skill in atom.skills:
            key = skill.casefold()
            per_document = grouped.setdefault(key, {})
            strength = _STRENGTH[atom.category]
            current = per_document.get(atom.source_document_id)
            if current is None or strength > current[0]:
                per_document[atom.source_document_id] = (strength, atom, skill)
    conflicts: list[DocumentConflict] = []
    for per_document in grouped.values():
        rows = list(per_document.values())
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if left[0] == right[0]:
                    continue
                first, second = (left, right) if left[1].evidence_id <= right[1].evidence_id else (right, left)
                skill_name = first[2]
                conflicts.append(
                    _make(
                        ConflictKind.skill_category,
                        first[1],
                        second[1],
                        f"{skill_name}: {first[1].category.value}",
                        f"{second[2]}: {second[1].category.value}",
                        (
                            f"Skill {skill_name!r} is {first[1].category.value} in {first[1].evidence_id} "
                            f"and {second[1].category.value} in {second[1].evidence_id}. Both atoms are kept."
                        ),
                    )
                )
    return conflicts


def _is_role_header(atom: ProfileAtom) -> bool:
    if atom.category != EvidenceCategory.used_in_job or not atom.employer or not atom.start_date:
        return False
    if atom.employer not in atom.statement:
        return False
    return atom.title is None or atom.title in atom.statement


def _field_conflict(
    kind: ConflictKind,
    left: ProfileAtom,
    right: ProfileAtom,
    field: str,
) -> DocumentConflict:
    left_value = _field_value(left, field)
    right_value = _field_value(right, field)
    return _make(
        kind,
        left,
        right,
        left_value,
        right_value,
        (
            f"{field.capitalize()} differs between {left.evidence_id} ({left_value}) "
            f"and {right.evidence_id} ({right_value}). Both atoms are kept."
        ),
    )


def _field_value(atom: ProfileAtom, field: str) -> str:
    if field == "employer":
        return atom.employer or ""
    if field == "title":
        return atom.title or ""
    if field == "dates":
        return _format_dates(atom)
    return ""


def _format_dates(atom: ProfileAtom) -> str:
    start = atom.start_date.raw if atom.start_date else "?"
    if atom.end_date is not None:
        end = atom.end_date.raw
    elif atom.start_date is not None:
        end = "open"
    else:
        end = "?"
    return f"{start} – {end}"


def _make(
    kind: ConflictKind,
    left: ProfileAtom,
    right: ProfileAtom,
    left_value: str,
    right_value: str,
    summary: str,
) -> DocumentConflict:
    return DocumentConflict(
        conflict_id=_conflict_id(kind.value, left.evidence_id, right.evidence_id),
        kind=kind,
        summary=summary,
        evidence_ids=[left.evidence_id, right.evidence_id],
        left_value=left_value,
        right_value=right_value,
    )


def _conflict_id(kind: str, left_id: str, right_id: str) -> str:
    first, second = sorted((left_id, right_id))
    payload = f"{kind}|{first}|{second}".encode()
    return "cf_" + hashlib.sha256(payload).hexdigest()[:16]


def _order(left: ProfileAtom, right: ProfileAtom) -> tuple[ProfileAtom, ProfileAtom]:
    if left.evidence_id <= right.evidence_id:
        return left, right
    return right, left


def _employer_relation(left: str, right: str) -> str:
    norm_left = _norm_org(left)
    norm_right = _norm_org(right)
    if not norm_left or not norm_right:
        return "different"
    if norm_left == norm_right:
        return "equal"
    if norm_left.startswith(norm_right + " ") or norm_right.startswith(norm_left + " "):
        return "contained"
    return "different"


def _norm_org(value: str) -> str:
    chars = [char.casefold() if char.isalnum() or char in "-&" else " " for char in value]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def _same_text(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return re.sub(r"\s+", " ", left.casefold()).strip() == re.sub(r"\s+", " ", right.casefold()).strip()


def _date_key(atom: ProfileAtom) -> tuple[object, object]:
    return (_parts(atom.start_date), _parts(atom.end_date) if atom.end_date else None)


def _parts(value: ExactDate | None) -> tuple[int, int | None, int | None] | None:
    if value is None:
        return None
    return (value.year, value.month, value.day)


def _overlaps(left: ProfileAtom, right: ProfileAtom) -> bool:
    """Inclusive overlap. A year with no month covers that whole year.

    This only decides whether two roles might be the same stint. It does not add
    those jobs together.
    """

    if left.start_date is None or right.start_date is None:
        return False
    left_start = _bound(left.start_date, is_end=False)
    right_start = _bound(right.start_date, is_end=False)
    left_end = _bound(left.end_date, is_end=True) if left.end_date else (9999, 12, 31)
    right_end = _bound(right.end_date, is_end=True) if right.end_date else (9999, 12, 31)
    return left_start <= right_end and right_start <= left_end


def _bound(value: ExactDate, *, is_end: bool) -> tuple[int, int, int]:
    month = value.month if value.month is not None else (12 if is_end else 1)
    day = value.day if value.day is not None else (28 if is_end else 1)
    return (value.year, month, day)


def _skeleton(text: str) -> str:
    replaced = re.sub(r"\d+(?:[.,]\d+)?%?", "#", text.casefold())
    return re.sub(r"\s+", " ", replaced).strip()
