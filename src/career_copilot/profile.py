"""Load, update, and save the local master profile."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from career_copilot.conflicts import detect_conflicts
from career_copilot.dates import is_ongoing, parse_date_token
from career_copilot.errors import ProfileError
from career_copilot.inputs import MAX_DOWNLOAD_BYTES, require_evidence_id, require_statement
from career_copilot.extract import extract_atoms
from career_copilot.models import (
    DEFAULT_DATA_NOTICE,
    ConfirmationState,
    DocumentConflict,
    DocumentKind,
    DocumentRecord,
    EvidenceCategory,
    ExactDate,
    MasterProfile,
    ProfileAtom,
    Residence,
    UserPreferences,
)
from career_copilot.ownership import OWNERSHIP_VERBS, verb_appears
from career_copilot.parsers import parse_document
from career_copilot.vocab import skill_named_in


class UnresolvedConflictsError(ProfileError):
    """confirm-all stopped because disagreements are still open."""


class _Missing:
    pass


MISSING: Any = _Missing()


def default_profile_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return Path.cwd() / ".career-copilot" / "profile.json"


def empty_profile() -> MasterProfile:
    return MasterProfile(data_notice=DEFAULT_DATA_NOTICE, updated_at=_now())


def load_profile(path: Path) -> MasterProfile:
    if not path.exists():
        return empty_profile()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"Could not read {path.name}.") from exc
    try:
        return MasterProfile.model_validate_json(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path.name} is not valid JSON.") from exc
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) for error in exc.errors()[:8]
        )
        raise ProfileError(f"{path.name} does not match the profile schema ({fields}).") from exc


def new_profile_id() -> str:
    return "pf_" + uuid.uuid4().hex[:16]


def save_profile(path: Path, profile: MasterProfile) -> MasterProfile:
    if profile.profile_id is None:
        profile = profile.model_copy(update={"profile_id": new_profile_id()})
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return profile


def set_display_name(profile: MasterProfile, name: str) -> MasterProfile:
    try:
        return profile.model_copy(update={"display_name": name, "updated_at": _now()})
    except ValidationError as exc:
        raise ProfileError(_validation_message(exc)) from exc


def set_residence(profile: MasterProfile, residence: Residence | None) -> MasterProfile:
    try:
        checked = None if residence is None else Residence.model_validate(residence.model_dump())
        return profile.model_copy(update={"residence": checked, "updated_at": _now()})
    except ValidationError as exc:
        raise ProfileError(_validation_message(exc)) from exc


def ingest_bytes(
    profile: MasterProfile,
    *,
    name: str,
    payload: bytes,
    kind: DocumentKind,
) -> tuple[MasterProfile, int, list[str]]:
    """Parse an uploaded PDF or DOCX from memory. The bytes are not logged."""

    checked = _upload_name(name)
    if not isinstance(payload, (bytes, bytearray)):
        raise ProfileError("The file could not be read.")
    data = bytes(payload)
    if not data:
        raise ProfileError("The file is empty.")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ProfileError("The file is too large.")
    if not isinstance(kind, DocumentKind):
        try:
            kind = DocumentKind(kind)
        except ValueError as exc:
            raise ProfileError("The document type is not valid.") from exc
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / checked
        path.write_bytes(data)
        return ingest_file(profile, path, kind)


def set_preferences(profile: MasterProfile, preferences: UserPreferences, *, update: bool) -> MasterProfile:
    """Store answers once. A second write needs update=True."""

    if profile.preferences is not None and not update:
        raise ProfileError("Preferences are already saved. Pass --update to replace them.")
    return profile.model_copy(update={"preferences": preferences, "updated_at": _now()})


def ingest_file(profile: MasterProfile, path: Path, kind: DocumentKind) -> tuple[MasterProfile, int, list[str]]:
    """Replace any stored copy of the same file bytes, then recompute conflicts."""

    document = parse_document(path, kind)
    atoms = extract_atoms(document)
    return merge_document(profile, document, atoms)


def merge_document(
    profile: MasterProfile,
    document: DocumentRecord,
    atoms: list[ProfileAtom],
) -> tuple[MasterProfile, int, list[str]]:
    """Add one parsed document. The profile id and preferences stay on this profile."""

    documents = [item for item in profile.documents if item.document_id != document.document_id]
    kept = [item for item in profile.atoms if item.source_document_id != document.document_id]
    updated = profile.model_copy(
        update={
            "documents": [*documents, document],
            "atoms": [*kept, *atoms],
            "updated_at": _now(),
        }
    )
    return _with_conflicts(updated, profile.conflicts), len(atoms), list(document.warnings)


def confirm_atoms(profile: MasterProfile, evidence_ids: list[str]) -> tuple[MasterProfile, list[str]]:
    if not evidence_ids:
        raise ProfileError("Name at least one evidence id to confirm.")
    evidence_ids = [require_evidence_id(evidence_id) for evidence_id in evidence_ids]
    known = {atom.evidence_id for atom in profile.atoms}
    missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in known]
    if missing:
        raise ProfileError("No atom with evidence id " + ", ".join(missing) + ".")
    selected = set(evidence_ids)
    warnings: list[str] = []
    conflict_for = _open_conflict_for(profile)
    atoms: list[ProfileAtom] = []
    for atom in profile.atoms:
        if atom.evidence_id not in selected:
            atoms.append(atom)
            continue
        if atom.confirmation == ConfirmationState.externally_verified:
            warnings.append(
                f"{atom.evidence_id} is externally_verified, which this version does not change."
            )
            atoms.append(atom)
            continue
        conflict_id = conflict_for.get(atom.evidence_id)
        if conflict_id is not None:
            warnings.append(
                f"{atom.evidence_id} is part of unresolved conflict {conflict_id}. Both atoms stay."
            )
        atoms.append(atom.model_copy(update={"confirmation": ConfirmationState.confirmed}))
    return profile.model_copy(update={"atoms": atoms, "updated_at": _now()}), warnings


def confirm_all(profile: MasterProfile) -> MasterProfile:
    unresolved = [conflict for conflict in profile.conflicts if not conflict.resolved]
    if unresolved:
        lines = ["Unresolved conflicts remain. Both versions of each fact stay in the profile."]
        for conflict in unresolved:
            lines.append(f"{conflict.conflict_id} {conflict.kind.value}: {conflict.summary}")
        raise UnresolvedConflictsError("\n".join(lines))
    if not profile.atoms:
        raise ProfileError("The profile has no atoms.")
    atoms = []
    for atom in profile.atoms:
        if atom.confirmation == ConfirmationState.externally_verified:
            atoms.append(atom)
            continue
        atoms.append(atom.model_copy(update={"confirmation": ConfirmationState.confirmed}))
    return profile.model_copy(update={"atoms": atoms, "updated_at": _now()})


def unconfirm_atom(profile: MasterProfile, evidence_id: str) -> MasterProfile:
    return _replace_atom(
        profile,
        evidence_id,
        lambda atom: atom.model_copy(update={"confirmation": ConfirmationState.extracted}),
    )


def resolve_conflict(profile: MasterProfile, conflict_id: str, note: str) -> MasterProfile:
    cleaned = note.strip()
    if not cleaned:
        raise ProfileError("A resolution note is required. Resolving does not merge atoms.")
    found = False
    conflicts: list[DocumentConflict] = []
    for conflict in profile.conflicts:
        if conflict.conflict_id != conflict_id:
            conflicts.append(conflict)
            continue
        found = True
        conflicts.append(conflict.model_copy(update={"resolved": True, "resolution_note": cleaned}))
    if not found:
        raise ProfileError(f"No conflict with id {conflict_id}.")
    return profile.model_copy(update={"conflicts": conflicts, "updated_at": _now()})


def edit_atom(
    profile: MasterProfile,
    evidence_id: str,
    *,
    statement: Any = MISSING,
    category: Any = MISSING,
    employer: Any = MISSING,
    title: Any = MISSING,
    start: Any = MISSING,
    end: Any = MISSING,
    skills: Any = MISSING,
    metrics: Any = MISSING,
    ownership_verb: Any = MISSING,
) -> MasterProfile:
    atom = _require_atom(profile, evidence_id)
    if atom.confirmation == ConfirmationState.externally_verified:
        raise ProfileError(
            f"{evidence_id} is externally_verified, which this version does not edit."
        )
    document = profile.document(atom.source_document_id)
    if document is None:
        raise ProfileError(f"{evidence_id} cites a missing document.")
    changes: dict[str, Any] = {"confirmation": ConfirmationState.extracted}
    if statement is not MISSING:
        changes["statement"] = require_statement(statement)
    if category is not MISSING:
        try:
            changes["category"] = EvidenceCategory(category)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in EvidenceCategory)
            raise ProfileError(f"Category must be one of: {allowed}.") from exc
    if employer is not MISSING:
        changes["employer"] = _exact_optional(employer, document.text, "Employer")
    if title is not MISSING:
        changes["title"] = _exact_optional(title, document.text, "Title")
    if start is not MISSING:
        changes["start_date"] = None if start is None else _user_date(str(start), document.text, "Start date")
    if end is not MISSING:
        changes["end_date"] = None if end is None else _user_date(str(end), document.text, "End date")
    if skills is not MISSING:
        changes["skills"] = _skills(skills, atom.source_excerpt)
    if metrics is not MISSING:
        changes["metrics"] = _metrics(metrics, atom.source_excerpt)
    if ownership_verb is not MISSING:
        changes["ownership_verb"] = _verb(ownership_verb, atom.source_excerpt)
    if len(changes) == 1:
        raise ProfileError("Nothing to change.")
    try:
        replacement = atom.model_copy(update=changes)
        ProfileAtom.model_validate(replacement.model_dump())
    except ValidationError as exc:
        raise ProfileError(_validation_message(exc)) from exc
    updated = _replace_atom(profile, evidence_id, lambda _atom: replacement)
    return _with_conflicts(updated, profile.conflicts)


def _exact_optional(value: object, document_text: str, label: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text not in document_text:
        raise ProfileError(f"{label} must be an exact string from the source document.")
    return text


def _user_date(token: str, document_text: str, label: str) -> ExactDate | None:
    cleaned = token.strip()
    if not cleaned:
        raise ProfileError(f"{label} is empty.")
    if cleaned not in document_text:
        raise ProfileError(f"{label} must be an exact date string from the source document.")
    if is_ongoing(cleaned):
        return None
    parsed = parse_date_token(cleaned)
    if parsed is None:
        raise ProfileError(
            f"Could not read {label.lower()} {cleaned!r}. "
            "Use a date copied from the document, such as Jan 2020 or 2020."
        )
    return parsed


def _skills(value: object, excerpt: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileError("Skills must be a list of strings.")
    skills: list[str] = []
    for item in value:
        skill = str(item).strip()
        if not skill:
            raise ProfileError("Skill cannot be empty.")
        if not skill_named_in(skill, excerpt):
            raise ProfileError(f"Skill {skill!r} is not named in the source excerpt.")
        skills.append(skill)
    return skills


def _metrics(value: object, excerpt: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileError("Metrics must be a list of strings.")
    metrics: list[str] = []
    for item in value:
        metric = str(item).strip()
        if not metric:
            raise ProfileError("Metric cannot be empty.")
        if metric not in excerpt:
            raise ProfileError(f"Metric {metric!r} is not an exact copy from the source excerpt.")
        metrics.append(metric)
    return metrics


def _verb(value: object, excerpt: str) -> str | None:
    if value is None:
        return None
    verb = str(value).strip()
    if not verb:
        return None
    allowed = {item.casefold(): item for item in OWNERSHIP_VERBS}
    canonical = allowed.get(verb.casefold())
    if canonical is None:
        names = ", ".join(OWNERSHIP_VERBS)
        raise ProfileError(f"Ownership verb must be one of: {names}.")
    if not verb_appears(verb, excerpt):
        raise ProfileError("Ownership verb is not written in the source excerpt.")
    surface = _surface_verb(verb, excerpt)
    return surface or canonical


def _surface_verb(verb: str, excerpt: str) -> str | None:
    match = re.search(r"(?<!\w)" + re.escape(verb) + r"(?!\w)", excerpt, flags=re.IGNORECASE)
    if match is None:
        return None
    return match.group(0)


def _upload_name(name: object) -> str:
    if not isinstance(name, str) or "\x00" in name:
        raise ProfileError("The file name is not valid.")
    cleaned = name.strip()
    if not cleaned or cleaned != Path(cleaned).name or cleaned in {".", ".."}:
        raise ProfileError("The file name is not valid.")
    if len(cleaned) > 180:
        raise ProfileError("The file name is too long.")
    if Path(cleaned).suffix.casefold() not in {".pdf", ".docx"}:
        raise ProfileError("Upload a PDF or DOCX file.")
    return cleaned


def _validation_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        message = str(error["msg"])
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        messages.append(message)
    return "; ".join(messages) if messages else "The edit does not match the atom schema."


def _with_conflicts(profile: MasterProfile, previous: list[DocumentConflict]) -> MasterProfile:
    fresh = detect_conflicts(profile.atoms)
    prior = {conflict.conflict_id: conflict for conflict in previous}
    merged: list[DocumentConflict] = []
    for conflict in fresh:
        old = prior.get(conflict.conflict_id)
        if old is not None and old.resolved:
            conflict = conflict.model_copy(
                update={"resolved": True, "resolution_note": old.resolution_note}
            )
        merged.append(conflict)
    return profile.model_copy(update={"conflicts": merged})


def _replace_atom(profile: MasterProfile, evidence_id: str, transform) -> MasterProfile:
    _require_atom(profile, evidence_id)
    atoms = [
        transform(atom) if atom.evidence_id == evidence_id else atom for atom in profile.atoms
    ]
    return profile.model_copy(update={"atoms": atoms, "updated_at": _now()})


def _require_atom(profile: MasterProfile, evidence_id: str) -> ProfileAtom:
    evidence_id = require_evidence_id(evidence_id)
    atom = profile.atom(evidence_id)
    if atom is None:
        raise ProfileError(f"No atom with evidence id {evidence_id}.")
    return atom


def _open_conflict_for(profile: MasterProfile) -> dict[str, str]:
    found: dict[str, str] = {}
    for conflict in profile.conflicts:
        if conflict.resolved:
            continue
        for evidence_id in conflict.evidence_ids:
            found.setdefault(evidence_id, conflict.conflict_id)
    return found


def _now() -> datetime:
    return datetime.now(timezone.utc)
