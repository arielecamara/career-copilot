"""Turn pasted job text into a checklist. This module does not generate a resume."""

from __future__ import annotations

import re
from datetime import date

from career_copilot.extract import skills_in_text
from career_copilot.inputs import require_as_of, require_job_text
from career_copilot.models import (
    ChecklistItem,
    ChecklistTally,
    ConfirmationState,
    ConstraintKind,
    ConstraintResult,
    DocumentRequest,
    EvidenceCategory,
    ExperienceYears,
    HardConstraint,
    JobAnalysis,
    ProfileAtom,
    RequirementKind,
    RequirementStatus,
)
from career_copilot.vocab import SKILL_LEXICON, skill_named_in
from career_copilot.years import experience_span, meets_minimum

# SPEC.md allows this set and no other skill synonym. Docker is not Kubernetes.
SKILL_SYNONYMS: tuple[tuple[str, ...], ...] = (("Postgres", "PostgreSQL", "SQL"),)

# A higher degree does not satisfy a lower one. Bare "master" is not a degree.
DEGREE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("phd", "ph.d", "doctorate", "doctoral"),
    ("master's", "masters", "msc", "m.sc"),
    ("bachelor's", "bachelors", "bsc", "b.sc"),
)

_MANDATORY_HEADINGS = {
    "requirements",
    "required",
    "required qualifications",
    "minimum qualifications",
    "basic qualifications",
    "qualifications",
    "must have",
    "must-have",
    "what you need",
    "what you'll need",
    "what you will need",
    "what we're looking for",
    "what we are looking for",
}
_PREFERRED_HEADINGS = {
    "preferred",
    "preferred qualifications",
    "nice to have",
    "nice-to-have",
    "nice to haves",
    "bonus",
    "plus",
    "desired",
    "good to have",
}
_DUTY_HEADINGS = {
    "responsibilities",
    "responsibility",
    "what you'll do",
    "what you will do",
    "the role",
    "your role",
}
_SKIP_HEADINGS = {
    "about",
    "about us",
    "about the company",
    "benefits",
    "perks",
    "what we offer",
    "what you'll get",
    "equal opportunity",
    "how to apply",
    "our team",
    "who we are",
    "compensation",
}

_BULLET = re.compile(r"^(?:[-*•▪‣]|\d+[.)])\s+")
_HANDS_ON = re.compile(
    r"\b(?:experience|experienced|hands-on|hands on|production|proficient|proficiency|"
    r"worked with|administer(?:ed|ing)?|built|building|develop(?:ed|ing)|"
    r"implement(?:ed|ing)?|delivered)\b",
    re.IGNORECASE,
)
_CITIZENSHIP = re.compile(r"\b(?:citizen(?:ship)?|nationality|nationals?)\b", re.IGNORECASE)
_CITIZEN_PHRASE = re.compile(
    r"\b(?:[A-Za-z]+\s+citizen(?:ship)?|citizen(?:ship)?\s+of\s+[A-Za-z]+)\b",
    re.IGNORECASE,
)
_WORK_AUTH = re.compile(
    r"\b(?:authori[sz]ed to work|authori[sz]ation to work|work authori[sz]ation|"
    r"right to work|eligible to work|security clearance)\b",
    re.IGNORECASE,
)
_LICENSE = re.compile(
    r"\b(?:licen[sc]e[ds]?|licen[sc]ure|bar admission|certification required|must be certified)\b",
    re.IGNORECASE,
)
_YEARS = re.compile(
    r"\b(?:at least|minimum(?:\s+of)?|min\.?)?\s*(\d+)\s*\+?\s*years?\b",
    re.IGNORECASE,
)
_YEARS_CUE = re.compile(
    r"\b(?:experience|professional|relevant|proficient|background)\b|\byears?\s+of\b|\d+\s*\+\s*years?",
    re.IGNORECASE,
)
_DOCUMENT_CUES: tuple[tuple[DocumentRequest, re.Pattern[str]], ...] = (
    (DocumentRequest.cover_letter, re.compile(r"\b(?:cover(?:ing)? letter|letter of motivation|motivation letter)\b", re.IGNORECASE)),
    (DocumentRequest.portfolio, re.compile(r"\bportfolio\b", re.IGNORECASE)),
    (DocumentRequest.writing_sample, re.compile(r"\bwriting sample\b", re.IGNORECASE)),
    (DocumentRequest.transcript, re.compile(r"\bacademic transcripts?\b|\btranscripts?\b", re.IGNORECASE)),
)


