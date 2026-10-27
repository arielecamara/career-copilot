"""Read a CV and ask the local Grok login which roles that experience can support.

The page shows the roles. It does not show text copied from the CV.
The call uses `grok -p` with the signed-in account. An API key in the
environment is removed before the process starts, so this path does not
spend console credits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_copilot.errors import CareerCopilotError, ParseError
from career_copilot.models import DocumentKind
from career_copilot.parsers import parse_document

MAX_CV_CHARS = 60_000
MAX_ROLES = 8
MAX_ROLE_NAME = 60
MAX_REASON = 280
GROK_TIMEOUT_SECONDS = 180
_SECRET_ENV = ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY", "XAI_MANAGEMENT_KEY", "XAI_MGMT_KEY")


class WorkRole(BaseModel):
    """One kind of work the experience can support."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str = ""
    kept: bool = True
    added: bool = False

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("role name is empty")
        if len(cleaned) > MAX_ROLE_NAME:
            raise ValueError("role name is too long")
        return cleaned

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > MAX_REASON:
            raise ValueError("role reason is too long")
        return cleaned


class RoleList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[WorkRole] = Field(default_factory=list)


class CvReading(BaseModel):
    """Roles and skill names read from one CV."""

    model_config = ConfigDict(extra="forbid")

    roles: list[WorkRole]
    skills: list[str] = Field(default_factory=list)


class SkillList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)


def roles_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "roles.json"


def load_roles(path: Path) -> list[WorkRole]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RoleList.model_validate(payload).roles
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CareerCopilotError("The saved roles could not be read.") from exc


