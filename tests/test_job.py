import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_copilot.cli import main
from career_copilot.job import analyze_job
from career_copilot.models import (
    ConfirmationState,
    ConstraintResult,
    EvidenceCategory,
    ExactDate,
    RequirementKind,
    RequirementStatus,
)
from career_copilot.profile import save_profile
from tests.factories import atom, document, profile_for

AS_OF = date(2026, 10, 13)
JOB = """
About us
We started 2 years ago and offer a pension.

Requirements:
- 6+ years of experience
- Python
- Experience building production Neo4j services
- Kubernetes
- Bachelor's degree
- Excellent communication skills
- Must be a US citizen
- Authorized to work in the Netherlands

Preferred:
- PostgreSQL
- Driver's license required
"""


def _confirmed(**overrides: object):
    payload: dict[str, object] = {"confirmation": ConfirmationState.confirmed}
    payload.update(overrides)
    return atom(**payload)


def _python_mention():
    return _confirmed(
        evidence_id="ev_" + "1" * 16,
        statement="Python",
        source_excerpt="Python",
        category=EvidenceCategory.mentioned_only,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
        skills=["Python"],
    )


def _neo4j_job():
    return _confirmed(
        evidence_id="ev_" + "2" * 16,
        statement="Built Neo4j services | North 2018 – 2022",
        source_excerpt="Built Neo4j services | North 2018 – 2022",
        employer="North",
        title="Engineer",
        skills=["Neo4j"],
        start_date=ExactDate(raw="2018", year=2018),
        end_date=ExactDate(raw="2022", year=2022),
    )


def _overlap_job():
    return _confirmed(
        evidence_id="ev_" + "3" * 16,
        statement="Engineer | South 2020 – 2022",
        source_excerpt="Engineer | South 2020 – 2022",
        employer="South",
        title="Engineer",
        start_date=ExactDate(raw="2020", year=2020),
        end_date=ExactDate(raw="2022", year=2022),
    )


def _bsc():
    return _confirmed(
        evidence_id="ev_" + "4" * 16,
        statement="BSc in History",
        source_excerpt="BSc in History",
        category=EvidenceCategory.coursework,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )


def _postgres():
    return _confirmed(
        evidence_id="ev_" + "5" * 16,
        statement="Postgres",
        source_excerpt="Postgres",
        category=EvidenceCategory.mentioned_only,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
        skills=["Postgres"],
    )


def _analysis():
    return analyze_job(
        JOB,
        [_python_mention(), _neo4j_job(), _overlap_job(), _bsc(), _postgres()],
        as_of=AS_OF,
    )


def _item(analysis, text: str):
    return next(item for item in analysis.requirements if item.text == text)


def test_checklist_uses_only_confirmed_atoms_and_keeps_the_two_lists():
    analysis = _analysis()
    python = _item(analysis, "Python")
    assert python.kind == RequirementKind.mandatory
    assert python.status == RequirementStatus.met
    assert python.evidence_ids == ["ev_" + "1" * 16]
    assert python.excerpt == "Python"

    neo4j = _item(analysis, "Experience building production Neo4j services")
    assert neo4j.status == RequirementStatus.met
    assert neo4j.evidence_ids == ["ev_" + "2" * 16]

    kubernetes = _item(analysis, "Kubernetes")
    assert kubernetes.status == RequirementStatus.missing
    assert kubernetes.evidence_ids == []
    assert kubernetes.excerpt == ""

    degree = _item(analysis, "Bachelor's degree")
    assert degree.status == RequirementStatus.met
    assert degree.excerpt == "BSc in History"

    soft = _item(analysis, "Excellent communication skills")
    assert soft.status == RequirementStatus.unknown
    assert soft.evidence_ids == []

    preferred = _item(analysis, "PostgreSQL")
    assert preferred.kind == RequirementKind.preferred
    assert preferred.status == RequirementStatus.met
    assert preferred.evidence_ids == ["ev_" + "5" * 16]

    mentioned = _python_mention()
    hands_on = analyze_job(
        "Requirements:\n- Experience building production Python services",
        [mentioned],
        as_of=AS_OF,
    )
    assert hands_on.requirements[0].status == RequirementStatus.partial

    unconfirmed = atom(
        evidence_id="ev_" + "9" * 16,
        statement="Kubernetes",
        source_excerpt="Kubernetes",
        category=EvidenceCategory.mentioned_only,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
        skills=["Kubernetes"],
    )
    missing = analyze_job("Requirements:\n- Kubernetes", [unconfirmed], as_of=AS_OF)
    assert missing.requirements[0].status == RequirementStatus.missing