def analyze_job(text: str, atoms: list[ProfileAtom], *, as_of: date) -> JobAnalysis:
    """Map pasted job text to confirmed atoms. Unconfirmed atoms are ignored."""

    text = require_job_text(text)
    as_of = require_as_of(as_of)
    confirmed = [atom for atom in atoms if atom.confirmation == ConfirmationState.confirmed]
    phrases = _search_phrases(confirmed)
    requirements: list[ChecklistItem] = []
    constraints: list[HardConstraint] = []
    seen_requirements: set[str] = set()
    seen_constraints: set[tuple[str, str]] = set()
    for line, kind in _requirement_lines(text, phrases):
        item = _requirement(line, kind, confirmed, phrases)
        if item is not None and item.text.casefold() not in seen_requirements:
            seen_requirements.add(item.text.casefold())
            requirements.append(item)
    for line, section in _all_lines(text):
        for constraint in _constraints(line, section, confirmed, phrases, as_of):
            key = (constraint.kind.value, constraint.text.casefold())
            if key in seen_constraints:
                continue
            seen_constraints.add(key)
            constraints.append(constraint)
    span = experience_span(confirmed, as_of=as_of)
    return JobAnalysis(
        requirements=requirements,
        hard_constraints=constraints,
        years=ExperienceYears(
            days=span.days,
            as_of=as_of.isoformat(),
            complete=span.complete,
            intervals=[f"{start.isoformat()}..{end.isoformat()}" for start, end in span.intervals],
        ),
        tally=checklist_tally(requirements),
        requested_documents=requested_documents(text),
    )


def requested_documents(text: str) -> list[DocumentRequest]:
    """Documents named by the posting. This reader does not call a model."""

    found: list[DocumentRequest] = []
    for kind, pattern in _DOCUMENT_CUES:
        if pattern.search(text) and kind not in found:
            found.append(kind)
    return found


def checklist_tally(requirements: list[ChecklistItem]) -> ChecklistTally:
    """Count statuses. Unknown lines stay out of the percentage.

    met counts 1, partial counts 0.5, and missing counts 0. The percentage is
    that total divided by the number of judged lines. It does not judge a sentence.
    """

    counts = {status: 0 for status in RequirementStatus}
    for item in requirements:
        counts[item.status] += 1
    judged = counts[RequirementStatus.met] + counts[RequirementStatus.partial] + counts[RequirementStatus.missing]
    percent = None
    if judged:
        percent = round(
            (counts[RequirementStatus.met] + 0.5 * counts[RequirementStatus.partial]) / judged * 100,
            1,
        )
    return ChecklistTally(
        met=counts[RequirementStatus.met],
        partial=counts[RequirementStatus.partial],
        missing=counts[RequirementStatus.missing],
        unknown=counts[RequirementStatus.unknown],
        percent=percent,
    )


def atom_supports(atom: ProfileAtom, term: str) -> bool:
    """True when the atom writes `term` or a SPEC synonym of it."""

    for name in _synonym_group(term):
        if skill_named_in(name, atom.source_excerpt) or skill_named_in(name, atom.statement):
            return True
        if any(skill.casefold() == name.casefold() for skill in atom.skills):
            return True
    return False


def _requirement(
    line: str,
    kind: RequirementKind,
    atoms: list[ProfileAtom],
    phrases: list[str],
) -> ChecklistItem | None:
    skills = _terms(line, phrases)
    degrees = _degree_groups(line)
    if not skills and not degrees:
        if _is_hard_constraint_line(line):
            return None
        return ChecklistItem(text=line, kind=kind, status=RequirementStatus.unknown)
    hands_on = _HANDS_ON.search(line) is not None
    parts: list[tuple[RequirementStatus, list[ProfileAtom]]] = []
    for term in skills:
        parts.append(_skill_status(term, atoms, hands_on=hands_on))
    if degrees:
        parts.append(_degree_status(line, degrees, atoms))
    status, evidence = _combine(parts)
    excerpt = evidence[0].source_excerpt if evidence else ""
    return ChecklistItem(
        text=line,
        kind=kind,
        status=status,
        evidence_ids=[atom.evidence_id for atom in evidence],
        excerpt=excerpt,
    )


def _skill_status(
    term: str,
    atoms: list[ProfileAtom],
    *,
    hands_on: bool,
) -> tuple[RequirementStatus, list[ProfileAtom]]:
    supporters = [atom for atom in atoms if atom_supports(atom, term)]
    if not supporters:
        return RequirementStatus.missing, []
    ordered = sorted(supporters, key=lambda atom: _work_rank(atom.category), reverse=True)
    if not hands_on or ordered[0].category == EvidenceCategory.used_in_job:
        return RequirementStatus.met, ordered
    return RequirementStatus.partial, ordered


