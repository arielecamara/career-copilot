"""The local page stays on loopback and does not write a profile just by opening."""

from __future__ import annotations

from pathlib import Path

import pytest

from career_copilot.cli import _parser, main
from career_copilot.errors import CareerCopilotError
from career_copilot.library import ui_launch_command
from career_copilot.profile import save_profile
from career_copilot.ui_app import (
    _box_text,
    _is_loopback,
    _profile_label,
    _public_error,
    _stored_or_edit,
)
from tests.test_library import _profile


def test_ui_launch_stays_on_loopback_and_generate_is_absent():
    command = ui_launch_command(8501)
    assert command[command.index("--server.address") + 1] == "127.0.0.1"
    assert command[command.index("--browser.gatherUsageStats") + 1] == "false"
    assert command[command.index("--server.headless") + 1] == "true"
    assert command[command.index("--client.showErrorDetails") + 1] == "none"
    assert command[command.index("--client.showErrorLinks") + 1] == "false"
    hosts = [command[index + 1] for index, item in enumerate(command) if item == "--server.allowedHosts"]
    assert hosts == ["localhost", "127.0.0.1"]
    assert "0.0.0.0" not in command
    assert Path(command[4]).name == "ui_app.py"
    assert "ui" in _parser().format_help()
    with pytest.raises(SystemExit) as caught:
        main(["generate"])
    assert caught.value.code == 2


def test_app_opens_a_temp_profile_without_writing_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    path = tmp_path / "profile.json"
    path.write_text(_profile().model_dump_json(indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setenv("CAREER_COPILOT_PROFILE", str(path))
    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    captions = [item.value for item in app.caption]
    captions.extend(item.value for item in app.sidebar.caption)
    assert all("Local only" not in value for value in captions)
    visible = _visible(app)
    assert "User" in visible
    assert "Upload a CV" in visible
    assert "Ada Lovelace" not in visible
    assert "Career Copilot" not in visible
    assert "N/A" not in visible
    assert "Onsite location" not in visible
    assert "Extracted facts" not in visible
    assert "Possible roles" not in visible
    assert [item.label for item in app.button] == ["Read the CV"]
    assert [item.value for item in app.sidebar.radio] == ["User"]
    assert not app.sidebar.selectbox
    assert path.read_bytes() == before


def test_the_open_page_is_the_user_role_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    labels = [item.label for item in app.button]
    assert "Read the CV" in labels
    assert "Continue" not in labels
    assert "Analyze" not in labels
    pages = app.sidebar.radio[0].options
    assert pages == ["User", "Search", "Jobs", "Documents"]
    app.sidebar.radio[0].set_value("Documents").run()
    assert not app.exception, app.exception
    assert any("No CV has been written yet." in str(item.value) for item in app.caption)


def test_the_page_hides_paths_and_unexpected_errors(tmp_path: Path):
    folder = tmp_path / "secret-folder"
    folder.mkdir()
    path = folder / "profile.json"
    save_profile(path, _profile())
    label = _profile_label(path)
    assert label == "Ada Lovelace"
    assert "pf_" not in label
    assert str(path) not in label
    assert str(tmp_path) not in label
    assert folder.name not in label
    secret = "secret@example.com"
    assert secret not in _public_error(RuntimeError(f"profile text {secret}"))
    assert _public_error(CareerCopilotError("The job description is empty.")) == "The job description is empty."
    assert _public_error(CareerCopilotError("x" * 501)) == "Something went wrong. Nothing was uploaded."
    assert _is_loopback(None) is True
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
    assert _box_text("a" * 8001, 8000, "short") == "short"
    assert _box_text("Python\x00", 8000, "Python") == "Python"
    assert _stored_or_edit("árvore", "aˆrvore") == "aˆrvore"
    assert _stored_or_edit("árvore extra", "aˆrvore") == "árvore extra"


def _visible(app) -> str:
    chunks: list[str] = []
    for group_name in ("markdown", "caption", "subheader", "text", "header"):
        for item in getattr(app, group_name):
            chunks.append(str(item.value))
    for group_name in ("table", "dataframe"):
        for item in getattr(app, group_name):
            chunks.append(str(item.value))
    for item in app.text_area:
        chunks.append(str(item.label))
    for item in app.selectbox:
        chunks.append(str(item.label))
    return "\n".join(chunks)
