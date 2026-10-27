"""Job list for phase 3.

Automatic search builds one short query, asks the fast model for https
links, opens pages until one is still open, and returns that job.
A second query runs only when the first call returns no link.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_copilot.errors import CareerCopilotError
from career_copilot.roles import FAST_MODEL, WorkRole, child_env, grok_score_argv, grok_search_argv
from career_copilot.search_intent import SearchConditions

HIGH_MATCH = 70
MIN_MATCH = 50
MAX_SCORED = 5
WEB_SHOWN = 3
_CALL_TIMEOUT_SECONDS = 60
FALLBACK_REASON = "Nada foi encontrado, então retornamos a vaga com o maior match com o seu CV."
EMPTY_REASON = "Nada foi encontrado."
UNSCORED_NOTE = "The match was not scored."
DEADLINE_NOTE = "The application deadline was not found."
_POSTING_CAP = 8000
_FETCH_CAP = 24000
_score_budget: ContextVar[int | None] = ContextVar("score_budget", default=None)
PRIMARY_FEEDS = ("Remotive", "Arbeitnow")
EXTRA_FEEDS = ("Himalayas", "Jobicy", "Remote OK")
REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit=50"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
HIMALAYAS_URL = "https://himalayas.app/jobs/api?limit=20"
JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs?count=20"
REMOTEOK_URL = "https://remoteok.com/api"
_LOG_FIELDS = {"kept", "latest", "skipped_urls", "reason", "found_count", "high_count", "below_count"}
_NL_COUNTRIES = {"netherlands", "the netherlands", "nederland", "holland", "nl"}
_NL_CITIES = (
    "amsterdam",
    "rotterdam",
    "utrecht",
    "the hague",
    "den haag",
    "eindhoven",
    "groningen",
    "tilburg",
    "almere",
    "breda",
    "nijmegen",
    "haarlem",
    "arnhem",
    "amersfoort",
    "apeldoorn",
    "leiden",
    "delft",
    "enschede",
    "maastricht",
    "dordrecht",
    "zoetermeer",
    "zwolle",
    "deventer",
    "leeuwarden",
    "alkmaar",
    "hilversum",
    "amstelveen",
    "gouda",
    "zaandam",
    "hoofddorp",
    "schiedam",
    "nieuwegein",
    "veenendaal",
    "helmond",
    "venlo",
    "heerlen",
    "emmen",
    "roosendaal",
)


class MatchPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    status: str
    basis: str = ""

    @field_validator("text", "basis")
    @classmethod
    def check_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > 180:
            raise ValueError("requirement text is invalid")
        return cleaned

    @field_validator("text")
    @classmethod
    def text_present(cls, value: str) -> str:
        if not value:
            raise ValueError("requirement text is invalid")
        return value

    @field_validator("status")
    @classmethod
    def check_status(cls, value: str) -> str:
        if value not in {"match", "partial", "no_match", "unknown", "assumed"}:
            raise ValueError("requirement status is invalid")
        return value


class AssumedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ask: str
    basis: str

    @field_validator("ask", "basis")
    @classmethod
    def check_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 180:
            raise ValueError("assumed text is invalid")
        return cleaned


class Trait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ask: str
    basis: str
    vote: str

    @field_validator("ask", "basis")
    @classmethod
    def check_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 180:
            raise ValueError("trait text is invalid")
        return cleaned

    @field_validator("vote")
    @classmethod
    def check_vote(cls, value: str) -> str:
        if value not in {"up", "down"}:
            raise ValueError("trait vote is invalid")
        return value


class ListedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    company: str
    url: str = ""
    source: str
    match_percent: int = Field(ge=0, le=100)
    description: str
    covered: list[str] = Field(default_factory=list)
    partial: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    assumed: list[AssumedPoint] = Field(default_factory=list)
    answered: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(str(item).split())[:200] for item in value]
        return [item for item in cleaned if item][:6]


class SearchLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kept: list[ListedJob] = Field(default_factory=list)
    latest: list[ListedJob] = Field(default_factory=list)
    skipped_urls: list[str] = Field(default_factory=list)
    reason: str = ""
    found_count: int = 0
    high_count: int = 0
    below_count: int = 0


class _Opening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    company: str
    url: str
    location: str
    workplace: str
    employment: list[str] = Field(default_factory=list)
    text: str
    closes_on: date | None = None
    source: str
    points: list[MatchPoint] = Field(default_factory=list)


def jobs_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "jobs.json"


def traits_path(root: Path | None = None) -> Path:
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "traits.json"


def traits_for_jobs(jobs: Path | None) -> Path:
    if jobs is None:
        return traits_path()
    return jobs.with_name("traits.json")


def load_traits(path: Path | None = None) -> list[Trait]:
    target = traits_path() if path is None else path
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CareerCopilotError("The saved traits could not be read.") from exc
    items = raw.get("traits") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise CareerCopilotError("The saved traits could not be read.")
    traits: list[Trait] = []
    for item in items:
        try:
            traits.append(Trait.model_validate(item))
        except ValidationError as exc:
            raise CareerCopilotError("The saved traits could not be read.") from exc
    return traits


def save_trait(ask: str, basis: str, vote: str, path: Path | None = None) -> None:
    target = traits_path() if path is None else path
    trait = Trait(ask=ask, basis=basis, vote=vote)
    kept = [item for item in load_traits(target) if item.ask.casefold() != trait.ask.casefold()]
    kept.append(trait)
    _write_traits(kept, target)


def delete_trait(ask: str, path: Path | None = None) -> None:
    target = traits_path() if path is None else path
    kept = [item for item in load_traits(target) if item.ask.casefold() != ask.casefold()]
    _write_traits(kept, target)


def _write_traits(traits: list[Trait], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"traits": [item.model_dump(mode="json") for item in traits]}
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def revise_job(job: ListedJob, ask: str, basis: str, vote: str | None) -> ListedJob:
    cleaned_ask = " ".join(ask.split())
    cleaned_basis = " ".join((basis or ask).split())
    if not cleaned_ask or vote not in {"up", "down", None}:
        return job
    in_partial = any(_same_line(line, cleaned_ask) for line in job.partial)
    in_assumed = any(_same_line(item.ask, cleaned_ask) for item in job.assumed)
    in_answered = any(_same_line(line, cleaned_ask) for line in job.answered)
    in_covered = any(_same_line(line, cleaned_ask) for line in job.covered)
    in_missing = any(_same_line(line, cleaned_ask) for line in job.missing)
    if vote == "up":
        if in_covered and in_answered:
            return job
        if not (in_partial or in_assumed or in_answered):
            return job
        destination = "match"
    elif vote == "down":
        if in_missing and in_answered:
            return job
        if not (in_partial or in_assumed or in_answered):
            return job
        destination = "missing"
    elif not in_answered:
        return job
    else:
        destination = "possible"
    covered = _without_line(job.covered, cleaned_ask)
    partial = _without_line(job.partial, cleaned_ask)
    missing = _without_line(job.missing, cleaned_ask)
    assumed = [item for item in job.assumed if not _same_line(item.ask, cleaned_ask)]
    answered = _without_line(job.answered, cleaned_ask)
    if destination == "match":
        covered.append(cleaned_ask)
        answered.append(cleaned_ask)
    elif destination == "missing":
        missing.append(cleaned_ask)
        answered.append(cleaned_ask)
    elif cleaned_basis.casefold() != cleaned_ask.casefold():
        assumed.append(AssumedPoint(ask=cleaned_ask, basis=cleaned_basis))
    else:
        partial.append(cleaned_ask)
    updated = job.model_copy(
        update={
            "covered": covered,
            "partial": partial,
            "missing": missing,
            "assumed": assumed,
            "answered": answered,
        }
    )
    percent = match_percent(_points_of(updated))
    if percent is None:
        percent = job.match_percent
    return updated.model_copy(update={"match_percent": percent})


def record_answer(ask: str, basis: str, vote: str | None, path: Path | None = None) -> None:
    traits_target = traits_for_jobs(path)
    if vote is None:
        delete_trait(ask, traits_target)
    elif vote in {"up", "down"}:
        save_trait(ask, basis or ask, vote, traits_target)
    else:
        raise CareerCopilotError("The saved traits could not be read.")
    log = load_log(path)
    log.kept = [revise_job(job, ask, basis, vote) for job in log.kept]
    log.latest = [revise_job(job, ask, basis, vote) for job in log.latest]
    save_log(log, path)


def apply_saved_traits(path: Path | None = None) -> None:
    traits = load_traits(traits_for_jobs(path))
    if not traits:
        return
    log = load_log(path)
    if not log.kept and not log.latest:
        return
    changed = False

    def fold(job: ListedJob) -> ListedJob:
        nonlocal changed
        current = job
        for trait in traits:
            revised = revise_job(current, trait.ask, trait.basis, trait.vote)
            if revised != current:
                changed = True
                current = revised
        return current

    kept = [fold(job) for job in log.kept]
    latest = [fold(job) for job in log.latest]
    if changed:
        log.kept = kept
        log.latest = latest
        save_log(log, path)


def _same_line(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _without_line(lines: list[str], ask: str) -> list[str]:
    return [line for line in lines if not _same_line(line, ask)]


def _points_of(job: ListedJob) -> list[MatchPoint]:
    points = [MatchPoint(text=line, status="match") for line in job.covered]
    points.extend(MatchPoint(text=line, status="partial") for line in job.partial)
    points.extend(MatchPoint(text=line, status="no_match") for line in job.missing)
    return points


def load_log(path: Path | None = None) -> SearchLog:
    target = jobs_path() if path is None else path
    if not target.is_file():
        return SearchLog()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise CareerCopilotError("The saved job list could not be read.")
        if "kept" not in raw and "latest" not in raw:
            raw = {
                "kept": [],
                "latest": raw.get("results") or [],
                "skipped_urls": [],
                "reason": "",
                "found_count": 0,
            }
        return SearchLog.model_validate({key: raw[key] for key in _LOG_FIELDS if key in raw})
    except CareerCopilotError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CareerCopilotError("The saved job list could not be read.") from exc


def save_log(log: SearchLog, path: Path | None = None) -> None:
    target = jobs_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(log.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def job_key(job: ListedJob) -> str:
    if job.url.startswith("https://"):
        return job.url
    return f"paste:{job.title.casefold()}:{job.company.casefold()}"


def visible_jobs(log: SearchLog) -> list[ListedJob]:
    skipped = set(log.skipped_urls)
    kept = {job_key(job) for job in log.kept}
    seen: set[str] = set()
    jobs: list[ListedJob] = []

    def add(job: ListedJob) -> None:
        key = job_key(job)
        if key in skipped or key in seen:
            return
        seen.add(key)
        jobs.append(job)

    for job in log.kept:
        add(job)
    latest = [job for job in log.latest if job_key(job) not in skipped and job_key(job) not in kept]
    unscored = [job for job in latest if _unscored(job)]
    if unscored:
        add(unscored[0])
    qualified = [job for job in latest if job.match_percent >= MIN_MATCH and not _unscored(job)]
    if qualified:
        ranked = sorted(qualified, key=lambda job: job.match_percent, reverse=True)
        for job in ranked[:WEB_SHOWN]:
            add(job)
    elif log.reason == FALLBACK_REASON and latest:
        add(max(latest, key=lambda job: job.match_percent))
    for job in latest:
        if job.answered and job.match_percent < MIN_MATCH:
            add(job)
    return jobs


def keep_job(job: ListedJob, path: Path | None = None) -> None:
    log = load_log(path)
    key = job_key(job)
    log.latest = [item for item in log.latest if job_key(item) != key]
    if not any(job_key(item) == key for item in log.kept):
        log.kept.append(job)
    save_log(log, path)


def drop_job(job: ListedJob, path: Path | None = None) -> None:
    log = load_log(path)
    key = job_key(job)
    log.kept = [item for item in log.kept if job_key(item) != key]
    log.latest = [item for item in log.latest if job_key(item) != key]
    if key not in log.skipped_urls:
        log.skipped_urls.append(key)
    save_log(log, path)


def match_percent(points: list[MatchPoint]) -> int | None:
    judged = [point for point in points if point.status not in {"unknown", "assumed"}]
    if not judged:
        return None
    score = 0.0
    for point in judged:
        if point.status == "match":
            score += 1
        elif point.status == "partial":
            score += 0.5
    return round(100 * score / len(judged))


def is_open(closes_on: date | None, today: date) -> bool:
    return closes_on is None or closes_on >= today


def fits_workplace(location: str, workplace: str, conditions: SearchConditions) -> bool:
    selected = conditions.workplaces
    if selected:
        if workplace == "Remote" and "Remote" not in selected:
            return False
        if workplace in {"Local", "Hybrid"} and workplace not in selected:
            return False
        if workplace and workplace not in {"Remote", "Local", "Hybrid"}:
            return False
    if workplace == "Remote" or (not conditions.country and not conditions.city):
        return True
    if workplace in {"Local", "Hybrid"} and not location.strip():
        return True
    if location.strip() and not _place_matches(location, conditions):
        return False
    return True


def fits_employment(stated: list[str], conditions: SearchConditions) -> bool:
    if not stated or not conditions.employment:
        return True
    return any(item in conditions.employment for item in stated)


def fits_language(text: str, conditions: SearchConditions) -> bool:
    if not conditions.languages.strip():
        return True
    cleaned = strip_page_chrome(text)
    spoken = _user_languages(conditions.languages)
    body = body_language(cleaned)
    if body and body not in spoken:
        return False
    hard = _hard_languages(cleaned)
    if not hard:
        return True
    return hard <= spoken


def short_line(below: int) -> str:
    if below == 1:
        return "1 listing fell short of 50%."
    return f"{below} listings fell short of 50%."


@dataclass(frozen=True)
class PasteOutcome:
    note: str = ""
    job: ListedJob | None = None


def real_search(
    cv_text: str,
    roles: list[WorkRole],
    conditions: SearchConditions,
    *,
    score: Callable[[str, str], list[MatchPoint]],
    feeds: dict[str, Callable[[], object]] | None = None,
    discover: Callable[[], list[_Opening]] | None = None,
    skills: list[str] | None = None,
    today: date | None = None,
    path: Path | None = None,
    require_grok: bool = True,
    progress: Callable[[str], None] | None = None,
) -> SearchLog:
    cleaned = cv_text.strip()
    if not cleaned:
        raise CareerCopilotError("Read the CV on the User page first.")
    if require_grok and shutil.which("grok") is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    log = load_log(path)
    current = today or date.today()
    blocked = set(log.skipped_urls)
    blocked.update(job_key(job) for job in log.kept)

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    if feeds is None:
        return _search_web(
            cleaned,
            roles,
            conditions,
            score=score,
            discover=discover,
            skills=skills or [],
            today=current,
            path=path,
            progress=report,
            log=log,
            blocked=blocked,
        )
    candidates, attempted, failed, raw_count, open_count = _collect(feeds, conditions, current, blocked)
    found: list[ListedJob] = []
    below = 0
    traits = load_traits(traits_for_jobs(path))
    batch = candidates[:8]
    for opening in batch:
        judged, answered = _judge(
            list(score(cleaned, _scoring_text(opening.text, roles, conditions))),
            traits,
        )
        judged.extend(_condition_gaps(opening, conditions))
        judged.extend(_language_gap(opening, conditions))
        percent = match_percent(judged)
        if percent is None:
            continue
        if _accept_percent(percent, opening, conditions):
            found.append(_listed(opening, percent, judged, conditions, answered=answered))
        else:
            below += 1
    found.sort(key=lambda job: job.match_percent, reverse=True)
    found = found[:MAX_SCORED]
    log.latest = found
    log.found_count = len(found)
    log.high_count = sum(1 for job in found if job.match_percent >= HIGH_MATCH)
    log.below_count = below
    if found:
        log.reason = ""
    elif below:
        log.reason = _shortfall_reason(below)
    else:
        log.reason = _reason(attempted, failed, raw_count, open_count, len(candidates))
    save_log(log, path)
    return log


def _unscored(job: ListedJob) -> bool:
    return (
        not job.covered
        and not job.partial
        and not job.missing
        and not job.assumed
        and UNSCORED_NOTE in job.notes
    )


def _search_web(
    cv_text: str,
    roles: list[WorkRole],
    conditions: SearchConditions,
    *,
    score: Callable[[str, str], list[MatchPoint]],
    discover: Callable[[], list[_Opening]] | None,
    skills: list[str],
    today: date,
    path: Path | None,
    progress: Callable[[str], None],
    log: SearchLog,
    blocked: set[str],
) -> SearchLog:
    del cv_text, score
    if discover is not None:
        progress("Opening the page.")
        for opening in discover():
            if opening.url in blocked or not _opening_is_open(opening, today):
                continue
            return _save_open_job(log, opening, path)
        return _save_nothing(log, path)

    removed = sorted(url for url in blocked if url.startswith("https://"))
    queries = _short_queries(roles, skills, conditions)
    for query in queries:
        progress(f"Searching with {FAST_MODEL}.")
        try:
            links = _links_for_query(query, skipped=removed, timeout_seconds=_CALL_TIMEOUT_SECONDS)
        except CareerCopilotError as exc:
            if "too long" not in str(exc).casefold() and "fast model" not in str(exc).casefold():
                if "did not return a search" not in str(exc).casefold():
                    raise
            links = []
        if not links:
            continue
        progress("Opening the page.")
        for item in links:
            url = str(item.get("url", "")).strip()
            if not url.startswith("https://") or url in blocked:
                continue
            try:
                page = fetch_posting(url)
            except CareerCopilotError:
                continue
            opening = _opening_from_page(item, url, page)
            if not _opening_is_open(opening, today):
                continue
            return _save_open_job(log, opening, path)
        # The first call returned links. A second query runs only when there are none.
        break
    return _save_nothing(log, path)


def _short_queries(roles: list[WorkRole], skills: list[str], conditions: SearchConditions) -> list[str]:
    """One short query, plus a second only for the caller to use when the first has no link."""

    role_names = _unique_terms([role.name for role in roles if role.kept])[:2]
    skill_names = _unique_terms(list(skills))[:3]
    place_terms: list[str] = []
    if conditions.country.strip():
        place_terms.append(conditions.country.strip())
    if any(item.casefold() == "remote" for item in conditions.workplaces):
        place_terms.append("remote")
    place = _or_group(place_terms)
    job = "(job OR vacancy)"

    def line(terms: list[str]) -> str:
        return " ".join(part for part in (_or_group(terms), place, job) if part)

    first = line(skill_names or role_names)
    second = line(role_names) if skill_names and role_names else ""
    queries = [query for query in (first, second) if query]
    seen: set[str] = set()
    chosen: list[str] = []
    for query in queries:
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            chosen.append(query)
    return chosen[:2] or ["job OR vacancy"]


def _opening_is_open(opening: _Opening, today: date) -> bool:
    if not is_open(opening.closes_on, today):
        return False
    if _explicitly_closed(opening.text):
        return False
    deadline = _deadline_date(opening.text)
    return deadline is None or deadline >= today


def _opening_from_page(item: dict[str, object], url: str, page: str) -> _Opening:
    text = prepare_posting(page)
    title = " ".join(str(item.get("title") or "").split())
    if not title:
        title = next((line.lstrip("#").strip() for line in text.splitlines() if line.strip()), "Job")
    company = " ".join(str(item.get("company") or "").split()) or "Unknown"
    return _Opening(
        title=title[:120],
        company=company[:120],
        url=url[:500],
        location="",
        workplace="",
        text=prepare_posting(page, title),
        source="Web",
    )


def _save_open_job(log: SearchLog, opening: _Opening, path: Path | None) -> SearchLog:
    text = prepare_posting(opening.text, opening.title) or opening.text
    listed = ListedJob(
        title=opening.title[:120] or "Job",
        company=opening.company[:120] or "Unknown",
        url=opening.url,
        source=opening.source or "Web",
        match_percent=0,
        description=text,
        notes=[UNSCORED_NOTE],
    )
    log.latest = [listed]
    log.found_count = 1
    log.high_count = 0
    log.below_count = 0
    log.reason = ""
    save_log(log, path)
    return log


def _save_nothing(log: SearchLog, path: Path | None) -> SearchLog:
    log.latest = []
    log.found_count = 0
    log.high_count = 0
    log.below_count = 0
    log.reason = EMPTY_REASON
    save_log(log, path)
    return log


def requested_documents(text: str) -> list[str]:
    blob = readable_text(text)
    documents = ["CV"]
    if _word(blob, r"cover\s+letters?") or _word(blob, r"letters?\s+of\s+motivation"):
        documents.append("Cover letter")
    if _word(blob, r"portfolios?"):
        documents.append("Portfolio")
    if _word(blob, r"transcripts?"):
        documents.append("Transcript")
    if _word(blob, r"writing\s+samples?"):
        documents.append("Writing sample")
    if _word(blob, r"certificates?") or _word(blob, r"certifications?"):
        documents.append("Certificate")
    if _word(blob, r"id"):
        documents.append("ID")
    return documents


def consider_posting(
    cv_text: str,
    posting: str,
    roles: list[WorkRole],
    conditions: SearchConditions,
    *,
    score: Callable[[str, str], list[MatchPoint]],
    path: Path | None = None,
    require_grok: bool = True,
    fetch: Callable[[str], str] | None = None,
) -> PasteOutcome:
    cleaned = cv_text.strip()
    if not cleaned:
        raise CareerCopilotError("Read the CV on the User page first.")
    source_link = _lone_https_link(posting)
    page = posting
    if source_link:
        host = urllib.parse.urlparse(source_link).hostname or ""
        if _blocked_job_host(host):
            return PasteOutcome(note="LinkedIn stays paste-only. Paste the posting text.")
        try:
            page = (fetch or fetch_posting)(source_link)
        except CareerCopilotError as exc:
            message = str(exc).strip() or "This page did not return the posting. Paste the text."
            return PasteOutcome(note=message)
    if require_grok and shutil.which("grok") is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    raw = readable_text(page)[:_FETCH_CAP]
    text = prepare_posting(raw)
    if not text:
        raise CareerCopilotError("Paste the posting text.")
    first = next((line.lstrip("#").strip() for line in text.splitlines() if line.strip()), "")
    title = " ".join((first or "Job").split())[:120]
    text = prepare_posting(raw, title)
    if source_link:
        link = source_link
    else:
        link_match = re.search(r"https://\S+", text)
        link = link_match.group(0).rstrip(").,]}>\"'")[:500] if link_match else ""
    log = load_log(path)
    opening = _Opening(
        title=title,
        company="Pasted",
        url=link,
        location=_paste_location(text, conditions),
        workplace=_mentioned_workplace(text),
        employment=_mentioned_employment(text),
        text=text,
        source="Pasted",
    )
    key = job_key(_listed(opening, 0, []))
    removed = key in log.skipped_urls
    existing = next((job for job in log.kept + log.latest if job_key(job) == key), None)
    if existing is not None:
        return PasteOutcome(note="This posting is already on the list.", job=existing)
    judged, answered = _judge(
        list(score(cleaned, _scoring_text(opening.text, roles, conditions))),
        load_traits(traits_for_jobs(path)),
    )
    judged.extend(_condition_gaps(opening, conditions))
    judged.extend(_language_gap(opening, conditions))
    percent = match_percent(judged)
    if percent is None:
        listed = _listed(opening, 0, [], conditions)
        note = "This posting could not be scored."
        if removed:
            note = "This posting was removed from the list. " + note
        return PasteOutcome(note=note, job=listed)
    listed = _listed(opening, percent, judged, conditions, answered=answered)
    if removed:
        return PasteOutcome(note="This posting was removed from the list. The match is shown here only.", job=listed)
    log.latest.append(listed)
    log.latest.sort(key=lambda job: job.match_percent, reverse=True)
    log.found_count = len(log.latest)
    log.high_count = sum(1 for job in log.latest if job.match_percent >= HIGH_MATCH)
    log.reason = ""
    save_log(log, path)
    return PasteOutcome(job=listed)


def parse_remotive(payload: object) -> list[_Opening]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        raise CareerCopilotError("The job feed did not respond.")
    openings: list[_Opening] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        opening = _opening(
            item.get("title", ""),
            item.get("company_name", ""),
            item.get("url", ""),
            item.get("candidate_required_location", ""),
            "Remote",
            item.get("description", ""),
            "Remotive",
            _closing_date(item),
            item.get("job_type"),
        )
        if opening is not None:
            openings.append(opening)
    return openings


def parse_arbeitnow(payload: object) -> list[_Opening]:
    jobs = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise CareerCopilotError("The job feed did not respond.")
    openings: list[_Opening] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("description", ""))
        opening = _opening(
            item.get("title", ""),
            item.get("company_name", ""),
            item.get("url", ""),
            item.get("location", ""),
            _arbeitnow_workplace(_flag(item.get("remote")), raw),
            raw,
            "Arbeitnow",
            None,
            item.get("job_types"),
        )
        if opening is not None:
            openings.append(opening)
    return openings


def parse_himalayas(payload: object) -> list[_Opening]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise CareerCopilotError("The job feed did not respond.")
    openings: list[_Opening] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        places = item.get("locationRestrictions")
        location = " ".join(str(place) for place in places) if isinstance(places, list) else ""
        opening = _opening(
            item.get("title", ""),
            item.get("companyName", ""),
            item.get("applicationLink") or item.get("guid") or "",
            location,
            "Remote",
            item.get("description", ""),
            "Himalayas",
            _date_value(item.get("expiryDate")),
            item.get("employmentType"),
        )
        if opening is not None:
            openings.append(opening)
    return openings


def parse_jobicy(payload: object) -> list[_Opening]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise CareerCopilotError("The job feed did not respond.")
    openings: list[_Opening] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        opening = _opening(
            item.get("jobTitle", ""),
            item.get("companyName", ""),
            item.get("url", ""),
            item.get("jobGeo", ""),
            "Remote",
            item.get("jobDescription", ""),
            "Jobicy",
            None,
            item.get("jobType"),
        )
        if opening is not None:
            openings.append(opening)
    return openings


def parse_remoteok(payload: object) -> list[_Opening]:
    jobs: object = payload
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise CareerCopilotError("The job feed did not respond.")
    openings: list[_Opening] = []
    for item in jobs:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        opening = _opening(
            item.get("position", ""),
            item.get("company", ""),
            item.get("url") or item.get("apply_url") or "",
            item.get("location", ""),
            "Remote",
            item.get("description", ""),
            "Remote OK",
            None,
        )
        if opening is not None:
            openings.append(opening)
    return openings


def score_posting(cv_text: str, posting: str) -> list[MatchPoint]:
    return _score_with_grok(cv_text, posting)


def _default_feeds() -> dict[str, Callable[[], object]]:
    return {
        "Remotive": lambda: _fetch_json(REMOTIVE_URL),
        "Arbeitnow": lambda: _fetch_json(ARBEITNOW_URL),
        "Himalayas": lambda: _fetch_json(HIMALAYAS_URL),
        "Jobicy": lambda: _fetch_json(JOBICY_URL),
        "Remote OK": lambda: _fetch_json(REMOTEOK_URL),
    }


def _collect(
    feeds: dict[str, Callable[[], object]],
    conditions: SearchConditions,
    today: date,
    blocked: set[str],
) -> tuple[list[_Opening], int, int, int, int]:
    parsers = {
        "Remotive": parse_remotive,
        "Arbeitnow": parse_arbeitnow,
        "Himalayas": parse_himalayas,
        "Jobicy": parse_jobicy,
        "Remote OK": parse_remoteok,
    }
    candidates: list[_Opening] = []
    seen: set[str] = set()
    attempted = 0
    failed = 0
    raw_count = 0
    open_count = 0

    def pull(name: str) -> None:
        nonlocal attempted, failed, raw_count, open_count
        fetch = feeds.get(name)
        parser = parsers.get(name)
        if fetch is None or parser is None:
            return
        attempted += 1
        try:
            openings = parser(fetch())
        except Exception:
            failed += 1
            return
        for opening in openings:
            raw_count += 1
            if not is_open(opening.closes_on, today):
                continue
            open_count += 1
            if opening.url in blocked or opening.url in seen:
                continue
            if not fits_workplace(opening.location, opening.workplace, conditions):
                continue
            if not fits_employment(opening.employment, conditions):
                continue
            if not fits_language(opening.text, conditions):
                continue
            seen.add(opening.url)
            candidates.append(opening)

    for name in PRIMARY_FEEDS:
        pull(name)
    if len(candidates) < MAX_SCORED:
        for name in EXTRA_FEEDS:
            if len(candidates) >= MAX_SCORED:
                break
            pull(name)
    return candidates, attempted, failed, raw_count, open_count


def _reason(attempted: int, failed: int, raw_count: int, open_count: int, candidates: int) -> str:
    if attempted == 0 or failed == attempted:
        return "The job feeds did not respond."
    if raw_count == 0:
        return "The feeds returned no listing."
    if open_count == 0:
        return "No listing was still open."
    if candidates == 0:
        return "No open listing fit the saved search conditions."
    return "No listing could be scored."


def _listed(
    opening: _Opening,
    percent: int,
    points: list[MatchPoint],
    conditions: SearchConditions | None = None,
    *,
    answered: list[str] | None = None,
    deadline_missing: bool = False,
) -> ListedJob:
    notes = _notes(opening, conditions)
    if deadline_missing:
        notes = [DEADLINE_NOTE, *notes]
    assumed = [
        AssumedPoint(ask=point.text, basis=point.basis)
        for point in points
        if point.status == "assumed" and point.basis.strip()
    ]
    return ListedJob(
        title=opening.title,
        company=opening.company,
        url=opening.url,
        source=opening.source,
        match_percent=percent,
        description=prepare_posting(opening.text, opening.title),
        covered=[point.text for point in points if point.status == "match"],
        partial=[point.text for point in points if point.status == "partial"],
        missing=[point.text for point in points if point.status == "no_match"],
        assumed=assumed,
        answered=list(answered or []),
        notes=notes,
    )


def _apply_traits(points: list[MatchPoint], traits: list[Trait]) -> list[MatchPoint]:
    votes = {trait.ask.casefold(): trait.vote for trait in traits}
    prepared: list[MatchPoint] = []
    for point in points:
        vote = votes.get(point.text.casefold())
        if point.status == "assumed" and not point.basis.strip() and vote is None:
            continue
        if vote == "up" and point.status in {"assumed", "partial"}:
            prepared.append(MatchPoint(text=point.text, status="match", basis=point.basis))
            continue
        if vote == "down" and point.status in {"assumed", "partial"}:
            prepared.append(MatchPoint(text=point.text, status="no_match", basis=point.basis))
            continue
        prepared.append(point)
    return prepared


def _judge(points: list[MatchPoint], traits: list[Trait]) -> tuple[list[MatchPoint], list[str]]:
    calmed = _calm_points(points)
    adjusted = _apply_traits(calmed, traits)
    return adjusted, _trait_answers(calmed, adjusted)


def _trait_answers(before: list[MatchPoint], after: list[MatchPoint]) -> list[str]:
    prior = {point.text.casefold(): point.status for point in before}
    names: list[str] = []
    for point in after:
        if prior.get(point.text.casefold()) in {"partial", "assumed"} and point.status in {"match", "no_match"}:
            names.append(point.text)
    return names


def _opening_points(
    opening: _Opening,
    cv_text: str,
    roles: list[WorkRole],
    conditions: SearchConditions,
    score: Callable[[str, str], list[MatchPoint]],
    seconds_left: int,
) -> list[MatchPoint]:
    token = _score_budget.set(max(1, seconds_left))
    try:
        return list(score(cv_text, _scoring_text(opening.text, roles, conditions)))
    finally:
        _score_budget.reset(token)


def _with_posting(opening: _Opening) -> _Opening:
    text = prepare_posting(opening.text, opening.title)
    if text == opening.text:
        return opening
    return opening.model_copy(update={"text": text})


_CLOSED_PHRASES = (
    "applications are closed",
    "application is closed",
    "no longer accepting",
    "position has been filled",
    "this position has been filled",
    "vacancy is closed",
    "job is closed",
    "deadline has passed",
)
_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_LEVEL_WORDS = (
    ("Entry level", (r"entry[\s-]?level", "junior")),
    ("Senior", ("senior",)),
    ("Manager", ("manager",)),
    ("Director", ("director",)),
    ("Executive", ("executive",)),
)


def _explicitly_closed(text: str) -> bool:
    blob = text.casefold()
    return any(phrase in blob for phrase in _CLOSED_PHRASES)


def _needs_deadline_note(opening: _Opening) -> bool:
    if opening.closes_on is not None:
        return False
    return _deadline_date(opening.text) is None


_DEADLINE_HINT = r"deadline|apply by|closing date|applications close|last day to apply|close date|closes on"
_DATE_APOSTROPHE = re.compile(r"[\u2018\u2019\u02bc`]")


def deadline_on(text: str) -> date | None:
    return _deadline_date(text)


def format_deadline(value: date) -> str:
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{value.day} {names[value.month - 1]} {value.year}"


def notes_for_display(notes: list[str], description: str) -> list[str]:
    if deadline_on(description) is None:
        return list(notes)
    return [note for note in notes if note != DEADLINE_NOTE]


def _deadline_date(text: str) -> date | None:
    normalized = _DATE_APOSTROPHE.sub("'", text or "")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        lines = _sentences(normalized)
    for index, line in enumerate(lines):
        if not _word(line, _DEADLINE_HINT):
            continue
        window = line if index + 1 >= len(lines) else f"{line} {lines[index + 1]}"
        found = _date_in(window)
        if found is not None:
            return found
    return None


def _full_year(year: int) -> int:
    if year >= 100:
        return year
    if year >= 70:
        return 1900 + year
    return 2000 + year


def _date_in(sentence: str) -> date | None:
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", sentence)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    day_first = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\.?\s+'?(\d{2,4})\b", sentence)
    if day_first and day_first.group(2).casefold() in _MONTHS:
        return _safe_date(_full_year(int(day_first.group(3))), _MONTHS[day_first.group(2).casefold()], int(day_first.group(1)))
    month_first = re.search(r"\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+'?(\d{2,4})\b", sentence)
    if month_first and month_first.group(1).casefold() in _MONTHS:
        return _safe_date(_full_year(int(month_first.group(3))), _MONTHS[month_first.group(1).casefold()], int(month_first.group(2)))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _level_gap(opening: _Opening, conditions: SearchConditions) -> list[MatchPoint]:
    if not conditions.levels:
        return []
    blob = f"{opening.title}\n{opening.text}"
    stated = [label for label, patterns in _LEVEL_WORDS if any(_word(blob, pattern) for pattern in patterns)]
    if not stated or any(label in conditions.levels for label in stated):
        return []
    named = " and ".join(stated)
    return [MatchPoint(text=f"The experience level is {named}, outside the saved experience levels.", status="no_match")]


def _scoring_text(posting: str, roles: list[WorkRole], conditions: SearchConditions) -> str:
    names = ", ".join(role.name for role in roles)
    levels = ", ".join(conditions.levels)
    employment = ", ".join(conditions.employment)
    places = ", ".join(conditions.workplaces)
    if conditions.city and conditions.country:
        where = f"{conditions.city}, {conditions.country}"
    elif conditions.country:
        where = conditions.country
    elif conditions.city:
        where = conditions.city
    else:
        where = "any place"
    return (
        f"Target roles: {names}.\n"
        f"Target level: {levels}.\n"
        f"Target employment: {employment}.\n"
        f"Target workplace: {places}. Place: {where}.\n"
        f"{_preference_line(conditions)}"
        f"Posting:\n{posting[:7500]}"
    )


def _place_matches(location: str, conditions: SearchConditions) -> bool:
    blob = location.casefold()
    city = conditions.city.casefold().strip()
    country = conditions.country.casefold().strip()
    if city and _has_place(blob, city):
        return True
    if country and _has_place(blob, country):
        return True
    if country in _NL_COUNTRIES:
        if any(_has_place(blob, name) for name in ("netherlands", "nederland", "holland")):
            return True
        return any(_has_place(blob, name) for name in _NL_CITIES)
    return False


def _has_place(blob: str, name: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", blob) is not None


def _word(text: str, pattern: str) -> bool:
    return re.search(rf"(?<![a-z])(?:{pattern})(?![a-z])", text.casefold()) is not None


def _mentioned_workplace(text: str) -> str:
    hybrid = _word(text, "hybrid")
    remote = _word(text, "remote") or "work from home" in text.casefold()
    local = _word(text, r"on[\s-]?site") or _word(text, r"in[\s-]office")
    if hybrid:
        return "Hybrid"
    if remote and not local:
        return "Remote"
    if local and not remote:
        return "Local"
    return ""


def _arbeitnow_workplace(remote_flag: bool, text: str) -> str:
    mentioned = _mentioned_workplace(readable_text(text))
    if remote_flag:
        return "Hybrid" if mentioned == "Hybrid" else "Remote"
    if mentioned in {"Hybrid", "Local"}:
        return mentioned
    return ""


def _mentioned_employment(text: str) -> list[str]:
    patterns = (
        ("Full-time", r"full[\s_-]?time"),
        ("Part-time", r"part[\s_-]?time"),
        ("Internship", r"intern(?:ship)?"),
        ("Volunteer", r"volunteer"),
        ("Contract", r"freelancers?|contractors?|per[\s-]+(?:approved[\s-]+)?tasks?|fixed[\s-]?term|month contract"),
    )
    return [label for label, pattern in patterns if _word(text, pattern)]


def _flatten_field(value: object) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _level_mentioned(text: str) -> bool:
    patterns = (r"entry[\s-]?level", "junior", "senior", "manager", "director", "executive")
    return any(_word(text, pattern) for pattern in patterns)


def _both_workplaces(text: str) -> bool:
    remote = _word(text, "remote") or "work from home" in text.casefold()
    local = _word(text, r"on[\s-]?site") or _word(text, r"in[\s-]office")
    return remote and local and not _word(text, "hybrid")


def _paste_location(text: str, conditions: SearchConditions) -> str:
    if _place_matches(text, conditions):
        return conditions.country or conditions.city
    match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*([A-Z][a-z]+)\b", text)
    if match:
        return match.group(0)
    return ""


def _condition_gaps(opening: _Opening, conditions: SearchConditions) -> list[MatchPoint]:
    gaps: list[MatchPoint] = []
    if conditions.workplaces and opening.workplace == "Remote" and "Remote" not in conditions.workplaces:
        gaps.append(MatchPoint(text="The workplace is Remote, outside the saved workplaces.", status="no_match"))
    elif conditions.workplaces and opening.workplace in {"Local", "Hybrid"} and opening.workplace not in conditions.workplaces:
        gaps.append(MatchPoint(text=f"The workplace is {opening.workplace}, outside the saved workplaces.", status="no_match"))
    elif (
        opening.workplace != "Remote"
        and opening.location.strip()
        and (conditions.country.strip() or conditions.city.strip())
        and not _place_matches(opening.location, conditions)
    ):
        gaps.append(MatchPoint(text="The place is outside the saved country.", status="no_match"))
    if opening.employment and conditions.employment and not fits_employment(opening.employment, conditions):
        stated = " and ".join(opening.employment)
        gaps.append(MatchPoint(text=f"The employment type is {stated}, outside the saved employment types.", status="no_match"))
    return gaps


def _notes(opening: _Opening, conditions: SearchConditions | None = None) -> list[str]:
    notes: list[str] = []
    if _both_workplaces(opening.text):
        notes.append("The posting mentions more than one workplace.")
    elif not opening.workplace:
        notes.append("The posting does not mention the workplace.")
    elif opening.workplace in {"Local", "Hybrid"} and not opening.location.strip():
        notes.append("The posting does not mention the place.")
    if not opening.employment:
        notes.append("The posting does not mention the employment type.")
    if not _level_mentioned(f"{opening.title}\n{opening.text}"):
        notes.append("The posting does not mention the experience level.")
    if conditions is not None and conditions.languages.strip() and not _mentions_language(opening.text):
        notes.append("The posting does not mention a language.")
    return notes


_LANGUAGE_NAMES = (
    ("English", ("english", "engels", "engelse")),
    ("Dutch", ("dutch", "nederlands", "nederlandse")),
    ("Portuguese", ("portuguese", "portugues", "português")),
    ("German", ("german", "deutsch", "duits", "duitse")),
    ("French", ("french", "francais", "français", "frans", "franse")),
    ("Spanish", ("spanish", "espanol", "español", "spaans", "spaanse")),
)
_BODY_WORDS = (
    (
        "English",
        frozenset(
            "the and to with that this you your we our are from not can will have has if but about "
            "which their they them for been would these those there what when where who how its into "
            "after before during without such".split()
        ),
    ),
    (
        "Dutch",
        frozenset(
            "het een van voor niet zijn jouw wij naar ook bij deze wordt hebben door maar nog onze "
            "ons waar moet worden bent kunt dat jullie wanneer hebt heb met te om op aan uit dan "
            "wel meer tot".split()
        ),
    ),
    (
        "German",
        frozenset(
            "der und eine für auf ist sind nicht sich auch oder nach aus nur dass wird wurde über "
            "zwischen durch wenn einer einem".split()
        ),
    ),
    (
        "French",
        frozenset(
            "le les une des pour dans qui pas sur aux avec cette sont nous vous votre notre mais "
            "où être aussi chez donc".split()
        ),
    ),
    (
        "Portuguese",
        frozenset(
            "não uma para com você está são das pela pelo também porque muito ela ele essa esse "
            "esta este isso".split()
        ),
    ),
    (
        "Spanish",
        frozenset("los las del por sus más también cuando donde porque está son como".split()),
    ),
)
_HARD_LANGUAGE = r"required|must|fluent|native|beheersing|vereist|mandatory|proficiency"
_SOFT_LANGUAGE = r"plus|pr[eé]|preferred|advantage|nice to have"
_SENIOR_LEVELS = ("Senior", "Director", "Executive")


def _calm_points(points: list[MatchPoint]) -> list[MatchPoint]:
    calmed: list[MatchPoint] = []
    for point in points:
        if point.status in {"match", "partial", "assumed"} and _interview_line(point.text):
            calmed.append(MatchPoint(text=point.text, status="unknown"))
        else:
            calmed.append(point)
    return calmed


def _interview_line(text: str) -> bool:
    blob = text.casefold()
    if "team player" in blob or "interpersonal" in blob:
        return True
    if _word(blob, r"communication skills") or _word(blob, r"(?:strong|excellent|good|clear) communication"):
        return True
    if _word(blob, r"clearly and concisely"):
        return True
    return re.search(r"\bexplain\w*.{0,80}\bclear", blob) is not None


def _accept_percent(percent: int, opening: _Opening, conditions: SearchConditions) -> bool:
    if not conditions.levels:
        return percent >= MIN_MATCH
    senior_posting = _senior_posting(opening)
    senior_wanted = any(level in conditions.levels for level in _SENIOR_LEVELS)
    if senior_posting and not senior_wanted:
        return percent >= HIGH_MATCH
    return percent >= MIN_MATCH


def _senior_posting(opening: _Opening) -> bool:
    blob = f"{opening.title}\n{opening.text}"
    return any(_word(blob, word) for word in ("senior", "director", "executive"))


def _shortfall_reason(below: int) -> str:
    if below == 1:
        return "1 listing was scored and it did not reach 50%."
    return f"{below} listings were scored and none reached 50%."


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?;\n]+", text) if part.strip()]


def _languages_in(text: str) -> set[str]:
    found: set[str] = set()
    for label, names in _LANGUAGE_NAMES:
        if any(_word(text, name) for name in names):
            found.add(label)
    return found


def _language_required(sentence: str, names: tuple[str, ...]) -> bool:
    """A language is required only when the requirement sits next to its name."""

    cue = (
        r"fluent|native|proficiency|proficient|beheersing|taal|language|"
        r"speak(?:ing)?|spoken|vereist|required|must|mandatory"
    )
    for name in names:
        token = re.escape(name)
        pattern = rf"(?:{cue})\W+(?:\w+\W+){{0,4}}{token}|{token}\W+(?:\w+\W+){{0,4}}(?:{cue})"
        if re.search(pattern, sentence, flags=re.IGNORECASE):
            return True
    return False


def _hard_languages(text: str) -> set[str]:
    hard: set[str] = set()
    for sentence in _sentences(text):
        if _word(sentence, _SOFT_LANGUAGE):
            continue
        for label, names in _LANGUAGE_NAMES:
            if _language_required(sentence, names):
                hard.add(label)
    return hard


def _user_languages(text: str) -> set[str]:
    return _languages_in(text)


def body_language(text: str) -> str:
    """The language of a cleaned description, when one language is clearly ahead."""

    words = re.findall(r"[a-zà-ÿ']+", text.casefold())
    scores = [(label, sum(1 for word in words if word in vocab)) for label, vocab in _BODY_WORDS]
    scores.sort(key=lambda item: item[1], reverse=True)
    if not scores or scores[0][1] < 8:
        return ""
    if len(scores) > 1 and scores[0][1] < scores[1][1] * 2:
        return ""
    return scores[0][0]


def _mentions_language(text: str) -> bool:
    return bool(_languages_in(text))


def _preference_line(conditions: SearchConditions) -> str:
    notes = conditions.notes.strip()
    if not notes:
        return ""
    return f"Preferences:\n{notes}\n"


def _language_gap(opening: _Opening, conditions: SearchConditions) -> list[MatchPoint]:
    if not conditions.languages.strip():
        return []
    missing = sorted(_hard_languages(opening.text) - _user_languages(conditions.languages))
    if not missing:
        return []
    named = " and ".join(missing)
    return [MatchPoint(text=f"The posting requires {named}, outside the saved languages.", status="no_match")]


def _unique_terms(values: list[str]) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            chosen.append(text)
    return chosen


def _quoted(term: str) -> str:
    text = " ".join(term.split())
    if " " in text:
        return f'"{text}"'
    return text


def _or_join(terms: list[str]) -> str:
    return " OR ".join(_quoted(term) for term in _unique_terms(terms))


def _or_group(terms: list[str]) -> str:
    text = _or_join(terms)
    if " OR " in text:
        return f"({text})"
    return text


def _links_for_query(
    query: str,
    *,
    skipped: list[str] | None = None,
    timeout_seconds: int = _CALL_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    executable = shutil.which("grok")
    if executable is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(_query_prompt(query), encoding="utf-8")
        try:
            completed = subprocess.run(
                grok_search_argv(executable, prompt_file, directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, timeout_seconds),
                env=child_env(os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            late = exc.stdout or ""
            if isinstance(late, bytes):
                late = late.decode("utf-8", errors="replace")
            if not str(late).strip():
                raise CareerCopilotError("Grok took too long. Nothing was saved.") from exc
            completed = subprocess.CompletedProcess(
                grok_search_argv(executable, prompt_file, directory),
                0,
                stdout=str(late),
                stderr="",
            )
    stdout = completed.stdout or ""
    if stdout.strip() and not _fast_model_used(stdout):
        raise CareerCopilotError("The fast model was not used.")
    found: list[dict[str, object]] = []
    if stdout.strip():
        found = _parse_search_jobs(stdout)
    if completed.returncode != 0 and not found:
        raise CareerCopilotError("Grok did not return a search. Stay signed in with grok login and try again.")
    avoided = {url for url in (skipped or []) if url.startswith("https://")}
    kept: list[dict[str, object]] = []
    for item in found:
        url = str(item.get("url", "")).strip()[:500]
        if not url.startswith("https://") or url in avoided:
            continue
        kept.append({"title": item.get("title", ""), "company": item.get("company", ""), "url": url})
    return kept


def _query_prompt(query: str) -> str:
    return (
        "Use the fastest model available.\n"
        "Use one web search for the query below, then stop.\n"
        "Return only https links of job postings that look open.\n"
        "Each item needs a title, a company, and one https url.\n"
        "Do not open the pages. Do not invent a job, a company, or a link. Do not use LinkedIn.\n"
        f"Query: {query}\n"
    )


def _fast_model_used(stdout: str) -> bool:
    """A reply with no model record is kept. A reply from another model is not."""

    for item in _json_values(stdout):
        if isinstance(item, dict) and isinstance(item.get("modelUsage"), dict) and item["modelUsage"]:
            return FAST_MODEL in item["modelUsage"]
    return True


def _json_values(text: str) -> list[object]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")].strip()
    decoder = json.JSONDecoder()
    found: list[object] = []
    index = 0
    while index < len(raw):
        starts = [point for point in (raw.find("{", index), raw.find("[", index)) if point >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, offset = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", raw[start:])
            try:
                value, offset = decoder.raw_decode(repaired, 0)
            except json.JSONDecodeError:
                index = start + 1
                continue
            found.append(value)
            index = start + offset
            continue
        found.append(value)
        index = offset
    return found


def _jobs_in(value: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(value, dict):
        jobs = value.get("jobs")
        if isinstance(jobs, list):
            found.extend(job for job in jobs if isinstance(job, dict))
        text = value.get("text")
        if isinstance(text, str) and not isinstance(jobs, list):
            inner = _json_values(text)
            if inner:
                for item in inner:
                    found.extend(_jobs_in(item))
            else:
                found.extend(_jobs_from_text(text))
    elif isinstance(value, list):
        found.extend(job for job in value if isinstance(job, dict))
    return found


def _parse_search_jobs(stdout: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for value in _json_values(stdout):
        found.extend(_jobs_in(value))
    if not found:
        found.extend(_jobs_from_text(stdout))
    kept: list[dict[str, object]] = []
    seen: set[str] = set()
    for job in found:
        url = str(job.get("url", "")).strip()
        host = (urllib.parse.urlparse(url).hostname or "").casefold()
        if host == "example.com" or host.endswith(".example.com"):
            continue
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        kept.append(job)
    return kept


def _jobs_from_text(text: str) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in re.finditer(r"https://[^\s\"'<>\\]+", text):
        url = match.group(0).rstrip(").,;]")
        host = (urllib.parse.urlparse(url).hostname or "").casefold()
        if not host or host == "example.com" or host.endswith(".example.com") or _blocked_job_host(host):
            continue
        if url in seen:
            continue
        seen.add(url)
        jobs.append({"title": "", "company": "", "url": url})
    return jobs


def _opening(
    title: object,
    company: object,
    url: object,
    location: object,
    workplace: str,
    text: object,
    source: str,
    closes_on: date | None,
    employment_field: object = None,
) -> _Opening | None:
    label = " ".join(str(title).split())
    employer = " ".join(str(company).split())
    link = str(url).strip()
    if not label or not employer or not link.startswith("https://"):
        return None
    readable = prepare_posting(readable_text(str(text))[:_FETCH_CAP], label)
    stated = _mentioned_employment(_flatten_field(employment_field)) or _mentioned_employment(readable)
    return _Opening(
        title=label[:120],
        company=employer[:120],
        url=link[:500],
        location=" ".join(str(location).split()),
        workplace=workplace,
        employment=stated,
        text=readable,
        closes_on=closes_on,
        source=source,
    )


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "career-copilot", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CareerCopilotError("The job feed did not respond.") from exc


def _score_with_grok(cv_text: str, posting: str) -> list[MatchPoint]:
    executable = shutil.which("grok")
    if executable is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(_match_prompt(cv_text, posting), encoding="utf-8")
        try:
            budget = _score_budget.get()
            completed = subprocess.run(
                grok_score_argv(executable, prompt_file, directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180 if budget is None else max(1, min(60, int(budget))),
                env=child_env(os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CareerCopilotError("Grok took too long. Nothing was saved.") from exc
    if completed.returncode != 0:
        raise CareerCopilotError("Grok did not return a match. Stay signed in with grok login and try again.")
    return _parse_points(completed.stdout or "")


def _match_prompt(cv_text: str, posting: str) -> str:
    return (
        "Compare the CV with the job posting. Return only JSON:\n"
        '{"requirements":[{"text":"short requirement from the posting","status":"match"}]}\n'
        "status is match, partial, no_match, unknown, or assumed.\n"
        "match means the CV states that requirement. partial means the CV shows only a related part, not the requirement itself. "
        "no_match means the CV does not show it, or the posting conflicts with a saved target. "
        "unknown means the line is an interview habit, such as explaining clearly or being a team player, or the posting is unclear. "
        "Do not use unknown lines as matches.\n"
        "assumed means the CV does not state the requirement, but one fact in the CV makes it plausible. "
        'Add "basis" with that fact. Leave basis empty for every other status. Do not use assumed for a skill the CV states.\n'
        "If the posting does not mention the level, the employment type, or the workplace, do not add a requirement for it.\n"
        "The posting text may start with target roles, level, employment, workplace, and preferences. "
        "Do not turn those lines into requirements. A preference is context only.\n"
        "Use at most 8 requirements. Do not quote the CV. Do not copy dates, employers, or numbers from the CV.\n"
        f"CV:\n{cv_text[:60000]}\n"
        f"POSTING:\n{posting[:8000]}\n"
    )


def _points_from_raw(raw: object) -> list[MatchPoint]:
    if not isinstance(raw, list):
        return []
    points: list[MatchPoint] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip().casefold().replace(" ", "_").replace("-", "_")
        if status == "nomatch":
            status = "no_match"
        text = " ".join(str(item.get("text", "")).split())[:180]
        basis = " ".join(str(item.get("basis", "")).split())[:180]
        if status == "assumed" and not basis:
            continue
        try:
            points.append(MatchPoint.model_validate({"text": text, "status": status, "basis": basis}))
        except ValidationError:
            continue
        if len(points) == 8:
            break
    return points


def _parse_points(stdout: str) -> list[MatchPoint]:
    from career_copilot.roles import _json_object

    body = _json_object(stdout)
    if isinstance(body.get("text"), str) and "requirements" not in body:
        body = _json_object(str(body["text"]))
    raw = body.get("requirements")
    if not isinstance(raw, list):
        raise CareerCopilotError("Grok did not return a match.")
    points: list[MatchPoint] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            points.extend(_points_from_raw([item]))
        except ValidationError:
            continue
        if len(points) == 8:
            break
    return points


def _lone_https_link(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) != 1 or " " in lines[0] or not lines[0].startswith("https://"):
        return ""
    return lines[0][:500]


def _blocked_job_host(host: str) -> bool:
    name = host.casefold().rstrip(".")
    return name == "linkedin.com" or name.endswith(".linkedin.com")


def _private_host(host: str) -> bool:
    name = host.casefold().rstrip(".")
    if name in {"localhost", "metadata.google.internal"} or name.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def _posting_page_ok(text: str) -> bool:
    if len(text) < 200:
        return False
    blob = text.casefold()
    gated = any(phrase in blob for phrase in ("sign in", "log in", "login", "create an account"))
    if not gated:
        return True
    useful = any(
        phrase in blob
        for phrase in ("requirements", "qualifications", "responsibilities", "what you will", "about the role", "we are looking")
    )
    return useful and len(text) >= 400


def fetch_posting(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or _blocked_job_host(host) or _private_host(host):
        raise CareerCopilotError("This page did not return the posting. Paste the text.")
    request = urllib.request.Request(url, headers={"User-Agent": "career-copilot", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = urllib.parse.urlparse(response.geturl())
            final_host = final.hostname or ""
            if final.scheme != "https" or _blocked_job_host(final_host) or _private_host(final_host):
                raise CareerCopilotError("This page did not return the posting. Paste the text.")
            raw = response.read(1_000_000)
    except CareerCopilotError:
        raise
    except Exception as exc:
        raise CareerCopilotError("This page did not return the posting. Paste the text.") from exc
    text = readable_text(raw.decode("utf-8", errors="replace"))[:_FETCH_CAP]
    if not _posting_page_ok(text):
        raise CareerCopilotError("This page did not return the posting. Paste the text.")
    return text


_NOISE_LINE = (
    "cookie",
    "privacy policy",
    "skip to",
    "share this",
    "back to all",
    "similar jobs",
    "create an alert",
    "job alert",
    "accept all",
    "reject all",
)
_ALWAYS_CHROME = frozenset(
    {
        "for candidates",
        "for employers",
        "for members",
        "apply now",
        "save",
        "view on google maps",
        "bekijk vacatures",
        "mijn profiel",
        "activeer jobalerts",
        "visit career sites",
        "global careers site",
        "vacatures bekijk vacatures",
        "profiel aanmaken | inloggen",
        "profiel aanmaken",
        "inloggen",
        "klik hier voor vergelijkbare vacatures",
        "sign in",
        "log in",
        "create an account",
    }
)
_MENU_LINES = _ALWAYS_CHROME | frozenset(
    {
        "academictransfer",
        "jobs and employers",
        "jobs",
        "premium employers",
        "academic employers",
        "universities of applied sciences",
        "career journey",
        "orientation",
        "matching",
        "preparation",
        "e-learning",
        "events",
        "vacatures",
        "contact",
        "home",
        "about",
        "about us",
        "careers",
        "technology",
        "solution",
        "solutions",
        "compliance",
        "navigation menu",
        "blog",
        "news",
        "login",
        "menu",
        "en",
        "nl",
        "de",
        "fr",
    }
)
_COUNTRIES = frozenset(
    {
        "australia",
        "belgium",
        "brasil",
        "brazil",
        "canada",
        "ceska republika",
        "česká republika",
        "danmark",
        "denmark",
        "deutschland",
        "germany",
        "estonia",
        "espana",
        "españa",
        "spain",
        "finland",
        "france",
        "hungary",
        "india",
        "italy",
        "luxembourg",
        "malaysia",
        "maroc",
        "morocco",
        "nederland",
        "netherlands",
        "norge",
        "norway",
        "philippines",
        "polska",
        "poland",
        "portugal",
        "romania",
        "slovensko",
        "slovakia",
        "sverige",
        "sweden",
        "united kingdom",
        "united states",
    }
)
_LANGUAGE_MENU = frozenset(
    {
        "english",
        "nederlands",
        "dutch",
        "engels",
        "frans",
        "french",
        "duits",
        "german",
        "portugees",
        "portuguese",
        "spaans",
        "spanish",
        "zweeds",
        "swedish",
        "pools",
        "polish",
        "tsjechisch",
        "czech",
        "slowaaks",
        "slovak",
        "noors",
        "norwegian",
        "deens",
        "danish",
        "italiano",
        "italian",
        "francais",
        "français",
        "deutsch",
        "espanol",
        "español",
    }
)
_CONSENT_MARKERS = (
    "trustarc",
    "auto-block",
    "autoblock",
    "consent mode",
    "before any other script",
    "3rd-party",
    "third-party script",
    "core.min.js",
    "data-ta-type",
    "gtm-consent",
    "notice id",
    "gtm consent",
)
_LANG_CODE = re.compile(
    r"^(?:english|nederlands|dutch|german|french|spanish|portuguese|engels|frans|duits|"
    r"portugees|spaans|italiano)\s*\([a-z]{2}\)$"
)
_DAYS_REMAINING = re.compile(r"^\d+\s+days remaining$")
_DASH_LINE = re.compile(r"^[\s\-–—_=.*|·•]+$")
_DASH_RUN = re.compile(r"(?:\s*[-–—]){3,}")
_SECTION_HEADING = re.compile(
    r"(?i)^(about( the (role|job|team))?|the role|requirements|responsibilities|qualifications|"
    r"what we offer|benefits|how to apply|your profile|skills|tasks|the team|who you are|"
    r"what you.ll do|what you will do|job description|functiebeschrijving|functie-eisen|"
    r"wat je gaat doen|wat wij vragen|wat wij bieden|jouw profiel|over de functie)$"
)
_META_LINE = re.compile(
    r"(?i)^(deadline|published|vacancy id|vacaturenummer|research fields|job types|education level|"
    r"weekly hours|salary(?: indication)?|location|hoofdlokatie|type dienstverband|categorie|"
    r"hours|apply by|closing date)\b"
)
_PLACE_LABEL = re.compile(r"(?i)^(?:location|local|place|based in|hoofdlokatie)\s*[:\-]?\s*(.+)$")


def prepare_posting(text: str, title: str = "") -> str:
    """The job text without the site menu, cut on a line before the cap."""

    cleaned = strip_page_chrome(text, title).strip()
    if len(cleaned) <= _POSTING_CAP:
        return cleaned
    cut = cleaned.rfind("\n", 0, _POSTING_CAP)
    if cut < _POSTING_CAP // 2:
        cut = cleaned.rfind(" ", 0, _POSTING_CAP)
    if cut < _POSTING_CAP // 2:
        cut = _POSTING_CAP
    return cleaned[:cut].strip()


def strip_page_chrome(text: str, title: str = "") -> str:
    """Drop navigation, consent comments, and dash runs. Keep the description."""

    source = [_tidy_line(line) for line in readable_text(text).splitlines()]
    drop: set[int] = set()
    for index, line in enumerate(source):
        if line and _always_chrome(line):
            drop.add(index)
    run: list[int] = []

    def flush() -> None:
        chosen = [index for index in run if source[index] and _menu_item(source[index])]
        if len(chosen) >= 4:
            drop.update(chosen)
        run.clear()

    for index, line in enumerate(source):
        if not line or _menu_item(line) or _always_chrome(line):
            run.append(index)
            continue
        flush()
    flush()
    kept = [line for index, line in enumerate(source) if line and index not in drop]
    kept = _drop_repeated_short_runs(kept)
    if title:
        kept = _drop_banners(kept, title)
    kept = _drop_topic_catalog(kept, title)
    return "\n".join(kept).strip()


def cleaned_posting(text: str, title: str = "") -> str:
    """A short posting for the page. Blank lines and site chrome are left out."""

    lines = [line.strip() for line in strip_page_chrome(text, title).splitlines() if line.strip()]
    if title and lines and lines[0].lstrip("#").strip().casefold() == title.casefold():
        lines = lines[1:]
    blocks: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        if prose:
            blocks.append(" ".join(prose))
            prose.clear()

    for line in lines:
        plain = line.lstrip("#").strip().rstrip(":")
        if _SECTION_HEADING.match(plain):
            flush()
            blocks.append(f"**{plain}**")
            continue
        if _META_LINE.match(plain):
            flush()
            blocks.append(plain)
            continue
        if line.startswith(("- ", "* ")):
            flush()
            blocks.append(line if line.startswith("- ") else f"- {line[2:].strip()}")
            continue
        prose.append(plain)
    flush()
    return "\n\n".join(blocks).strip()


def posting_place(text: str) -> str:
    for line in [item.strip() for item in strip_page_chrome(text).splitlines() if item.strip()]:
        labeled = _PLACE_LABEL.match(line.lstrip("#").strip())
        if labeled:
            return labeled.group(1)[:80]
    for line in [item.strip() for item in strip_page_chrome(text).splitlines() if item.strip()]:
        if len(line) <= 60 and any(_has_place(line, city) for city in _NL_CITIES):
            return line[:80]
    return ""


def posting_workplace(text: str) -> str:
    return _mentioned_workplace(strip_page_chrome(text))


def _tidy_line(line: str) -> str:
    cleaned = re.sub(r"(?i)\s*view on google maps\s*$", "", _DASH_RUN.sub(" ", line))
    cleaned = re.sub(r"(?i)\s*what are you searching for\??\s*search\b", " ", cleaned)
    cleaned = re.sub(r"(?i)\s*geselecteerde taal:\s*\w+", " ", cleaned)
    return " ".join(cleaned.split())


def _button_glue(line: str) -> bool:
    folded = re.sub(r"\d+\s*days remaining", " ", line.casefold())
    folded = folded.replace("apply now", " ").replace("view on google maps", " ")
    folded = re.sub(r"\bsave\b", " ", folded)
    return not folded.strip(" -|")


def _bare(line: str) -> str:
    stripped = re.sub(r"^(?:[-*•]\s+)+", "", line.strip())
    return stripped.lstrip("#").strip()


def _always_chrome(line: str) -> bool:
    folded = line.casefold().strip()
    bare = _bare(line).casefold().strip()
    if not folded or _DASH_LINE.match(folded) or _button_glue(folded):
        return True
    if bare in _ALWAYS_CHROME or bare.endswith(" logo"):
        return True
    if bare.startswith("-->") or bare in {"terug", "back", "search", "directly to"}:
        return True
    if bare.startswith("go to ") and len(bare) <= 80 and not bare.endswith((".", "!", "?")):
        return True
    if "what are you searching for" in bare and len(bare) <= 90 and "." not in bare:
        return True
    if _doubled_label(bare):
        return True
    if _DAYS_REMAINING.match(bare) or _LANG_CODE.match(bare):
        return True
    if re.match(r"^careers\s*[|\-–—]\s*\S", bare) and len(bare) <= 40:
        return True
    if _nav_glue(bare):
        return True
    if any(marker in bare for marker in _CONSENT_MARKERS):
        return True
    if len(bare) <= 120 and ("skip navigation" in bare or "back to navigation" in bare):
        return True
    if len(bare) <= 80 and any(phrase in bare for phrase in _NOISE_LINE):
        return True
    return False


_NAV_TOKENS = frozenset(
    {
        "home",
        "about",
        "us",
        "careers",
        "contact",
        "technology",
        "solution",
        "solutions",
        "compliance",
        "navigation",
        "menu",
        "blog",
        "news",
        "login",
    }
)


def _nav_glue(line: str) -> bool:
    folded = line.casefold()
    if "navigation menu" in folded and len(folded) <= 80:
        return True
    tokens = folded.split()
    return len(tokens) >= 2 and all(token in _NAV_TOKENS for token in tokens)


def _doubled_label(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120 or stripped.endswith((".", "!", "?")) or ". " in stripped:
        return False
    folded = re.sub(r"\s+", "", stripped.casefold())
    if len(folded) < 8 or len(folded) % 2:
        return False
    half = len(folded) // 2
    return folded[:half] == folded[half:]


def _sentence_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("- ", "* ")):
        return False
    if len(stripped) >= 80 and stripped.endswith((".", "!", "?")):
        return True
    return len(stripped.split()) >= 14 and "." in stripped


def _catalog_line(line: str, title: str) -> bool:
    bare = _bare(line).rstrip(":")
    if not bare:
        return False
    if _META_LINE.match(bare):
        return False
    if title and bare.casefold() == " ".join(title.split()).casefold():
        return False
    if ". " in bare or bare.endswith((".", "!")):
        return False
    if bare.endswith("?") and not line.strip().startswith(("- ", "* ")):
        return False
    return len(bare.split()) <= 12 and len(bare) <= 110


def _menu_heading(line: str) -> bool:
    plain = _bare(line).rstrip(":")
    return bool(_SECTION_HEADING.match(plain)) and len(plain.split()) <= 4


def _vacancy_start(lines: list[str], title: str) -> int | None:
    name = " ".join(title.split()).casefold()
    for index, line in enumerate(lines):
        if name and len(name) >= 8 and line.casefold().startswith(name) and line.casefold() != name:
            return index
        if _sentence_line(line):
            return index
    for index, line in enumerate(lines):
        plain = _bare(line).rstrip(":")
        if not _SECTION_HEADING.match(plain):
            continue
        following = lines[index + 1 : index + 5]
        if sum(1 for item in following if _catalog_line(item, title)) >= 3:
            continue
        return index
    for index, line in enumerate(lines):
        if _SECTION_HEADING.match(_bare(line).rstrip(":")):
            return index
    return None


def _drop_topic_catalog(lines: list[str], title: str) -> list[str]:
    """Drop a long topic menu that sits above the vacancy."""

    start = _vacancy_start(lines, title)
    if start is None:
        return lines
    drop: set[int] = set()
    run: list[int] = []

    def flush() -> None:
        if len(run) >= 8:
            drop.update(run)
        run.clear()

    for index, line in enumerate(lines[:start]):
        if _catalog_line(line, title) or _menu_heading(line):
            run.append(index)
            continue
        flush()
    flush()
    return [line for index, line in enumerate(lines) if index not in drop]


def _plain_short_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("- ", "* ")):
        return False
    plain = stripped.lstrip("#").rstrip(":")
    if _META_LINE.match(plain) or _SECTION_HEADING.match(plain):
        return False
    if len(stripped) > 40:
        return False
    return 1 <= len(stripped.split()) <= 3


def _repeated_width(texts: list[str]) -> int:
    size = len(texts)
    for width in range(4, size // 2 + 1):
        block = texts[:width]
        count = 1
        while width * (count + 1) <= size and texts[width * count : width * (count + 1)] == block:
            count += 1
        if count >= 2:
            return width * count
    return 0


def _drop_repeated_short_runs(lines: list[str]) -> list[str]:
    """A block of short lines that appears twice is a site menu."""

    drop: set[int] = set()
    runs: list[tuple[tuple[str, ...], list[int]]] = []
    index = 0
    while index < len(lines):
        if not _plain_short_line(lines[index]):
            index += 1
            continue
        texts: list[str] = []
        positions: list[int] = []
        while index < len(lines) and _plain_short_line(lines[index]):
            texts.append(lines[index].casefold())
            positions.append(index)
            index += 1
        repeated = _repeated_width(texts)
        if repeated:
            drop.update(positions[:repeated])
            continue
        if len(texts) >= 4:
            runs.append((tuple(texts), positions))
    counts: dict[tuple[str, ...], int] = {}
    for signature, _positions in runs:
        counts[signature] = counts.get(signature, 0) + 1
    for signature, positions in runs:
        if counts[signature] >= 2:
            drop.update(positions)
    return [line for index, line in enumerate(lines) if index not in drop]


def _menu_item(line: str) -> bool:
    folded = _bare(line).casefold().strip()
    if folded in _MENU_LINES or folded in _COUNTRIES or folded in _LANGUAGE_MENU:
        return True
    return bool(_LANG_CODE.match(folded) or folded.endswith(" logo"))


def _drop_banners(lines: list[str], title: str) -> list[str]:
    name = " ".join(title.split()).casefold()
    if len(name) < 8:
        return lines
    kept: list[str] = []
    for index, line in enumerate(lines):
        folded = line.casefold()
        later = lines[index + 1 :]
        if (
            folded.startswith(name)
            and folded != name
            and ("—" in line or "|" in line or " - " in line)
            and any(item.casefold().startswith(name) for item in later)
        ):
            continue
        kept.append(line)
    return kept


def readable_text(value: str) -> str:
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"(?i)<\s*(script|style)\b[^>]*>.*?</\s*\1\s*>", " ", text, flags=re.DOTALL)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)<\s*h([1-6])[^>]*>",
        lambda match: "\n" + "#" * min(int(match.group(1)), 3) + " ",
        text,
    )
    text = re.sub(r"(?i)</\s*h[1-6]\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|section|article|tr|ul|ol|li|table)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    kept: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if kept and not previous_blank:
                kept.append("")
            previous_blank = True
            continue
        previous_blank = False
        kept.append(line)
    return "\n".join(kept).strip()


def _closing_date(item: dict[str, object]) -> date | None:
    return _date_value(item.get("closing_date") or item.get("deadline"))


def _date_value(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.casefold() in {"true", "1", "yes", "remote"}
    return False
