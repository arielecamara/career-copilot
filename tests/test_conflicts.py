from career_copilot.conflicts import detect_conflicts
from career_copilot.models import EvidenceCategory, ExactDate
from tests.factories import atom


def _role(evidence: str, document: str, **overrides: object):
    payload = {
        "evidence_id": evidence,
        "source_document_id": document,
        "statement": "Engineer | Acme 2020 – 2022",
        "source_excerpt": "Engineer | Acme 2020 – 2022",
        "employer": "Acme",
        "title": "Engineer",
        "start_date": ExactDate(raw="2020", year=2020),
        "end_date": ExactDate(raw="2022", year=2022),
    }
    payload.update(overrides)
    return atom(**payload)


def test_identical_roles_are_kept_and_not_reported_as_conflicts():
    left = _role("ev_" + "1" * 16, "doc_" + "a" * 16)
    right = _role("ev_" + "2" * 16, "doc_" + "b" * 16)
    assert detect_conflicts([left, right]) == []


def test_same_title_with_different_dates_is_a_date_conflict():
    left = _role("ev_" + "1" * 16, "doc_" + "a" * 16)
    right = _role(
        "ev_" + "2" * 16,
        "doc_" + "b" * 16,
        statement="Engineer | Acme 2020 – 2023",
        source_excerpt="Engineer | Acme 2020 – 2023",
        end_date=ExactDate(raw="2023", year=2023),
    )
    conflicts = detect_conflicts([left, right])
    assert [conflict.kind.value for conflict in conflicts] == ["dates"]
    assert "Both atoms are kept." in conflicts[0].summary


def test_overlapping_roles_with_different_titles_are_a_title_conflict():
    left = _role(
        "ev_" + "1" * 16,
        "doc_" + "a" * 16,
        statement="Engineer | Acme 2010 – 2015",
        source_excerpt="Engineer | Acme 2010 – 2015",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2015", year=2015),
    )
    right = _role(
        "ev_" + "2" * 16,
        "doc_" + "b" * 16,
        statement="Analyst | Acme 2012 – 2014",
        source_excerpt="Analyst | Acme 2012 – 2014",
        title="Analyst",
        start_date=ExactDate(raw="2012", year=2012),
        end_date=ExactDate(raw="2014", year=2014),
    )
    kinds = {conflict.kind.value for conflict in detect_conflicts([left, right])}
    assert "title" in kinds
    assert "employer" not in kinds


def test_separate_stints_with_different_titles_are_not_conflicts():
    left = _role(
        "ev_" + "1" * 16,
        "doc_" + "a" * 16,
        statement="Engineer | Acme 2010 – 2012",
        source_excerpt="Engineer | Acme 2010 – 2012",
        start_date=ExactDate(raw="2010", year=2010),
        end_date=ExactDate(raw="2012", year=2012),
    )
    right = _role(
        "ev_" + "2" * 16,
        "doc_" + "b" * 16,
        statement="Analyst | Acme 2018 – 2020",
        source_excerpt="Analyst | Acme 2018 – 2020",
        title="Analyst",
        start_date=ExactDate(raw="2018", year=2018),
        end_date=ExactDate(raw="2020", year=2020),
    )
    assert detect_conflicts([left, right]) == []


def test_contained_employer_names_are_shown():
    left = _role(
        "ev_" + "1" * 16,
        "doc_" + "a" * 16,
        statement="Engineer | Google LLC 2020 – 2022",
        source_excerpt="Engineer | Google LLC 2020 – 2022",
        employer="Google LLC",
    )
    right = _role(
        "ev_" + "2" * 16,
        "doc_" + "b" * 16,
        statement="Engineer | Google 2020 – 2022",
        source_excerpt="Engineer | Google 2020 – 2022",
        employer="Google",
    )
    conflicts = detect_conflicts([left, right])
    assert [conflict.kind.value for conflict in conflicts] == ["employer"]


def test_meta_is_not_treated_as_metabase():
    left = _role(
        "ev_" + "1" * 16,
        "doc_" + "a" * 16,
        statement="Engineer | Meta 2020 – 2022",
        source_excerpt="Engineer | Meta 2020 – 2022",
        employer="Meta",
    )
    right = _role(
        "ev_" + "2" * 16,
        "doc_" + "b" * 16,
        statement="Engineer | Metabase 2020 – 2022",
        source_excerpt="Engineer | Metabase 2020 – 2022",
        employer="Metabase",
    )
    assert detect_conflicts([left, right]) == []


def test_metric_conflict_needs_the_same_claim_and_different_numbers():
    left = atom(
        evidence_id="ev_" + "1" * 16,
        source_document_id="doc_" + "a" * 16,
        statement="Reduced latency by 20%.",
        source_excerpt="Reduced latency by 20%.",
        metrics=["20%"],
    )
    right = atom(
        evidence_id="ev_" + "2" * 16,
        source_document_id="doc_" + "b" * 16,
        statement="Reduced latency by 35%.",
        source_excerpt="Reduced latency by 35%.",
        metrics=["35%"],
    )
    unrelated = atom(
        evidence_id="ev_" + "3" * 16,
        source_document_id="doc_" + "b" * 16,
        statement="Increased sales by 20%.",
        source_excerpt="Increased sales by 20%.",
        metrics=["20%"],
    )
    conflicts = detect_conflicts([left, right, unrelated])
    assert len(conflicts) == 1
    assert conflicts[0].kind.value == "metric"


def test_skill_category_conflict_is_cross_document_only():
    job = atom(
        evidence_id="ev_" + "1" * 16,
        source_document_id="doc_" + "a" * 16,
        statement="Used Python in production.",
        source_excerpt="Used Python in production.",
        skills=["Python"],
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    listed = atom(
        evidence_id="ev_" + "2" * 16,
        source_document_id="doc_" + "a" * 16,
        statement="Python",
        source_excerpt="Python",
        category=EvidenceCategory.mentioned_only,
        skills=["Python"],
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    course = atom(
        evidence_id="ev_" + "3" * 16,
        source_document_id="doc_" + "b" * 16,
        statement="Python coursework",
        source_excerpt="Python coursework",
        category=EvidenceCategory.coursework,
        skills=["Python"],
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    same_strength = atom(
        evidence_id="ev_" + "4" * 16,
        source_document_id="doc_" + "c" * 16,
        statement="Used Python on a job.",
        source_excerpt="Used Python on a job.",
        skills=["Python"],
        employer=None,
        title=None,
        start_date=None,
        end_date=None,
    )
    assert detect_conflicts([job, listed]) == []
    disagree = detect_conflicts([job, course])
    assert [conflict.kind.value for conflict in disagree] == ["skill_category"]
    assert detect_conflicts([job, same_strength]) == []
