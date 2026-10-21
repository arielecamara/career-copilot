"""Input limits. These tests use temporary profiles and do not open profile.json."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_copilot.cli import main
from career_copilot.errors import CareerCopilotError, ProfileError
from career_copilot.inputs import (
    MAX_JOB_FILE_BYTES,
    MAX_JOB_TEXT,
    MAX_SENTENCE_TEXT,
    MAX_STATEMENT_TEXT,
    allowed_profile,
    directory_inside,
    path_inside,
    read_job_file,
    read_job_stream,
    require_as_of,
    require_bool,
    require_evidence_id,
    require_job_text,
    require_statement,
)
from career_copilot.job import analyze_job
from career_copilot.library import legacy_docx, list_generations, load_generation, save_generation, with_texts
from career_copilot.models import (
    ChecklistTally,
    ExperienceYears,
    JobAnalysis,
    PriorityLevel,
    ResumeSentence,
    SectorPreference,
    SentenceKind,
    UserPreferences,
    WorkMode,
    WorkModePreference,
)
from career_copilot.profile import confirm_atoms, edit_atom, load_profile, save_profile
from tests.factories import atom, document, profile_for

KEY = "pf_" + "a" * 16
RECORD = "gen_" + "1" * 16
EVIDENCE = "ev_" + "1" * 16


def test_job_text_rejects_blank_null_and_oversized_values():
    assert require_job_text("  Python\n- SQL") == "  Python\n- SQL"
    with pytest.raises(CareerCopilotError, match="empty"):
        require_job_text(" \n\t ")
    with pytest.raises(CareerCopilotError, match="must be text"):
        require_job_text(None)
    with pytest.raises(CareerCopilotError, match="null character"):
        require_job_text("Python\x00")
    with pytest.raises(CareerCopilotError, match="too long"):
        require_job_text("a" * (MAX_JOB_TEXT + 1))
    with pytest.raises(CareerCopilotError, match="too long"):
        analyze_job("a" * (MAX_JOB_TEXT + 1), [], as_of=date(2026, 10, 13))


def test_as_of_accepts_a_datetime_inside_the_window_only():
    assert require_as_of(datetime(2026, 10, 13, 23, 59)) == date(2026, 10, 13)
    assert require_as_of(date(1900, 1, 1)) == date(1900, 1, 1)
    assert require_as_of("2100-12-31") == date(2100, 12, 31)
    with pytest.raises(CareerCopilotError, match="1900-01-01 through 2100-12-31"):
        require_as_of("1899-12-31")
    with pytest.raises(CareerCopilotError, match="1900-01-01 through 2100-12-31"):
        require_as_of(date(2101, 1, 1))
    with pytest.raises(CareerCopilotError, match="--as-of must be YYYY-MM-DD."):
        require_as_of("tomorrow", invalid_message="--as-of must be YYYY-MM-DD.")
    with pytest.raises(CareerCopilotError, match="--as-of must be YYYY-MM-DD."):
        require_as_of(False, invalid_message="--as-of must be YYYY-MM-DD.")


def test_job_command_rejects_dates_outside_the_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    profile_path = tmp_path / "profile.json"
    save_profile(profile_path, profile_for(document([("paragraph", "Python")]), []))
    common = ["job", "--text", "Python", "--profile", str(profile_path)]
    assert main([*common, "--as-of", "1899-12-31"]) == 1
    assert "1900-01-01" in capsys.readouterr().err
    assert main([*common, "--as-of", "2101-01-01"]) == 1
    assert main([*common, "--as-of", "2100-12-31"]) == 0


def test_job_file_is_sized_before_it_is_decoded(tmp_path: Path):
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"a" * (MAX_JOB_FILE_BYTES + 1))
    with pytest.raises(CareerCopilotError, match="too large"):
        read_job_file(huge)
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(CareerCopilotError, match="UTF-8"):
        read_job_file(binary)
    valid = tmp_path / "job.txt"
    valid.write_text("Requirements:\n- Python\n", encoding="utf-8")
    assert read_job_file(valid) == "Requirements:\n- Python\n"

    class _Reader:
        def __init__(self) -> None:
            self.requested = 0

        def read(self, count: int) -> str:
            self.requested = count
            return "b" * count

    reader = _Reader()
    with pytest.raises(CareerCopilotError, match="too long"):
        read_job_stream(reader)
    assert reader.requested == MAX_JOB_TEXT + 1


def test_statement_limits_do_not_rewrite_the_profile_file(tmp_path: Path):
    evidence_id = EVIDENCE
    profile = profile_for(
        document([("paragraph", "Python")]),
        [atom(evidence_id=evidence_id, statement="Python", source_excerpt="Python", employer=None, title=None, start_date=None, end_date=None)],
    )
    path = tmp_path / "profile.json"
    save_profile(path, profile)
    before = path.read_bytes()
    loaded = load_profile(path)
    with pytest.raises(CareerCopilotError, match="too long"):
        edit_atom(loaded, evidence_id, statement="x" * (MAX_STATEMENT_TEXT + 1))
    with pytest.raises(CareerCopilotError, match="null character"):
        edit_atom(loaded, evidence_id, statement="Python\x00")
    with pytest.raises(CareerCopilotError, match="must be text"):
        edit_atom(loaded, evidence_id, statement=123)
    assert path.read_bytes() == before
    assert loaded.atoms[0].statement == "Python"
    updated = edit_atom(loaded, evidence_id, statement="  " + ("y" * 20) + "  ")
    assert updated.atoms[0].statement == "y" * 20
    assert path.read_bytes() == before


def test_a_bad_evidence_id_is_refused_without_echoing_it():
    bad = "ev_" + ("g" * 5000)
    with pytest.raises(CareerCopilotError, match="^That evidence id is not valid\\.$") as caught:
        require_evidence_id(bad)
    assert bad not in str(caught.value)
    profile = profile_for(document([("paragraph", "Python")]), [])
    missing = "ev_" + "f" * 16
    with pytest.raises(ProfileError, match=f"No atom with evidence id {missing}"):
        confirm_atoms(profile, [missing])


def test_sentence_text_and_flags_are_checked_before_a_file_is_written(tmp_path: Path):
    sentence = ResumeSentence(text="Python", evidence_ids=[EVIDENCE], kind=SentenceKind.skill)
    with pytest.raises(CareerCopilotError, match="too long"):
        with_texts([sentence], ["z" * (MAX_SENTENCE_TEXT + 1)])
    with pytest.raises(CareerCopilotError, match="null character"):
        require_statement(" \x00 ")
    assert require_bool(False) is False
    with pytest.raises(CareerCopilotError, match="Choose"):
        require_bool("false")

    analysis = JobAnalysis(
        years=ExperienceYears(days=0, as_of="2026-10-13", complete=True),
        tally=ChecklistTally(met=0, partial=0, missing=0, unknown=0),
    )
    profile = profile_for(document([("paragraph", "Python")]), [])
    long_sentence = ResumeSentence(
        text="q" * (MAX_SENTENCE_TEXT + 1),
        evidence_ids=[EVIDENCE],
        kind=SentenceKind.skill,
    )
    root = tmp_path / "generated"
    with pytest.raises(CareerCopilotError, match="too long"):
        save_generation(
            root,
            profile=profile,
            profile_key=KEY,
            job_text="Python",
            as_of=date(2026, 10, 13),
            analysis=analysis,
            base_sentences=[sentence],
            base_cover=[],
            base_diff="diff",
            sentences=[long_sentence],
            cover=[],
            write_resume_doc=True,
            write_cover=False,
        )
    assert not root.exists()


def test_preferences_reject_a_location_past_200_characters():
    def build(location: str, availability: str | None = None) -> UserPreferences:
        return UserPreferences(
            work_modes=[
                WorkModePreference(mode=WorkMode.remote, priority=PriorityLevel.high),
                WorkModePreference(mode=WorkMode.hybrid, priority=PriorityLevel.medium),
                WorkModePreference(mode=WorkMode.onsite, priority=PriorityLevel.low),
            ],
            onsite_location=location,
            sector=SectorPreference.both,
            time_availability=availability,
        )

    with pytest.raises(ValidationError, match="onsite location is too long"):
        build("L" * 201)
    with pytest.raises(ValidationError, match="null character"):
        build("Lisbon\x00")
    with pytest.raises(ValidationError, match="time availability is too long"):
        build("Lisbon", "t" * 201)
    assert build("  Lisbon  ").onsite_location == "Lisbon"


def test_paths_must_stay_inside_the_allowed_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret = tmp_path / "secret.docx"
    secret.write_bytes(b"secret")
    folder = tmp_path / "out"
    folder.mkdir()
    kept = folder / "resume.docx"
    kept.write_bytes(b"1234")
    huge = folder / "huge.docx"
    huge.write_bytes(b"12345")
    assert path_inside(folder / ".." / "secret.docx", folder) is None
    monkeypatch.setattr("career_copilot.library.MAX_DOWNLOAD_BYTES", 4)
    listed = {path.name for path in legacy_docx(folder)}
    assert listed == {"resume.docx"}
    assert secret.resolve() not in {path.resolve() for path in legacy_docx(folder)}

    root = tmp_path / "generated"
    root.mkdir()
    safe = directory_inside(root, KEY, RECORD)
    assert safe == root.resolve() / KEY / RECORD
    with pytest.raises(CareerCopilotError, match="not valid"):
        directory_inside(root, "..")
    with pytest.raises(CareerCopilotError, match="not valid"):
        directory_inside(root, KEY, "../secret")

    allowed = tmp_path / "profile.json"
    allowed.write_text("{}\n", encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("{}\n", encoding="utf-8")
    assert allowed_profile(str(allowed), [allowed]) == allowed
    with pytest.raises(CareerCopilotError, match="Choose a profile from the list"):
        allowed_profile(str(other), [allowed])
    missing = tmp_path / "missing.json"
    assert allowed_profile(str(missing), [missing]) == missing


def test_a_record_outside_its_folder_or_over_the_size_limit_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    folder = tmp_path / KEY / RECORD
    folder.mkdir(parents=True)
    payload = {
        "record_id": RECORD,
        "created_at": "2026-10-13T00:00:00Z",
        "updated_at": "2026-10-13T00:00:00Z",
        "profile_key": KEY,
        "job_text": "Python",
        "as_of": "2026-10-13",
        "tally_percent": None,
        "wrote_resume": True,
        "wrote_cover": False,
        "sentences": [{"text": "Python", "evidence_ids": [EVIDENCE], "kind": "skill"}],
        "cover": [],
        "diff_markdown": "diff",
    }
    record_path = folder / "record.json"
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    assert [item.record_id for item in list_generations(tmp_path, KEY)] == [RECORD]
    assert load_generation(tmp_path, KEY, RECORD).record_id == RECORD

    monkeypatch.setattr("career_copilot.library.MAX_RECORD_BYTES", record_path.stat().st_size - 1)
    assert list_generations(tmp_path, KEY) == []
    with pytest.raises(CareerCopilotError, match="not found"):
        load_generation(tmp_path, KEY, RECORD)
    monkeypatch.undo()

    mismatched = dict(payload)
    mismatched["record_id"] = "gen_" + "2" * 16
    record_path.write_text(json.dumps(mismatched), encoding="utf-8")
    assert list_generations(tmp_path, KEY) == []
    with pytest.raises(CareerCopilotError, match="not found"):
        load_generation(tmp_path, KEY, "../secret")
