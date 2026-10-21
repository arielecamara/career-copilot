"""Collect work preferences for one profile. This does not search for jobs."""

from __future__ import annotations

from pydantic import ValidationError

from career_copilot.errors import ProfileError
from career_copilot.models import (
    MAX_PREFERENCE_CHARS,
    AreaChoice,
    PriorityLevel,
    ScheduleChoice,
    SectorPreference,
    UserPreferences,
    WorkMode,
    WorkModePreference,
)

_LEVELS = tuple(level.value for level in PriorityLevel)
_SECTORS = tuple(sector.value for sector in SectorPreference)
_SCHEDULE_LABELS = {
    ScheduleChoice.part_time: "Part-time",
    ScheduleChoice.full_time: "Full-time",
    ScheduleChoice.other: "Other",
}


def preferences_from_flags(
    *,
    remote: str | None,
    hybrid: str | None,
    onsite: str | None,
    location: str | None,
    sector: str | None,
    availability: str | None,
) -> UserPreferences | None:
    """Build preferences when every required flag is present. None means 'not provided'."""

    provided = [remote, hybrid, onsite, location, sector]
    if all(item is None for item in provided) and availability is None:
        return None
    if any(item is None for item in provided):
        raise ProfileError("Pass --remote, --hybrid, --onsite, --location, and --sector together.")
    assert remote is not None and hybrid is not None and onsite is not None
    assert location is not None and sector is not None
    return _build(remote, hybrid, onsite, location, sector, availability)


def prompt_preferences(*, input_fn=input, write=print) -> UserPreferences:
    write("These answers are saved on this profile. They do not start a job search.")
    remote = _choice(input_fn, write, "Remote priority", _LEVELS)
    hybrid = _choice(input_fn, write, "Hybrid priority", _LEVELS)
    onsite = _choice(input_fn, write, "Onsite priority", _LEVELS)
    location = _required(input_fn, write, "Location or region for onsite work: ")
    sector = _choice(input_fn, write, "Sector", _SECTORS)
    availability = _optional(input_fn, write, "Time availability (optional, Enter to skip): ")
    return _build(remote, hybrid, onsite, location, sector, availability)


def preferences_from_choices(
    *,
    job_types: object,
    areas: object,
    schedules: object,
    location: object,
) -> UserPreferences:
    """Map the page's multi-select answers onto the stored preference record.

    Selected job types are high priority. The others stay on the record as low
    so older tools still see remote, hybrid, and onsite once each.
    """

    modes = _members(job_types, WorkMode, "Job type")
    chosen_areas = _members(areas, AreaChoice, "Area")
    chosen_schedules = _members(schedules, ScheduleChoice, "Time")
    if not modes:
        raise ProfileError("Choose at least one job type.")
    if not chosen_areas:
        raise ProfileError("Choose at least one area.")
    if not chosen_schedules:
        raise ProfileError("Choose at least one time option.")
    place = _location(location, onsite_selected=WorkMode.onsite in modes)
    availability = ", ".join(_SCHEDULE_LABELS[item] for item in chosen_schedules)
    selected = set(modes)
    try:
        return UserPreferences(
            work_modes=[
                WorkModePreference(
                    mode=mode,
                    priority=PriorityLevel.high if mode in selected else PriorityLevel.low,
                )
                for mode in (WorkMode.remote, WorkMode.hybrid, WorkMode.onsite)
            ],
            onsite_location=place,
            sector=_sector(chosen_areas),
            time_availability=availability,
            job_types=modes,
            areas=chosen_areas,
            schedules=chosen_schedules,
        )
    except (ValidationError, ValueError) as exc:
        raise ProfileError(_preference_message(exc)) from exc


