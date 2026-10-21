"""Deterministic checks for a generated resume. An LLM is not the judge."""

from __future__ import annotations

import re

from career_copilot.extract import skills_in_text
from career_copilot.job import SKILL_SYNONYMS
from career_copilot.models import (
    STATEMENT_NUMBER,
    ConfirmationState,
    JobAnalysis,
    MasterProfile,
    ProfileAtom,
    RequirementStatus,
    ResumeSentence,
    SentenceKind,
    Violation,
)
from career_copilot.ownership import OWNERSHIP_VERBS, verb_appears
from career_copilot.presentation import COVER_LEAD
from career_copilot.vocab import SKILL_LEXICON, skill_named_in


def role_line(atom: ProfileAtom) -> str:
    """Role header built only from the atom's title, employer, and date text."""

    if atom.title is None or atom.employer is None or atom.start_date is None:
        return atom.statement
    when = atom.start_date.raw
    if atom.end_date is not None:
        when = f"{atom.start_date.raw} – {atom.end_date.raw}"
    return f"{atom.title} | {atom.employer} | {when}"


def validate_resume(
    sentences: list[ResumeSentence],
    profile: MasterProfile,
    analysis: JobAnalysis,
) -> list[Violation]:
    """Reject a package that cites nothing, changes a fact, or adds a skill."""

    violations: list[Violation] = []
    absent = absent_skills(analysis, profile.atoms)
    for sentence in sentences:
        violations.extend(_sentence_violations(sentence, profile))
        violations.extend(_absent_violations(sentence.text, absent))
    return violations


def absent_skills(analysis: JobAnalysis, atoms: list[ProfileAtom]) -> list[str]:
    """Skill words the job asks for that no confirmed atom names."""

    allowed = _allowed_names(atoms)
    phrases = list(SKILL_LEXICON)
    for group in SKILL_SYNONYMS:
        phrases.extend(group)
    found: list[str] = []
    seen: set[str] = set()
    for item in analysis.requirements:
        if item.status == RequirementStatus.met:
            continue
        for term in skills_in_text(item.text, phrases):
            key = term.casefold()
            if key in allowed or key in seen:
                continue
            seen.add(key)
            found.append(term)
    return found


def _sentence_violations(sentence: ResumeSentence, profile: MasterProfile) -> list[Violation]:
    cited: list[ProfileAtom] = []
    for evidence_id in sentence.evidence_ids:
        atom = profile.atom(evidence_id)
        if atom is None:
            return [_violation(sentence, "evidence", f"{evidence_id} is not in the profile")]
        if atom.confirmation != ConfirmationState.confirmed:
            return [_violation(sentence, "confirmation", f"{evidence_id} is not confirmed")]
        cited.append(atom)
    violations: list[Violation] = []
    if not _grounded_rewrite(sentence, cited):
        violations.append(_violation(sentence, "rewrite", "the sentence is not the confirmed atom"))
    for number in STATEMENT_NUMBER.findall(sentence.text):
        if not any(number in atom.source_excerpt for atom in cited):
            violations.append(_violation(sentence, "number", f"{number} is not in the cited excerpt"))
    for verb in OWNERSHIP_VERBS:
        if verb_appears(verb, sentence.text) and not any(verb_appears(verb, atom.source_excerpt) for atom in cited):
            violations.append(_violation(sentence, "ownership", f"{verb} is not in the cited excerpt"))
    for term in skills_in_text(sentence.text, list(SKILL_LEXICON)):
        if not any(_skill_allowed(term, atom) for atom in cited):
            violations.append(_violation(sentence, "skill", f"{term} is not in the cited excerpt"))
    if sentence.kind == SentenceKind.role:
        violations.extend(_role_fact_violations(sentence, cited[0]))
    return violations


def _role_fact_violations(sentence: ResumeSentence, atom: ProfileAtom) -> list[Violation]:
    exact = sentence.text == atom.statement or sentence.text == role_line(atom)
    if not exact:
        return [_violation(sentence, "employer", "the role line does not match the confirmed atom")]
    violations: list[Violation] = []
    if atom.employer and atom.employer not in sentence.text:
        violations.append(_violation(sentence, "employer", "employer is not an exact copy"))
    if atom.title and atom.title not in sentence.text:
        violations.append(_violation(sentence, "title", "title is not an exact copy"))
    if atom.start_date and atom.start_date.raw not in sentence.text:
        violations.append(_violation(sentence, "dates", "start date is not an exact copy"))
    if atom.end_date and atom.end_date.raw not in sentence.text:
        violations.append(_violation(sentence, "dates", "end date is not an exact copy"))
    return violations


def _absent_violations(text: str, absent: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for term in absent:
        if skill_named_in(term, text):
            violations.append(
                Violation(
                    code="absent_skill",
                    sentence=text,
                    detail=f"{term} is not in the confirmed profile",
                )
            )
    return violations


def _grounded_rewrite(sentence: ResumeSentence, cited: list[ProfileAtom]) -> bool:
    if sentence.kind == SentenceKind.role and sentence.text == role_line(cited[0]):
        return True
    folded = _fold_synonyms(sentence.text)
    for atom in cited:
        statement = _fold_synonyms(atom.statement)
        if folded == statement:
            return True
        if sentence.kind == SentenceKind.cover and folded == _fold_synonyms(f"{COVER_LEAD}{atom.statement}"):
            return True
    return False


def _skill_allowed(term: str, atom: ProfileAtom) -> bool:
    for name in _group(term):
        if skill_named_in(name, atom.source_excerpt):
            return True
    return False


def _allowed_names(atoms: list[ProfileAtom]) -> set[str]:
    names: set[str] = set()
    for atom in atoms:
        if atom.confirmation != ConfirmationState.confirmed:
            continue
        for term in skills_in_text(atom.source_excerpt, list(SKILL_LEXICON)):
            names.update(name.casefold() for name in _group(term))
        for skill in atom.skills:
            names.update(name.casefold() for name in _group(skill))
    return names


def _group(term: str) -> tuple[str, ...]:
    folded = term.casefold()
    for group in SKILL_SYNONYMS:
        if any(name.casefold() == folded for name in group):
            return group
    return (term,)


def _fold_synonyms(text: str) -> str:
    folded = text.casefold()
    for group in SKILL_SYNONYMS:
        canonical = group[0].casefold()
        for name in sorted(group, key=len, reverse=True):
            folded = re.sub(
                r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)",
                canonical,
                folded,
            )
    return re.sub(r"\s+", " ", folded).strip()


def _violation(sentence: ResumeSentence, code: str, detail: str) -> Violation:
    return Violation(code=code, sentence=sentence.text, detail=detail)  # type: ignore[arg-type]
