"""A local list of CVs written for a vacancy. Nothing is submitted."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from career_copilot.errors import CareerCopilotError


class WrittenCv(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    company: str
    written_on: str
    applied: str = "not_yet"

    def check(self) -> "WrittenCv":
        if self.applied not in {"yes", "not_yet"}:
            raise ValueError("applied")
        if not self.key.strip() or not self.written_on.strip():
            raise ValueError("record")
        date.fromisoformat(self.written_on)
        return self


def applications_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "applications.json"


def load_applications(root: Path | None = None) -> list[WrittenCv]:
    path = applications_path(root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CareerCopilotError("The application list could not be read.") from exc
    items = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise CareerCopilotError("The application list could not be read.")
    found: list[WrittenCv] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            found.append(WrittenCv.model_validate(item).check())
        except (ValidationError, ValueError):
            continue
    return found


def remember_cv(
    key: str,
    title: str,
    company: str,
    *,
    root: Path | None = None,
    today: date | None = None,
) -> None:
    token = key.strip()
    if not token:
        raise CareerCopilotError("The application list could not be saved.")
    records = load_applications(root)
    written = (today or date.today()).isoformat()
    name = " ".join(title.split())[:120] or "Job"
    employer = " ".join(company.split())[:120] or "Unknown"
    kept = [item for item in records if item.key != token]
    previous = next((item for item in records if item.key == token), None)
    applied = previous.applied if previous is not None else "not_yet"
    kept.append(WrittenCv(key=token, title=name, company=employer, written_on=written, applied=applied))
    _save(kept, root)


def set_applied(
    key: str,
    applied: str,
    *,
    title: str = "",
    company: str = "",
    root: Path | None = None,
    today: date | None = None,
) -> None:
    if applied not in {"yes", "not_yet"}:
        raise CareerCopilotError("The application list could not be saved.")
    token = key.strip()
    records = load_applications(root)
    current = next((item for item in records if item.key == token), None)
    if current is None:
        remember_cv(token, title, company, root=root, today=today)
        records = load_applications(root)
        current = next((item for item in records if item.key == token), None)
    if current is None:
        raise CareerCopilotError("The application list could not be saved.")
    updated = [
        item.model_copy(update={"applied": applied}) if item.key == token else item for item in records
    ]
    _save(updated, root)


def _save(records: list[WrittenCv], root: Path | None) -> None:
    path = applications_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda item: (item.written_on, item.title.casefold()), reverse=True)
    payload = {"records": [item.model_dump() for item in ordered]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
