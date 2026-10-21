"""Search conditions are the choices for a later job list."""

from __future__ import annotations

from pathlib import Path

import pytest

from career_copilot.errors import CareerCopilotError
from career_copilot.roles import WorkRole, save_roles
from career_copilot.search_intent import clean_conditions, load_search, save_search


def test_a_blank_field_is_not_required():
    saved = clean_conditions(
        ["Senior", "Entry level"],
        ["Full-time"],
        ["Remote"],
        "Portugal",
        "Lisbon",
    )
    assert saved.levels == ["Entry level", "Senior"]
    assert saved.country == "Portugal"
    assert saved.city == "Lisbon"
    blank = clean_conditions([], [], [], "", "")
    assert blank.levels == []
    assert blank.employment == []
    assert blank.workplaces == []
    assert blank.country == ""
    assert blank.city == ""
    assert blank.programming == ""
    assert blank.outside_academia == ""
    assert blank.notes == ""
    noted = clean_conditions([], [], [], "", "", notes="Remote preferred.\nLight programming only.")
    assert noted.notes == "Remote preferred.\nLight programming only."
    with pytest.raises(CareerCopilotError, match="other notes"):
        clean_conditions([], [], [], "", "", notes="x" * 1001)
    country_only = clean_conditions(["Manager"], ["Part-time"], ["Hybrid"], "", "Lisbon")
    assert country_only.country == ""
    assert country_only.city == "Lisbon"
    both = clean_conditions(["Director"], ["Internship", "Volunteer", "Contract"], ["Local", "Remote"], "Portugal", "Lisbon")
    assert both.employment == ["Internship", "Volunteer", "Contract"]
    assert both.workplaces == ["Local", "Remote"]
    assert both.city == "Lisbon"


def test_an_old_search_file_keeps_the_fixed_fields_and_starts_notes_blank(tmp_path: Path):
    path = tmp_path / "search.json"
    path.write_text(
        "{\n"
        '  "levels": ["Entry level"],\n'
        '  "employment": [],\n'
        '  "workplaces": ["Remote"],\n'
        '  "country": "Netherlands",\n'
        '  "city": "",\n'
        '  "languages": "English",\n'
        '  "programming": "Light",\n'
        '  "outside_academia": "Yes"\n'
        "}\n",
        encoding="utf-8",
    )
    loaded = load_search(path)
    assert loaded is not None
    assert loaded.programming == "Light"
    assert loaded.outside_academia == "Yes"
    assert loaded.notes == ""
    assert loaded.languages == "English"


def test_search_page_save_clears_the_old_fixed_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", [WorkRole(name="researcher", reason="Research work.")])
    save_search(
        clean_conditions([], [], ["Remote"], "", "", programming="Light", outside_academia="Yes", notes="Keep this."),
        tmp_path / ".career-copilot" / "search.json",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Search").run()
    assert not app.exception, app.exception
    next(item for item in app.button if item.label == "Save search").click().run()
    assert not app.exception, app.exception
    loaded = load_search(tmp_path / ".career-copilot" / "search.json")
    assert loaded is not None
    assert loaded.programming == ""
    assert loaded.outside_academia == ""
    assert loaded.notes == "Keep this."
    assert loaded.workplaces == ["Remote"]


def test_search_file_round_trip_has_no_cv(tmp_path: Path):
    path = tmp_path / "search.json"
    save_search(
        clean_conditions(["Executive"], ["Full-time"], ["Hybrid"], "Netherlands", "Amsterdam"),
        path,
    )
    loaded = load_search(path)
    assert loaded is not None
    assert loaded.city == "Amsterdam"
    text = path.read_text(encoding="utf-8")
    assert "CV" not in text
    assert "curriculum" not in text.casefold()


def test_search_page_waits_for_saved_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Search").run()
    assert not app.exception, app.exception
    visible = "\n".join(item.value for item in app.caption)
    assert "Save the roles first." in visible
    assert not app.multiselect


def test_continue_opens_search_after_roles_are_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", [WorkRole(name="researcher", reason="Research work.")])
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    continue_button = next(item for item in app.button if item.label == "Continue")
    continue_button.click().run()
    assert not app.exception, app.exception
    assert app.sidebar.radio[0].value == "Search"
    labels = [item.label for item in app.multiselect]
    assert labels == ["Experience level", "Employment type", "Workplace"]
    text_labels = [item.label for item in app.text_input]
    assert "Country of residence" in text_labels
    assert "City of residence" in text_labels
    assert "Languages" in text_labels
    assert not app.selectbox
    assert [item.label for item in app.text_area] == ["Other notes"]
