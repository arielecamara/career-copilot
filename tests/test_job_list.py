"""The job list keeps up to five open matches at 50 percent or above."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from career_copilot.errors import CareerCopilotError
from career_copilot.job_list import (
    DEADLINE_NOTE,
    EMPTY_REASON,
    FALLBACK_REASON,
    HIGH_MATCH,
    SEARCH_TIMEOUT_SECONDS,
    WEB_SHOWN,
    ListedJob,
    MatchPoint,
    SearchLog,
    _Opening,
    consider_posting,
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
    assert "What partly matches" in visible
    assert "Related research" in visible
    assert "Read the CV on the User page first." in visible
    assert "Remotive" in visible
    assert "+" in labels
    assert "−" in labels
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
    assert "What partly matches" in visible
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


def test_web_discovery_rescores_opened_pages_and_skips_a_dead_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from career_copilot.job_list import _search_prompt, openings_from_search

    monkeypatch.setattr(
        "career_copilot.job_list._discover_with_grok",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("grok")),
    )
    monkeypatch.setattr(
        "career_copilot.job_list._default_feeds",
        lambda: (_ for _ in ()).throw(AssertionError("feed")),
    )
    page = "Remote researcher role in Utrecht. " * 12
    fetched: list[str] = []
    opened: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        if "dead" in url:
            raise CareerCopilotError("This page did not return the posting. Paste the text.")
        return page + " Guessed percent 90 must be ignored."

    items = [
        {"title": "Researcher", "company": "Lab", "url": "https://example.com/jobs/one", "match": 90},
        {"title": "Ignored", "company": "In", "url": "https://www.linkedin.com/jobs/view/1"},
        {"title": "Closed", "company": "Lab", "url": "https://example.com/jobs/dead"},
        {"title": "Loop", "company": "Lab", "url": "https://127.0.0.1/jobs/secret"},
    ]
    openings = openings_from_search(items, fetch, _remote(), progress=opened.append)
    assert opened == ["Opening page 1 of 2.", "Opening page 2 of 2."]
    assert [item.url for item in openings] == ["https://example.com/jobs/one"]
    assert openings[0].source == "Web"
    assert "SECRET" not in openings[0].text
    scored: list[str] = []
    outcome = real_search(
        "SECRET CURRICULUM researcher",
        _roles(),
        _remote(),
        score=lambda _cv, _posting: _points(1, 1),
        discover=lambda: openings,
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        progress=scored.append,
    )
    assert scored == ["Searching.", "Scoring 1 of 1."]
    assert outcome.latest[0].match_percent == 50
    assert outcome.latest[0].match_percent != 90
    assert "SECRET CURRICULUM" not in (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert outcome.latest[0].notes[0] == "The application deadline was not found."
    assert fetched == ["https://example.com/jobs/one", "https://example.com/jobs/dead"]
    prompt = _search_prompt(_roles(), clean_conditions([], [], [], "", ""), ["Neo4j"])
    assert "Neo4j" in prompt
    assert "several different web searches" in prompt
    assert "at most 4" not in prompt
    assert "A blank field is not a preference" in prompt
    assert "SECRET" not in prompt
    assert "site:linkedin" not in prompt
    assert "2026" not in prompt
    assert "Prefer light programming" not in prompt
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
    from career_copilot.job_list import _hard_languages, _scoring_text, _search_prompt, fits_language

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
    prompt = _search_prompt(_roles(), noted, ["Neo4j"])
    assert "at most 4" not in prompt
    assert "several different web searches" in prompt
    assert "Light programming only." in prompt
    assert "A preference does not by itself exclude a page." in prompt
    assert "Prefer light programming" not in prompt
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


def test_web_search_stops_at_three_and_keeps_four_minutes():
    assert SEARCH_TIMEOUT_SECONDS == 240
    assert WEB_SHOWN == 3


def test_web_search_keeps_a_hybrid_job_and_names_a_missing_deadline(tmp_path: Path):
    page = _web_opening(
        "https://example.com/jobs/cgi",
        "Hybrid Neo4j role for a researcher in Eindhoven. " * 8,
    )
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions(["Entry level"], ["Full-time"], ["Remote"], "Netherlands", ""),
        score=lambda _cv, _posting: [MatchPoint(text="Neo4j", status="match")],
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert len(outcome.latest) == 1
    assert outcome.latest[0].match_percent == 50
    assert any("Hybrid" in line for line in outcome.latest[0].missing)
    assert outcome.latest[0].notes[0] == DEADLINE_NOTE
    assert outcome.reason == ""


def test_a_closed_page_and_a_page_without_overlap_stay_out(tmp_path: Path):
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
        score=lambda _cv, _posting: [MatchPoint(text="Neo4j", status="match")],
        discover=lambda: [closed, past, unrelated, future],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
        today=date(2026, 10, 21),
    )
    assert [job.url for job in outcome.latest] == ["https://example.com/jobs/open"]
    assert DEADLINE_NOTE not in outcome.latest[0].notes


def test_nothing_opened_and_a_low_score_use_the_two_empty_messages(tmp_path: Path):
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
    low = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], [], "", ""),
        score=lambda _cv, _posting: _points(1, 3),
        discover=lambda: [
            _web_opening("https://example.com/jobs/low", "Remote Neo4j researcher role. " * 8, workplace="Remote")
        ],
        skills=["Neo4j"],
        path=tmp_path / "low.json",
        require_grok=False,
    )
    assert low.reason == FALLBACK_REASON
    assert low.found_count == 0
    assert len(visible_jobs(low)) == 1
    assert visible_jobs(low)[0].match_percent < 50


def test_three_matches_stop_the_web_search(tmp_path: Path):
    calls: list[str] = []

    def score(_cv: str, posting: str) -> list[MatchPoint]:
        calls.append(posting)
        return [MatchPoint(text="Neo4j", status="match")]

    pages = [
        _web_opening(f"https://example.com/jobs/{index}", "Remote Neo4j researcher role. " * 8, workplace="Remote")
        for index in range(4)
    ]
    outcome = real_search(
        "Neo4j researcher",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=score,
        discover=lambda: pages,
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert len(calls) == 3
    assert len(outcome.latest) == 3
    assert len(visible_jobs(outcome)) == 3


def test_an_assumed_point_waits_for_a_vote_and_then_counts(tmp_path: Path):
    def score(_cv: str, _posting: str) -> list[MatchPoint]:
        return [
            MatchPoint(text="Neo4j", status="match"),
            MatchPoint(text="License", status="no_match"),
            MatchPoint(text="Works independently", status="assumed", basis="PhD"),
        ]

    page = _web_opening(
        "https://example.com/jobs/assumed",
        "Remote Neo4j researcher role. Works independently. " * 6,
        workplace="Remote",
    )
    first = real_search(
        "Neo4j researcher with a PhD",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=score,
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert first.latest[0].match_percent == 50
    assert first.latest[0].assumed[0].ask == "Works independently"
    assert first.latest[0].assumed[0].basis == "PhD"
    assert "Works independently" not in first.latest[0].covered
    save_trait("Works independently", "PhD", "up", tmp_path / "traits.json")
    second = real_search(
        "Neo4j researcher with a PhD",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=score,
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert second.latest[0].match_percent == 67
    assert second.latest[0].assumed == []
    assert "Works independently" in second.latest[0].covered
    save_trait("Works independently", "PhD", "down", tmp_path / "traits.json")
    third = real_search(
        "Neo4j researcher with a PhD",
        _roles(),
        clean_conditions([], [], ["Remote"], "", ""),
        score=score,
        discover=lambda: [page],
        skills=["Neo4j"],
        path=tmp_path / "jobs.json",
        require_grok=False,
    )
    assert third.latest[0].match_percent == 50
    assert third.latest[0].assumed == []
    assert "Works independently" not in third.latest[0].covered
    assert load_traits(tmp_path / "traits.json")[0].vote == "down"
    assert "Neo4j researcher with a PhD" not in (tmp_path / "jobs.json").read_text(encoding="utf-8")
