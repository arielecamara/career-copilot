"""Role reading returns roles, not lines copied from a CV."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from career_copilot.errors import CareerCopilotError
from career_copilot.roles import (
    child_env,
    grok_argv,
    grok_search_argv,
    load_roles,
    load_skills,
    parse_grok_stdout,
    save_roles,
    save_skills,
    understand_text,
)


def test_parse_grok_stdout_keeps_priority_and_drops_a_long_copy():
    raw = json.dumps(
        {
            "text": json.dumps(
                {
                    "roles": [
                        {"name": "researcher", "reason": "The work is research."},
                        {"name": "Researcher", "reason": "duplicate"},
                        {"name": "professor", "reason": "The work includes teaching."},
                        {"name": "x" * 80, "reason": "too long to be a role name"},
                    ]
                }
            )
        }
    )
    roles = parse_grok_stdout(raw)
    assert [role.name for role in roles] == ["researcher", "professor"]
    assert roles[0].reason == "The work is research."
    assert "CV line" not in roles[0].reason


def test_understand_text_strips_api_keys_and_reads_the_reply(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("career_copilot.roles.shutil.which", lambda name: "grok" if name == "grok" else None)
    seen: dict[str, object] = {}

    def runner(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        prompt = Path(args[args.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        seen["prompt"] = prompt
        body = json.dumps({"roles": [{"name": "architect", "reason": "The work is design."}]})
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"text": body}), stderr="")

    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    reading = understand_text("Designed buildings for ten years.", runner=runner)
    assert [role.name for role in reading.roles] == ["architect"]
    assert reading.skills == []
    env = seen["env"]
    assert isinstance(env, dict)
    assert "XAI_API_KEY" not in env
    args = seen["args"]
    assert isinstance(args, list)
    assert "--max-turns" in args
    assert "run_terminal_cmd" in args[args.index("--disallowed-tools") + 1]
    prompt = str(seen["prompt"])
    assert "Do not quote the CV" in prompt
    assert "Designed buildings" in prompt


def test_child_env_and_argv_do_not_carry_a_key():
    env = child_env({"XAI_API_KEY": "xai-secret", "PATH": "C:\\bin"})
    assert env == {"PATH": "C:\\bin"}
    argv = grok_argv("grok", Path("prompt.txt"), Path("."))
    assert "xai-secret" not in " ".join(argv)
    assert "--verbatim" in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "web_search" in argv[argv.index("--disallowed-tools") + 1]
    search = grok_search_argv("grok", Path("prompt.txt"), Path("."))
    assert search[search.index("--max-turns") + 1] == "8"
    assert "web_search" not in search[search.index("--disallowed-tools") + 1]
    assert "web_fetch" not in search[search.index("--disallowed-tools") + 1]


def test_save_and_load_roles_round_trip(tmp_path: Path):
    from career_copilot.roles import WorkRole

    path = tmp_path / "roles.json"
    save_roles(path, [WorkRole(name="secretary", reason="The work is office coordination.", kept=True)])
    loaded = load_roles(path)
    assert loaded[0].name == "secretary"
    assert "experience" not in path.read_text(encoding="utf-8") or "secretary" in path.read_text(encoding="utf-8")


def test_a_failed_grok_call_does_not_return_the_cv(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("career_copilot.roles.shutil.which", lambda name: "grok")

    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="secret CV text")

    with pytest.raises(CareerCopilotError) as caught:
        understand_text("secret CV text", runner=runner)
    assert "secret CV text" not in str(caught.value)


def test_a_skill_is_kept_only_when_the_cv_states_it(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("career_copilot.roles.shutil.which", lambda name: "grok")

    def runner(args, **kwargs):
        body = json.dumps(
            {
                "roles": [{"name": "researcher", "reason": "The work is research."}],
                "skills": ["Neo4j", "Kubernetes"],
            }
        )
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"text": body}), stderr="")

    reading = understand_text("Research with Neo4j graphs.", runner=runner)
    assert reading.skills == ["Neo4j"]
    assert "Kubernetes" not in reading.skills


def test_typed_skills_round_trip_without_the_cv(tmp_path: Path):
    path = tmp_path / "skills.json"
    save_skills(["Neo4j", "neo4j", "Cypher"], path)
    assert load_skills(path) == ["Neo4j", "Cypher"]
    assert "curriculum" not in path.read_text(encoding="utf-8")
