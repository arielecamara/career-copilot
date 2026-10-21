"""Decide which profile a new document belongs to. Profiles stay separate."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from career_copilot.errors import ProfileError
from career_copilot.models import PROFILE_ID, EvidenceCategory, ExactDate, MasterProfile, ProfileAtom
from career_copilot.profile import load_profile
from career_copilot.vocab import SKILL_LEXICON

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){7,}(?!\w)")
_OPEN_END = date(2100, 12, 31)


@dataclass(frozen=True)
class ProfileCandidate:
    profile_id: str
    label: str
    reason: str


@dataclass(frozen=True)
class MatchDecision:
    action: Literal["attach", "create", "ask"]
    profile_id: str | None
    reason: str
    candidates: tuple[ProfileCandidate, ...]


@dataclass(frozen=True)
class _Signals:
    emails: frozenset[str]
    phones: frozenset[str]
    roles: tuple[tuple[str, date, date], ...]
    names: frozenset[str]
    label: str


def default_profiles_root() -> Path:
    return Path.cwd() / ".career-copilot" / "profiles"


def list_stored_profiles(root: Path) -> list[tuple[Path, MasterProfile]]:
    """Load each `profile.json` whose folder name is that profile's id."""

    if not root.is_dir():
        return []
    stored: list[tuple[Path, MasterProfile]] = []
    for path in sorted(root.glob("*/profile.json")):
        profile = load_profile(path)
        if profile.profile_id is None or path.parent.name != profile.profile_id:
            continue
        if not PROFILE_ID.match(profile.profile_id):
            continue
        stored.append((path, profile))
    return stored


def decide_match(incoming: list[ProfileAtom], profiles: list[MasterProfile]) -> MatchDecision:
    """Attach on one strong signal. Ask when the match is uncertain. Otherwise create."""

    fresh = _signals(incoming)
    strong: list[tuple[MasterProfile, str]] = []
    weak: list[tuple[MasterProfile, str]] = []
    for profile in profiles:
        if profile.profile_id is None:
            continue
        known = _signals(profile.atoms)
        reasons: list[str] = []
        if fresh.emails & known.emails:
            reasons.append("email")
        if fresh.phones & known.phones:
            reasons.append("phone")
        if _employment_overlap(fresh.roles, known.roles):
            reasons.append("overlapping employment")
        if reasons:
            strong.append((profile, ", ".join(reasons)))
        elif fresh.names & known.names:
            weak.append((profile, "name"))
    if len(strong) == 1:
        profile, reason = strong[0]
        return MatchDecision("attach", profile.profile_id, reason, ())
    if len(strong) > 1:
        return MatchDecision(
            "ask",
            None,
            "strong signals match more than one profile",
            _candidates(strong),
        )
    if weak:
        return MatchDecision("ask", None, "only a name matches", _candidates(weak))
    return MatchDecision("create", None, "no profile matched", ())


def choose_profile(
    decision: MatchDecision,
    *,
    choice: str | None,
    stored: list[tuple[Path, MasterProfile]],
    input_fn=input,
    write=print,
) -> tuple[Path, MasterProfile] | Literal["new"] | None:
    """Return a stored profile, `new`, or None when the person stops."""

    if choice is not None:
        return _forced_choice(choice, stored)
    if decision.action == "attach":
        return _require_stored(decision.profile_id or "", stored)
    if decision.action == "create":
        return "new"
    write("This document needs a profile choice.")
    write(decision.reason + ".")
    for index, candidate in enumerate(decision.candidates, start=1):
        write(f"  [{index}] {candidate.profile_id}  {candidate.label}  {candidate.reason}")
    write("  [n] new profile")
    while True:
        try:
            answer = input_fn("Choice: ").strip().lower()
        except EOFError:
            return None
        if answer in {"n", "new"}:
            return "new"
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(decision.candidates):
                return _require_stored(decision.candidates[index - 1].profile_id, stored)
        write("Enter a number from the list, or n for a new profile.")