def _degree_status(
    line: str,
    groups: list[tuple[str, ...]],
    atoms: list[ProfileAtom],
) -> tuple[RequirementStatus, list[ProfileAtom]]:
    matched = [group for group in groups if _atoms_for_degree(group, atoms)]
    alternative = " or " in line.casefold()
    if alternative and matched:
        chosen = matched[0]
        return RequirementStatus.met, _atoms_for_degree(chosen, atoms)
    if len(matched) == len(groups):
        evidence: list[ProfileAtom] = []
        for group in matched:
            evidence.extend(_atoms_for_degree(group, atoms))
        return RequirementStatus.met, _unique(evidence)
    if matched:
        evidence = []
        for group in matched:
            evidence.extend(_atoms_for_degree(group, atoms))
        return RequirementStatus.partial, _unique(evidence)
    return RequirementStatus.missing, []


def _combine(
    parts: list[tuple[RequirementStatus, list[ProfileAtom]]],
) -> tuple[RequirementStatus, list[ProfileAtom]]:
    statuses = [status for status, _evidence in parts]
    evidence = _unique([atom for _status, atoms in parts for atom in atoms])
    if all(status == RequirementStatus.met for status in statuses):
        return RequirementStatus.met, evidence
    if any(status in {RequirementStatus.met, RequirementStatus.partial} for status in statuses):
        return RequirementStatus.partial, evidence
    return RequirementStatus.missing, []


def _constraints(
    line: str,
    section: str,
    atoms: list[ProfileAtom],
    phrases: list[str],
    as_of: date,
) -> list[HardConstraint]:
    found: list[HardConstraint] = []
    if _CITIZENSHIP.search(line):
        found.append(_phrase_constraint(line, ConstraintKind.citizenship, atoms, _CITIZEN_PHRASE))
    if _WORK_AUTH.search(line):
        found.append(_phrase_constraint(line, ConstraintKind.work_authorization, atoms, _WORK_AUTH))
    if _LICENSE.search(line):
        found.append(_phrase_constraint(line, ConstraintKind.license, atoms, _LICENSE))
    years = _minimum_years(line, section)
    if years is not None:
        found.append(_years_constraint(line, years, atoms, phrases, as_of))
    return found


def _phrase_constraint(
    line: str,
    kind: ConstraintKind,
    atoms: list[ProfileAtom],
    pattern: re.Pattern[str],
) -> HardConstraint:
    """Pass only when a confirmed excerpt contains the same constraint phrase.

    Silence stays unknown. A different citizenship or license is not a fail.
    """

    match = pattern.search(line)
    if match is None:
        return HardConstraint(text=line, kind=kind, result=ConstraintResult.unknown)
    phrase = match.group(0).casefold()
    for atom in atoms:
        if phrase in atom.source_excerpt.casefold():
            return HardConstraint(text=line, kind=kind, result=ConstraintResult.passed)
    return HardConstraint(text=line, kind=kind, result=ConstraintResult.unknown)


def _years_constraint(
    line: str,
    required: int,
    atoms: list[ProfileAtom],
    phrases: list[str],
    as_of: date,
) -> HardConstraint:
    skills = _terms(line, phrases)
    if skills:
        return _skill_years_constraint(line, required, skills, atoms, as_of)
    span = experience_span(atoms, as_of=as_of)
    if span.days == 0:
        result = ConstraintResult.unknown
        observed = None
    elif meets_minimum(span.days, required):
        result = ConstraintResult.passed
        observed = span.days
    elif span.complete:
        result = ConstraintResult.failed
        observed = span.days
    else:
        result = ConstraintResult.unknown
        observed = span.days
    return HardConstraint(
        text=line,
        kind=ConstraintKind.minimum_years,
        result=result,
        required_years=required,
        observed_days=observed,
    )


def _skill_years_constraint(
    line: str,
    required: int,
    skills: list[str],
    atoms: list[ProfileAtom],
    as_of: date,
) -> HardConstraint:
    results: list[ConstraintResult] = []
    observed: list[int] = []
    for skill in skills:
        span = experience_span(atoms, as_of=as_of, include=lambda atom, name=skill: atom_supports(atom, name))
        known = any(atom_supports(atom, skill) for atom in atoms)
        if not known or span.days == 0:
            results.append(ConstraintResult.unknown)
            continue
        observed.append(span.days)
        if meets_minimum(span.days, required):
            results.append(ConstraintResult.passed)
        elif span.complete:
            results.append(ConstraintResult.failed)
        else:
            results.append(ConstraintResult.unknown)
    if any(result == ConstraintResult.failed for result in results):
        result = ConstraintResult.failed
    elif any(result == ConstraintResult.unknown for result in results):
        result = ConstraintResult.unknown
    else:
        result = ConstraintResult.passed
    return HardConstraint(
        text=line,
        kind=ConstraintKind.minimum_years,
        result=result,
        required_years=required,
        observed_days=min(observed) if observed else None,
    )


