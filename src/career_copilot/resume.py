"""Assemble a resume from confirmed atoms. This module does not invent claims."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from career_copilot.errors import CareerCopilotError
from career_copilot.extract import skills_in_text
from career_copilot.job import SKILL_SYNONYMS, atom_supports
from career_copilot.models import (
    ConfirmationState,
    ConstraintResult,
    EvidenceCategory,
    JobAnalysis,
    ProfileAtom,
    RequirementStatus,
    ResumeSentence,
    SentenceKind,
)
from career_copilot.presentation import COVER_LEAD, cover_word_budget, is_publication
from career_copilot.validator import absent_skills
from career_copilot.vocab import SKILL_LEXICON

_LANGUAGE = re.compile(r"^([^:\d][^:]{0,40}?):\s+\S")


@dataclass
class ResumeBundle:
    sentences: list[ResumeSentence]
    cover: list[ResumeSentence]
    diff_markdown: str
    publication_note: bool = False


@dataclass
class _RoleGroup:
    header: ProfileAtom
    bullets: list[ProfileAtom] = field(default_factory=list)


def build_resume(
    atoms: list[ProfileAtom],
    analysis: JobAnalysis,
    *,
    cover_letter: bool = False,
    job_text: str = "",
    academic: bool = False,
) -> ResumeBundle:
    """Select confirmed atoms in profile order. Missing job skills are refused."""

    confirmed = [atom for atom in atoms if atom.confirmation == ConfirmationState.confirmed]
    if not confirmed:
        raise CareerCopilotError("The profile has no confirmed atoms.")
    groups = _groups(confirmed)
    preferred = _preferred_terms(analysis)
    sentences: list[ResumeSentence] = []
    changes: list[str] = []
    kept: list[str] = []
    omitted: list[str] = []
    shown_publications, hidden_publications, other_projects = _publication_split(groups["projects"], preferred)
    diff: list[str] = ["# Resume diff", "", "Role order follows the confirmed profile. It is not reordered.", ""]
    if academic:
        diff.insert(3, "Section order follows an academic profile: education, then experience, then publications.")
        diff.insert(4, "")

    def add_identity() -> None:
        for atom in groups["names"]:
            sentences.append(_copy(atom, SentenceKind.name))
        for atom in groups["contacts"]:
            sentences.append(_copy(atom, SentenceKind.contact))
        for atom in groups["summaries"]:
            sentence = _swap(atom, SentenceKind.summary, preferred)
            sentences.append(sentence)
            _note_change(changes, "Summary", atom, sentence.text)

    def add_experience() -> None:
        _experience_section(groups["roles"], analysis, preferred, sentences, kept, omitted, changes)
        for atom in groups["loose"]:
            sentence = _swap(atom, SentenceKind.bullet, preferred)
            sentences.append(sentence)
            _note_change(changes, "Bullet", atom, sentence.text)
            kept.append(f"- {sentence.text} ({atom.evidence_id})")

    def add_education() -> None:
        for atom in groups["education"]:
            sentences.append(_copy(atom, SentenceKind.education))

    def add_skills() -> None:
        _skill_section(groups["skills"], preferred, sentences, changes)
        for atom in groups["languages"]:
            sentences.append(_copy(atom, SentenceKind.language))

    def add_publications() -> None:
        for atom in shown_publications:
            sentences.append(_copy(atom, SentenceKind.publication))
            kept.append(f"- {atom.statement} ({atom.evidence_id})")
        for atom in hidden_publications:
            omitted.append(
                f"- {atom.statement} ({atom.evidence_id}). Publications are limited to the two most relevant."
            )

    def add_other_projects() -> None:
        for atom in other_projects:
            sentences.append(_copy(atom, SentenceKind.project))

    add_identity()
    if academic:
        add_education()
        add_experience()
        add_publications()
        add_skills()
    else:
        add_experience()
        add_publications()
        add_education()
        add_skills()
    add_other_projects()

    cover: list[ResumeSentence] = []
    if cover_letter:
        cover = _cover(groups, analysis, job_text)
    if changes:
        diff.extend(["## Changed", "", *changes, ""])
    if kept:
        diff.extend(["## Kept", "", *kept, ""])
    if omitted:
        diff.extend(["## Omitted", "", *omitted, ""])
    diff.extend(_refused(analysis, atoms))
    return ResumeBundle(
        sentences=sentences,
        cover=cover,
        diff_markdown="\n".join(diff).rstrip() + "\n",
        publication_note=bool(shown_publications or hidden_publications),
    )


def _groups(atoms: list[ProfileAtom]) -> dict[str, list]:
    grouped: dict[str, list] = {
        "names": [],
        "contacts": [],
        "summaries": [],
        "skills": [],
        "roles": [],
        "education": [],
        "projects": [],
        "languages": [],
        "loose": [],
    }
    role: _RoleGroup | None = None
    for atom in atoms:
        if _is_role_header(atom):
            role = _RoleGroup(atom)
            grouped["roles"].append(role)
            continue
        if role is not None and atom.category == EvidenceCategory.used_in_job and _same_role(role.header, atom):
            role.bullets.append(atom)
            continue
        if atom.category == EvidenceCategory.used_in_job:
            grouped["loose"].append(atom)
            role = None
            continue
        role = None
        if atom.category == EvidenceCategory.coursework:
            grouped["education"].append(atom)
            continue
        if atom.category == EvidenceCategory.project:
            grouped["projects"].append(atom)
            continue
        if atom.category != EvidenceCategory.mentioned_only:
            continue
        if _is_skill_atom(atom):
            grouped["skills"].append(atom)
        elif _is_language(atom):
            grouped["languages"].append(atom)
        elif _is_contact(atom):
            grouped["contacts"].append(atom)
        elif _is_summary(atom):
            grouped["summaries"].append(atom)
        else:
            grouped["names"].append(atom)
    return grouped


def _skill_section(
    skills: list[ProfileAtom],
    preferred: list[str],
    sentences: list[ResumeSentence],
    changes: list[str],
) -> None:
    if not skills:
        return
    ranked = sorted(skills, key=lambda atom: (not _matches_any(atom, preferred), skills.index(atom)))
    original = [atom.statement for atom in skills]
    tailored = [_surface(atom, preferred) for atom in ranked]
    if tailored != original:
        changes.append(f"- Skills: {', '.join(tailored)}.")
        changes.append(f"- Original skill order: {', '.join(original)}.")
    for atom, label in zip(ranked, tailored, strict=True):
        text = label if _fold_pair(label, atom.statement) else atom.statement
        sentences.append(ResumeSentence(text=text, evidence_ids=[atom.evidence_id], kind=SentenceKind.skill))


def _experience_section(
    roles: list[_RoleGroup],
    analysis: JobAnalysis,
    preferred: list[str],
    sentences: list[ResumeSentence],
    kept: list[str],
    omitted: list[str],
    changes: list[str],
) -> None:
    cited = {
        evidence_id
        for item in analysis.requirements
        if item.status in {RequirementStatus.met, RequirementStatus.partial}
        for evidence_id in item.evidence_ids
    }
    for group in roles:
        header_text = group.header.statement
        sentences.append(
            ResumeSentence(text=header_text, evidence_ids=[group.header.evidence_id], kind=SentenceKind.role)
        )
        kept.append(f"- {header_text} ({group.header.evidence_id})")
        chosen = _choose_bullets(group.bullets, cited, preferred)
        chosen_ids = {atom.evidence_id for atom in chosen}
        for atom in chosen:
            sentence = _swap(atom, SentenceKind.bullet, preferred)
            sentences.append(sentence)
            _note_change(changes, "Bullet", atom, sentence.text)
            kept.append(f"- {sentence.text} ({atom.evidence_id})")
        for atom in group.bullets:
            if atom.evidence_id in chosen_ids:
                continue
            omitted.append(f"- {atom.statement} ({atom.evidence_id}). Another bullet in this role matches the checklist.")


def _choose_bullets(bullets: list[ProfileAtom], cited: set[str], preferred: list[str]) -> list[ProfileAtom]:
    matched = [atom for atom in bullets if atom.evidence_id in cited or _matches_any(atom, preferred)]
    if not matched:
        return list(bullets)
    return matched


def _cover(groups: dict[str, list], analysis: JobAnalysis, job_text: str) -> list[ResumeSentence]:
    """One confirmed work sentence for each met or partial requirement.

    The sentence leads with the contribution and then copies the atom.
    Requirement text is not copied, so a missing skill named only in the job
    cannot enter the letter. The body stops at the job's word limit, or one page.
    """

    pool = _cover_pool(groups)
    used: set[str] = set()
    sentences: list[ResumeSentence] = []
    for item in analysis.requirements:
        if item.status not in {RequirementStatus.met, RequirementStatus.partial}:
            continue
        chosen = _best_cover_atom(item.evidence_ids, pool, used)
        if chosen is None:
            continue
        used.add(chosen.evidence_id)
        sentences.append(_framed(chosen))
    if not sentences:
        roles: list[_RoleGroup] = groups["roles"]
        if not roles:
            return []
        fallback = roles[0].bullets[0] if roles[0].bullets else roles[0].header
        sentences = [_framed(fallback)]
    return _fit_cover(sentences, job_text)


def _framed(atom: ProfileAtom) -> ResumeSentence:
    return ResumeSentence(
        text=f"{COVER_LEAD}{atom.statement}",
        evidence_ids=[atom.evidence_id],
        kind=SentenceKind.cover,
    )


def _fit_cover(sentences: list[ResumeSentence], job_text: str) -> list[ResumeSentence]:
    budget = cover_word_budget(job_text)
    kept: list[ResumeSentence] = []
    words = 0
    for sentence in sentences:
        count = len(sentence.text.split())
        if kept and words + count > budget:
            break
        kept.append(sentence)
        words += count
    return kept


def _publication_split(
    projects: list[ProfileAtom],
    preferred: list[str],
) -> tuple[list[ProfileAtom], list[ProfileAtom], list[ProfileAtom]]:
    publications = [atom for atom in projects if is_publication(atom)]
    others = [atom for atom in projects if not is_publication(atom)]
    ranked = sorted(
        publications,
        key=lambda atom: (not _matches_any(atom, preferred), publications.index(atom)),
    )
    return ranked[:2], ranked[2:], others


def _cover_pool(groups: dict[str, list]) -> dict[str, tuple[int, ProfileAtom]]:
    pool: dict[str, tuple[int, ProfileAtom]] = {}
    for group in groups["roles"]:
        pool[group.header.evidence_id] = (1, group.header)
        for bullet in group.bullets:
            pool[bullet.evidence_id] = (3, bullet)
    for atom in groups["loose"]:
        pool[atom.evidence_id] = (3, atom)
    for atom in groups["projects"]:
        pool[atom.evidence_id] = (2, atom)
    return pool


def _best_cover_atom(
    evidence_ids: list[str],
    pool: dict[str, tuple[int, ProfileAtom]],
    used: set[str],
) -> ProfileAtom | None:
    best: tuple[int, ProfileAtom] | None = None
    for evidence_id in evidence_ids:
        if evidence_id in used or evidence_id not in pool:
            continue
        rank, atom = pool[evidence_id]
        if best is None or rank > best[0]:
            best = (rank, atom)
    if best is None:
        return None
    return best[1]


def _refused(analysis: JobAnalysis, atoms: list[ProfileAtom]) -> list[str]:
    lines = ["## Refused", ""]
    for term in absent_skills(analysis, atoms):
        lines.append(f"- {term}. The job asks for it. No confirmed atom names it.")
    for item in analysis.requirements:
        if item.status in {RequirementStatus.missing, RequirementStatus.unknown}:
            lines.append(f"- {item.text} Status: {item.status.value}.")
    for constraint in analysis.hard_constraints:
        if constraint.result != ConstraintResult.passed:
            lines.append(f"- {constraint.text} Hard constraint: {constraint.result.value}.")
    if len(lines) == 2:
        lines.append("- Nothing from the job was written without a confirmed atom.")
    lines.append("")
    return lines


def _note_change(changes: list[str], label: str, atom: ProfileAtom, text: str) -> None:
    if text == atom.statement:
        return
    changes.append(f"- {label}: {text} ({atom.evidence_id}).")
    changes.append(f"- Original {label.lower()}: {atom.statement} ({atom.evidence_id}).")


def _copy(atom: ProfileAtom, kind: SentenceKind) -> ResumeSentence:
    return ResumeSentence(text=atom.statement, evidence_ids=[atom.evidence_id], kind=kind)


def _swap(atom: ProfileAtom, kind: SentenceKind, preferred: list[str]) -> ResumeSentence:
    text = _surface(atom, preferred) if kind == SentenceKind.skill else _rewrite_synonym(atom.statement, preferred)
    if not _fold_pair(text, atom.statement):
        text = atom.statement
    return ResumeSentence(text=text, evidence_ids=[atom.evidence_id], kind=kind)


def _surface(atom: ProfileAtom, preferred: list[str]) -> str:
    for term in preferred:
        for name in _group(atom.statement):
            if term.casefold() == name.casefold() and term.casefold() != atom.statement.casefold():
                return term
    return atom.statement


def _rewrite_synonym(text: str, preferred: list[str]) -> str:
    rewritten = text
    for group in SKILL_SYNONYMS:
        present = [name for name in group if _contains(rewritten, name)]
        if not present:
            continue
        target = next((term for term in preferred if term.casefold() in {name.casefold() for name in group}), None)
        if target is None:
            continue
        for name in present:
            if name.casefold() == target.casefold():
                continue
            rewritten = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", target, rewritten, flags=re.IGNORECASE)
    return rewritten


def _preferred_terms(analysis: JobAnalysis) -> list[str]:
    phrases = list(SKILL_LEXICON)
    for group in SKILL_SYNONYMS:
        phrases.extend(group)
    terms: list[str] = []
    for item in analysis.requirements:
        if item.status not in {RequirementStatus.met, RequirementStatus.partial}:
            continue
        for term in skills_in_text(item.text, phrases):
            if term.casefold() not in {item.casefold() for item in terms}:
                terms.append(term)
    return terms


def _matches_any(atom: ProfileAtom, terms: list[str]) -> bool:
    return any(atom_supports(atom, term) for term in terms)


def _is_role_header(atom: ProfileAtom) -> bool:
    if atom.category != EvidenceCategory.used_in_job or not atom.employer or not atom.title or not atom.start_date:
        return False
    return (
        atom.title in atom.statement
        and atom.employer in atom.statement
        and atom.start_date.raw in atom.statement
    )


def _same_role(header: ProfileAtom, atom: ProfileAtom) -> bool:
    if atom.employer != header.employer or atom.title != header.title:
        return False
    if atom.start_date is None or header.start_date is None:
        return False
    if atom.start_date.raw != header.start_date.raw:
        return False
    header_end = header.end_date.raw if header.end_date else None
    atom_end = atom.end_date.raw if atom.end_date else None
    return header_end == atom_end


def _is_skill_atom(atom: ProfileAtom) -> bool:
    return len(atom.skills) == 1 and atom.statement.strip().casefold() == atom.skills[0].strip().casefold()


def _is_language(atom: ProfileAtom) -> bool:
    return _LANGUAGE.match(atom.statement.strip()) is not None and not _is_skill_atom(atom)


def _is_contact(atom: ProfileAtom) -> bool:
    return "@" in atom.statement


def _is_summary(atom: ProfileAtom) -> bool:
    return len(atom.statement) > 80 or atom.statement.rstrip().endswith(".")


def _group(term: str) -> tuple[str, ...]:
    folded = term.casefold()
    for group in SKILL_SYNONYMS:
        if any(name.casefold() == folded for name in group):
            return group
    return (term,)


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def _fold_pair(left: str, right: str) -> bool:
    return _fold(left) == _fold(right)


def _fold(text: str) -> str:
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