def _forced_choice(
    choice: str,
    stored: list[tuple[Path, MasterProfile]],
) -> tuple[Path, MasterProfile] | Literal["new"]:
    cleaned = choice.strip().lower()
    if cleaned == "new":
        return "new"
    if not PROFILE_ID.match(choice.strip()):
        raise ProfileError("--choose must be new or a profile id like pf_ plus 16 hex characters.")
    return _require_stored(choice.strip(), stored)


def _require_stored(profile_id: str, stored: list[tuple[Path, MasterProfile]]) -> tuple[Path, MasterProfile]:
    for path, profile in stored:
        if profile.profile_id == profile_id:
            return path, profile
    raise ProfileError(f"No profile with id {profile_id}.")


def _candidates(rows: list[tuple[MasterProfile, str]]) -> tuple[ProfileCandidate, ...]:
    return tuple(
        ProfileCandidate(profile.profile_id or "", _signals(profile.atoms).label, reason)
        for profile, reason in rows
    )


def _signals(atoms: list[ProfileAtom]) -> _Signals:
    emails: set[str] = set()
    phones: set[str] = set()
    roles: list[tuple[str, date, date]] = []
    names: set[str] = set()
    label = "unnamed"
    for atom in atoms:
        for piece in (atom.statement, atom.source_excerpt):
            emails.update(match.group(0).casefold() for match in _EMAIL.finditer(piece))
            phones.update(_phone_numbers(piece))
        role = _role(atom)
        if role is not None:
            roles.append(role)
        name = _name(atom)
        if name is not None:
            names.add(name.casefold())
            if label == "unnamed":
                label = name
    return _Signals(frozenset(emails), frozenset(phones), tuple(roles), frozenset(names), label)


def _phone_numbers(text: str) -> set[str]:
    found: set[str] = set()
    for match in _PHONE.finditer(text):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8:
            continue
        separators = re.sub(r"[\d+]", "", raw)
        if "+" not in raw and not re.search(r"[\s().]", raw) and separators.count("-") < 2:
            continue
        found.add(digits)
    return found


def _name(atom: ProfileAtom) -> str | None:
    if atom.category != EvidenceCategory.mentioned_only:
        return None
    text = atom.statement.strip()
    if "@" in text or any(char.isdigit() for char in text):
        return None
    words = text.split()
    if not 2 <= len(words) <= 4 or len(text) > 60:
        return None
    if any(len(word) < 2 or not word[0].isalpha() for word in words):
        return None
    if text.casefold() in {skill.casefold() for skill in SKILL_LEXICON}:
        return None
    if len(atom.skills) == 1 and atom.skills[0].casefold() == text.casefold():
        return None
    return text


def _role(atom: ProfileAtom) -> tuple[str, date, date] | None:
    if atom.category != EvidenceCategory.used_in_job or not atom.employer or atom.start_date is None:
        return None
    start = _bound(atom.start_date, is_end=False)
    end = _bound(atom.end_date, is_end=True) if atom.end_date is not None else _OPEN_END
    if end < start:
        return None
    return (_normalize_employer(atom.employer), start, end)


def _employment_overlap(
    left: tuple[tuple[str, date, date], ...],
    right: tuple[tuple[str, date, date], ...],
) -> bool:
    for employer, start, end in left:
        for other_employer, other_start, other_end in right:
            if employer != other_employer:
                continue
            if start <= other_end and other_start <= end:
                return True
    return False


def _bound(value: ExactDate, *, is_end: bool) -> date:
    month = value.month if value.month is not None else (12 if is_end else 1)
    last = monthrange(value.year, month)[1]
    day = value.day if value.day is not None else (last if is_end else 1)
    return date(value.year, month, min(day, last))


def _normalize_employer(value: str) -> str:
    chars = [char.casefold() if char.isalnum() or char in "-&" else " " for char in value]
    return re.sub(r"\s+", " ", "".join(chars)).strip()