def _requirement_lines(text: str, phrases: list[str]) -> list[tuple[str, RequirementKind]]:
    section = "none"
    lines: list[tuple[str, RequirementKind]] = []
    for raw in text.splitlines():
        heading = _heading(raw)
        if heading is not None:
            section = heading
            continue
        body = _BULLET.sub("", raw.strip()).strip()
        if not body:
            continue
        bullet = _BULLET.match(raw.strip()) is not None
        if section in {"mandatory", "duties"} and (bullet or len(body) <= 240):
            lines.append((body, RequirementKind.mandatory))
        elif section == "preferred" and (bullet or len(body) <= 240):
            lines.append((body, RequirementKind.preferred))
        elif section == "none" and bullet and (
            _terms(body, phrases) or _degree_groups(body) or _is_hard_constraint_line(body)
        ):
            lines.append((body, RequirementKind.mandatory))
    return lines


def _all_lines(text: str) -> list[tuple[str, str]]:
    section = "none"
    lines: list[tuple[str, str]] = []
    for raw in text.splitlines():
        heading = _heading(raw)
        if heading is not None:
            section = heading
            continue
        body = _BULLET.sub("", raw.strip()).strip()
        if body:
            lines.append((body, section))
    return lines


def _heading(raw: str) -> str | None:
    if _BULLET.match(raw.strip()):
        return None
    text = re.sub(r"\s+", " ", raw.strip().casefold()).rstrip(":").strip()
    if not text or len(text) > 60 or any(mark in text for mark in ".!?"):
        return None
    if text in _MANDATORY_HEADINGS:
        return "mandatory"
    if text in _PREFERRED_HEADINGS:
        return "preferred"
    if text in _DUTY_HEADINGS:
        return "duties"
    if text in _SKIP_HEADINGS:
        return "skip"
    return None


def _minimum_years(line: str, section: str) -> int | None:
    if _YEARS_CUE.search(line) is None:
        return None
    if section == "skip" and re.search(r"\bexperience\b", line, re.IGNORECASE) is None:
        return None
    match = _YEARS.search(line)
    if match is None:
        return None
    return int(match.group(1))


def _is_hard_constraint_line(line: str) -> bool:
    return (
        _CITIZENSHIP.search(line) is not None
        or _WORK_AUTH.search(line) is not None
        or _LICENSE.search(line) is not None
        or _minimum_years(line, "mandatory") is not None
    )


def _search_phrases(atoms: list[ProfileAtom]) -> list[str]:
    phrases = list(SKILL_LEXICON)
    for group in SKILL_SYNONYMS:
        phrases.extend(group)
    for atom in atoms:
        phrases.extend(atom.skills)
    unique = list(dict.fromkeys(phrase.strip() for phrase in phrases if phrase.strip()))
    unique.sort(key=len, reverse=True)
    return unique


def _terms(line: str, phrases: list[str]) -> list[str]:
    return skills_in_text(line, phrases)


def _synonym_group(term: str) -> tuple[str, ...]:
    folded = term.casefold()
    for group in SKILL_SYNONYMS:
        if any(name.casefold() == folded for name in group):
            return group
    return (term,)


def _degree_groups(line: str) -> list[tuple[str, ...]]:
    found = [group for group in DEGREE_GROUPS if any(skill_named_in(token, line) for token in group)]
    return found


def _atoms_for_degree(group: tuple[str, ...], atoms: list[ProfileAtom]) -> list[ProfileAtom]:
    matched = [
        atom
        for atom in atoms
        if any(skill_named_in(token, atom.source_excerpt) for token in group)
    ]
    return sorted(matched, key=lambda atom: _degree_rank(atom.category), reverse=True)


def _degree_rank(category: EvidenceCategory) -> int:
    if category == EvidenceCategory.coursework:
        return 4
    if category == EvidenceCategory.used_in_job:
        return 3
    if category == EvidenceCategory.project:
        return 2
    return 1


def _work_rank(category: EvidenceCategory) -> int:
    if category == EvidenceCategory.used_in_job:
        return 3
    if category == EvidenceCategory.project:
        return 2
    if category == EvidenceCategory.coursework:
        return 2
    return 1


def _unique(atoms: list[ProfileAtom]) -> list[ProfileAtom]:
    chosen: list[ProfileAtom] = []
    seen: set[str] = set()
    for atom in atoms:
        if atom.evidence_id in seen:
            continue
        seen.add(atom.evidence_id)
        chosen.append(atom)
    return chosen
