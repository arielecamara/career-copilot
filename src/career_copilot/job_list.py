"""Job list for phase 3.

A search asks the local Grok login for public job pages, opens those pages,
and keeps up to three jobs at 50 percent or above. The CV is used for the
match. It is not sent to a job site and it is not stored in the job list.
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
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_copilot.errors import CareerCopilotError
from career_copilot.roles import WorkRole, child_env, grok_argv, grok_search_argv
from career_copilot.search_intent import SearchConditions

HIGH_MATCH = 70
MIN_MATCH = 50
MAX_SCORED = 5
MAX_FETCHED = 12
WEB_SCORED = 4
WEB_SHOWN = 3
SEARCH_TIMEOUT_SECONDS = 240
FALLBACK_REASON = "Nada foi encontrado, então retornamos a vaga com o maior match com o seu CV."
EMPTY_REASON = "Nada foi encontrado."
DEADLINE_NOTE = "The application deadline was not found."
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
    qualified = [job for job in latest if job.match_percent >= MIN_MATCH]
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
    hard = _hard_languages(text)
    if not hard:
        return True
    return hard <= _user_languages(conditions.languages)


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
    started = time.monotonic()

    def remaining() -> int:
        return max(0, SEARCH_TIMEOUT_SECONDS - int(time.monotonic() - started))

    if discover is None:
        try:
            discovered = _discover_with_grok(
                roles,
                conditions,
                skills,
                cv_text=cv_text,
                timeout_seconds=max(1, remaining()),
                progress=progress,
            )
        except CareerCopilotError as exc:
            if "too long" not in str(exc).casefold():
                raise
            discovered = []
    else:
        progress("Searching.")
        discovered = discover()
    candidates = [
        opening
        for opening in discovered
        if opening.url not in blocked and _web_candidate(opening, cv_text, roles, skills, today)
    ]
    traits = load_traits(traits_for_jobs(path))
    qualified: list[ListedJob] = []
    weaker: list[ListedJob] = []
    for index, opening in enumerate(candidates, start=1):
        if len(qualified) >= WEB_SHOWN:
            break
        if remaining() <= 0 and (qualified or weaker):
            break
        progress(f"Scoring {index} of {len(candidates)}.")
        try:
            raw = _opening_points(opening, cv_text, roles, conditions, score, remaining())
        except CareerCopilotError as exc:
            if "too long" not in str(exc).casefold():
                raise
            break
        judged, answered = _judge(raw, traits)
        judged.extend(_condition_gaps(opening, conditions))
        judged.extend(_language_gap(opening, conditions))
        judged.extend(_level_gap(opening, conditions))
        percent = match_percent(judged)
        if percent is None:
            continue
        listed = _listed(
            opening,
            percent,
            judged,
            conditions,
            answered=answered,
            deadline_missing=_needs_deadline_note(opening),
        )
        if percent >= MIN_MATCH:
            qualified.append(listed)
        else:
            weaker.append(listed)
    qualified.sort(key=lambda job: job.match_percent, reverse=True)
    shown = qualified[:WEB_SHOWN]
    log.below_count = len(weaker)
    log.high_count = sum(1 for job in shown if job.match_percent >= HIGH_MATCH)
    if shown:
        log.latest = shown
        log.found_count = len(shown)
        log.reason = ""
    elif weaker:
        best = max(weaker, key=lambda job: job.match_percent)
        log.latest = [best]
        log.found_count = 0
        log.reason = FALLBACK_REASON
    else:
        log.latest = []
        log.found_count = 0
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
    text = readable_text(page)[:8000]
    if not text:
        raise CareerCopilotError("Paste the posting text.")
    first = next((line.lstrip("#").strip() for line in text.splitlines() if line.strip()), "")
    title = " ".join((first or "Job").split())[:120]
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
    if key in log.skipped_urls:
        return PasteOutcome(note="This posting was removed from the list.")
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
        return PasteOutcome(note="This posting could not be scored.")
    listed = _listed(opening, percent, judged, conditions, answered=answered)
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
        description=opening.text[:4000],
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
    if opening.points:
        return list(opening.points)
    token = _score_budget.set(max(1, seconds_left))
    try:
        return list(score(cv_text, _scoring_text(opening.text, roles, conditions)))
    finally:
        _score_budget.reset(token)


def _web_candidate(
    opening: _Opening,
    cv_text: str,
    roles: list[WorkRole],
    skills: list[str],
    today: date,
) -> bool:
    if not is_open(opening.closes_on, today):
        return False
    if _explicitly_closed(opening.text):
        return False
    deadline = _deadline_date(opening.text)
    if deadline is not None and deadline < today:
        return False
    return _shares_cv_term(cv_text, f"{opening.title}\n{opening.text}", roles, skills)


def _shares_cv_term(cv_text: str, posting: str, roles: list[WorkRole], skills: list[str]) -> bool:
    cv_blob = cv_text.casefold()
    post = posting.casefold()
    terms = [skill.strip() for skill in skills if skill.strip()]
    terms.extend(role.name.strip() for role in roles if role.name.strip())
    for term in terms:
        folded = term.casefold()
        if len(folded) < 2:
            continue
        if _term_in(cv_blob, folded) and _term_in(post, folded):
            return True
    return False


def _term_in(blob: str, term: str) -> bool:
    if " " in term:
        return term in blob
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", blob) is not None


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


def _hard_languages(text: str) -> set[str]:
    hard: set[str] = set()
    for sentence in _sentences(text):
        if not _word(sentence, _HARD_LANGUAGE) or _word(sentence, _SOFT_LANGUAGE):
            continue
        hard.update(_languages_in(sentence))
    return hard


def _user_languages(text: str) -> set[str]:
    return _languages_in(text)


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


def _discover_with_grok(
    roles: list[WorkRole],
    conditions: SearchConditions,
    skills: list[str],
    cv_text: str = "",
    timeout_seconds: int = SEARCH_TIMEOUT_SECONDS,
    progress: Callable[[str], None] | None = None,
) -> list[_Opening]:
    if progress is not None:
        progress("Searching.")
    executable = shutil.which("grok")
    if executable is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(_search_prompt(roles, conditions, skills, cv_text), encoding="utf-8")
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
            raise CareerCopilotError("Grok took too long. Nothing was saved.") from exc
    if completed.returncode != 0:
        raise CareerCopilotError("Grok did not return a search. Stay signed in with grok login and try again.")
    return openings_from_search(
        _parse_search_jobs(completed.stdout or ""),
        fetch_posting,
        conditions,
        progress=progress,
    )


def _search_prompt(roles: list[WorkRole], conditions: SearchConditions, skills: list[str], cv_text: str = "") -> str:
    names = ", ".join(role.name for role in roles) or "open roles"
    lines = [
        "Search the public web for job postings that are still open.",
        "Run several different web searches. Use the roles, the skills, and the filled fields below.",
        "A blank field is not a preference. Do not add a preference that is not listed.",
        "Open the promising pages. Return only jobs whose pages you actually opened.",
        "Return only JSON:",
        '{"jobs":[{"title":"role","company":"employer","url":"https://example.com/job","requirements":[{"text":"short requirement","status":"match","basis":""}]}]}',
        "Rules:",
        "- Use an https link to the employer or another public job page.",
        "- Do not use LinkedIn.",
        "- Do not invent a job, a company, or a link.",
        "- Do not put the CV text into the web search.",
        "- Do not guess a match percent.",
        "- status is match, partial, no_match, or assumed.",
        "- match means the CV states it. partial means only a related part. no_match means the CV does not show it.",
        "- assumed means the CV does not state it, but one fact in the CV makes it plausible. Put that fact in basis.",
        "- Leave basis empty unless status is assumed.",
        f"Roles: {names}.",
    ]
    if skills:
        lines.append("Skills: " + ", ".join(skills) + ".")
    if conditions.levels:
        lines.append("Experience level: " + ", ".join(conditions.levels) + ".")
    if conditions.employment:
        lines.append("Employment: " + ", ".join(conditions.employment) + ".")
    if conditions.workplaces:
        lines.append("Workplace: " + ", ".join(conditions.workplaces) + ".")
    if conditions.country:
        lines.append(f"Country: {conditions.country}.")
    if conditions.city:
        lines.append(f"City: {conditions.city}.")
    if not conditions.country and not conditions.city:
        lines.append("Place: worldwide.")
    elif conditions.country and not conditions.city:
        lines.append("A country without a city covers the whole country.")
    if conditions.languages.strip():
        lines.append(f"Languages: {conditions.languages}.")
    if conditions.notes.strip():
        lines.append("Preferences:")
        lines.append(conditions.notes.strip())
        lines.append("Use the preferences as guidance. A preference does not by itself exclude a page.")
    if cv_text.strip():
        lines.append("CV, for the requirement status only. Do not paste it into a web query:")
        lines.append(cv_text.strip()[:60000])
    return "\n".join(lines) + "\n"


def _parse_search_jobs(stdout: str) -> list[dict[str, object]]:
    from career_copilot.roles import _json_object

    try:
        body = _json_object(stdout)
    except CareerCopilotError as exc:
        raise CareerCopilotError("Grok did not return a search. Stay signed in with grok login and try again.") from exc
    if isinstance(body.get("text"), str) and "jobs" not in body:
        try:
            body = _json_object(str(body["text"]))
        except CareerCopilotError as exc:
            raise CareerCopilotError(
                "Grok did not return a search. Stay signed in with grok login and try again."
            ) from exc
    raw = body.get("jobs")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def openings_from_search(
    items: list[dict[str, object]],
    fetch: Callable[[str], str],
    conditions: SearchConditions | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[_Opening]:
    chosen = conditions or SearchConditions()
    planned: list[tuple[dict[str, object], str]] = []
    seen: set[str] = set()
    for item in items:
        if len(planned) == MAX_FETCHED:
            break
        url = str(item.get("url", "")).strip()[:500]
        if not url.startswith("https://") or url in seen:
            continue
        host = urllib.parse.urlparse(url).hostname or ""
        if _blocked_job_host(host) or _private_host(host):
            continue
        seen.add(url)
        planned.append((item, url))
    openings: list[_Opening] = []
    total = len(planned)
    for index, (item, url) in enumerate(planned, start=1):
        if progress is not None:
            progress(f"Opening page {index} of {total}.")
        try:
            page = fetch(url)
        except Exception:
            continue
        readable = readable_text(page)[:8000]
        if len(readable) < 200:
            continue
        title = " ".join(str(item.get("title") or "").split())
        if not title:
            title = next((line.lstrip("#").strip() for line in readable.splitlines() if line.strip()), "Job")
        company = " ".join(str(item.get("company") or "").split()) or "Unknown"
        location = _paste_location(readable, chosen)
        if not location:
            location = " ".join(str(item.get("location") or "").split())
        openings.append(
            _Opening(
                title=title[:120],
                company=company[:120],
                url=url,
                location=location[:200],
                workplace=_mentioned_workplace(readable),
                employment=_mentioned_employment(readable),
                text=readable,
                source="Web",
                points=_points_from_raw(item.get("requirements")),
            )
        )
    return openings


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
    readable = readable_text(str(text))[:8000]
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
            completed = subprocess.run(
                grok_argv(executable, prompt_file, directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180 if _score_budget.get() is None else max(1, int(_score_budget.get() or 1)),
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
        status = str(item.get("status", ""))
        basis = " ".join(str(item.get("basis", "")).split())[:180]
        if status == "assumed" and not basis:
            continue
        try:
            points.append(MatchPoint.model_validate({"text": item.get("text", ""), "status": status, "basis": basis}))
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
    text = readable_text(raw.decode("utf-8", errors="replace"))[:8000]
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
_SECTION_HEADING = re.compile(
    r"(?i)^(about( the (role|job|team))?|the role|requirements|responsibilities|qualifications|"
    r"what we offer|benefits|how to apply|your profile|skills|tasks|the team|who you are|"
    r"what you.ll do|what you will do|job description)$"
)
_PLACE_LABEL = re.compile(r"(?i)^(?:location|local|place|based in)\s*[:\-]?\s*(.+)$")


def cleaned_posting(text: str, title: str = "") -> str:
    """A short posting for the page. Blank lines and site chrome are left out."""

    lines = [line.strip() for line in readable_text(text).splitlines() if line.strip() and not _noise_line(line)]
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
        if line.startswith(("- ", "* ")):
            flush()
            blocks.append(line if line.startswith("- ") else f"- {line[2:].strip()}")
            continue
        prose.append(plain)
    flush()
    return "\n\n".join(blocks).strip()


def posting_place(text: str) -> str:
    for line in [item.strip() for item in readable_text(text).splitlines() if item.strip()]:
        labeled = _PLACE_LABEL.match(line.lstrip("#").strip())
        if labeled:
            return labeled.group(1)[:80]
    for line in [item.strip() for item in readable_text(text).splitlines() if item.strip()]:
        if len(line) <= 60 and any(_has_place(line, city) for city in _NL_CITIES):
            return line[:80]
    return ""


def posting_workplace(text: str) -> str:
    return _mentioned_workplace(text)


def _noise_line(line: str) -> bool:
    blob = line.casefold()
    if len(blob) > 80:
        return False
    return any(phrase in blob for phrase in _NOISE_LINE)


def readable_text(value: str) -> str:
    text = html.unescape(html.unescape(value or ""))
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
