"""The job list keeps up to five open matches at 50 percent or above."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from career_copilot.errors import CareerCopilotError
from career_copilot.job_list import (
    DEADLINE_NOTE,
    EMPTY_REASON,
    FALLBACK_REASON,
    HIGH_MATCH,
    UNSCORED_NOTE,
    AssumedPoint,
    ListedJob,
    MatchPoint,
    SearchLog,
    _Opening,
    cleaned_posting,
    consider_posting,
    deadline_on,
    format_deadline,
    notes_for_display,
    posting_place,
    posting_workplace,
    drop_job,
    fetch_posting,
    fits_workplace,
    is_open,
    keep_job,
    load_log,
    load_traits,
    match_percent,
    parse_arbeitnow,
    parse_himalayas,
    parse_jobicy,
    parse_remoteok,
    parse_remotive,
    real_search,
    record_answer,
    requested_documents,
    save_log,
    save_trait,
    visible_jobs,
)
from career_copilot.roles import WorkRole, save_roles
from career_copilot.search_intent import clean_conditions, save_search


def _roles() -> list[WorkRole]:
    return [WorkRole(name="researcher", reason="The work is research.")]


def _remote():
    return clean_conditions(["Entry level"], ["Part-time", "Full-time"], ["Remote"], "Netherlands", "Utrecht")


def _local():
    return clean_conditions(["Entry level"], ["Full-time"], ["Local", "Hybrid"], "Netherlands", "Utrecht")


def _high(_cv: str, _posting: str) -> list[MatchPoint]:
    return [MatchPoint(text="Research", status="match")]


def _low(_cv: str, _posting: str) -> list[MatchPoint]:
    return [MatchPoint(text="Research", status="match"), MatchPoint(text="License", status="no_match")]


def _remotive_job(index: int, **extra: object) -> dict[str, object]:
    item: dict[str, object] = {
        "title": f"Designer {index}",
        "company_name": f"Lab {index}",
        "url": f"https://remotive.com/remote-jobs/job-{index}",
        "description": "Design work.",
        "candidate_required_location": "Worldwide",
    }
    item.update(extra)
    return item


def _feeds(remotive: object, arbeitnow: object | None = None, extra: dict | None = None) -> dict:
    calls: list[str] = []
    feeds = {
        "Remotive": lambda: calls.append("Remotive") or remotive,
        "Arbeitnow": lambda: calls.append("Arbeitnow") or (arbeitnow if arbeitnow is not None else {"data": []}),
    }
    if extra:
        for name, payload in extra.items():
            feeds[name] = lambda payload=payload, name=name: calls.append(name) or payload
    feeds["calls"] = calls  # type: ignore[assignment]
    return feeds


def test_match_percent_leaves_unknown_out():
    high = match_percent(
        [MatchPoint(text="Research", status="match"), MatchPoint(text="Unclear", status="unknown")]
    )
    low = match_percent(
        [MatchPoint(text="Research", status="match"), MatchPoint(text="License", status="no_match")]
    )
    assert high == 100
    assert low == 50
    assert low < HIGH_MATCH


def test_workplace_filter_and_open_jobs():
    remote = _remote()
    local = _local()
    portugal = clean_conditions(["Entry level"], ["Full-time"], ["Local"], "Portugal", "Lisbon")
    assert fits_workplace("Berlin", "Remote", remote)
    assert fits_workplace("", "Remote", remote)
    assert not fits_workplace("Berlin", "Local", local)
    assert fits_workplace("Utrecht, Netherlands", "Local", local)
    assert fits_workplace("Utrecht", "Hybrid", local)
    assert fits_workplace("Amsterdam", "Local", local)
    assert not fits_workplace("Utrecht", "Local", remote)
    assert fits_workplace("Lisbon", "Local", portugal)
    assert fits_workplace("Porto, Portugal", "Local", portugal)
    assert not fits_workplace("Madrid", "Local", portugal)
    assert fits_workplace("", "", local)
    assert fits_workplace("", "Local", local)
    assert not fits_workplace("Berlin", "", local)
    assert is_open(None, date(2026, 10, 20))
    assert not is_open(date(2026, 10, 1), date(2026, 10, 20))


def test_parsers_read_each_feed_shape():
    remotive = parse_remotive({"jobs": [_remotive_job(1, description="<p>Design work.</p>")]})
    assert remotive[0].source == "Remotive"
    assert remotive[0].workplace == "Remote"
    assert remotive[0].text == "Design work."
    marked = parse_remotive(
        {
            "jobs": [
                _remotive_job(
                    2,
                    description="&lt;h2&gt;The role&lt;/h2&gt;&lt;p&gt;Build systems.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
                )
            ]
        }
    )
    assert "## The role" in marked[0].text
    assert "Build systems." in marked[0].text
    assert "- Python" in marked[0].text
    assert "<" not in marked[0].text
    local = parse_arbeitnow(
        {"data": [{"title": "Assistant", "company_name": "Office", "url": "https://www.arbeitnow.com/jobs/one", "description": "On site.", "remote": False, "location": "Utrecht"}]}
    )
    assert local[0].workplace == "Local"
    assert local[0].location == "Utrecht"
    hybrid = parse_arbeitnow(
        {"data": [{"title": "Assistant", "company_name": "Office", "url": "https://www.arbeitnow.com/jobs/two", "description": "Hybrid week.", "remote": False, "location": "Utrecht"}]}
    )
    assert hybrid[0].workplace == "Hybrid"
    unnamed = parse_arbeitnow(
        {
            "data": [
                {
                    "title": "Assistant",
                    "company_name": "Office",
                    "url": "https://www.arbeitnow.com/jobs/three",
                    "description": "Work from anywhere.",
                    "remote": False,
                    "location": "Amsterdam",
                }
            ]
        }
    )
    assert unnamed[0].workplace == ""
    assert unnamed[0].location == "Amsterdam"
    himalayas = parse_himalayas(
        {"jobs": [{"title": "Researcher", "companyName": "Lab", "applicationLink": "https://himalayas.app/jobs/one", "description": "Work", "locationRestrictions": ["Netherlands"], "expiryDate": "2020-01-01"}]}
    )
    assert himalayas[0].closes_on == date(2020, 1, 1)
    jobicy = parse_jobicy(
        {"jobs": [{"jobTitle": "Researcher", "companyName": "Lab", "url": "https://jobicy.com/jobs/one", "jobDescription": "Work", "jobGeo": "Netherlands"}]}
    )
    assert jobicy[0].source == "Jobicy"
    remoteok = parse_remoteok([
        {"legal": "Please link back."},
        {"position": "Researcher", "company": "Lab", "url": "https://remoteok.com/remote-jobs/one", "description": "Work", "location": ""},
    ])
    assert len(remoteok) == 1
    assert remoteok[0].source == "Remote OK"


def test_real_search_scores_five_and_drops_a_low_match(tmp_path: Path):
    calls: list[str] = []

    def score(cv_text: str, posting: str) -> list[MatchPoint]:
        calls.append(cv_text)
        if "FORTY" in posting:
            return _points(2, 3)
        if "LOW MATCH" in posting:
            return _low(cv_text, posting)
        return _high(cv_text, posting)

    jobs = [_remotive_job(index) for index in range(6)]
    jobs[0]["description"] = "LOW MATCH design work."
    jobs[1]["description"] = "FORTY design work."
    jobs.append(_remotive_job(9, url="https://remotive.com/remote-jobs/closed", closing_date="2020-01-01"))
    feeds = _feeds({"jobs": jobs})
    path = tmp_path / "jobs.json"
    outcome = real_search(
        "SECRET CURRICULUM",
        _roles(),
        _remote(),
        score=score,
        feeds={name: feeds[name] for name in ("Remotive", "Arbeitnow")},
        today=date(2026, 10, 20),
        path=path,
        require_grok=False,
    )
    assert len(calls) == 6
    assert outcome.found_count == 5
    assert outcome.high_count == 4
    assert outcome.below_count == 1
    assert outcome.latest[0].match_percent == 100
    assert outcome.latest[-1].match_percent == 50
    saved = path.read_text(encoding="utf-8")
    assert "SECRET CURRICULUM" not in saved
    assert "Target level" not in saved
    assert "LOW MATCH" in saved
    assert "FORTY" not in saved
    steps: list[str] = []
    real_search(
        "SECRET CURRICULUM",
        _roles(),
        _remote(),
        score=score,
        feeds={name: feeds[name] for name in ("Remotive", "Arbeitnow")},
        today=date(2026, 10, 20),
        path=tmp_path / "feed-progress.json",
        require_grok=False,
        progress=steps.append,
    )
    assert steps == []


def test_a_second_search_replaces_unkept_jobs_and_skips_a_removed_link(tmp_path: Path):
    path = tmp_path / "jobs.json"
    first = real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        feeds={name: _feeds({"jobs": [_remotive_job(1)]})[name] for name in ("Remotive", "Arbeitnow")},
        path=path,
        require_grok=False,
    )
    keep_job(first.latest[0], path)
    drop_target = ListedJob(
        title="Other",
        company="Other Lab",
        url="https://remotive.com/remote-jobs/job-2",
        source="Remotive",
        match_percent=80,
        description="Other",
    )
    log = load_log(path)
    log.latest.append(drop_target)
    save_log(log, path)
    drop_job(drop_target, path)
    outcome = real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        feeds={
            "Remotive": lambda: {"jobs": [_remotive_job(1), _remotive_job(2), _remotive_job(3)]},
            "Arbeitnow": lambda: {"data": []},
        },
        path=path,
        require_grok=False,
    )
    assert [job.url for job in outcome.kept] == ["https://remotive.com/remote-jobs/job-1"]
    assert [job.url for job in outcome.latest] == ["https://remotive.com/remote-jobs/job-3"]
    assert "https://remotive.com/remote-jobs/job-2" in outcome.skipped_urls
    assert len(visible_jobs(outcome)) == 2


def test_thin_feeds_read_the_next_remote_page(tmp_path: Path):
    calls: list[str] = []

    def feed(name: str, payload: object):
        def fetch() -> object:
            calls.append(name)
            return payload

        return fetch

    himalayas = {
        "jobs": [
            {
                "title": f"Researcher {index}",
                "companyName": "Hill",
                "applicationLink": f"https://himalayas.app/jobs/{index}",
                "description": "Remote research.",
                "locationRestrictions": ["Anywhere"],
            }
            for index in range(5)
        ]
    }
    real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        feeds={
            "Remotive": feed("Remotive", {"jobs": [_remotive_job(1)]}),
            "Arbeitnow": feed("Arbeitnow", {"data": []}),
            "Himalayas": feed("Himalayas", himalayas),
            "Jobicy": feed("Jobicy", {"jobs": []}),
            "Remote OK": feed("Remote OK", []),
        },
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert calls == ["Remotive", "Arbeitnow", "Himalayas"]


def test_a_full_primary_page_does_not_read_extra_feeds(tmp_path: Path):
    calls: list[str] = []

    def feed(name: str, payload: object):
        def fetch() -> object:
            calls.append(name)
            return payload

        return fetch

    real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        feeds={
            "Remotive": feed("Remotive", {"jobs": [_remotive_job(index) for index in range(5)]}),
            "Arbeitnow": feed("Arbeitnow", {"data": []}),
            "Himalayas": feed("Himalayas", {"jobs": []}),
        },
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert calls == ["Remotive", "Arbeitnow"]


def test_no_match_explains_why(tmp_path: Path):
    path = tmp_path / "jobs.json"
    low = real_search(
        "notes",
        _roles(),
        _remote(),
        score=_low,
        feeds={"Remotive": lambda: {"jobs": [_remotive_job(1)]}, "Arbeitnow": lambda: {"data": []}},
        path=path,
        require_grok=False,
    )
    assert len(low.latest) == 1
    assert low.latest[0].match_percent == 50
    assert low.high_count == 0
    assert low.reason == ""
    place = real_search(
        "notes",
        _roles(),
        _local(),
        score=_high,
        feeds={"Remotive": lambda: {"jobs": [_remotive_job(1)]}, "Arbeitnow": lambda: {"data": []}},
        path=path,
        require_grok=False,
    )
    assert place.reason == "No open listing fit the saved search conditions."
    down = real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        feeds={
            "Remotive": lambda: (_ for _ in ()).throw(CareerCopilotError("down")),
            "Arbeitnow": lambda: (_ for _ in ()).throw(CareerCopilotError("down")),
        },
        path=path,
        require_grok=False,
    )
    assert down.reason == "The job feeds did not respond."
    assert "notes" not in path.read_text(encoding="utf-8")


def test_a_paste_uses_the_same_rules_and_does_not_read_a_feed(tmp_path: Path):
    path = tmp_path / "jobs.json"
    calls: list[str] = []

    def score(cv_text: str, posting: str) -> list[MatchPoint]:
        calls.append(posting)
        return _high(cv_text, posting)

    outcome = consider_posting(
        "SECRET CURRICULUM",
        "Researcher\nRemote research role.\nhttps://example.com/jobs/one",
        _roles(),
        _remote(),
        score=score,
        path=path,
        require_grok=False,
    )
    assert outcome.note == ""
    assert outcome.job is not None
    assert outcome.job.source == "Pasted"
    assert outcome.job.url == "https://example.com/jobs/one"
    assert outcome.job.match_percent == 100
    assert "SECRET CURRICULUM" not in path.read_text(encoding="utf-8")
    scored_calls = len(calls)
    again = consider_posting(
        "SECRET CURRICULUM",
        "Researcher\nRemote research role.\nhttps://example.com/jobs/one",
        _roles(),
        _remote(),
        score=score,
        path=path,
        require_grok=False,
    )
    assert again.note == "This posting is already on the list."
    assert again.job is not None
    assert again.job.match_percent == 100
    assert len(calls) == scored_calls
    low = consider_posting(
        "notes",
        "Assistant\nRemote assistant role in Utrecht.",
        _roles(),
        _remote(),
        score=_low,
        path=path,
        require_grok=False,
    )
    assert low.note == ""
    assert low.job is not None
    assert low.job.match_percent == 50
    assert any(job.match_percent == 50 for job in load_log(path).latest)
    city = consider_posting(
        "notes",
        "Office assistant\nOn-site work in Berlin, Germany.",
        _roles(),
        _local(),
        score=_high,
        path=path,
        require_grok=False,
    )
    assert city.note == ""
    assert city.job is not None
    assert city.job.match_percent == 50
    assert "The place is outside the saved country." in city.job.missing
    assert "The posting does not mention the employment type." in city.job.notes
    assert "The posting does not mention the experience level." in city.job.notes
    assert calls and "Target level" in calls[0]


def test_an_older_job_log_still_loads(tmp_path: Path):
    path = tmp_path / "jobs.json"
    path.write_text('{"real_searches": 1, "results": []}\n', encoding="utf-8")
    log = load_log(path)
    assert log.latest == []
    assert log.kept == []
    assert log.reason == ""


def test_selected_options_are_alternatives_and_silence_is_kept(tmp_path: Path):
    conditions = clean_conditions(
        ["Entry level", "Senior"],
        ["Full-time", "Part-time"],
        ["Remote", "Local", "Hybrid"],
        "Netherlands",
        "Amsterdam",
    )
    outcome = real_search(
        "notes",
        _roles(),
        conditions,
        score=_high,
        feeds={
            "Remotive": lambda: {"jobs": [_remotive_job(1, description="Full-time senior design.")]},
            "Arbeitnow": lambda: {
                "data": [
                    {
                        "title": "Part-time assistant",
                        "company_name": "Office",
                        "url": "https://www.arbeitnow.com/jobs/part",
                        "description": "Part-time on site.",
                        "remote": False,
                        "location": "Amsterdam",
                    },
                    {
                        "title": "Berlin assistant",
                        "company_name": "Office",
                        "url": "https://www.arbeitnow.com/jobs/berlin",
                        "description": "Full-time on site.",
                        "remote": False,
                        "location": "Berlin",
                    },
                    {
                        "title": "Quiet assistant",
                        "company_name": "Office",
                        "url": "https://www.arbeitnow.com/jobs/quiet",
                        "description": "Help the team.",
                        "remote": False,
                        "location": "Amsterdam",
                    },
                    {
                        "title": "Intern",
                        "company_name": "Office",
                        "url": "https://www.arbeitnow.com/jobs/intern",
                        "description": "Internship on site.",
                        "remote": False,
                        "location": "Amsterdam",
                    },
                ]
            },
        },
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    titles = [job.title for job in outcome.latest]
    assert "Designer 1" in titles
    assert "Part-time assistant" in titles
    assert "Quiet assistant" in titles
    assert "Berlin assistant" not in titles
    assert "Intern" not in titles
    quiet = next(job for job in outcome.latest if job.title == "Quiet assistant")
    assert quiet.notes == [
        "The posting does not mention the workplace.",
        "The posting does not mention the employment type.",
        "The posting does not mention the experience level.",
    ]
    part_only = clean_conditions(["Entry level"], ["Part-time"], ["Local"], "Netherlands", "Amsterdam")
    kept = real_search(
        "notes",
        _roles(),
        part_only,
        score=_high,
        feeds={
            "Remotive": lambda: {"jobs": []},
            "Arbeitnow": lambda: {
                "data": [
                    {
                        "title": "Either assistant",
                        "company_name": "Office",
                        "url": "https://www.arbeitnow.com/jobs/either",
                        "description": "Full-time or part-time on site.",
                        "remote": False,
                        "location": "Amsterdam",
                    }
                ]
            },
        },
        path=tmp_path / "either.json",
        require_grok=False,
    )
    assert [job.title for job in kept.latest] == ["Either assistant"]


def test_requested_documents_always_include_the_cv():
    assert requested_documents("Remote research role.") == ["CV"]
    assert requested_documents("Send a cover letter, a portfolio, and a transcript.") == [
        "CV",
        "Cover letter",
        "Portfolio",
        "Transcript",
    ]
    assert requested_documents("A letter of motivation is welcome.") == ["CV", "Cover letter"]
    assert requested_documents("Bring a writing sample, a certificate, and an ID.") == [
        "CV",
        "Writing sample",
        "Certificate",
        "ID",
    ]


def test_a_lone_link_is_read_without_sending_the_cv(tmp_path: Path):
    path = tmp_path / "jobs.json"
    seen: list[str] = []

    def fetch(url: str) -> str:
        seen.append(url)
        return "Researcher\n" + ("Remote research role in Utrecht. " * 12)

    def score(cv_text: str, posting: str) -> list[MatchPoint]:
        seen.append(cv_text)
        assert "Researcher" in posting
        return _high(cv_text, posting)

    outcome = consider_posting(
        "SECRET CURRICULUM",
        "https://example.com/jobs/one",
        _roles(),
        _remote(),
        score=score,
        path=path,
        require_grok=False,
        fetch=fetch,
    )
    assert seen[0] == "https://example.com/jobs/one"
    assert "SECRET CURRICULUM" not in seen[0]
    assert outcome.note == ""
    assert outcome.job is not None
    assert outcome.job.url == "https://example.com/jobs/one"
    assert "SECRET CURRICULUM" not in path.read_text(encoding="utf-8")

    def fail_fetch(_url: str) -> str:
        raise AssertionError("fetched")

    linkedin = consider_posting(
        "notes",
        "https://www.linkedin.com/jobs/view/1",
        _roles(),
        _remote(),
        score=_high,
        path=path,
        require_grok=False,
        fetch=fail_fetch,
    )
    assert linkedin.note == "LinkedIn stays paste-only. Paste the posting text."
    assert linkedin.job is None
    private = consider_posting(
        "notes",
        "https://127.0.0.1/jobs/one",
        _roles(),
        _remote(),
        score=_high,
        path=path,
        require_grok=False,
    )
    assert private.note == "This page did not return the posting. Paste the text."
    assert private.job is None


def test_fetch_posting_reads_a_public_page_and_refuses_a_login_wall(monkeypatch: pytest.MonkeyPatch):
    class _Page:
        def __init__(self, body: bytes, url: str):
            self._body = body
            self._url = url

        def read(self, _limit: int) -> bytes:
            return self._body

        def geturl(self) -> str:
            return self._url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout=30):
        assert timeout == 30
        assert request.get_header("User-agent") == "career-copilot"
        body = b"<h2>The role</h2><p>" + b"Research and teaching duties. " * 20 + b"</p>"
        return _Page(body, request.full_url)

    monkeypatch.setattr("career_copilot.job_list.urllib.request.urlopen", urlopen)
    text = fetch_posting("https://example.com/jobs/one")
    assert "The role" in text
    assert "Research and teaching duties." in text
    assert "<" not in text

    def gated(request, timeout=30):
        return _Page(b"<p>Sign in to continue. " + b"Please sign in. " * 40 + b"</p>", request.full_url)

    monkeypatch.setattr("career_copilot.job_list.urllib.request.urlopen", gated)
    with pytest.raises(CareerCopilotError, match="Paste the text"):
        fetch_posting("https://example.com/jobs/one")


def test_jobs_page_has_no_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(_remote(), tmp_path / ".career-copilot" / "search.json")
    save_log(
        SearchLog(
            latest=[
                ListedJob(
                    title="Researcher",
                    company="Lab",
                    url="https://remotive.com/remote-jobs/one",
                    source="Remotive",
                    match_percent=80,
                    description="Remote research.",
                    covered=["Research"],
                    partial=["Related research"],
                    missing=["A license"],
                    notes=["The posting does not mention the employment type."],
                )
            ],
            found_count=1,
        ),
        tmp_path / ".career-copilot" / "jobs.json",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    visible = "\n".join(
        str(item.value)
        for item in list(app.table) + list(app.markdown) + list(app.caption) + list(app.subheader)
    )
    labels = [item.label for item in app.button]
    assert "Mock" not in visible
    assert "of 2" not in visible
    assert "Researcher" in visible
    assert "https://remotive.com/remote-jobs/one" in visible
    assert "reached 70%" not in visible
    assert "fell short" not in visible
    assert "Possible matches" in visible
    assert "What partly matches" not in visible
    assert "Assumed" not in visible
    assert "Related research" in visible
    assert "Read the CV on the User page first." in visible
    assert "Remotive" in visible
    assert "Keep this job" in labels
    assert "Remove this job" in labels
    assert "+" not in labels
    assert "−" not in labels
    assert "Strengths" not in visible
    assert "Gaps" not in visible
    assert "Move to gaps" not in labels
    assert "Automatic search" in labels
    assert "Manual search" in labels
    assert "The posting does not mention the employment type." in visible
    assert "What matches" in visible
    assert "What is missing" in visible
    assert "Documents" in visible
    assert "Only the CV is required." in visible
    assert "What the experience covers" not in visible
    assert "Compare" not in labels
    assert "Match analysis" not in labels
    assert "Search again" not in labels
    assert "Check a job you found" not in labels
    assert "SECRET" not in visible
    next(item for item in app.button if item.label == "Manual search").click().run()
    assert not app.exception, app.exception
    assert any(item.label == "Match analysis" for item in app.button)
    assert [item.label for item in app.text_area] == ["Skills", "Other notes", "Job posting or link"]
    assert "Profile" in visible
    assert "The work is research." in visible


def test_an_unscored_web_job_says_not_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(_remote(), tmp_path / ".career-copilot" / "search.json")
    save_log(
        SearchLog(
            latest=[
                ListedJob(
                    title="Knowledge engineer",
                    company="Lab",
                    url="https://jobs.example.org/open",
                    source="Web",
                    match_percent=0,
                    description="Remote Neo4j researcher role.",
                    notes=[UNSCORED_NOTE],
                )
            ],
            found_count=1,
        ),
        tmp_path / ".career-copilot" / "jobs.json",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    visible = "\n".join(
        str(item.value)
        for item in list(app.table) + list(app.markdown) + list(app.caption) + list(app.subheader)
    )
    assert "Not scored" in visible
    assert "Match: 0%" not in visible
    assert "What matches" not in visible
    assert "What is missing" not in visible
    assert UNSCORED_NOTE in visible
    assert "https://jobs.example.org/open" in visible


def test_jobs_page_searches_when_automatic_search_is_clicked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    calls: list[str] = []
    seen: dict[str, object] = {}

    def fake_search(cv_text: str, roles, conditions, **kwargs):
        calls.append(cv_text)
        seen["notes"] = conditions.notes
        seen["programming"] = conditions.programming
        progress = kwargs.get("progress")
        if progress is not None:
            progress("Searching.")
            progress("Opening page 1 of 1.")
            progress("Scoring 1 of 1.")
        return SearchLog(reason="No open listing was found.")

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("career_copilot.job_list.real_search", fake_search)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(
        clean_conditions(
            ["Entry level"],
            ["Part-time", "Full-time"],
            ["Remote"],
            "Netherlands",
            "Utrecht",
            programming="Light",
            outside_academia="Yes",
        ),
        tmp_path / ".career-copilot" / "search.json",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.session_state["cv_text"] = "SESSION CV"
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    assert calls == []
    next(item for item in app.text_area if item.label == "Skills").set_value("Neo4j\nneo4j")
    next(item for item in app.text_area if item.label == "Other notes").set_value(
        "  Remote preferred.  \n\n  Light programming only.  "
    )
    next(item for item in app.button if item.label == "Automatic search").click().run()
    assert not app.exception, app.exception
    assert calls == ["SESSION CV"]
    assert seen["notes"] == "Remote preferred.\nLight programming only."
    assert seen["programming"] == "Light"
    saved = (tmp_path / ".career-copilot" / "search.json").read_text(encoding="utf-8")
    assert "Remote preferred." in saved
    assert '"programming": "Light"' in saved
    assert "SESSION CV" not in saved
    skills = (tmp_path / ".career-copilot" / "skills.json").read_text(encoding="utf-8")
    assert skills.count("Neo4j") == 1
    assert "neo4j" not in skills
    visible = "\n".join(str(item.value) for item in list(app.markdown) + list(app.caption) + list(app.success))
    assert "SESSION CV" not in visible


def test_manual_search_shows_the_match_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from career_copilot.job_list import PasteOutcome

    def fail_search(*_args, **_kwargs):
        raise AssertionError("search started by itself")

    def fake_consider(cv_text: str, posting: str, roles, conditions, **_kwargs):
        assert cv_text == "SESSION CV"
        assert "Museum researcher" in posting
        return PasteOutcome(
            job=ListedJob(
                title="Museum researcher",
                company="Pasted",
                url="",
                source="Pasted",
                match_percent=64,
                description="FULL POSTING",
                covered=["Structured research"],
                partial=["Related catalog work"],
                missing=["A museum license"],
            )
        )

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("career_copilot.job_list.real_search", fail_search)
    monkeypatch.setattr("career_copilot.job_list.consider_posting", fake_consider)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(_remote(), tmp_path / ".career-copilot" / "search.json")
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.session_state["cv_text"] = "SESSION CV"
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    next(item for item in app.button if item.label == "Manual search").click().run()
    next(item for item in app.text_area if item.label == "Job posting or link").set_value(
        "Museum researcher\nHybrid research post."
    ).run()
    next(item for item in app.button if item.label == "Match analysis").click().run()
    assert not app.exception, app.exception
    visible = "\n".join(
        str(item.value)
        for item in list(app.markdown) + list(app.caption) + list(app.success) + list(app.subheader)
    )
    assert "Match: 64%" in visible
    assert "What matches" in visible
    assert "Possible matches" in visible
    assert "What partly matches" not in visible
    assert "Assumed" not in visible
    assert "What is missing" in visible
    assert "Structured research" in visible
    assert "Related catalog work" in visible
    assert "A museum license" in visible
    assert "FULL POSTING" in visible
    assert "Documents" in visible
    assert "Only the CV is required." in visible
    assert "SESSION CV" not in visible


def _points(match: int, missing: int, partial: int = 0) -> list[MatchPoint]:
    points = [MatchPoint(text=f"Has {index}", status="match") for index in range(match)]
    points.extend(MatchPoint(text=f"Half {index}", status="partial") for index in range(partial))
    points.extend(MatchPoint(text=f"Lacks {index}", status="no_match") for index in range(missing))
    return points


def test_a_half_match_is_separate_and_an_interview_line_is_unknown(tmp_path: Path):
    from career_copilot.job_list import _mentioned_employment

    def score(_cv: str, _posting: str) -> list[MatchPoint]:
        return [
            MatchPoint(text="Python", status="match"),
            MatchPoint(text="Experience writing tests, rubrics, or evaluation criteria", status="partial"),
            MatchPoint(text="Can explain a change clearly and concisely", status="match"),
        ]

    outcome = consider_posting(
        "notes",
        "Remote Python role.\nExplain a change clearly.",
        _roles(),
        _remote(),
        score=score,
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert outcome.job is not None
    assert outcome.job.covered == ["Python"]
    assert outcome.job.partial == ["Experience writing tests, rubrics, or evaluation criteria"]
    assert "Can explain a change clearly and concisely" not in outcome.job.covered
    assert "Can explain a change clearly and concisely" not in outcome.job.partial
    assert outcome.job.match_percent == 75
    assert _mentioned_employment("You will sign a contract with the company.") == []
    assert _mentioned_employment("Paid US$1,000 per approved task.") == ["Contract"]
    assert _mentioned_employment("This is a freelance month contract.") == ["Contract"]


def test_a_contract_posting_is_named_and_a_low_score_stays_off_the_list(tmp_path: Path):
    path = tmp_path / "jobs.json"
    kept = consider_posting(
        "notes",
        "Remote researcher.\nAbout 10 hours a week and US$1,000 per approved task.",
        _roles(),
        clean_conditions(["Entry level"], ["Full-time"], ["Remote"], "", ""),
        score=_high,
        path=path,
        require_grok=False,
    )
    assert kept.job is not None
    assert "The posting does not mention the employment type." not in kept.job.notes
    assert any("employment type is Contract" in line for line in kept.job.missing)
    low = real_search(
        "notes",
        _roles(),
        _remote(),
        score=lambda _cv, _posting: _points(2, 3),
        feeds={"Remotive": lambda: {"jobs": [_remotive_job(1)]}, "Arbeitnow": lambda: {"data": []}},
        path=path,
        require_grok=False,
    )
    assert low.latest == []
    assert low.found_count == 0
    assert low.below_count == 1
    assert low.reason == "1 listing was scored and it did not reach 50%."
    assert visible_jobs(low) == []
    held = ListedJob(
        title="Kept low",
        company="Lab",
        url="https://example.com/kept",
        source="Web",
        match_percent=40,
        description="Kept.",
    )
    assert visible_jobs(SearchLog(latest=[held])) == []
    assert visible_jobs(SearchLog(kept=[held])) == [held]


def test_sixty_percent_stays_and_a_senior_posting_needs_seventy_when_senior_was_not_chosen(tmp_path: Path):
    entry = clean_conditions(["Entry level"], [], ["Remote"], "", "")
    senior_job = _remotive_job(1, title="Senior Engineer", description="Remote senior design work.")
    dropped = real_search(
        "notes",
        _roles(),
        entry,
        score=lambda _cv, _posting: _points(3, 2),
        feeds={"Remotive": lambda: {"jobs": [senior_job]}, "Arbeitnow": lambda: {"data": []}},
        path=tmp_path / "dropped.json",
        require_grok=False,
    )
    assert dropped.found_count == 0
    assert dropped.below_count == 1
    kept = real_search(
        "notes",
        _roles(),
        entry,
        score=lambda _cv, _posting: _points(4, 1),
        feeds={"Remotive": lambda: {"jobs": [senior_job]}, "Arbeitnow": lambda: {"data": []}},
        path=tmp_path / "kept.json",
        require_grok=False,
    )
    assert kept.latest[0].match_percent == 80
    blank = clean_conditions([], [], ["Remote"], "", "")
    opened = real_search(
        "notes",
        _roles(),
        blank,
        score=lambda _cv, _posting: _points(3, 2),
        feeds={"Remotive": lambda: {"jobs": [senior_job]}, "Arbeitnow": lambda: {"data": []}},
        path=tmp_path / "blank.json",
        require_grok=False,
    )
    assert opened.latest[0].match_percent == 60
    contract = real_search(
        "notes",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=_high,
        feeds={
            "Remotive": lambda: {"jobs": [_remotive_job(2, description="Freelance remote research work.")]},
            "Arbeitnow": lambda: {"data": []},
        },
        path=tmp_path / "contract.json",
        require_grok=False,
    )
    assert [job.title for job in contract.latest] == ["Designer 2"]
    assert contract.latest[0].missing == []


def test_the_first_open_page_is_returned_and_a_dead_link_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from career_copilot.job_list import _CALL_TIMEOUT_SECONDS, _query_prompt, _short_queries

    page = "Remote researcher role in Utrecht. " * 12
    fetched: list[str] = []
    calls: list[str] = []

    def links(query: str, *, skipped=None, timeout_seconds: int = _CALL_TIMEOUT_SECONDS):
        del skipped
        calls.append(query)
        assert timeout_seconds == 60
        assert "SECRET" not in query
        return [
            {"title": "Ignored", "company": "In", "url": "https://www.linkedin.com/jobs/view/1"},
            {"title": "Closed", "company": "Lab", "url": "https://jobs.example.org/dead"},
            {"title": "Loop", "company": "Lab", "url": "https://127.0.0.1/jobs/secret"},
            {"title": "Researcher", "company": "Lab", "url": "https://jobs.example.org/one"},
            {"title": "Later", "company": "Lab", "url": "https://jobs.example.org/two"},
        ]

    def fetch(url: str) -> str:
        fetched.append(url)
        if "dead" in url or "linkedin" in url or url.startswith("https://127.0.0.1"):
            raise CareerCopilotError("This page did not return the posting. Paste the text.")
        return page + " Guessed percent 90 must be ignored."

    monkeypatch.setattr("career_copilot.job_list._links_for_query", links)
    monkeypatch.setattr("career_copilot.job_list.fetch_posting", fetch)
    scored: list[str] = []
    outcome = real_search(
        "SECRET CURRICULUM researcher",
        _roles(),
        _remote(),
        score=lambda _cv, _posting: scored.append("scored") or _points(1, 1),
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        progress=scored.append,
    )
    assert calls and len(calls) == 1
    assert scored == ["Searching with grok-4.7-build-fast.", "Opening the page."]
    assert fetched == [
        "https://www.linkedin.com/jobs/view/1",
        "https://jobs.example.org/dead",
        "https://127.0.0.1/jobs/secret",
        "https://jobs.example.org/one",
    ]
    assert outcome.latest[0].url == "https://jobs.example.org/one"
    assert outcome.latest[0].source == "Web"
    assert outcome.latest[0].match_percent == 0
    assert outcome.latest[0].notes == [UNSCORED_NOTE]
    assert outcome.reason == ""
    assert "SECRET CURRICULUM" not in (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert "90" not in outcome.latest[0].notes
    prompt = _query_prompt(_short_queries(_roles(), ["Neo4j"], clean_conditions([], [], [], "", ""))[0])
    assert "Neo4j" in prompt
    assert "Use one web search" in prompt
    assert "SECRET" not in prompt
    assert "site:linkedin" not in prompt
    assert "2026" not in prompt
    query_line = next(line for line in prompt.splitlines() if line.startswith("Query:"))
    assert "Remote" not in query_line
    assert "Amsterdam" not in query_line
    empty = real_search(
        "notes",
        _roles(),
        _remote(),
        score=_high,
        discover=lambda: [],
        path=tmp_path / "empty.json",
        require_grok=False,
    )
    assert empty.reason == "Nada foi encontrado."
    assert empty.found_count == 0


def test_notes_guide_the_search_and_do_not_add_a_hard_gap(tmp_path: Path):
    from career_copilot.job_list import _hard_languages, _query_prompt, _scoring_text, _short_queries, fits_language

    dutch = "Uitstekende beheersing van de Engelse taal; Nederlands is een pré."
    assert _hard_languages(dutch) == {"English"}
    english = clean_conditions([], [], [], "", "", languages="English")
    portuguese = clean_conditions([], [], [], "", "", languages="Portuguese")
    assert fits_language(dutch, english)
    assert not fits_language(dutch, portuguese)
    assert fits_language("No language is named here. " * 5, english)
    noted = clean_conditions(
        [],
        [],
        ["Remote"],
        "",
        "",
        notes="Light programming only.\nPrefer work outside academia.",
    )
    seen: list[str] = []

    def score(_cv: str, posting: str) -> list[MatchPoint]:
        seen.append(posting)
        return _high(_cv, posting)

    heavy = consider_posting(
        "notes",
        "Software Engineer\n" + ("Build backend services for the product team. " * 8),
        _roles(),
        noted,
        score=score,
        path=tmp_path / "heavy.json",
        require_grok=False,
    )
    assert heavy.job is not None
    assert "heavy software" not in " ".join(heavy.job.missing).casefold()
    assert "academic" not in " ".join(heavy.job.missing).casefold()
    assert seen
    assert "Preferences:\nLight programming only.\nPrefer work outside academia." in seen[0]
    legacy = clean_conditions([], [], ["Remote"], "", "", programming="Light", outside_academia="Yes")
    academic = consider_posting(
        "notes",
        "Postdoctoral researcher\n" + ("Remote research on collections. " * 8),
        _roles(),
        legacy,
        score=_high,
        path=tmp_path / "academic.json",
        require_grok=False,
    )
    assert academic.job is not None
    assert "academic role" not in " ".join(academic.job.missing).casefold()
    assert "heavy software" not in " ".join(academic.job.missing).casefold()
    quiet = consider_posting(
        "notes",
        "Researcher\n" + ("Remote research role with no language line. " * 8),
        _roles(),
        english,
        score=_high,
        path=tmp_path / "quiet.json",
        require_grok=False,
    )
    assert quiet.job is not None
    assert "The posting does not mention a language." in quiet.job.notes
    prompt = _query_prompt(_short_queries(_roles(), ["Neo4j"], noted)[0])
    assert "Use one web search" in prompt
    assert "Light programming only." not in prompt
    query_line = next(line for line in prompt.splitlines() if line.startswith("Query:"))
    assert "Light programming" not in query_line
    assert "remote" in query_line.casefold()
    scored = _scoring_text("The posting body.", _roles(), noted)
    assert "Preferences:\nLight programming only." in scored
    assert "The posting body." in scored


def _web_opening(url: str, text: str, *, title: str = "Knowledge Graph Engineer", workplace: str = "Hybrid") -> _Opening:
    return _Opening(
        title=title,
        company="CGI",
        url=url,
        location="Eindhoven",
        workplace=workplace,
        employment=["Full-time"],
        text=text,
        source="Web",
    )


def test_an_open_page_is_returned_without_a_match_score(tmp_path: Path):
    page = _web_opening(
        "https://example.com/jobs/cgi",
        "Hybrid Neo4j role for a researcher in Eindhoven. " * 8,
    )
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions(["Entry level"], ["Full-time"], ["Remote"], "Netherlands", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert len(outcome.latest) == 1
    assert outcome.latest[0].match_percent == 0
    assert outcome.latest[0].missing == []
    assert outcome.latest[0].notes == [UNSCORED_NOTE]
    assert outcome.reason == ""
    assert visible_jobs(outcome) == outcome.latest


def test_a_closed_page_is_skipped_and_overlap_is_ignored(tmp_path: Path):
    closed = _web_opening(
        "https://example.com/jobs/closed",
        "Applications are closed. Neo4j researcher role. " * 8,
    )
    past = _web_opening(
        "https://example.com/jobs/past",
        "Apply by 1 January 2020. Neo4j researcher role. " * 6,
    )
    unrelated = _web_opening(
        "https://example.com/jobs/other",
        "Office administration and filing. " * 12,
        title="Administrator",
        workplace="Remote",
    )
    future = _web_opening(
        "https://example.com/jobs/open",
        "Apply by 1 January 2027. Remote Neo4j researcher role. " * 6,
        workplace="Remote",
    )
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        discover=lambda: [closed, past, unrelated, future],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert [job.url for job in outcome.latest] == ["https://example.com/jobs/other"]
    assert outcome.latest[0].notes == [UNSCORED_NOTE]


def test_nothing_opened_says_nothing_was_found(tmp_path: Path):
    empty = real_search(
        "Neo4j researcher",
        _roles(),
        _remote(),
        score=_high,
        discover=lambda: [],
        skills=["Neo4j"],
        path=tmp_path / "empty.json",
        require_grok=False,
    )
    assert empty.reason == EMPTY_REASON
    assert visible_jobs(empty) == []
    closed = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], [], "", ""),
        score=_high,
        discover=lambda: [
            _web_opening("https://example.com/jobs/closed", "Applications are closed. " * 8, workplace="Remote")
        ],
        skills=["Neo4j"],
        path=tmp_path / "closed.json",
        require_grok=False,
    )
    assert closed.latest == []
    assert closed.reason == EMPTY_REASON
    fallback = SearchLog(
        latest=[
            ListedJob(
                title="Low",
                company="Lab",
                url="https://example.com/jobs/low",
                source="Web",
                match_percent=40,
                description="Remote role.",
                covered=["Python"],
                missing=["License", "Graph", "Dutch"],
            )
        ],
        reason=FALLBACK_REASON,
        found_count=0,
    )
    assert len(visible_jobs(fallback)) == 1
    assert visible_jobs(fallback)[0].match_percent == 40


def test_the_first_open_page_stops_the_search(tmp_path: Path):
    pages = [
        _web_opening(f"https://example.com/jobs/{index}", "Remote Neo4j researcher role. " * 8, workplace="Remote")
        for index in range(4)
    ]
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        discover=lambda: pages,
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert [job.url for job in outcome.latest] == ["https://example.com/jobs/0"]
    assert len(visible_jobs(outcome)) == 1
    assert outcome.latest[0].notes == [UNSCORED_NOTE]


def test_an_assumed_point_waits_for_a_vote_and_then_counts(tmp_path: Path):
    def score(_cv: str, _posting: str) -> list[MatchPoint]:
        return [
            MatchPoint(text="Neo4j", status="match"),
            MatchPoint(text="License", status="no_match"),
            MatchPoint(text="Works independently", status="assumed", basis="PhD"),
        ]

    def paste(title: str):
        return consider_posting(
            "Neo4j researcher with a PhD",
            f"{title}\nRemote Neo4j researcher role. Works independently. " * 4,
            _roles(),
            clean_conditions([], [], ["Remote"], "", ""),
            score=score,
            path=tmp_path / "jobs.json",
            require_grok=False,
        )

    first = paste("Researcher one")
    assert first.job is not None
    assert first.job.match_percent == 50
    assert first.job.assumed[0].ask == "Works independently"
    assert first.job.assumed[0].basis == "PhD"
    assert "Works independently" not in first.job.covered
    save_trait("Works independently", "PhD", "up", tmp_path / "traits.json")
    second = paste("Researcher two")
    assert second.job is not None
    assert second.job.match_percent == 67
    assert second.job.assumed == []
    assert "Works independently" in second.job.covered
    save_trait("Works independently", "PhD", "down", tmp_path / "traits.json")
    third = paste("Researcher three")
    assert third.job is not None
    assert third.job.match_percent == 33
    assert third.job.assumed == []
    assert third.job.partial == []
    assert "Works independently" not in third.job.covered
    assert "Works independently" in third.job.missing
    assert load_traits(tmp_path / "traits.json")[0].vote == "down"
    assert "Neo4j researcher with a PhD" not in (tmp_path / "jobs.json").read_text(encoding="utf-8")


def test_a_short_deadline_on_the_next_line_is_read():
    text = "Researcher role.\nDeadline\n15 Nov \u201926"
    found = deadline_on(text)
    assert found == date(2026, 11, 15)
    assert format_deadline(found) == "15 Nov 2026"
    assert deadline_on("Apply by 15 Nov 2026. Remote researcher.") == date(2026, 11, 15)


def test_an_abbreviated_deadline_keeps_a_future_job_and_drops_a_past_one(tmp_path: Path):
    past = _web_opening(
        "https://example.com/jobs/old-deadline",
        "Deadline\n15 Nov '20. Remote Neo4j researcher role. " * 4,
        workplace="Remote",
    )
    future = _web_opening(
        "https://example.com/jobs/new-deadline",
        "Deadline\n15 Nov '26. Remote Neo4j researcher role. " * 4,
        workplace="Remote",
    )
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: [MatchPoint(text="Neo4j", status="match")],
        discover=lambda: [past, future],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert [job.url for job in outcome.latest] == ["https://example.com/jobs/new-deadline"]
    assert DEADLINE_NOTE not in outcome.latest[0].notes
    assert deadline_on(outcome.latest[0].description) == date(2026, 11, 15)


def test_the_posting_view_drops_blank_lines_and_site_chrome():
    raw = "\n".join(
        [
            "Knowledge Graph Engineer",
            "",
            "",
            "About the role",
            "",
            "We hire a researcher.",
            "",
            "Cookie settings",
            "",
            "Requirements",
            "",
            "- Neo4j",
            "",
            "Location: Eindhoven",
        ]
    )
    view = cleaned_posting(raw, title="Knowledge Graph Engineer")
    assert "Knowledge Graph Engineer" not in view
    assert "**About the role**" in view
    assert "We hire a researcher." in view
    assert "Cookie" not in view
    assert "**Requirements**" in view
    assert "- Neo4j" in view
    assert "\n\n\n" not in view
    assert posting_place(raw) == "Eindhoven"
    assert posting_workplace("Hybrid role in Eindhoven.") == "Hybrid"
    assert notes_for_display([DEADLINE_NOTE, "Kept"], "Deadline\n15 Nov '26") == ["Kept"]


def test_page_chrome_is_left_out_and_the_description_is_kept():
    from career_copilot.job_list import prepare_posting

    academic = "\n".join(
        [
            "Postdoc on Geodata Science and GeoAI — Utrecht University — AcademicTransfer - - - - -",
            "For candidates",
            "For employers",
            "For members",
            "AcademicTransfer",
            "Jobs and employers",
            "Premium employers",
            "English (EN)",
            "Nederlands (NL)",
            "X Logo",
            "Facebook Logo",
            "Postdoc on Geodata Science and GeoAI Apply spatial analytics and GeoAI approaches to improve human health.",
            "Deadline",
            "15 Nov '26",
            "Published 29 Sep '26",
            "Vacancy ID 5736",
            "Apply now25 days remaining Save",
            "Research fields Geography",
            "Weekly hours 36-40 hours per week",
            "Salary indication €3706-€5760 per month",
            "Location Princetonlaan 8a, 3584CB, Utrecht",
            "View on Google Maps",
            "Job description",
            "The researcher will build spatial tools for the team and with the lab.",
        ]
    )
    view = cleaned_posting(academic, title="Postdoc on Geodata Science and GeoAI")
    assert "For candidates" not in view
    assert "AcademicTransfer" not in view
    assert "X Logo" not in view
    assert "Apply now" not in view
    assert "Google Maps" not in view
    assert "- - -" not in view
    assert "Deadline" in view
    assert "15 Nov '26" in view
    assert "Vacancy ID 5736" in view
    assert "Salary indication" in view
    assert "Princetonlaan" in view
    assert "**Job description**" in view
    assert "spatial tools" in view
    cgi_menu = "\n".join(
        [
            "--> , before ANY other script. TrustArc auto-block core.min.js Notice ID: c1yyug",
            "Careers | CGI.com",
            "Bekijk vacatures",
            "Mijn profiel",
            "Activeer jobalerts",
            "Australia",
            "Belgium",
            "Brasil",
            "Canada",
            "France",
            "Nederlands",
            "Engels",
            "Frans",
            "Duits",
            "Vacatures Bekijk vacatures",
            "Profiel aanmaken | Inloggen",
        ]
    )
    cgi_body = (
        "Senior Linked Data & Knowledge Graph Engineer - High Tech\n"
        "Hoofdlokatie Nederland, Eindhoven\n"
        "Vacaturenummer J1026-1094\n"
        "Functiebeschrijving\n"
        "Is het jouw passie om complexe data slim met elkaar te verbinden? "
        "Je werkt aan Knowledge Graphs en Neo4j voor het team in Eindhoven. "
        "Een afgeronde opleiding met een sterke achtergrond wordt gevraagd.\n"
    )
    padding = "\n".join(["Australia", "Belgium", "Canada", "France", "Germany"] * 500)
    stored = prepare_posting(f"{cgi_menu}\n{padding}\n{cgi_body}", "Senior Linked Data & Knowledge Graph Engineer")
    assert "TrustArc" not in stored
    assert "Bekijk vacatures" not in stored
    assert "Australia" not in stored
    assert "Functiebeschrijving" in stored
    assert "J1026-1094" in stored
    assert "Eindhoven" in stored
    assert len(stored) <= 8000
    assert not stored.endswith("ee")


def test_a_repeated_menu_and_a_site_title_stay_out_of_the_posting():
    luo = "\n".join(
        [
            "Careers – Luo",
            "Home",
            "Technology",
            "Solution",
            "Compliance",
            "About Us",
            "Careers",
            "Contact Contact Navigation Menu Navigation Menu",
            "Home",
            "Technology",
            "Solution",
            "Compliance",
            "About Us",
            "Careers",
            "Spatial Data Engineer",
            "The researcher will build Neo4j tools for the team and with the lab.",
            "Requirements",
            "- Python",
            "- SQL",
            "What we offer",
            "- A pension",
            "Location: Utrecht",
        ]
    )
    view = cleaned_posting(luo, title="Spatial Data Engineer")
    assert "Home" not in view
    assert "Technology" not in view
    assert "About Us" not in view
    assert "Navigation Menu" not in view
    assert "Luo" not in view
    assert "Neo4j" in view
    assert "- Python" in view
    assert "pension" in view
    assert "Utrecht" in view
    repeated = "\n".join(
        [
            "Alpha",
            "Bravo",
            "Charlie",
            "Delta",
            "Alpha",
            "Bravo",
            "Charlie",
            "Delta",
            "The researcher will build Neo4j tools for the team and with the lab.",
            "- Python",
        ]
    )
    hidden = cleaned_posting(repeated, title="Spatial Data Engineer")
    assert "Alpha" not in hidden
    assert "Neo4j" in hidden
    assert "- Python" in hidden
    once = "\n".join(
        [
            "Alpha",
            "Bravo",
            "Charlie",
            "Delta",
            "The researcher will build Neo4j tools for the team and with the lab.",
        ]
    )
    kept = cleaned_posting(once, title="Spatial Data Engineer")
    assert "Alpha" in kept
    assert "Neo4j" in kept


def test_a_topic_menu_above_the_vacancy_stays_out_of_the_posting():
    page = "\n".join(
        [
            "Vacancy Expert Synthetic Aperture Radar at TNO in Den Haag What are you searching for?Search Geselecteerde taal: EN",
            "English",
            "Nederlands",
            "Sustainable",
            "Circular and industrial constructionCircular and industrial construction",
            "terug",
            "Biobased building materials",
            "Industrial and conceptual construction",
            "Safety of buildings",
            "Safe efficient care building",
            "Go to Circular and industrial construction",
            "Earth observation",
            "Energy in the built environmentEnergy in the built environment",
            "terug",
            "Climate adaptation",
            "Healthy living",
            "Go to Earth observation",
            "Mobility",
            "Smart industry",
            "The researcher will build synthetic aperture radar tools for the team and with the lab.",
            "What we offer",
            "- A pension",
            "Location: Den Haag",
        ]
    )
    view = cleaned_posting(page, title="Expert Synthetic Aperture Radar")
    assert "What are you searching for" not in view
    assert "Geselecteerde" not in view
    assert "Biobased building materials" not in view
    assert "Earth observation" not in view
    assert "terug" not in view
    assert "Go to " not in view
    assert "Circular and industrial construction" not in view
    assert "English" not in view
    assert "Nederlands" not in view
    assert "synthetic aperture radar tools" in view
    assert "What we offer" in view
    assert "- A pension" in view
    assert "Den Haag" in view


def test_a_bullet_topic_menu_does_not_push_the_vacancy_past_the_cap():
    from career_copilot.job_list import prepare_posting

    topics = [f"- Circular topic area number {index:03d}" for index in range(500)]
    page = "\n".join(
        [
            "Vacancy Expert Synthetic Aperture Radar at TNO in Den Haag",
            *topics,
            "Skip navigation (subjects beneath theme Careers)",
            "Directly to",
            "- Vacancies",
            "- Working at TNO",
            "As a SAR expert at TNO you truly work in the frontline of airborne and spaceborne radar technology.",
            "Requirements",
            "- Python",
            "- A pension",
            "Location: Den Haag",
        ]
    )
    assert len(page) > 8000
    stored = prepare_posting(page, "Expert Synthetic Aperture Radar")
    assert "Topic area" not in stored
    assert "Skip navigation" not in stored
    assert "Directly to" not in stored
    assert "Working at TNO" not in stored
    assert "frontline of airborne" in stored
    assert "- Python" in stored
    assert "- A pension" in stored
    assert "Den Haag" in stored
    short = "\n".join(
        [
            "- Alpha",
            "- Bravo",
            "- Charlie",
            "- Delta",
            "The researcher will build Neo4j tools for the team and with the lab.",
        ]
    )
    kept = cleaned_posting(short, title="Spatial Data Engineer")
    assert "Alpha" in kept
    assert "Neo4j" in kept


def test_a_dutch_institution_is_not_a_required_language():
    from career_copilot.job_list import _hard_languages, fits_language

    certificate = (
        "As you will be working for clients in the domain of defence and security, "
        "a Certificate of No Objection issued by AIVD (the Dutch General Intelligence and Security Service) is mandatory."
    )
    english = (
        "The researcher will build Neo4j tools for the team and with the lab. "
        "This role is for someone who can explain the model. We have the data and the graph. "
        "They will work with our group after the start. The team can share the work when the project needs it."
    )
    assert _hard_languages(certificate) == set()
    english_only = clean_conditions([], [], ["Remote"], "", "", languages="English")
    assert fits_language(f"{english}\n{certificate}", english_only)
    assert not fits_language(english + " Fluent Dutch is required.", english_only)
    assert _hard_languages("Uitstekende beheersing van de Engelse taal; Nederlands is een pré.") == {"English"}


def test_language_checks_stay_for_paste_and_automatic_search_returns_the_first_open_page(tmp_path: Path):
    from career_copilot.job_list import body_language, fits_language

    dutch = (
        "Is het jouw passie om complexe data slim met elkaar te verbinden en innovatieve oplossingen te bouwen? "
        "Als researcher versterk je onze unit op de campus. Jouw rol in ons team ontwerp je met Neo4j. "
        "Je werkt aan Knowledge Graphs en denkt mee over de architectuur. "
        "Je hebt ervaring met graph-technologie. Vanuit jouw ervaring ben je in staat om concepten te maken. "
        "Je kijkt verder dan alleen de techniek en kunt onderwerpen uitleggen voor stakeholders. "
        "Binnen onze unit werk je samen aan oplossingen. Je bent een ervaren specialist."
    )
    english = (
        "The researcher will build Neo4j tools for the team and with the lab. "
        "This role is for someone who can explain the model. We have the data and the graph. "
        "They will work with our group after the start. The team can share the work when the project needs it."
    )
    menu = "\n".join(
        [
            "For candidates",
            "For employers",
            "Nederlands",
            "Engels",
            "Frans",
            "Duits",
            "Australia",
            "Belgium",
            "Canada",
            "France",
            "English (EN)",
            "Nederlands (NL)",
        ]
    )
    assert body_language(dutch) == "Dutch"
    assert body_language(f"{menu}\n{english}") == "English"
    english_only = clean_conditions([], [], ["Remote"], "", "", languages="English")
    both = clean_conditions([], [], ["Remote"], "", "", languages="English, Dutch")
    assert not fits_language(dutch, english_only)
    assert fits_language(dutch, both)
    assert fits_language(f"{menu}\n{english}", english_only)
    plus = english + " Dutch is a plus."
    assert fits_language(plus, english_only)
    required = english + " Fluent Dutch is required."
    assert not fits_language(required, english_only)
    dutch_job = _web_opening("https://example.com/jobs/cgi", dutch, title="Onderzoeker", workplace="Remote")
    english_job = _web_opening(
        "https://example.com/jobs/geo",
        f"{menu}\n{english}\nDeadline\n15 Nov '26",
        title="Knowledge Graph Engineer",
        workplace="Remote",
    )
    hidden = real_search(
        "Neo4j researcher",
        _roles(),
        english_only,
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        discover=lambda: [dutch_job, english_job],
        skills=["Neo4j"],
        path=tmp_path / "english.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert [job.url for job in hidden.latest] == ["https://example.com/jobs/cgi"]
    assert hidden.latest[0].match_percent == 0
    assert hidden.latest[0].notes == [UNSCORED_NOTE]
    assert "Neo4j" in hidden.latest[0].description


def test_a_yes_on_a_possible_match_counts_on_the_next_search(tmp_path: Path):
    def score(_cv: str, _posting: str) -> list[MatchPoint]:
        return [
            MatchPoint(text="Neo4j", status="match"),
            MatchPoint(text="PhD in GIScience or a related field", status="partial"),
        ]

    def paste(title: str):
        return consider_posting(
            "Neo4j researcher",
            f"{title}\nRemote Neo4j researcher role. PhD in GIScience or a related field. " * 4,
            _roles(),
            clean_conditions([], [], ["Remote"], "", ""),
            score=score,
            path=tmp_path / "jobs.json",
            require_grok=False,
        )

    first = paste("Researcher one")
    assert first.job is not None
    assert first.job.partial == ["PhD in GIScience or a related field"]
    assert first.job.answered == []
    assert first.job.match_percent == 75
    save_trait("PhD in GIScience or a related field", "PhD in GIScience or a related field", "up", tmp_path / "traits.json")
    second = paste("Researcher two")
    assert second.job is not None
    assert second.job.match_percent == 100
    assert second.job.partial == []
    assert second.job.answered == ["PhD in GIScience or a related field"]
    assert "PhD in GIScience or a related field" in second.job.covered
    save_trait("PhD in GIScience or a related field", "PhD in GIScience or a related field", "down", tmp_path / "traits.json")
    third = paste("Researcher three")
    assert third.job is not None
    assert third.job.match_percent == 50
    assert third.job.partial == []
    assert third.job.answered == ["PhD in GIScience or a related field"]
    assert "PhD in GIScience or a related field" in third.job.missing


def _sample_job() -> ListedJob:
    return ListedJob(
        title="Researcher",
        company="Lab",
        url="https://example.com/jobs/open",
        source="Web",
        match_percent=50,
        description="Remote research.",
        covered=["Neo4j"],
        partial=["PhD in GIScience or a related field", "Teaching experience"],
        missing=["A license"],
    )


def test_yes_moves_the_line_now_and_an_unanswered_line_stays_off_the_profile(tmp_path: Path):
    path = tmp_path / "jobs.json"
    other = ListedJob(
        title="Graph engineer",
        company="Lab",
        url="https://example.com/jobs/graph",
        source="Web",
        match_percent=100,
        description="Remote research.",
        covered=["PhD in GIScience or a related field"],
    )
    save_log(SearchLog(latest=[_sample_job(), other]), path)
    record_answer(
        "PhD in GIScience or a related field",
        "PhD in GIScience or a related field",
        "up",
        path,
    )
    log = load_log(path)
    voted = log.latest[0]
    assert "PhD in GIScience or a related field" in voted.covered
    assert voted.partial == ["Teaching experience"]
    assert "PhD in GIScience or a related field" not in voted.missing
    assert voted.answered == ["PhD in GIScience or a related field"]
    assert voted.match_percent == match_percent(
        [
            MatchPoint(text="Neo4j", status="match"),
            MatchPoint(text="PhD in GIScience or a related field", status="match"),
            MatchPoint(text="Teaching experience", status="partial"),
            MatchPoint(text="A license", status="no_match"),
        ]
    )
    saved = load_traits(path.with_name("traits.json"))
    assert [trait.ask for trait in saved] == ["PhD in GIScience or a related field"]
    assert saved[0].vote == "up"
    assert "Teaching experience" not in path.with_name("traits.json").read_text(encoding="utf-8")
    untouched = log.latest[1]
    assert untouched.covered == ["PhD in GIScience or a related field"]
    assert untouched.answered == []
    assert untouched.match_percent == 100
    record_answer(
        "PhD in GIScience or a related field",
        "PhD in GIScience or a related field",
        "down",
        path,
    )
    lowered_log = load_log(path)
    lowered = lowered_log.latest[0]
    assert "PhD in GIScience or a related field" in lowered.missing
    assert "PhD in GIScience or a related field" not in lowered.covered
    assert lowered.match_percent < 50
    assert lowered in visible_jobs(lowered_log)
    assert lowered_log.latest[1].covered == ["PhD in GIScience or a related field"]
    assert lowered_log.latest[1].match_percent == 100
    record_answer("PhD in GIScience or a related field", "PhD in GIScience or a related field", None, path)
    cleared = load_log(path).latest[0]
    assert cleared.partial == ["Teaching experience", "PhD in GIScience or a related field"]
    assert cleared.answered == []
    assert load_traits(path.with_name("traits.json")) == []
    assert cleared.match_percent == 50


def test_clearing_an_assumed_answer_puts_the_line_back(tmp_path: Path):
    path = tmp_path / "jobs.json"
    save_log(
        SearchLog(
            latest=[
                ListedJob(
                    title="Researcher",
                    company="Lab",
                    url="https://example.com/jobs/assumed",
                    source="Web",
                    match_percent=50,
                    description="Remote research.",
                    covered=["Neo4j"],
                    missing=["License"],
                    assumed=[AssumedPoint(ask="Works independently", basis="PhD")],
                )
            ]
        ),
        path,
    )
    record_answer("Works independently", "PhD", "up", path)
    agreed = load_log(path).latest[0]
    assert agreed.assumed == []
    assert "Works independently" in agreed.covered
    assert agreed.match_percent == 67
    record_answer("Works independently", "PhD", None, path)
    restored = load_log(path).latest[0]
    assert restored.assumed[0].ask == "Works independently"
    assert restored.assumed[0].basis == "PhD"
    assert "Works independently" not in restored.covered
    assert restored.match_percent == 50
    assert load_traits(path.with_name("traits.json")) == []


def test_a_saved_answer_moves_an_older_job_when_the_page_opens(tmp_path: Path):
    path = tmp_path / "jobs.json"
    save_log(SearchLog(latest=[_sample_job()]), path)
    save_trait(
        "PhD in GIScience or a related field",
        "PhD in GIScience or a related field",
        "up",
        path.with_name("traits.json"),
    )
    from career_copilot.job_list import apply_saved_traits

    apply_saved_traits(path)
    moved = load_log(path).latest[0]
    assert "PhD in GIScience or a related field" in moved.covered
    apply_saved_traits(path)
    again = load_log(path).latest[0]
    assert again == moved


def test_yes_on_the_job_page_moves_the_line_into_what_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(_remote(), tmp_path / ".career-copilot" / "search.json")
    save_log(
        SearchLog(
            latest=[
                ListedJob(
                    title="Researcher",
                    company="Lab",
                    url="https://example.com/jobs/open",
                    source="Web",
                    match_percent=50,
                    description="Remote research.",
                    covered=["Research"],
                    partial=["Related research"],
                    missing=["A license"],
                )
            ]
        ),
        tmp_path / ".career-copilot" / "jobs.json",
    )
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Jobs").run()
    assert not app.exception, app.exception
    next(item for item in app.button if item.label == "Yes").click().run()
    assert not app.exception, app.exception
    visible = "\n".join(
        str(item.value)
        for item in list(app.markdown) + list(app.caption) + list(app.subheader)
    )
    assert "Possible matches" not in visible
    assert "Related research" in visible
    assert "Match: 67%" in visible
    assert "What matches" in visible
    assert "Strengths" not in visible
    assert "Gaps" not in visible
    assert "Move to gaps" not in [item.label for item in app.button]
    traits = (tmp_path / ".career-copilot" / "traits.json").read_text(encoding="utf-8")
    assert "Related research" in traits
    assert '"vote": "up"' in traits


def _manual_result() -> ListedJob:
    return ListedJob(
        title="Researcher",
        company="Pasted",
        url="https://example.com/jobs/manual?id=1",
        source="Pasted",
        match_percent=50,
        description="Remote research.",
        covered=["Research"],
        partial=["Related research"],
        missing=["A license"],
    )


def _open_manual_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: ListedJob):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from career_copilot.job_list import PasteOutcome

    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.chdir(tmp_path)
    save_roles(tmp_path / ".career-copilot" / "roles.json", _roles())
    save_search(_remote(), tmp_path / ".career-copilot" / "search.json")

    def consider(*_args, **_kwargs):
        return PasteOutcome(job=job)

    monkeypatch.setattr("career_copilot.job_list.consider_posting", consider)
    monkeypatch.setattr("career_copilot.ui_app.consider_posting", consider)
    script = Path(__file__).parents[1] / "src" / "career_copilot" / "ui_app.py"
    app = AppTest.from_file(str(script), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Jobs").run()
    next(item for item in app.button if item.label == "Manual search").click().run()
    next(item for item in app.button if item.label == "Match analysis").click().run()
    assert not app.exception, app.exception
    return app


def _visible_text(app) -> str:
    return "\n".join(
        str(item.value) for item in list(app.markdown) + list(app.caption) + list(app.subheader)
    )


def test_yes_on_a_manual_result_moves_the_line_and_saves_the_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = _open_manual_result(tmp_path, monkeypatch, _manual_result())
    assert "Possible matches" in _visible_text(app)
    next(item for item in app.button if item.label == "Yes").click().run()
    assert not app.exception, app.exception
    visible = _visible_text(app)
    assert "Possible matches" not in visible
    assert "Related research" in visible
    assert "Match: 67%" in visible
    traits = (tmp_path / ".career-copilot" / "traits.json").read_text(encoding="utf-8")
    assert "Related research" in traits
    assert '"vote": "up"' in traits
    assert load_log(tmp_path / ".career-copilot" / "jobs.json").latest == []


def test_no_on_a_manual_result_moves_the_line_into_what_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = _open_manual_result(tmp_path, monkeypatch, _manual_result())
    next(item for item in app.button if item.label == "No").click().run()
    assert not app.exception, app.exception
    visible = _visible_text(app)
    assert "Possible matches" not in visible
    assert "Related research" in visible
    assert "Match: 33%" in visible
    traits = (tmp_path / ".career-copilot" / "traits.json").read_text(encoding="utf-8")
    assert '"vote": "down"' in traits


def test_automatic_search_does_not_score_or_copy_guessed_points(tmp_path: Path):
    page = _web_opening(
        "https://example.com/jobs/plain",
        "Remote Neo4j researcher role. " * 8,
        workplace="Remote",
    ).model_copy(update={"points": [MatchPoint(text="Neo4j", status="match")]})
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert outcome.latest[0].url == "https://example.com/jobs/plain"
    assert outcome.latest[0].covered == []
    assert outcome.latest[0].notes == [UNSCORED_NOTE]


def test_a_long_search_requirement_is_kept():
    from career_copilot.job_list import _points_from_raw

    text = "Neo4j " + ("graph " * 80)
    points = _points_from_raw([{"text": text, "status": "Match", "basis": "x" * 300}])
    assert len(text) > 180
    assert len(points) == 1
    assert points[0].status == "match"
    assert 100 < len(points[0].text) <= 180
    assert len(points[0].basis) == 180
    assert points[0].text.startswith("Neo4j")


def test_a_plural_skill_still_opens_the_page(tmp_path: Path):
    page = _web_opening(
        "https://example.com/jobs/graph",
        "Remote role building a knowledge graph for the team. " * 8,
        title="Engineer",
        workplace="Remote",
    )
    outcome = real_search(
        "Built Knowledge Graphs for collections.",
        [WorkRole(name="cartographer", reason="Maps.")],
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: [MatchPoint(text="Knowledge Graphs", status="match")],
        discover=lambda: [page],
        skills=["Knowledge Graphs"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert [job.url for job in outcome.latest] == ["https://example.com/jobs/graph"]


def test_a_search_reply_keeps_real_links_and_drops_linkedin():
    from career_copilot.job_list import _parse_search_jobs

    listed = _parse_search_jobs(
        '{"jobs":[{"title":"Engineer","company":"Lab","url":"https://jobs.example.org/engineer"}]}'
    )
    assert [item["url"] for item in listed] == ["https://jobs.example.org/engineer"]
    prose = (
        "Found https://www.linkedin.com/jobs/view/1 and https://example.com/job "
        "and https://boards.greenhouse.io/acme/jobs/1"
    )
    found = _parse_search_jobs(prose)
    assert [item["url"] for item in found] == ["https://boards.greenhouse.io/acme/jobs/1"]
    wrapped = json.dumps(
        {
            "text": 'I will search once.{"jobs":[{"title":"Researcher","company":"Lab","url":"https://jobs.example.org/1"}]}'
        }
    )
    assert _parse_search_jobs(wrapped)[0]["url"] == "https://jobs.example.org/1"
    broken = json.dumps(
        {
            "text": '{"jobs":[{"title":"Engineer","company":"Lab","url":"https://jobs.example.org/x",}]}',
            "modelUsage": {"grok-4.7-build-fast": {"modelCalls": 1}},
        }
    )
    repaired = _parse_search_jobs(broken)
    assert repaired[0]["title"] == "Engineer"
    assert repaired[0]["url"] == "https://jobs.example.org/x"
    from career_copilot.job_list import _fast_model_used

    assert _fast_model_used(broken) is True
    high = json.dumps(
        {
            "text": '{"jobs":[{"title":"A","company":"B","url":"https://jobs.example.org/a"}]}',
            "modelUsage": {"grok-4.7-build": {"modelCalls": 1}},
        }
    )
    assert _fast_model_used(high) is False
    assert _fast_model_used('{"jobs":[]}') is True
    copied = json.dumps(
        {
            "text": (
                '{"jobs":[{"title":"role","company":"employer","url":"https://example.com/job"}]}'
                '{"jobs":[{"title":"Engineer","company":"CGI","url":"https://www.wizbii.com/company/cgi/job/1"}]}'
            ),
            "modelUsage": {"grok-4.7-build-fast": {"modelCalls": 1}},
        }
    )
    copied_jobs = _parse_search_jobs(copied)
    assert [(item["title"], item["url"]) for item in copied_jobs] == [
        ("Engineer", "https://www.wizbii.com/company/cgi/job/1")
    ]


def test_short_queries_use_saved_roles_skills_and_the_filled_place():
    from career_copilot.job_list import _CALL_TIMEOUT_SECONDS, _query_prompt, _short_queries

    assert _CALL_TIMEOUT_SECONDS == 60
    roles = [
        WorkRole(name="knowledge engineer", reason="The work is knowledge."),
        WorkRole(name="researcher", reason="The work is research."),
    ]
    skills = [
        "Knowledge Graphs",
        "Neo4j",
        "Ontology Design",
        "Cypher",
        "CIDOC-CRM",
        "Python",
        "Dataiku",
        "Data Modeling",
        "Information Retrieval",
        "Pattern Recognition",
    ]
    netherlands = clean_conditions([], [], [], "Netherlands", "")
    queries = _short_queries(roles, skills, netherlands)
    assert len(queries) == 2
    assert "Knowledge Graphs" in queries[0]
    assert "Neo4j" in queries[0]
    assert "Ontology Design" in queries[0]
    assert "Cypher" not in queries[0]
    assert "Netherlands" in queries[0]
    assert "job" in queries[0] and "vacancy" in queries[0]
    assert "knowledge engineer" in queries[1]
    assert "researcher" in queries[1]
    for query in queries:
        assert "2026" not in query
        assert "Amsterdam" not in query
        assert "Utrecht" not in query
        assert "Eindhoven" not in query
        assert "Remote" not in query
        assert "vacature" not in query
    germany = clean_conditions(["Senior"], ["Full-time"], ["Hybrid"], "Germany", "Berlin", notes="Light programming only.")
    abroad = _short_queries(roles, skills[:2], germany)
    assert abroad
    assert "Germany" in abroad[0]
    assert all("Senior" not in query and "Full-time" not in query for query in abroad)
    assert all("Berlin" not in query and "Hybrid" not in query and "Light" not in query for query in abroad)
    remote = clean_conditions([], [], ["Remote"], "Netherlands", "Utrecht")
    placed = _short_queries(roles, skills[:2], remote)[0]
    assert "Netherlands" in placed
    assert "remote" in placed.casefold()
    assert "Utrecht" not in placed
    prompt = _query_prompt(queries[0])
    assert "Do not open the pages" in prompt
    assert "LinkedIn" in prompt
    assert "https://example.com/jobs/old" not in prompt
    assert "SECRET" not in prompt


def test_a_high_model_call_is_skipped_and_the_cv_stays_out_of_the_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    page = "Remote Neo4j researcher role. " * 12
    calls: list[str] = []

    def links(query: str, *, skipped=None, timeout_seconds: int = 60) -> list[dict[str, str]]:
        del skipped
        calls.append(query)
        assert timeout_seconds == 60
        assert "SECRETLINE" not in query
        if len(calls) == 1:
            raise CareerCopilotError("The fast model was not used.")
        return [{"title": "Researcher", "company": "Lab", "url": "https://jobs.example.org/new"}]

    monkeypatch.setattr("career_copilot.job_list._links_for_query", links)
    monkeypatch.setattr("career_copilot.job_list.fetch_posting", lambda _url: page)
    path = tmp_path / "jobs.json"
    outcome = real_search(
        "Neo4j researcher SECRETLINE",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        skills=["Neo4j"],
        path=path,
        require_grok=False,
    )
    assert len(calls) == 2
    assert not (tmp_path / "search-run.txt").exists()
    assert "SECRETLINE" not in path.read_text(encoding="utf-8")
    assert [job.url for job in outcome.latest] == ["https://jobs.example.org/new"]
    assert outcome.latest[0].notes == [UNSCORED_NOTE]


def test_removed_links_stay_out_and_the_first_open_page_stops_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    page = "Remote Neo4j researcher role. " * 12
    calls: list[int] = []
    fetched: list[str] = []

    def links(_query: str, *, skipped=None, timeout_seconds: int = 60):
        calls.append(timeout_seconds)
        assert skipped is not None
        assert "https://example.com/jobs/old" in skipped
        assert "SECRET" not in _query
        return [
            {"title": "Old", "company": "Lab", "url": "https://example.com/jobs/old"},
            {"title": "Researcher", "company": "Lab", "url": "https://jobs.example.org/new"},
            {"title": "Later", "company": "Lab", "url": "https://jobs.example.org/later"},
        ]

    def fetch(url: str) -> str:
        fetched.append(url)
        return page

    monkeypatch.setattr("career_copilot.job_list._links_for_query", links)
    monkeypatch.setattr("career_copilot.job_list.fetch_posting", fetch)
    path = tmp_path / "jobs.json"
    save_log(SearchLog(skipped_urls=["https://example.com/jobs/old"]), path)
    outcome = real_search(
        "Neo4j researcher SECRETLINE",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        skills=["Neo4j"],
        path=path,
        require_grok=False,
    )
    assert calls == [60]
    assert fetched == ["https://jobs.example.org/new"]
    assert [job.url for job in outcome.latest] == ["https://jobs.example.org/new"]
    assert outcome.skipped_urls == ["https://example.com/jobs/old"]
    assert outcome.reason == ""


def test_a_second_query_runs_only_when_the_first_returns_no_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    page = "Remote Neo4j researcher role. " * 12
    calls: list[str] = []

    def links(query: str, *, skipped=None, timeout_seconds: int = 60):
        del skipped, timeout_seconds
        calls.append(query)
        if len(calls) == 1:
            raise CareerCopilotError("Grok did not return a search. Stay signed in with grok login and try again.")
        return [{"title": "Researcher", "company": "Lab", "url": "https://jobs.example.org/new"}]

    monkeypatch.setattr("career_copilot.job_list._links_for_query", links)
    monkeypatch.setattr("career_copilot.job_list.fetch_posting", lambda _url: page)
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert [job.url for job in outcome.latest] == ["https://jobs.example.org/new"]

    closed_calls: list[str] = []

    def closed_links(query: str, *, skipped=None, timeout_seconds: int = 60):
        del skipped, timeout_seconds
        closed_calls.append(query)
        return [{"title": "Closed", "company": "Lab", "url": "https://jobs.example.org/closed"}]

    monkeypatch.setattr("career_copilot.job_list._links_for_query", closed_links)
    monkeypatch.setattr(
        "career_copilot.job_list.fetch_posting",
        lambda _url: "Applications are closed. " * 20,
    )
    closed = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        skills=["Neo4j"],
        path=tmp_path / "closed.json",
        require_grok=False,
    )
    assert len(closed_calls) == 1
    assert closed.latest == []
    assert closed.reason == EMPTY_REASON


def test_a_search_with_no_saved_link_reports_nothing_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[int] = []

    def links(_query: str, *, skipped=None, timeout_seconds: int = 60):
        del skipped
        calls.append(timeout_seconds)
        raise CareerCopilotError("Grok took too long. Nothing was saved.")

    monkeypatch.setattr("career_copilot.job_list._links_for_query", links)
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert calls == [60, 60]
    assert outcome.latest == []
    assert outcome.reason == "Nada foi encontrado."
