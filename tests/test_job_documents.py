"""Job documents stay local, and locked CV sections stay word for word."""

from __future__ import annotations

from pathlib import Path

import pytest

from career_copilot.errors import CareerCopilotError
from career_copilot.job_documents import (
    apply_cv_note,
    document_folder,
    generate_cover_letter,
    generate_job_cv,
    note_is_locked,
)
from career_copilot.roles import cv_path, save_cv

CV = """Ada Example
ada@example.com

Professional summary
Ada studies maps and writes Python tools for spatial data.

Technical Skills
Python, SQL

Experience
Researcher, Map Lab, 2019-2023
Built a catalog of spatial datasets.

Education
PhD, Example University, 2018

Languages
English
"""


def _propose(cv: str, posting: str, note: str) -> tuple[str, str]:
    assert "Map Lab" in cv
    if "emphasize Python" in note:
        return ("Ada writes Python tools for spatial data.", "Python\nSQL")
    return (
        "Ada studies maps and writes Python tools. Ada used Kubernetes in 2024.",
        "Python, Kubernetes",
    )


def test_a_locked_note_is_refused_and_a_summary_note_is_not():
    assert note_is_locked("change the dates")
    assert note_is_locked("add a publication")
    assert note_is_locked("update the experience section")
    assert not note_is_locked("emphasize Python in the summary")


def test_generate_keeps_locked_sections_and_drops_new_facts(tmp_path: Path):
    source = cv_path(tmp_path)
    save_cv(CV, source)
    before = source.read_text(encoding="utf-8")
    pdf = generate_job_cv(
        CV,
        "Remote researcher role using Python.",
        "https://example.com/jobs/one",
        propose=_propose,
        root=tmp_path,
    )
    preview = pdf.with_name("cv-preview.txt").read_text(encoding="utf-8")
    assert "Researcher, Map Lab, 2019-2023" in preview
    assert "PhD, Example University, 2018" in preview
    assert "English" in preview
    assert "ada@example.com" in preview
    assert "Kubernetes" not in preview
    assert "2024" not in preview
    assert "Python" in preview
    assert source.read_text(encoding="utf-8") == before
    _assert_amsterdam(pdf)
    assert "Kubernetes" not in _pdf_text(pdf)
    assert "Map Lab" in _pdf_text(pdf)


def test_a_locked_note_leaves_the_file_and_a_summary_note_replaces_it(tmp_path: Path):
    generate_job_cv(CV, "Remote researcher role.", "job-1", propose=_propose, root=tmp_path)
    folder = document_folder("job-1", tmp_path)
    original = (folder / "cv-preview.txt").read_text(encoding="utf-8")
    with pytest.raises(CareerCopilotError, match="locked section"):
        apply_cv_note(CV, "Remote researcher role.", "job-1", "change the dates", propose=_propose, root=tmp_path)
    assert (folder / "cv-preview.txt").read_text(encoding="utf-8") == original
    apply_cv_note(
        CV,
        "Remote researcher role.",
        "job-1",
        "emphasize Python in the summary",
        propose=_propose,
        root=tmp_path,
    )
    updated = (folder / "cv-preview.txt").read_text(encoding="utf-8")
    assert "Ada writes Python tools for spatial data." in updated
    assert "Researcher, Map Lab, 2019-2023" in updated
    assert "Kubernetes" not in updated


def test_each_job_keeps_its_own_files_and_a_bad_letter_is_not_written(tmp_path: Path):
    generate_job_cv(CV, "Role one.", "job-a", propose=_propose, root=tmp_path)
    generate_job_cv(CV, "Role two.", "job-b", propose=_propose, root=tmp_path)
    assert document_folder("job-a", tmp_path) != document_folder("job-b", tmp_path)
    assert (document_folder("job-a", tmp_path) / "cv.pdf").is_file()
    assert (document_folder("job-b", tmp_path) / "cv.pdf").is_file()

    def bad_letter(_cv: str, _posting: str) -> str:
        return "I used Kubernetes in 2024 at Fake Corp."

    with pytest.raises(CareerCopilotError, match="could not be written"):
        generate_cover_letter(CV, "Send a cover letter.", "job-a", propose=bad_letter, root=tmp_path)
    assert not (document_folder("job-a", tmp_path) / "cover.pdf").is_file()

    def good_letter(_cv: str, _posting: str) -> str:
        return "I write Python tools for spatial data."

    letter = generate_cover_letter(CV, "Send a cover letter.", "job-a", propose=good_letter, root=tmp_path)
    assert "Python" in letter.with_name("cover-preview.txt").read_text(encoding="utf-8")
    _assert_amsterdam(letter)


def test_the_jobs_page_writes_a_cv_and_names_a_file_the_person_brings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from career_copilot.job_list import ListedJob, SearchLog, save_log
    from career_copilot.roles import save_roles
    from career_copilot.roles import WorkRole
    from career_copilot.search_intent import clean_conditions, save_search

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_cv(CV)
    save_roles(tmp_path / ".career-copilot" / "roles.json", [WorkRole(name="researcher", reason="The work is research.")])
    save_search(clean_conditions([], [], ["Remote"], "", ""), tmp_path / ".career-copilot" / "search.json")
    save_log(
        SearchLog(
            latest=[
                ListedJob(
                    title="Researcher",
                    company="Lab",
                    url="https://example.com/jobs/open",
                    source="Web",
                    match_percent=80,
                    description="Remote researcher role. Send a portfolio and a cover letter.",
                    covered=["Python"],
                )
            ]
        ),
        tmp_path / ".career-copilot" / "jobs.json",
    )
    monkeypatch.setattr("career_copilot.job_documents.propose_cv_sections", _propose)
    monkeypatch.setattr(
        "career_copilot.job_documents.propose_cover_letter",
        lambda _cv, _posting: "I write Python tools for spatial data.",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    labels = [item.label for item in app.button]
    assert "Generate CV for this job" in labels
    assert "Generate cover letter for this job" in labels
    visible = "\n".join(str(item.value) for item in list(app.markdown) + list(app.caption))
    assert "Portfolio" in visible
    assert "Bring your own file." in visible
    next(item for item in app.button if item.label == "Generate CV for this job").click().run()
    assert not app.exception, app.exception
    preview = "\n".join(str(item.value) for item in app.text)
    assert "Map Lab" in preview
    assert "Kubernetes" not in preview
    assert (tmp_path / ".career-copilot" / "cv.txt").read_text(encoding="utf-8") == CV.strip()
    note = next(item for item in app.text_area if item.label == "Notes")
    note.set_value("change the dates").run()
    next(item for item in app.button if item.label == "Apply").click().run()
    assert not app.exception, app.exception
    assert any("locked section" in str(item.value) for item in app.error)
    kept = "\n".join(str(item.value) for item in app.text)
    assert "Map Lab" in kept
    assert "1999" not in kept


def _pdf_text(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _assert_amsterdam(path: Path) -> None:
    import pdfplumber

    with pdfplumber.open(path) as document:
        assert document.pages
        page = document.pages[0]
        assert abs(page.width - 595.27) < 2
        assert abs(page.height - 841.89) < 2
        assert page.images == []
