from datetime import date

from career_copilot.models import ConfirmationState, EvidenceCategory, ExactDate
from career_copilot.years import experience_span, meets_minimum
from tests.factories import atom


def _role(evidence: str, employer: str, start: int, end: int | None, **overrides: object):
    payload: dict[str, object] = {
        "evidence_id": evidence,
        "statement": f"Engineer | {employer} {start}",
        "source_excerpt": f"Engineer | {employer} {start}",
        "employer": employer,
        "title": "Engineer",
        "confirmation": ConfirmationState.confirmed,
        "category": EvidenceCategory.used_in_job,
        "start_date": ExactDate(raw=str(start), year=start),
        "end_date": None if end is None else ExactDate(raw=str(end), year=end),
    }
    payload.update(overrides)
    return atom(**payload)


def test_overlapping_roles_are_merged():
    first = _role("ev_" + "a" * 16, "North", 2018, 2021)
    second = _role("ev_" + "b" * 16, "South", 2020, 2022)
    span = experience_span([first, second], as_of=date(2026, 10, 13))
    separate = (
        experience_span([first], as_of=date(2026, 10, 13)).days
        + experience_span([second], as_of=date(2026, 10, 13)).days
    )
    assert span.intervals == ((date(2018, 1, 1), date(2022, 12, 31)),)
    assert span.days == (date(2022, 12, 31) - date(2018, 1, 1)).days + 1
    assert span.days < separate
    assert span.complete is True


def test_a_gap_stays_a_gap():
    early = _role("ev_" + "a" * 16, "North", 2010, 2012)
    later = _role("ev_" + "b" * 16, "South", 2014, 2015)
    span = experience_span([early, later], as_of=date(2026, 1, 1))
    assert span.intervals == (
        (date(2010, 1, 1), date(2012, 12, 31)),
        (date(2014, 1, 1), date(2015, 12, 31)),
    )
    assert span.days == sum((end - start).days + 1 for start, end in span.intervals)


def test_repeated_role_bullets_count_once():
    role = _role("ev_" + "a" * 16, "Acme", 2019, 2020)
    bullet = _role(
        "ev_" + "b" * 16,
        "Acme",
        2019,
        2020,
        statement="Shipped the graph.",
        source_excerpt="Shipped the graph.",
    )
    assert experience_span([role], as_of=date(2026, 1, 1)).days == experience_span(
        [role, bullet], as_of=date(2026, 1, 1)
    ).days


def test_coursework_and_unconfirmed_roles_are_ignored():
    job = _role("ev_" + "a" * 16, "Acme", 2019, 2020)
    school = _role(
        "ev_" + "b" * 16,
        "University",
        2010,
        2018,
        category=EvidenceCategory.coursework,
        title="PhD",
    )
    draft = _role("ev_" + "c" * 16, "Hidden", 2000, 2010, confirmation=ConfirmationState.extracted)
    span = experience_span([job, school, draft], as_of=date(2026, 1, 1))
    assert span.intervals == ((date(2019, 1, 1), date(2020, 12, 31)),)


def test_open_role_ends_on_as_of_and_a_future_end_is_capped():
    open_role = _role("ev_" + "a" * 16, "Acme", 2024, None)
    future = _role("ev_" + "b" * 16, "Other", 2020, 2030)
    as_of = date(2026, 10, 13)
    assert experience_span([open_role], as_of=as_of).intervals == ((date(2024, 1, 1), as_of),)
    assert experience_span([future], as_of=as_of).intervals == ((date(2020, 1, 1), as_of),)


def test_an_undated_role_marks_the_span_incomplete():
    dated = _role("ev_" + "a" * 16, "Acme", 2020, 2021)
    undated = _role(
        "ev_" + "b" * 16,
        "Other",
        2022,
        2023,
        start_date=None,
        end_date=None,
        statement="Engineer | Other",
        source_excerpt="Engineer | Other",
    )
    span = experience_span([dated, undated], as_of=date(2026, 1, 1))
    assert span.complete is False
    assert span.intervals == ((date(2020, 1, 1), date(2021, 12, 31)),)


def test_minimum_years_accepts_a_leap_day_span():
    start = date(2018, 1, 1)
    end = date(2022, 12, 31)
    days = (end - start).days + 1
    assert meets_minimum(days, 5)
    assert not meets_minimum(days, 6)
