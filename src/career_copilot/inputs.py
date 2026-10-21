"""Checks for values a person types or points at before they are stored."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from career_copilot.errors import CareerCopilotError
from career_copilot.models import EVIDENCE_ID

MAX_JOB_TEXT = 100_000
MAX_JOB_FILE_BYTES = 1_000_000
MAX_STATEMENT_TEXT = 8_000
MAX_SENTENCE_TEXT = 8_000
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_RECORD_BYTES = 2_000_000
AS_OF_MIN = date(1900, 1, 1)
AS_OF_MAX = date(2100, 12, 31)


def require_job_text(value: object) -> str:
    """Accept pasted job text. Blank, null, and oversized values are refused."""

    text = _text(value, "The job description")
    if not text.strip():
        raise CareerCopilotError("The job description is empty.")
    if len(text) > MAX_JOB_TEXT:
        raise CareerCopilotError("The job description is too long.")
    return text


def require_statement(value: object) -> str:
    """Accept a typed atom statement. Extraction does not use this limit."""

    if not isinstance(value, str):
        raise CareerCopilotError("Statement must be text.")
    if not value.strip():
        raise CareerCopilotError("Statement cannot be empty.")
    _reject_null(value, "Statement")
    if len(value) > MAX_STATEMENT_TEXT:
        raise CareerCopilotError("Statement is too long.")
    return value.strip()


def require_sentence_text(value: object) -> str:
    """Accept a manual edit of one generated sentence."""

    if not isinstance(value, str):
        raise CareerCopilotError("Sentence text must be text.")
    if not value.strip():
        raise CareerCopilotError("Sentence text is empty.")
    _reject_null(value, "Sentence text")
    if len(value) > MAX_SENTENCE_TEXT:
        raise CareerCopilotError("Sentence text is too long.")
    return value


def require_as_of(value: object, *, invalid_message: str = "As of must be YYYY-MM-DD.") -> date:
    """Accept a calendar date inside the supported window."""

    if isinstance(value, datetime):
        chosen = value.date()
    elif isinstance(value, date):
        chosen = value
    elif isinstance(value, str):
        try:
            chosen = date.fromisoformat(value)
        except ValueError as exc:
            raise CareerCopilotError(invalid_message) from exc
    else:
        raise CareerCopilotError(invalid_message)
    if chosen < AS_OF_MIN or chosen > AS_OF_MAX:
        raise CareerCopilotError("As of must be from 1900-01-01 through 2100-12-31.")
    return chosen


def require_bool(value: object) -> bool:
    if value is True or value is False:
        return value
    raise CareerCopilotError("Choose a resume, a cover letter, or both.")


def require_evidence_id(value: object) -> str:
    if not isinstance(value, str) or EVIDENCE_ID.match(value) is None:
        raise CareerCopilotError("That evidence id is not valid.")
    return value


def read_job_file(path: Path) -> str:
    """Read a local UTF-8 job file. The size is checked before the read."""

    if not path.is_file():
        raise CareerCopilotError(f"No job file at {path}.")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CareerCopilotError(f"No job file at {path}.") from exc
    if size > MAX_JOB_FILE_BYTES:
        raise CareerCopilotError("The job file is too large.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CareerCopilotError("The job file must be UTF-8 text.") from exc
    except OSError as exc:
        raise CareerCopilotError(f"No job file at {path}.") from exc
    return require_job_text(text)


def read_job_stream(stream: object) -> str:
    """Read at most one character past the limit, then refuse an oversized paste."""

    try:
        chunk = stream.read(MAX_JOB_TEXT + 1)  # type: ignore[attr-defined]
    except UnicodeDecodeError as exc:
        raise CareerCopilotError("The job description must be UTF-8 text.") from exc
    except (AttributeError, OSError) as exc:
        raise CareerCopilotError("The job description must be text.") from exc
    if not isinstance(chunk, str):
        raise CareerCopilotError("The job description must be text.")
    if len(chunk) > MAX_JOB_TEXT:
        raise CareerCopilotError("The job description is too long.")
    return require_job_text(chunk)


def allowed_profile(value: object, allowed: list[Path]) -> Path:
    """Return a profile path only when it is one of the paths this page discovered."""

    if not isinstance(value, str) or not value.strip():
        raise CareerCopilotError("Choose a profile from the list.")
    try:
        chosen = Path(value).resolve()
    except OSError as exc:
        raise CareerCopilotError("That profile could not be opened.") from exc
    matches = []
    for path in allowed:
        try:
            if path.resolve() == chosen:
                matches.append(path)
        except OSError:
            continue
    if not matches:
        raise CareerCopilotError("Choose a profile from the list.")
    selected = matches[0]
    if selected.exists() and not selected.is_file():
        raise CareerCopilotError("That profile could not be opened.")
    return selected


def path_inside(path: Path, root: Path) -> Path | None:
    """Resolved file path when it stays inside root. Symlinks that leave root are refused."""

    try:
        root_resolved = root.resolve()
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def directory_inside(root: Path, *parts: str) -> Path:
    """A directory under root. Each part must be a single safe name."""

    if not parts:
        raise CareerCopilotError("The profile key is not valid.")
    for part in parts:
        if not isinstance(part, str) or part != Path(part).name or part in {"", ".", ".."}:
            raise CareerCopilotError("The profile key is not valid.")
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        raise CareerCopilotError("That folder could not be opened.") from exc
    resolved = root_resolved.joinpath(*parts).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise CareerCopilotError("That file is outside the local folder.") from exc
    return resolved


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise CareerCopilotError(f"{label} must be text.")
    _reject_null(value, label)
    return value


def _reject_null(value: str, label: str) -> None:
    if "\x00" in value:
        raise CareerCopilotError(f"{label} contains a null character.")