def test_docker_does_not_satisfy_kubernetes_and_phd_does_not_satisfy_bachelors():
    docker = _confirmed(
        evidence_id="ev_" + "a" * 16,
        statement="Docker",
        source_excerpt="Docker",
        category=EvidenceCategory.used_in_job,
        skills=["Docker"],
    )
    phd = _confirmed(
        evidence_id="ev_" + "b" * 16,
        statement="PhD in Information Science",
        source_excerpt="PhD in Information Science",
        category=EvidenceCategory.coursework,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    analysis = analyze_job(
        "Requirements:\n- Kubernetes\n- Bachelor's degree",
        [docker, phd],
        as_of=AS_OF,
    )
    assert _item(analysis, "Kubernetes").status == RequirementStatus.missing
    assert _item(analysis, "Bachelor's degree").status == RequirementStatus.missing


def test_hard_constraints_are_pass_fail_or_unknown_and_years_do_not_double_count():
    analysis = _analysis()
    assert analysis.years.intervals == ["2018-01-01..2022-12-31"]
    by_text = {item.text: item for item in analysis.hard_constraints}
    assert "We started 2 years ago and offer a pension." not in by_text
    years = by_text["6+ years of experience"]
    assert years.result == ConstraintResult.failed
    assert years.observed_days == analysis.years.days
    citizen = by_text["Must be a US citizen"]
    assert citizen.result == ConstraintResult.unknown
    assert citizen.required_years is None
    work = by_text["Authorized to work in the Netherlands"]
    assert work.result == ConstraintResult.unknown
    license_row = by_text["Driver's license required"]
    assert license_row.result == ConstraintResult.unknown

    long_enough = analyze_job(
        "Requirements:\n- 4+ years of experience",
        [_neo4j_job(), _overlap_job()],
        as_of=AS_OF,
    )
    assert long_enough.hard_constraints[0].result == ConstraintResult.passed

    silent = analyze_job("Requirements:\n- 5+ years of experience", [], as_of=AS_OF)
    assert silent.hard_constraints[0].result == ConstraintResult.unknown
    assert silent.hard_constraints[0].observed_days is None

    skill_years = analyze_job(
        "Requirements:\n- 5 years of Python experience",
        [_python_mention(), _neo4j_job()],
        as_of=AS_OF,
    )
    assert skill_years.hard_constraints[0].result == ConstraintResult.unknown

    short_python = _confirmed(
        evidence_id="ev_" + "c" * 16,
        statement="Used Python | Acme 2020 – 2021",
        source_excerpt="Used Python | Acme 2020 – 2021",
        employer="Acme",
        title="Engineer",
        skills=["Python"],
        start_date=ExactDate(raw="2020", year=2020),
        end_date=ExactDate(raw="2021", year=2021),
    )
    failed = analyze_job("Requirements:\n- 5 years of Python experience", [short_python], as_of=AS_OF)
    assert failed.hard_constraints[0].result == ConstraintResult.failed

    stated = _confirmed(
        evidence_id="ev_" + "d" * 16,
        statement="US citizen",
        source_excerpt="US citizen",
        category=EvidenceCategory.mentioned_only,
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    passed = analyze_job("Requirements:\n- Must be a US citizen", [stated], as_of=AS_OF)
    assert passed.hard_constraints[0].result == ConstraintResult.passed
    other = analyze_job("Requirements:\n- Must be a Portuguese citizen", [stated], as_of=AS_OF)
    assert other.hard_constraints[0].result == ConstraintResult.unknown


def test_checklist_model_has_no_score():
    analysis = analyze_job("Requirements:\n- Python", [], as_of=AS_OF)
    with pytest.raises(ValidationError):
        type(analysis).model_validate({**analysis.model_dump(), "score": 80})


def test_tally_weights_partial_and_excludes_unknown():
    from career_copilot.models import ChecklistTally

    mentioned = _confirmed(
        evidence_id="ev_" + "a" * 16,
        statement="Python",
        source_excerpt="Python",
        category=EvidenceCategory.mentioned_only,
        skills=["Python"],
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    analysis = analyze_job(
        "Requirements:\n- Production experience with Python\n- Enjoys collaboration\n- Kubernetes",
        [mentioned],
        as_of=AS_OF,
    )
    assert analysis.tally.partial == 1
    assert analysis.tally.unknown == 1
    assert analysis.tally.missing == 1
    assert analysis.tally.percent == 25.0
    with pytest.raises(ValidationError):
        ChecklistTally(met=1, partial=0, missing=0, unknown=0, percent=10)


def test_job_command_prints_the_checklist_and_does_not_change_the_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    profile_path = tmp_path / "profile.json"
    record = document([("paragraph", "Python")])
    save_profile(profile_path, profile_for(record, [_python_mention(), _bsc()]))
    before = profile_path.read_bytes()
    out = tmp_path / "checklist.json"
    code = main(
        [
            "job",
            "--text",
            "Requirements:\n- Python\nPreferred:\n- Kubernetes",
            "--profile",
            str(profile_path),
            "--as-of",
            "2026-10-13",
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert profile_path.read_bytes() == before
    assert "Mandatory requirements:" in captured
    assert "Preferred requirements:" in captured
    assert "\n  met  Python\n" in captured
    assert "\n  missing  Kubernetes\n" in captured
    assert "Match: 50.0% of judged requirements. Unknown lines are excluded." in captured
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert "score" not in saved
    assert saved["tally"]["percent"] == 50.0
    assert saved["tally"]["met"] == 1
    assert saved["tally"]["missing"] == 1
    assert saved["requirements"][0]["status"] == "met"
    assert saved["requirements"][1]["kind"] == "preferred"


def test_job_command_rejects_empty_text_and_still_has_no_generate(tmp_path: Path):
    profile_path = tmp_path / "profile.json"
    save_profile(profile_path, profile_for(document([("paragraph", "Python")]), []))
    common = ["--profile", str(profile_path)]
    assert main(["job", "--text", "  ", *common]) == 1
    assert main(["job", "--text", "Python", "--as-of", "tomorrow", *common]) == 1
    with pytest.raises(SystemExit) as caught:
        main(["generate"])
    assert caught.value.code == 2
