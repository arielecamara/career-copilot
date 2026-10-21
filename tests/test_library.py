"""Saved documents and profile discovery. These tests do not open a browser."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from docx import Document

from career_copilot.errors import CareerCopilotError
from career_copilot.library import (
    commit_profile,
    discover_profile_paths,
    draft_documents,
    list_generations,
    load_generation,
    profile_key,
    record_analysis,
    save_generation,
    set_record_status,
    with_texts,
)
from career_copilot.models import (
    ConfirmationState,
    EvidenceCategory,
    ExactDate,
    PriorityLevel,
    SectorPreference,
    UserPreferences,
    WorkMode,
    WorkModePreference,
)
from career_copilot.profile import load_profile, save_profile, set_preferences
from tests.factories import atom, document, profile_for

AS_OF = date(2026, 10, 13)
JOB = "A cover letter is required.\nRequirements:\n- Postgres\n- Python\n- Kubernetes"
KEY = "pf_" + "a" * 16
OTHER = "pf_" + "b" * 16


def _confirmed(**overrides: object):
    payload: dict[str, object] = {"confirmation": ConfirmationState.confirmed}
    payload.update(overrides)
    return atom(**payload)


def _mentioned(**overrides: object):
    payload: dict[str, object] = {
        "employer": None,
        "title": None,
        "start_date": None,
        "end_date": None,
        "category": EvidenceCategory.mentioned_only,
    }
    payload.update(overrides)
    return _confirmed(**payload)


def _profile():
    name = _mentioned(
        evidence_id="ev_" + "1" * 16,
        statement="Ada Lovelace",
        source_excerpt="Ada Lovelace",
    )
    skill = _mentioned(
        evidence_id="ev_" + "2" * 16,
        statement="Python",
        source_excerpt="Python",
        skills=["Python"],
    )
    database = _mentioned(
        evidence_id="ev_" + "9" * 16,
        statement="PostgreSQL",
        source_excerpt="PostgreSQL",
        skills=["PostgreSQL"],
    )
    role = _confirmed(
        evidence_id="ev_" + "4" * 16,
        statement="Engineer | OldCo 2010 – 2011",
        source_excerpt="Engineer | OldCo 2010 – 2011",
        employer="OldCo",
        title="Engineer",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2011", year=2011),
    )
    bullet = _confirmed(
        evidence_id="ev_" + "5" * 16,
        statement="Used Python to reduce latency by 20%.",
        source_excerpt="Used Python to reduce latency by 20%.",
        employer="OldCo",
        title="Engineer",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2011", year=2011),
        skills=["Python"],
    )
    return profile_for(document([("paragraph", "Ada Lovelace")]), [name, skill, database, role, bullet])


def _preferences() -> UserPreferences:
    return UserPreferences(
        work_modes=[
            WorkModePreference(mode=WorkMode.remote, priority=PriorityLevel.high),
            WorkModePreference(mode=WorkMode.hybrid, priority=PriorityLevel.medium),
            WorkModePreference(mode=WorkMode.onsite, priority=PriorityLevel.only_if_close),
        ],
        onsite_location="Lisbon",
        sector=SectorPreference.both,
    )


def _save(root: Path, profile, draft, sentences, cover, **extra):
    return save_generation(
        root,
        profile=profile,
        profile_key=extra.pop("profile_key", KEY),
        job_text=JOB,
        as_of=AS_OF,
        analysis=draft.analysis,
        base_sentences=draft.sentences,
        base_cover=draft.cover,
        base_diff=draft.diff_markdown,
        sentences=sentences,
        cover=cover,
        write_resume_doc=extra.pop("write_resume_doc", True),
        write_cover=extra.pop("write_cover", False),
        **extra,
    )


def _docx_text(path: Path) -> str:
    return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)


def test_draft_writes_nothing_and_a_missing_skill_blocks_the_save(tmp_path: Path):
    profile = _profile()
    before = list(tmp_path.rglob("*"))
    draft = draft_documents(profile, JOB, AS_OF, resume=True, cover=True)
    assert list(tmp_path.rglob("*")) == before
    assert draft.violations == []
    assert draft.cover
    assert "Kubernetes" not in "\n".join(sentence.text for sentence in draft.sentences)
    assert "Kubernetes" not in "\n".join(sentence.text for sentence in draft.cover)

    texts = [sentence.text for sentence in draft.sentences]
    index = next(item for item, text in enumerate(texts) if "20%" in text)
    texts[index] = "Used Python and Kubernetes to reduce latency by 20%."
    result = _save(tmp_path, profile, draft, with_texts(draft.sentences, texts), draft.cover, write_cover=True)
    assert result.written is False
    assert result.directory is None
    assert any(item.code == "absent_skill" for item in result.violations)
    assert not list(tmp_path.rglob("*.docx"))

    cover_texts = [sentence.text for sentence in draft.cover]
    cover_texts[0] = f"{cover_texts[0]} Kubernetes"
    cover_result = _save(
        tmp_path,
        profile,
        draft,
        draft.sentences,
        with_texts(draft.cover, cover_texts),
        write_cover=True,
    )
    assert cover_result.written is False
    assert not list(tmp_path.rglob("*.docx"))


def test_save_reload_and_a_synonym_edit(tmp_path: Path):
    profile = _profile()
    draft = draft_documents(profile, JOB, AS_OF, resume=True, cover=True)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, tzinfo=timezone.utc)
    first = _save(
        tmp_path,
        profile,
        draft,
        draft.sentences,
        draft.cover,
        write_cover=True,
        record_id="gen_" + "1" * 16,
        created_at=older,
        updated_at=older,
    )
    assert first.written
    assert first.directory is not None
    assert (first.directory / "resume.docx").is_file()
    assert (first.directory / "cover.docx").is_file()
    assert (first.directory / "diff.md").is_file()
    record = (first.directory / "record.json").read_text(encoding="utf-8")
    assert "source_excerpt" not in record
    assert "## Manual edits" not in (first.directory / "diff.md").read_text(encoding="utf-8")
    assert "Kubernetes" not in _docx_text(first.directory / "resume.docx")
    assert "Kubernetes" not in _docx_text(first.directory / "cover.docx")
    resume_before = (first.directory / "resume.docx").read_bytes()
    record_before = (first.directory / "record.json").read_bytes()

    texts = [sentence.text for sentence in draft.sentences]
    index = next(item for item, text in enumerate(texts) if "20%" in text)
    texts[index] = "Used Python and Kubernetes to reduce latency by 20%."
    rejected = _save(
        tmp_path,
        profile,
        draft,
        with_texts(draft.sentences, texts),
        draft.cover,
        write_cover=True,
        record_id="gen_" + "1" * 16,
        created_at=older,
    )
    assert rejected.written is False
    assert (first.directory / "resume.docx").read_bytes() == resume_before
    assert (first.directory / "record.json").read_bytes() == record_before

    skill_index = next(item for item, sentence in enumerate(draft.sentences) if sentence.evidence_ids == ["ev_" + "9" * 16])
    edited_texts = [sentence.text for sentence in draft.sentences]
    edited_texts[skill_index] = "Postgres" if edited_texts[skill_index] != "Postgres" else "PostgreSQL"
    edited = with_texts(draft.sentences, edited_texts)
    assert edited[skill_index].evidence_ids == draft.sentences[skill_index].evidence_ids
    assert edited[skill_index].kind == draft.sentences[skill_index].kind
    second = _save(
        tmp_path,
        profile,
        draft,
        edited,
        draft.cover,
        write_cover=True,
        record_id="gen_" + "2" * 16,
        created_at=newer,
        updated_at=newer,
    )
    assert second.written
    assert second.directory is not None
    diff = (second.directory / "diff.md").read_text(encoding="utf-8")
    assert diff.count("## Manual edits") == 1
    assert edited_texts[skill_index] in diff
    assert edited_texts[skill_index] in _docx_text(second.directory / "resume.docx")
    assert "Kubernetes" not in _docx_text(second.directory / "resume.docx")

    listed = list_generations(tmp_path, KEY)
    assert [item.record_id for item in listed] == ["gen_" + "2" * 16, "gen_" + "1" * 16]
    assert list_generations(tmp_path, OTHER) == []


def test_blank_sentence_and_no_document_choice_are_refused():
    profile = _profile()
    draft = draft_documents(profile, JOB, AS_OF, resume=True, cover=False)
    texts = [sentence.text for sentence in draft.sentences]
    texts[0] = "  "
    with pytest.raises(CareerCopilotError, match="empty"):
        with_texts(draft.sentences, texts)
    with pytest.raises(CareerCopilotError, match="Choose"):
        draft_documents(profile, JOB, AS_OF, resume=False, cover=False)


def test_discovery_prefers_the_env_path_and_then_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chosen = tmp_path / "chosen.json"
    workspace = tmp_path / "profile.json"
    chosen.write_text("{}\n", encoding="utf-8")
    workspace.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CAREER_COPILOT_PROFILE", str(chosen))
    assert discover_profile_paths(tmp_path) == [chosen]
    monkeypatch.delenv("CAREER_COPILOT_PROFILE")
    stored = _profile().model_copy(update={"profile_id": OTHER})
    registry = tmp_path / ".career-copilot" / "profiles" / OTHER / "profile.json"
    save_profile(registry, stored)
    found = discover_profile_paths(tmp_path)
    assert found[0] == workspace.resolve()
    assert registry.resolve() in found


def test_analysis_creates_a_local_record_that_can_be_kept_or_sent(tmp_path: Path):
    profile = _profile()
    draft = draft_documents(profile, JOB, AS_OF, resume=True, cover=False)
    stored = record_analysis(
        tmp_path,
        profile=profile,
        profile_key=KEY,
        job_text=JOB,
        as_of=AS_OF,
        analysis=draft.analysis,
    )
    assert stored.status == "draft"
    assert stored.job_name
    assert stored.wrote_resume is False
    folder = tmp_path / KEY / stored.record_id
    assert (folder / "record.json").is_file()
    assert not (folder / "resume.docx").exists()

    saved = _save(
        tmp_path,
        profile,
        draft,
        draft.sentences,
        [],
        record_id=stored.record_id,
        publication_note=draft.publication_note,
        job_label=stored.job_name,
    )
    assert saved.written
    assert (folder / "resume.docx").is_file()
    kept = set_record_status(tmp_path, KEY, stored.record_id, "kept")
    assert kept.status == "kept"
    sent = set_record_status(tmp_path, KEY, stored.record_id, "submitted", sent_on=AS_OF)
    assert sent.status == "submitted"
    assert sent.sent_on == AS_OF
    discarded = set_record_status(tmp_path, KEY, stored.record_id, "discarded")
    assert discarded.status == "discarded"
    assert load_generation(tmp_path, KEY, stored.record_id).status == "discarded"
    assert (folder / "resume.docx").is_file()


def test_assigning_a_profile_id_keeps_generated_documents(tmp_path: Path):
    profile = _profile()
    path = tmp_path / "person.json"
    path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    before = load_profile(path)
    assert before.profile_id is None
    draft = draft_documents(before, JOB, AS_OF, resume=True, cover=False)
    root = tmp_path / "generated"
    saved = _save(
        root,
        before,
        draft,
        draft.sentences,
        [],
        profile_key=profile_key(path, before),
    )
    assert saved.written
    updated = set_preferences(before, _preferences(), update=False)
    stored = commit_profile(path, before, updated, root=root)
    assert stored.profile_id is not None
    listed = list_generations(root, stored.profile_id)
    assert len(listed) == 1
    assert listed[0].profile_key == stored.profile_id
    assert listed[0].tally_percent is not None
    assert not (root / profile_key(path, before)).exists()
