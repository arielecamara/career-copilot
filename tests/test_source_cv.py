"""The original CV keeps every section except the summary and Technical Skills."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from docx import Document

from career_copilot.docx_export import write_source_resume
from career_copilot.job import analyze_job
from career_copilot.library import draft_documents
from career_copilot.models import BlockKind, ConfirmationState, EvidenceCategory, ExactDate, TextBlock
from career_copilot.profile import confirm_all, empty_profile, ingest_bytes
from career_copilot.models import DocumentKind
from career_copilot.source_cv import (
    ORIGINAL_CV_NAME,
    preview_sections,
    sections_from_blocks,
    sentences_from_edits,
)
from career_copilot.validator import validate_resume
from tests.factories import atom, document, profile_for
from tests.test_library import AS_OF, JOB, _profile

DOC = "doc_" + "c" * 16
SUMMARY = "Engineer who uses Python."
BULLET = "Used Postgres to cut latency by 20%."
ROLE = "Engineer | OldCo | 2010 – 2011"


def test_only_summary_and_skills_change_and_a_new_skill_is_refused():
    profile = _cv_profile()
    analysis = analyze_job(
        "Requirements:\n- Python\n- PostgreSQL\n- Kubernetes",
        profile.atoms,
        as_of=date(2026, 10, 19),
    )
    source = sections_from_blocks(
        _blocks(),
        profile.atoms,
        analysis,
        document_id=DOC,
        filename=ORIGINAL_CV_NAME,
    )
    frozen = [line.text for section in source.sections if section.role == "frozen" for line in section.lines]
    assert source.summary_text == SUMMARY
    assert BULLET in frozen
    assert "Selected papers" in frozen
    assert "PostgreSQL" not in source.skills_text
    assert "Dataiku" not in source.skills_text
    assert "Kubernetes" not in source.skills_text
    assert "Python" in source.skills_text and "SQL" in source.skills_text
    judged = [
        item.text
        for item in analysis.requirements
        if item.status.value in {"met", "partial"} and "Python" in item.text
    ]
    if judged:
        assert source.skills_text.index("Python") < source.skills_text.index("SQL")
    assert all(sentence.text != "Selected papers" for sentence in source.sentences)
    assert validate_resume(source.sentences, profile, analysis) == []
    kept, problems = sentences_from_edits(source, source.summary_text, source.skills_text)
    assert problems == []
    assert kept is not None
    assert validate_resume(kept, profile, analysis) == []
    rejected, violations = sentences_from_edits(source, source.summary_text, "Python, Kubernetes")
    assert rejected is None
    assert any(item.detail == "That skill is not in the original CV." for item in violations)


def test_source_docx_copies_frozen_lines(tmp_path: Path):
    profile = _cv_profile()
    analysis = analyze_job("Requirements:\n- Python\n- Kubernetes", profile.atoms, as_of=date(2026, 10, 19))
    source = sections_from_blocks(_blocks(), profile.atoms, analysis, document_id=DOC, filename=ORIGINAL_CV_NAME)
    path = tmp_path / "resume.docx"
    write_source_resume(path, preview_sections(source, source.summary_text, source.skills_text))
    text = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
    assert "Technical Skills" in text
    assert BULLET in text
    assert ROLE in text
    assert "Selected papers" in text
    assert "Dataiku" not in text
    assert "Kubernetes" not in text
    assert "PostgreSQL" not in text
    assert "Full list available upon request." not in text


def test_a_frozen_line_without_a_confirmed_fact_is_refused():
    profile = _cv_profile()
    analysis = analyze_job("Requirements:\n- Python", profile.atoms, as_of=date(2026, 10, 19))
    blocks = [*_blocks(), TextBlock(text="Invented a team of 12 people.", kind=BlockKind.paragraph)]
    with pytest.raises(Exception, match="not a confirmed fact"):
        sections_from_blocks(blocks, profile.atoms, analysis, document_id=DOC, filename=ORIGINAL_CV_NAME)


def test_a_profile_without_the_original_file_keeps_the_atom_resume():
    draft = draft_documents(_profile(), JOB, AS_OF, resume=True, cover=True)
    assert draft.source is None
    assert ORIGINAL_CV_NAME not in draft.diff_markdown


def test_ingest_of_a_local_copy_does_not_rename_a_bullet(tmp_path: Path):
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    path = tmp_path / ORIGINAL_CV_NAME
    sheet = canvas.Canvas(str(path))
    lines = [
        "Ada Lovelace",
        "Ada@example.com",
        "Professional Summary",
        SUMMARY,
        "Technical Skills",
        "- Python, SQL",
        "Professional Experience",
        ROLE,
        f"- {BULLET}",
        "Education",
        "BSc Example College",
    ]
    top = 760
    for line in lines:
        sheet.drawString(72, top, line)
        top -= 18
    sheet.save()
    profile, _count, _warnings = ingest_bytes(
        empty_profile(),
        name=ORIGINAL_CV_NAME,
        payload=path.read_bytes(),
        kind=DocumentKind.cv,
    )
    profile = confirm_all(profile)
    analysis = analyze_job("Requirements:\n- PostgreSQL\n- Kubernetes", profile.atoms, as_of=date(2026, 10, 19))
    source = sections_from_blocks(
        next(item.blocks for item in profile.documents),
        profile.atoms,
        analysis,
        document_id=profile.documents[0].document_id,
        filename=ORIGINAL_CV_NAME,
    )
    # Use the parsed file through assemble as well, limited to this folder.
    from career_copilot.source_cv import assemble_source

    assembled = assemble_source(profile, analysis, root=tmp_path)
    assert assembled is not None
    frozen = "\n".join(line.text for section in assembled.sections if section.role == "frozen" for line in section.lines)
    assert BULLET in frozen
    assert "PostgreSQL" not in frozen
    assert "Kubernetes" not in assembled.skills_text
    assert source.summary_text == assembled.summary_text


def _blocks() -> list[TextBlock]:
    rows = [
        ("paragraph", "Ada Lovelace"),
        ("paragraph", "Ada@example.com"),
        ("heading", "Professional Summary"),
        ("paragraph", SUMMARY),
        ("heading", "Technical Skills"),
        ("bullet", "SQL, Python, Machine Learning (Dataiku)"),
        ("heading", "Professional Experience"),
        ("paragraph", ROLE),
        ("bullet", BULLET),
        ("heading", "Education"),
        ("paragraph", "BSc Example College"),
        ("heading", "Projects and Publications"),
        ("paragraph", "Selected papers"),
        ("bullet", "A paper about Python."),
        ("heading", "Languages"),
        ("bullet", "English: Fluent"),
    ]
    return [TextBlock(text=text, kind=BlockKind(kind)) for kind, text in rows]


def _cv_profile():
    name = _mentioned("1", "Ada Lovelace")
    contact = _mentioned("2", "Ada@example.com")
    summary = _mentioned("3", SUMMARY, skills=["Python"])
    sql = _mentioned("4", "SQL", skills=["SQL"])
    python = _mentioned("5", "Python", skills=["Python"])
    role = _confirmed(
        "6",
        ROLE,
        employer="OldCo",
        title="Engineer",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2011", year=2011),
    )
    bullet = _confirmed(
        "7",
        BULLET,
        employer="OldCo",
        title="Engineer",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2011", year=2011),
        skills=["Postgres"],
    )
    education = _mentioned("8", "BSc Example College", category=EvidenceCategory.coursework)
    paper = _mentioned("9", "A paper about Python.", category=EvidenceCategory.project, skills=["Python"])
    language = _mentioned("a", "English: Fluent")
    return profile_for(
        document([("paragraph", "Ada Lovelace")], document_id=DOC, filename=ORIGINAL_CV_NAME),
        [name, contact, summary, sql, python, role, bullet, education, paper, language],
    )


def _mentioned(suffix: str, statement: str, **overrides: object):
    payload: dict[str, object] = {
        "employer": None,
        "title": None,
        "start_date": None,
        "end_date": None,
        "category": EvidenceCategory.mentioned_only,
    }
    payload.update(overrides)
    return _confirmed(suffix, statement, **payload)


def _confirmed(suffix: str, statement: str, **overrides: object):
    payload: dict[str, object] = {
        "evidence_id": "ev_" + suffix * 16,
        "statement": statement,
        "source_excerpt": statement,
        "source_document_id": DOC,
        "confirmation": ConfirmationState.confirmed,
    }
    payload.update(overrides)
    return atom(**payload)
