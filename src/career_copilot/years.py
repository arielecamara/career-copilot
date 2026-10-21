"""Employment length from confirmed roles. Overlapping jobs are merged, not added."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from career_copilot.models import ConfirmationState, EvidenceCategory, ExactDate, ProfileAtom


@dataclass(frozen=True)
class ExperienceSpan:
    """One timeline. `complete` is false when a confirmed role has no start date."""

    days: int
    as_of: date
    complete: bool
    intervals: tuple[tuple[date, date], ...]

    @property
    def years(self) -> float:
        return self.days / 365.25


def experience_span(
    atoms: list[ProfileAtom],
    *,
    as_of: date,
    include: Callable[[ProfileAtom], bool] | None = None,
) -> ExperienceSpan:
    """Union of confirmed `used_in_job` intervals.

    A year with no month covers that whole year. An open role ends on `as_of`.
    A future end date is capped at `as_of`. Bullets that repeat a role share
    its interval, so the union counts that role once. `include` limits the
    union to roles that name one skill.
    """

    confirmed = [atom for atom in atoms if atom.confirmation == ConfirmationState.confirmed]
    dated = [
        atom
        for atom in confirmed
        if atom.category == EvidenceCategory.used_in_job and atom.start_date is not None
    ]
    if include is not None:
        dated = [atom for atom in dated if include(atom)]
        supporters = [
            atom
            for atom in confirmed
            if atom.category == EvidenceCategory.used_in_job and include(atom)
        ]
        complete = bool(supporters) and all(atom.start_date is not None for atom in supporters)
    else:
        headers = [atom for atom in confirmed if _is_role(atom)]
        complete = bool(dated) and all(atom.start_date is not None for atom in headers)

    intervals: list[tuple[date, date]] = []
    for atom in dated:
        assert atom.start_date is not None
        start = _start_bound(atom.start_date)
        end = _end_bound(atom.end_date) if atom.end_date is not None else as_of
        if end > as_of:
            end = as_of
        if end < start:
            continue
        intervals.append((start, end))
    merged = _merge(intervals)
    days = sum((end - start).days + 1 for start, end in merged)
    return ExperienceSpan(days=days, as_of=as_of, complete=complete and bool(merged), intervals=tuple(merged))


def meets_minimum(days: int, required_years: int) -> bool:
    """True when the merged timeline covers the required number of years.

    A whole calendar year is 365 or 366 days. The extra day keeps a five-year
    span that includes one leap day from failing a five-year bar by a fraction.
    """

    if required_years < 0:
        return False
    return days + 1 >= required_years * 365.25


def _is_role(atom: ProfileAtom) -> bool:
    return (
        atom.category == EvidenceCategory.used_in_job
        and bool(atom.employer)
        and bool(atom.title)
    )


def _start_bound(value: ExactDate) -> date:
    month = value.month or 1
    last = monthrange(value.year, month)[1]
    day = min(value.day or 1, last)
    return date(value.year, month, day)


def _end_bound(value: ExactDate) -> date:
    month = value.month or 12
    last = monthrange(value.year, month)[1]
    day = min(value.day or last, last)
    return date(value.year, month, day)


def _merge(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        current_start, current_end = merged[-1]
        if start <= current_end:
            merged[-1] = (current_start, max(current_end, end))
            continue
        merged.append((start, end))
    return merged