def format_preferences(preferences: UserPreferences, profile_id: str | None) -> str:
    by_mode = {item.mode: item.priority.value for item in preferences.work_modes}
    lines = [
        f"Profile: {profile_id or 'unset'}",
        f"Remote: {by_mode[WorkMode.remote]}",
        f"Hybrid: {by_mode[WorkMode.hybrid]}",
        f"Onsite: {by_mode[WorkMode.onsite]}",
        f"Onsite location: {preferences.onsite_location}",
        f"Sector: {preferences.sector.value}",
        f"Time availability: {preferences.time_availability or 'not set'}",
    ]
    return "\n".join(lines)


def _build(
    remote: str,
    hybrid: str,
    onsite: str,
    location: str,
    sector: str,
    availability: str | None,
) -> UserPreferences:
    try:
        return UserPreferences(
            work_modes=[
                WorkModePreference(mode=WorkMode.remote, priority=PriorityLevel(remote)),
                WorkModePreference(mode=WorkMode.hybrid, priority=PriorityLevel(hybrid)),
                WorkModePreference(mode=WorkMode.onsite, priority=PriorityLevel(onsite)),
            ],
            onsite_location=location,
            sector=SectorPreference(sector),
            time_availability=availability,
        )
    except (ValidationError, ValueError) as exc:
        raise ProfileError(_preference_message(exc)) from exc


def _choice(input_fn, write, label: str, options: tuple[str, ...]) -> str:
    shown = "/".join(options)
    while True:
        try:
            answer = input_fn(f"{label} [{shown}]: ").strip().lower().replace(" ", "_")
        except EOFError as exc:
            raise ProfileError("Stopped. Preferences were not saved.") from exc
        if answer in options:
            return answer
        write(f"Use one of: {shown}.")


def _required(input_fn, write, prompt: str) -> str:
    while True:
        try:
            answer = input_fn(prompt).strip()
        except EOFError as exc:
            raise ProfileError("Stopped. Preferences were not saved.") from exc
        if not answer:
            write("Enter a location or region.")
            continue
        if not _fits(answer, write):
            continue
        return answer


def _optional(input_fn, write, prompt: str) -> str | None:
    try:
        answer = input_fn(prompt).strip()
    except EOFError as exc:
        raise ProfileError("Stopped. Preferences were not saved.") from exc
    if not answer:
        return None
    if "\x00" in answer:
        raise ProfileError("Time availability contains a null character.")
    if len(answer) > MAX_PREFERENCE_CHARS:
        raise ProfileError("Time availability is too long.")
    return answer


def _fits(answer: str, write) -> bool:
    if "\x00" in answer:
        write("That answer contains a null character.")
        return False
    if len(answer) > MAX_PREFERENCE_CHARS:
        write("That answer is too long.")
        return False
    return True


def _members(value: object, kind: type, label: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise ProfileError(f"{label} must be chosen from the list.")
    chosen: list = []
    for item in value:
        try:
            member = item if isinstance(item, kind) else kind(item)
        except ValueError as exc:
            raise ProfileError(f"{label} must be chosen from the list.") from exc
        if member not in chosen:
            chosen.append(member)
    return chosen


def _location(value: object, *, onsite_selected: bool) -> str:
    if not isinstance(value, str):
        raise ProfileError("Onsite location must be text.")
    if "\x00" in value:
        raise ProfileError("Onsite location contains a null character.")
    stripped = value.strip()
    if len(stripped) > MAX_PREFERENCE_CHARS:
        raise ProfileError("Onsite location is too long.")
    if stripped:
        return stripped
    if onsite_selected:
        raise ProfileError("Onsite location is required. Type none if you have no preferred region.")
    return "none"


def _sector(areas: list[AreaChoice]) -> SectorPreference:
    values = {item.value for item in areas}
    academia = AreaChoice.academia.value in values
    industry = AreaChoice.industry.value in values
    if academia and industry:
        return SectorPreference.both
    if academia:
        return SectorPreference.academia
    if industry:
        return SectorPreference.industry
    return SectorPreference.both


def _preference_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and exc.errors():
        message = str(exc.errors()[0].get("msg", ""))
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        if message:
            return message
    return "Preferences are not valid."
