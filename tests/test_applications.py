"""The application list records what the person said. Nothing is submitted."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from career_copilot.applications import applications_path, load_applications, remember_cv, set_applied
from career_copilot.errors import CareerCopilotError


def test_rewriting_a_cv_keeps_the_applied_answer_and_leaves_the_cv_out(tmp_path: Path):
    remember_cv(
        "https://example.com/jobs/one",
        "Researcher",
        "Lab",
        root=tmp_path,
        today=date(2026, 10, 1),
    )
    set_applied("https://example.com/jobs/one", "yes", root=tmp_path)
    remember_cv(
        "https://example.com/jobs/one",
        "Researcher",
        "Lab",
        root=tmp_path,
        today=date(2026, 10, 21),
    )
    remember_cv("paste:analyst:other", "Analyst", "Other", root=tmp_path, today=date(2026, 10, 2))
    records = load_applications(tmp_path)
    assert [(item.title, item.written_on, item.applied) for item in records] == [
        ("Researcher", "2026-10-21", "yes"),
        ("Analyst", "2026-10-02", "not_yet"),
    ]
    stored = applications_path(tmp_path).read_text(encoding="utf-8")
    assert "Map Lab" not in stored
    assert "Built a catalog of spatial datasets." not in stored
    assert "description" not in stored


def test_an_answer_can_create_the_row_and_a_broken_file_is_refused(tmp_path: Path):
    set_applied(
        "https://example.com/jobs/late",
        "yes",
        title="Scientist",
        company="Campus",
        root=tmp_path,
        today=date(2026, 10, 21),
    )
    found = load_applications(tmp_path)
    assert found[0].title == "Scientist"
    assert found[0].company == "Campus"
    assert found[0].applied == "yes"
    path = applications_path(tmp_path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(CareerCopilotError, match="could not be read"):
        load_applications(tmp_path)
