"""Local profile list and saved documents for the visual interface.

This module does not open a network connection unless the model switch, the API
key, and the daily quota all allow a call. Generation still goes through the
validator, and a rejected edit writes no file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_copilot.docx_export import write_cover_letter, write_resume, write_source_resume
from career_copilot.errors import CareerCopilotError
from career_copilot.identity import list_stored_profiles
from career_copilot.inputs import (
    MAX_DOWNLOAD_BYTES,
    MAX_RECORD_BYTES,
    directory_inside,
    path_inside,
    require_as_of,
    require_bool,
    require_job_text,
    require_sentence_text,
)
from career_copilot.llm import apply_model_draft, read_job
from career_copilot.models import (
    DocumentRequest,
    JobAnalysis,
    MasterProfile,
    ResumeSentence,
    SentenceKind,
    Violation,
)
from career_copilot.source_cv import SourceResume, SourceSection, prefer_source
from career_copilot.presentation import header_contact, is_academic, job_name, resume_header_name
from career_copilot.profile import load_profile, save_profile
from career_copilot.resume import ResumeBundle, build_resume
from career_copilot.validator import validate_resume

_RECORD_ID = re.compile(r"^gen_[0-9a-f]{16}$")
_PROFILE_KEY = re.compile(r"^(?:pf|file)_[0-9a-f]{16}$")
_MANUAL = "## Manual edits"


@dataclass
class Draft:
    """A resume package held in memory. Nothing has been written yet."""

    analysis: JobAnalysis
    sentences: list[ResumeSentence]
    cover: list[ResumeSentence]
    diff_markdown: str
    violations: list[Violation]
    publication_note: bool = False
    source: SourceResume | None = None


@dataclass
class SaveResult:
    written: bool
    violations: list[Violation]
    directory: Path | None


class StoredGeneration(BaseModel):
    """One local generation. The profile itself is not copied into this file."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    created_at: datetime
    updated_at: datetime
    profile_key: str
    job_text: str
    as_of: date
    tally_percent: float | None = None
    wrote_resume: bool
    wrote_cover: bool
    sentences: list[ResumeSentence]
    cover: list[ResumeSentence] = Field(default_factory=list)
    diff_markdown: str
    job_name: str = ""
    status: Literal["draft", "kept", "discarded", "submitted"] = "draft"
    sent_on: date | None = None
    publication_note: bool = False
    source_cv: bool = False
    summary_text: str = ""
    skills_text: str = ""

    @field_validator("summary_text", "skills_text")
    @classmethod
    def plain_section(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("section text contains a null character")
        if len(value) > 8_000:
            raise ValueError("section text is too long")
        return value

    @field_validator("record_id")
    @classmethod
    def check_record_id(cls, value: str) -> str:
        if not _RECORD_ID.match(value):
            raise ValueError("record_id must look like gen_ plus 16 hex characters")
        return value

    @field_validator("profile_key")
    @classmethod
    def check_profile_key(cls, value: str) -> str:
        if not _PROFILE_KEY.match(value):
            raise ValueError("profile_key is not valid")
        return value

    @field_validator("job_text")
    @classmethod
    def job_text_not_blank(cls, value: str) -> str:
        try:
            return require_job_text(value)
        except CareerCopilotError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("job_name")
    @classmethod
    def clean_job_name(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("job name contains a null character")
        stripped = value.strip()
        if len(stripped) > 120:
            raise ValueError("job name is too long")
        return stripped


def new_record_id() -> str:
    return "gen_" + uuid.uuid4().hex[:16]


def profile_key(path: Path, profile: MasterProfile) -> str:
    """Stable folder name for one profile file. A missing id uses the path."""

    if profile.profile_id is not None and _PROFILE_KEY.match(profile.profile_id):
        return profile.profile_id
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return "file_" + digest


def generated_root(start: Path | None = None) -> Path:
    return (start or Path.cwd()) / ".career-copilot" / "generated"


def discover_profile_paths(start: Path | None = None) -> list[Path]:
    """Profiles the interface may open. An env path hides every other file."""

    forced = os.environ.get("CAREER_COPILOT_PROFILE")
    if forced:
        return [Path(forced)]
    root = (start or Path.cwd()).resolve()
    found: list[Path] = []
    for candidate in (root / "profile.json", root / ".career-copilot" / "profile.json"):
        if candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in found:
                found.append(resolved)
    for path, _profile in list_stored_profiles(root / ".career-copilot" / "profiles"):
        resolved = path.resolve()
        if resolved not in found:
            found.append(resolved)
    return found


def draft_documents(
    profile: MasterProfile,
    job_text: str,
    as_of: date,
    *,
    resume: bool,
    cover: bool,
    analysis: JobAnalysis | None = None,
    profile_path: Path | None = None,
) -> Draft:
    """Build and validate a package. This function writes nothing."""

    job_text = require_job_text(job_text)
    as_of = require_as_of(as_of)
    resume = require_bool(resume)
    cover = require_bool(cover)
    if not resume and not cover:
        raise CareerCopilotError("Choose a resume, a cover letter, or both.")
    if analysis is None:
        analysis = read_job(job_text, profile.atoms, as_of=as_of)
    if cover and DocumentRequest.cover_letter not in analysis.requested_documents:
        raise CareerCopilotError("The posting does not ask for a cover letter. No cover letter was written.")
    bundle = build_resume(
        profile.atoms,
        analysis,
        cover_letter=cover,
        job_text=job_text,
        academic=is_academic(profile),
    )
    bundle, source = prefer_source(profile, analysis, bundle, profile_path=profile_path)
    replaceable = {SentenceKind.summary, SentenceKind.skill} if source is not None else None
    drafted = apply_model_draft(profile, analysis, bundle, job_text, replaceable=replaceable)
    if drafted is not None and source is not None:
        adopted = source.adopt(drafted.sentences)
        if adopted is None:
            drafted = None
        else:
            source = adopted
            bundle = ResumeBundle(
                sentences=adopted.sentences,
                cover=drafted.cover,
                diff_markdown=drafted.diff_markdown,
                publication_note=False,
            )
    elif drafted is not None:
        bundle = drafted
    if cover and not bundle.cover:
        raise CareerCopilotError("No confirmed role sentence is available for a cover letter.")
    sentences = bundle.sentences
    cover_sentences = bundle.cover if cover else []
    violations = validate_resume(
        _to_check(sentences, cover_sentences, resume=resume, include_cover=cover),
        profile,
        analysis,
    )
    return Draft(
        analysis=analysis,
        sentences=sentences,
        cover=cover_sentences,
        diff_markdown=bundle.diff_markdown,
        violations=violations,
        publication_note=bundle.publication_note,
        source=source,
    )


def with_texts(sentences: list[ResumeSentence], texts: list[str]) -> list[ResumeSentence]:
    """Replace sentence text. Evidence ids and kinds stay as they were."""

    if len(texts) != len(sentences):
        raise CareerCopilotError("The edited text does not match the generated sentences.")
    updated: list[ResumeSentence] = []
    for sentence, text in zip(sentences, texts):
        checked = require_sentence_text(text)
        try:
            updated.append(
                ResumeSentence.model_validate(
                    {
                        "text": checked,
                        "evidence_ids": list(sentence.evidence_ids),
                        "kind": sentence.kind,
                    }
                )
            )
        except ValidationError as exc:
            raise CareerCopilotError("Sentence text is empty.") from exc
    return updated


def save_generation(
    root: Path,
    *,
    profile: MasterProfile,
    profile_key: str,
    job_text: str,
    as_of: date,
    analysis: JobAnalysis,
    base_sentences: list[ResumeSentence],
    base_cover: list[ResumeSentence],
    base_diff: str,
    sentences: list[ResumeSentence],
    cover: list[ResumeSentence],
    write_resume_doc: bool,
    write_cover: bool,
    record_id: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    publication_note: bool = False,
    job_label: str | None = None,
    source_sections: tuple[SourceSection, ...] | list[SourceSection] | None = None,
    source_cv: bool = False,
    summary_text: str = "",
    skills_text: str = "",
) -> SaveResult:
    """Validate first. A violation leaves the directory untouched."""

    key = _require_key(profile_key)
    job_text = require_job_text(job_text)
    as_of = require_as_of(as_of)
    write_resume_doc = require_bool(write_resume_doc)
    write_cover = require_bool(write_cover)
    publication_note = require_bool(publication_note)
    source_cv = require_bool(source_cv)
    if not isinstance(summary_text, str) or not isinstance(skills_text, str):
        raise CareerCopilotError("Section text must be text.")
    if "\x00" in summary_text or "\x00" in skills_text:
        raise CareerCopilotError("Section text contains a null character.")
    if not write_resume_doc and not write_cover:
        raise CareerCopilotError("Choose a resume, a cover letter, or both.")
    if write_cover and not cover:
        raise CareerCopilotError("No confirmed role sentence is available for a cover letter.")
    for sentence in _to_check(sentences, cover, resume=write_resume_doc, include_cover=write_cover):
        require_sentence_text(sentence.text)
    violations = validate_resume(
        _to_check(sentences, cover, resume=write_resume_doc, include_cover=write_cover),
        profile,
        analysis,
    )
    if violations:
        return SaveResult(written=False, violations=violations, directory=None)
    stamp = datetime.now(timezone.utc)
    notes = _manual_notes(base_sentences, sentences) if write_resume_doc else []
    if write_cover:
        notes.extend(_manual_notes(base_cover, cover))
    diff = _compose_diff(base_diff, notes)
    chosen_id = record_id or new_record_id()
    previous = _previous_record(root, key, record_id)
    label = job_label if isinstance(job_label, str) and job_label.strip() else ""
    if not label and previous is not None and previous.job_name:
        label = previous.job_name
    if not label:
        label = job_name(job_text)
    try:
        stored = StoredGeneration(
            record_id=chosen_id,
            created_at=(previous.created_at if previous is not None else None) or created_at or stamp,
            updated_at=updated_at or stamp,
            profile_key=key,
            job_text=job_text,
            as_of=as_of,
            tally_percent=analysis.tally.percent,
            wrote_resume=write_resume_doc,
            wrote_cover=write_cover,
            sentences=sentences,
            cover=cover,
            diff_markdown=diff,
            job_name=label,
            status=previous.status if previous is not None else "draft",
            sent_on=previous.sent_on if previous is not None else None,
            publication_note=publication_note,
            source_cv=source_cv,
            summary_text=summary_text if source_cv else "",
            skills_text=skills_text if source_cv else "",
        )
    except ValidationError as exc:
        raise CareerCopilotError("The generated document could not be stored.") from exc
    directory = directory_inside(root, key, stored.record_id)
    directory.mkdir(parents=True, exist_ok=True)
    if write_resume_doc:
        if source_sections is not None:
            write_source_resume(directory / "resume.docx", source_sections)
        else:
            write_resume(
                directory / "resume.docx",
                sentences,
                header_contact=header_contact(profile),
                header_name=resume_header_name(profile, sentences),
                publication_note=publication_note,
            )
    if write_cover:
        name = next((sentence.text for sentence in sentences if sentence.kind == SentenceKind.name), "")
        if not name:
            name = resume_header_name(profile, sentences) or ""
        write_cover_letter(directory / "cover.docx", cover, name)
    (directory / "diff.md").write_text(stored.diff_markdown, encoding="utf-8")
    _write_record(directory, stored)
    return SaveResult(written=True, violations=[], directory=directory)


def record_analysis(
    root: Path,
    *,
    profile: MasterProfile,
    profile_key: str,
    job_text: str,
    as_of: date,
    analysis: JobAnalysis,
    record_id: str | None = None,
) -> StoredGeneration:
    """Store one analyzed job. This writes no DOCX and does not change the profile."""

    if profile.version != 1:
        raise CareerCopilotError("The profile could not be read.")
    key = _require_key(profile_key)
    job_text = require_job_text(job_text)
    as_of = require_as_of(as_of)
    label = job_name(job_text)
    stamp = datetime.now(timezone.utc)
    chosen_id = record_id or new_record_id()
    previous = _previous_record(root, key, record_id)
    try:
        stored = StoredGeneration(
            record_id=chosen_id,
            created_at=previous.created_at if previous is not None else stamp,
            updated_at=stamp,
            profile_key=key,
            job_text=job_text,
            as_of=as_of,
            tally_percent=analysis.tally.percent,
            wrote_resume=previous.wrote_resume if previous is not None else False,
            wrote_cover=previous.wrote_cover if previous is not None else False,
            sentences=list(previous.sentences) if previous is not None else [],
            cover=list(previous.cover) if previous is not None else [],
            diff_markdown=previous.diff_markdown if previous is not None and previous.diff_markdown else _analysis_note(as_of),
            job_name=label,
            status=previous.status if previous is not None else "draft",
            sent_on=previous.sent_on if previous is not None else None,
            publication_note=previous.publication_note if previous is not None else False,
            source_cv=previous.source_cv if previous is not None else False,
            summary_text=previous.summary_text if previous is not None else "",
            skills_text=previous.skills_text if previous is not None else "",
        )
    except ValidationError as exc:
        raise CareerCopilotError("The job record could not be stored.") from exc
    directory = directory_inside(root, key, stored.record_id)
    directory.mkdir(parents=True, exist_ok=True)
    _write_record(directory, stored)
    return stored


def set_record_status(
    root: Path,
    profile_key: str,
    record_id: str,
    status: str,
    *,
    sent_on: date | None = None,
) -> StoredGeneration:
    """Update a local record. Discard hides it in the page and deletes nothing."""

    if status not in {"draft", "kept", "discarded", "submitted"}:
        raise CareerCopilotError("That status is not valid.")
    key = _require_key(profile_key)
    stored = load_generation(root, key, record_id)
    update: dict[str, object] = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == "submitted":
        if sent_on is None:
            raise CareerCopilotError("Enter the date you sent the application.")
        update["sent_on"] = require_as_of(sent_on)
    try:
        rewritten = stored.model_copy(update=update)
    except ValidationError as exc:
        raise CareerCopilotError("The job record could not be stored.") from exc
    directory = directory_inside(root, key, rewritten.record_id)
    _write_record(directory, rewritten)
    return rewritten


def list_generations(root: Path, profile_key: str) -> list[StoredGeneration]:
    key = _require_key(profile_key)
    folder = root / key
    if not folder.is_dir():
        return []
    records: list[StoredGeneration] = []
    for path in folder.glob("*/record.json"):
        resolved = path_inside(path, folder)
        if resolved is None or not _small_enough(resolved, MAX_RECORD_BYTES):
            continue
        try:
            stored = StoredGeneration.model_validate_json(resolved.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError):
            continue
        if resolved.parent.name != stored.record_id:
            continue
        records.append(stored)
    records.sort(key=lambda item: item.updated_at, reverse=True)
    return records


def load_generation(root: Path, profile_key: str, record_id: str) -> StoredGeneration:
    if not _RECORD_ID.match(record_id):
        raise CareerCopilotError("That generated document was not found.")
    folder = directory_inside(root, _require_key(profile_key))
    path = path_inside(folder / record_id / "record.json", folder)
    if path is None or path.parent.name != record_id or not _small_enough(path, MAX_RECORD_BYTES):
        raise CareerCopilotError("That generated document was not found.")
    try:
        return StoredGeneration.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        raise CareerCopilotError("That generated document could not be read.") from exc


def legacy_docx(directory: Path) -> list[Path]:
    """DOCX files already in a folder, newest first. The search is not recursive."""

    if not directory.is_dir():
        return []
    files: list[Path] = []
    for path in directory.glob("*.docx"):
        resolved = path_inside(path, directory)
        if resolved is None or resolved.suffix.casefold() != ".docx":
            continue
        try:
            size = resolved.stat().st_size
        except OSError:
            continue
        if size > MAX_DOWNLOAD_BYTES:
            continue
        files.append(resolved)
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def commit_profile(path: Path, before: MasterProfile, updated: MasterProfile, *, root: Path) -> MasterProfile:
    """Save a profile and keep its generated folder when the first id is assigned."""

    stored = save_profile(path, updated)
    old = profile_key(path, before)
    new = profile_key(path, stored)
    if old != new:
        source = directory_inside(root, old)
        target = directory_inside(root, new)
        if source.is_dir() and not target.exists():
            source.rename(target)
            _rewrite_profile_key(target, old, new)
    return stored


def ui_launch_command(port: int) -> list[str]:
    """Argv for the local page. The address is loopback and usage stats are off."""

    app = Path(__file__).resolve().with_name("ui_app.py")
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--client.showErrorDetails",
        "none",
        "--client.showErrorLinks",
        "false",
        "--server.allowedHosts",
        "localhost",
        "--server.allowedHosts",
        "127.0.0.1",
        "--server.port",
        str(port),
    ]


def _to_check(
    sentences: list[ResumeSentence],
    cover: list[ResumeSentence],
    *,
    resume: bool,
    include_cover: bool,
) -> list[ResumeSentence]:
    chosen: list[ResumeSentence] = []
    if resume:
        chosen.extend(sentences)
    if include_cover:
        chosen.extend(cover)
    return chosen


def _rewrite_profile_key(folder: Path, old: str, new: str) -> None:
    for path in folder.glob("*/record.json"):
        try:
            stored = StoredGeneration.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError):
            continue
        if stored.profile_key != old:
            continue
        rewritten = stored.model_copy(update={"profile_key": new})
        payload = json.dumps(rewritten.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
        path.write_text(payload, encoding="utf-8")


def _small_enough(path: Path, limit: int) -> bool:
    try:
        return path.stat().st_size <= limit
    except OSError:
        return False


def _require_key(value: str) -> str:
    if not _PROFILE_KEY.match(value):
        raise CareerCopilotError("The profile key is not valid.")
    return value


def _manual_notes(base: list[ResumeSentence], edited: list[ResumeSentence]) -> list[str]:
    originals = {_identity(sentence): sentence.text for sentence in base}
    notes: list[str] = []
    for sentence in edited:
        previous = originals.get(_identity(sentence))
        if previous == sentence.text:
            continue
        notes.append(f"- {sentence.kind.value} {sentence.evidence_ids[0]}: {sentence.text}")
    return notes


def _identity(sentence: ResumeSentence) -> tuple[str, tuple[str, ...]]:
    return (sentence.kind.value, tuple(sentence.evidence_ids))


def _previous_record(root: Path, key: str, record_id: str | None) -> StoredGeneration | None:
    if not isinstance(record_id, str):
        return None
    try:
        return load_generation(root, key, record_id)
    except CareerCopilotError:
        return None


def _write_record(directory: Path, stored: StoredGeneration) -> None:
    payload = json.dumps(stored.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    (directory / "record.json").write_text(payload, encoding="utf-8")


def _analysis_note(as_of: date) -> str:
    return f"Analyzed on {as_of.isoformat()}. No resume has been saved yet.\n"


def _compose_diff(base_diff: str, notes: list[str]) -> str:
    cleaned = base_diff
    if _MANUAL in cleaned:
        cleaned = cleaned[: cleaned.index(_MANUAL)]
    cleaned = cleaned.rstrip() + "\n"
    if not notes:
        return cleaned
    extra = "\n".join(["", _MANUAL, "", *notes, ""])
    return cleaned.rstrip() + "\n" + extra