def save_roles(path: Path, roles: list[WorkRole]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = RoleList(roles=roles).model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def cv_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "cv.txt"


def skills_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "skills.json"


def save_skills(skills: list[str], path: Path | None = None) -> None:
    target = skills_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = SkillList(skills=_clean_skills(skills)).model_dump(mode="json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_skills(path: Path | None = None) -> list[str]:
    target = skills_path() if path is None else path
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return _clean_skills(SkillList.model_validate(payload).skills)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CareerCopilotError("The saved skills could not be read.") from exc


def _clean_skills(skills: list[str]) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for item in skills:
        name = " ".join(str(item).split())[:40]
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        chosen.append(name)
        if len(chosen) == 12:
            break
    return chosen


def save_cv(text: str, path: Path | None = None) -> None:
    cleaned = text.strip()
    if not cleaned:
        raise CareerCopilotError("The CV had no text.")
    target = cv_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(cleaned[:MAX_CV_CHARS], encoding="utf-8")


def load_cv(path: Path | None = None) -> str:
    target = cv_path() if path is None else path
    if not target.is_file():
        return ""
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CareerCopilotError("The saved CV could not be read.") from exc


def text_from_upload(filename: str, data: bytes) -> str:
    name = Path(filename).name
    suffix = Path(name).suffix.casefold()
    if suffix not in {".pdf", ".docx"}:
        raise ParseError("Supported files are .docx and single-column text .pdf.")
    if not data:
        raise ParseError("The file is empty.")
    if len(data) > 20 * 1024 * 1024:
        raise ParseError("The file is larger than 20 MiB.")
    temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temporary_path = Path(temporary.name)
    try:
        temporary.write(data)
        temporary.close()
        record = parse_document(temporary_path, DocumentKind.cv)
    finally:
        temporary_path.unlink(missing_ok=True)
    text = record.text.strip()
    if len(text) > MAX_CV_CHARS:
        raise CareerCopilotError("This CV is too long to read in one pass.")
    return text


def understand_text(text: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> CvReading:
    cleaned = text.strip()
    if not cleaned:
        raise CareerCopilotError("This CV has no text to read.")
    executable = shutil.which("grok")
    if executable is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    call = runner or subprocess.run
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(_prompt(cleaned), encoding="utf-8")
        try:
            completed = call(
                grok_argv(executable, prompt_file, directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GROK_TIMEOUT_SECONDS,
                env=child_env(os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CareerCopilotError("Grok took too long. Nothing was saved.") from exc
    if completed.returncode != 0:
        raise CareerCopilotError("Grok did not return roles. Stay signed in with grok login and try again.")
    roles = parse_grok_stdout(completed.stdout or "")
    if not roles:
        raise CareerCopilotError("Grok did not name any roles. Try the CV again.")
    return CvReading(roles=roles, skills=_ground_skills(parse_skills(completed.stdout or ""), cleaned))


def parse_skills(stdout: str) -> list[str]:
    body = _json_object(stdout)
    if isinstance(body.get("text"), str) and "skills" not in body and "roles" in body:
        body = _json_object(str(body["text"]))
    elif isinstance(body.get("text"), str) and "skills" not in body:
        try:
            body = _json_object(str(body["text"]))
        except CareerCopilotError:
            return []
    raw = body.get("skills")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _ground_skills(skills: list[str], cv_text: str) -> list[str]:
    blob = cv_text.casefold()
    return [skill for skill in _clean_skills(skills) if skill.casefold() in blob]


def grok_argv(executable: str, prompt_file: Path, cwd: Path) -> list[str]:
    return [
        executable,
        "--prompt-file",
        str(prompt_file),
        "--verbatim",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--cwd",
        str(cwd),
        "--no-auto-update",
        "--disallowed-tools",
        "run_terminal_cmd,write,search_replace,web_search,web_fetch,Agent",
    ]


FAST_MODEL = "grok-4.7-build-fast"


def grok_search_argv(executable: str, prompt_file: Path, cwd: Path) -> list[str]:
    return [
        executable,
        "--prompt-file",
        str(prompt_file),
        "--verbatim",
        "--output-format",
        "json",
        "--model",
        FAST_MODEL,
        "--max-turns",
        "2",
        "--always-approve",
        "--no-subagents",
        "--cwd",
        str(cwd),
        "--no-auto-update",
        "--disallowed-tools",
        "run_terminal_cmd,write,search_replace,Agent",
    ]


def grok_score_argv(executable: str, prompt_file: Path, cwd: Path) -> list[str]:
    argv = grok_argv(executable, prompt_file, cwd)
    index = argv.index("--max-turns")
    return [*argv[:index], "--model", FAST_MODEL, *argv[index:]]


def child_env(source: Mapping[str, str]) -> dict[str, str]:
    env = dict(source)
    for name in _SECRET_ENV:
        env.pop(name, None)
    return env


def parse_grok_stdout(stdout: str) -> list[WorkRole]:
    body = _json_object(stdout)
    if isinstance(body.get("text"), str) and "roles" not in body:
        body = _json_object(body["text"])
    raw_roles = body.get("roles")
    if not isinstance(raw_roles, list):
        raise CareerCopilotError("Grok did not return roles.")
    chosen: list[WorkRole] = []
    seen: set[str] = set()
    for item in raw_roles:
        if not isinstance(item, dict):
            continue
        try:
            role = WorkRole.model_validate(
                {"name": item.get("name", ""), "reason": item.get("reason", ""), "kept": True, "added": False}
            )
        except ValidationError:
            continue
        key = role.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(role)
        if len(chosen) == MAX_ROLES:
            break
    return chosen


def _prompt(text: str) -> str:
    return (
        "Read the CV below and decide which kinds of jobs this person can do.\n"
        "Return only JSON with this shape:\n"
        '{"roles":[{"name":"researcher","reason":"one sentence"}],"skills":["Neo4j"]}\n'
        "Rules:\n"
        "- name is a short role, such as professor, secretary, architect, or researcher.\n"
        "- Return one or more roles, at most 8, highest priority first.\n"
        "- reason is one plain sentence about why the experience supports that role.\n"
        "- skills are short names that appear in the CV, at most 12. Do not invent a skill.\n"
        "- Do not quote the CV. Do not copy its lines, dates, or employers.\n"
        "- Do not write a biography.\n"
        "CV:\n"
        f"{text}\n"
    )


def _json_object(text: str) -> dict[str, object]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise CareerCopilotError("Grok did not return roles.")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CareerCopilotError("Grok did not return roles.") from exc
    if not isinstance(payload, dict):
        raise CareerCopilotError("Grok did not return roles.")
    return payload
