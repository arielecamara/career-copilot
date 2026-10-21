"""Search conditions chosen after the roles. The file does not store a CV."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_copilot.errors import CareerCopilotError

LEVELS = ("Entry level", "Senior", "Manager", "Director", "Executive")
EMPLOYMENT = ("Part-time", "Full-time", "Internship", "Volunteer", "Contract")
WORKPLACES = ("Local", "Remote", "Hybrid")
PROGRAMMING = ("", "Light", "Any")
OUTSIDE = ("", "Yes", "No")
_PLACE_LIMIT = 80
_NOTES_LIMIT = 1000


class SearchConditions(BaseModel):
    """What kind of job to look for. A later search reads this file."""

    model_config = ConfigDict(extra="forbid")

    levels: list[str] = Field(default_factory=list)
    employment: list[str] = Field(default_factory=list)
    workplaces: list[str] = Field(default_factory=list)
    country: str = ""
    city: str = ""
    languages: str = ""
    programming: str = ""
    outside_academia: str = ""
    notes: str = ""

    @field_validator("levels")
    @classmethod
    def check_levels(cls, value: list[str]) -> list[str]:
        return _choices(value, LEVELS, "experience level")

    @field_validator("employment")
    @classmethod
    def check_employment(cls, value: list[str]) -> list[str]:
        return _choices(value, EMPLOYMENT, "employment type")

    @field_validator("workplaces")
    @classmethod
    def check_workplaces(cls, value: list[str]) -> list[str]:
        return _choices(value, WORKPLACES, "workplace")

    @field_validator("country", "city")
    @classmethod
    def check_place(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("place text is invalid")
        cleaned = " ".join(value.split())
        if len(cleaned) > _PLACE_LIMIT:
            raise ValueError("place text is too long")
        return cleaned

    @field_validator("languages")
    @classmethod
    def check_languages(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > 120:
            raise ValueError("languages text is too long")
        return cleaned

    @field_validator("programming")
    @classmethod
    def check_programming(cls, value: str) -> str:
        if value not in PROGRAMMING:
            raise ValueError("unknown programming preference")
        return value

    @field_validator("outside_academia")
    @classmethod
    def check_outside(cls, value: str) -> str:
        if value not in OUTSIDE:
            raise ValueError("unknown academia preference")
        return value

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("notes text is invalid")
        lines = [" ".join(line.split()) for line in value.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        if len(cleaned) > _NOTES_LIMIT:
            raise ValueError("notes text is too long")
        return cleaned


def search_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "search.json"


def needs_residence(workplaces: list[str]) -> bool:
    return "Local" in workplaces or "Hybrid" in workplaces


def clean_conditions(
    levels: list[str],
    employment: list[str],
    workplaces: list[str],
    country: str,
    city: str,
    languages: str = "",
    programming: str = "",
    outside_academia: str = "",
    notes: str = "",
) -> SearchConditions:
    try:
        draft = SearchConditions(
            levels=levels,
            employment=employment,
            workplaces=workplaces,
            country=country,
            city=city,
            languages=languages,
            programming=programming,
            outside_academia=outside_academia,
            notes=notes,
        )
    except ValidationError as exc:
        raise CareerCopilotError(_validation_message(exc)) from exc
    return draft


def load_search(path: Path | None = None) -> SearchConditions | None:
    target = search_path() if path is None else path
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return SearchConditions.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CareerCopilotError("The saved search conditions could not be read.") from exc


def save_search(conditions: SearchConditions, path: Path | None = None) -> None:
    target = search_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = conditions.model_dump(mode="json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _choices(value: list[str], allowed: tuple[str, ...], label: str) -> list[str]:
    chosen: list[str] = []
    for item in value:
        if item not in allowed:
            raise ValueError(f"unknown {label}")
        if item not in chosen:
            chosen.append(item)
    return [item for item in allowed if item in chosen]


def _validation_message(exc: ValidationError) -> str:
    text = str(exc).casefold()
    if "notes" in text and "too long" in text:
        return "The other notes are too long."
    if "notes" in text and "invalid" in text:
        return "The other notes are not valid text."
    if "languages" in text and "too long" in text:
        return "The languages text is too long."
    if "too long" in text:
        return "The country or the city is too long."
    if "invalid" in text:
        return "The country or the city is not valid text."
    return "Choose the search conditions from the lists."
