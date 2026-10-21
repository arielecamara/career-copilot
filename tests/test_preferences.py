from pathlib import Path

from career_copilot.cli import main
from career_copilot.models import AreaChoice, PriorityLevel, ScheduleChoice, SectorPreference, WorkMode
from career_copilot.preferences import preferences_from_choices
from career_copilot.profile import load_profile


def _flags(path: Path, location: str = "Netherlands") -> list[str]:
    return [
        "preferences",
        "--profile",
        str(path),
        "--remote",
        "high",
        "--hybrid",
        "medium",
        "--onsite",
        "only_if_close",
        "--location",
        location,
        "--sector",
        "both",
        "--availability",
        "full-time",
    ]


def test_preferences_are_saved_once_and_can_be_replaced(tmp_path: Path):
    path = tmp_path / "profile.json"
    assert main(_flags(path)) == 0
    stored = load_profile(path)
    assert stored.profile_id is not None
    assert stored.preferences is not None
    by_mode = {item.mode: item.priority.value for item in stored.preferences.work_modes}
    assert by_mode[WorkMode.remote] == "high"
    assert by_mode[WorkMode.hybrid] == "medium"
    assert by_mode[WorkMode.onsite] == "only_if_close"
    assert stored.preferences.onsite_location == "Netherlands"
    assert stored.preferences.sector == SectorPreference.both
    assert stored.preferences.time_availability == "full-time"

    before = path.read_bytes()
    assert main(_flags(path, location="Lisbon")) == 1
    assert path.read_bytes() == before

    assert main(["preferences", "--profile", str(path)]) == 0
    assert path.read_bytes() == before

    assert main([* _flags(path, location="Lisbon"), "--update"]) == 0
    assert load_profile(path).preferences.onsite_location == "Lisbon"


def test_page_choices_keep_the_older_preference_fields():
    academic = preferences_from_choices(
        job_types=["remote", "onsite"],
        areas=["academia", "other"],
        schedules=["part_time"],
        location="Lisbon",
    )
    by_mode = {item.mode: item.priority for item in academic.work_modes}
    assert by_mode[WorkMode.remote] == PriorityLevel.high
    assert by_mode[WorkMode.hybrid] == PriorityLevel.low
    assert by_mode[WorkMode.onsite] == PriorityLevel.high
    assert academic.sector == SectorPreference.academia
    assert academic.areas == [AreaChoice.academia, AreaChoice.other]
    assert academic.schedules == [ScheduleChoice.part_time]
    assert academic.time_availability == "Part-time"
    other = preferences_from_choices(
        job_types=["remote"],
        areas=["other"],
        schedules=["full_time", "other"],
        location="  ",
    )
    assert other.sector == SectorPreference.both
    assert other.onsite_location == "none"
    assert other.time_availability == "Full-time, Other"
