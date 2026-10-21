"""Resume text copied from the original CV file.

Only the summary and Technical Skills may change. Every other section stays
word for word. A skill that is not already in that file is not added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from career_copilot.errors import CareerCopilotError
from career_copilot.extract import skills_in_text, split_skill_items
from career_copilot.job import SKILL_SYNONYMS
from career_copilot.models import (
    BlockKind,
    ConfirmationState,
    DocumentKind,
    EvidenceCategory,
    JobAnalysis,
    MasterProfile,
    ProfileAtom,
    RequirementStatus,
    ResumeSentence,
    SentenceKind,
    TextBlock,
    Violation,
)
from career_copilot.parsers import parse_pdf
from career_copilot.presentation import is_publication
from career_copilot.resume import ResumeBundle, _is_language, _is_role_header, _is_skill_atom, _refused
from career_copilot.vocab import SKILL_LEXICON, Section, classify_heading, skill_named_in

ORIGINAL_CV_NAME = "sample-cv.pdf"
_REPLACEABLE = {SentenceKind.summary, SentenceKind.skill}


@dataclass(frozen=True)
class SourceLine:
    text: str
    bullet: bool = False
    style: str = "text"


@dataclass(frozen=True)
class SourceSection:
    heading: str
    role: str
    lines: tuple[SourceLine, ...]


@dataclass
class SourceResume:
    """One resume laid out like the original file."""

    filename: str
    sections: tuple[SourceSection, ...]
    sentences: list[ResumeSentence]
    summary_text: str
    skills_text: str
    skills_are_bullets: bool
    diff_markdown: str
    allowed_skills: tuple[str, ...]
    source_summary: str

    def adopt(self, sentences: list[ResumeSentence]) -> SourceResume | None:
        """Keep a checked draft that still copies the frozen sections."""

        if not _same_frozen(self.sentences, sentences):
            return None
        if not _skills_allowed(sentences, self.allowed_skills):
            return None
        if not _summary_uses_cv_skills(sentences, self.source_summary, self.allowed_skills):
            return None
        summary = "\n\n".join(sentence.text for sentence in sentences if sentence.kind == SentenceKind.summary)
        current = [sentence.text for sentence in self.sentences if sentence.kind == SentenceKind.skill]
        proposed = [sentence.text for sentence in sentences if sentence.kind == SentenceKind.skill]
        skills = self.skills_text if proposed == current else ", ".join(proposed)
        return SourceResume(
            filename=self.filename,
            sections=self.sections,
            sentences=list(sentences),
            summary_text=summary,
            skills_text=skills,
            skills_are_bullets=self.skills_are_bullets,
            diff_markdown=self.diff_markdown,
            allowed_skills=self.allowed_skills,
            source_summary=self.source_summary,
        )


def original_cv_path(profile_path: Path | None = None, root: Path | None = None) -> Path | None:
    """The original CV beside the profile, or in an explicit folder."""

    if root is not None:
        candidate = Path(root) / ORIGINAL_CV_NAME
        return candidate if candidate.is_file() else None
    folders: list[Path] = []
    if profile_path is not None:
        folders.append(Path(profile_path).resolve().parent)
    else:
        folders.append(Path.cwd())
    seen: set[Path] = set()
    for folder in folders:
        resolved = folder.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        candidate = resolved / ORIGINAL_CV_NAME
        if candidate.is_file():
            return candidate
    return None


def assemble_source(
    profile: MasterProfile,
    analysis: JobAnalysis,
    *,
    profile_path: Path | None = None,
    root: Path | None = None,
) -> SourceResume | None:
    """Build from the original CV when this profile includes that file."""

    record = next((item for item in profile.documents if item.filename == ORIGINAL_CV_NAME), None)
    if record is None:
        return None
    path = original_cv_path(profile_path, root)
    if path is None:
        return None
    parsed = parse_pdf(path, DocumentKind.cv)
    return sections_from_blocks(
        parsed.blocks,
        profile.atoms,
        analysis,
        document_id=record.document_id,
        filename=path.name,
    )


def prefer_source(
    profile: MasterProfile,
    analysis: JobAnalysis,
    bundle: ResumeBundle,
    *,
    profile_path: Path | None = None,
) -> tuple[ResumeBundle, SourceResume | None]:
    """Use the original CV when it belongs to this profile. Otherwise keep the bundle."""

    source = assemble_source(profile, analysis, profile_path=profile_path)
    if source is None:
        return bundle, None
    return (
        ResumeBundle(
            sentences=source.sentences,
            cover=bundle.cover,
            diff_markdown=source.diff_markdown,
            publication_note=False,
        ),
        source,
    )


def preview_sections(source: SourceResume, summary: str, skills: str) -> tuple[SourceSection, ...]:
    """Show the original sections, with the two editable bodies replaced."""

    shown: list[SourceSection] = []
    for section in source.sections:
        if section.role == "summary":
            lines = tuple(SourceLine(text=part) for part in _paragraphs(summary))
            shown.append(SourceSection(section.heading, section.role, lines))
        elif section.role == "skills":
            lines = tuple(
                SourceLine(text=_skill_line(part), bullet=source.skills_are_bullets)
                for part in skills.splitlines()
                if part.strip()
            )
            shown.append(SourceSection(section.heading, section.role, lines))
        else:
            shown.append(section)
    return tuple(shown)


def sentences_from_edits(
    source: SourceResume,
    summary: str,
    skills: str,
) -> tuple[list[ResumeSentence] | None, list[Violation]]:
    """Apply the two edits. A new skill returns violations and no sentences."""

    violations: list[Violation] = []
    summary_sentences = [sentence for sentence in source.sentences if sentence.kind == SentenceKind.summary]
    edited_summary, summary_violations = _edited_summary(summary_sentences, summary)
    violations.extend(summary_violations)
    edited_skills, skill_violations = _edited_skills(source, skills)
    violations.extend(skill_violations)
    if violations:
        return None, violations
    sentences: list[ResumeSentence] = []
    wrote_summary = False
    wrote_skills = False
    for sentence in source.sentences:
        if sentence.kind == SentenceKind.summary:
            if not wrote_summary:
                sentences.extend(edited_summary)
                wrote_summary = True
            continue
        if sentence.kind == SentenceKind.skill:
            if not wrote_skills:
                sentences.extend(edited_skills)
                wrote_skills = True
            continue
        sentences.append(sentence)
    if summary_sentences and not wrote_summary:
        sentences[0:0] = edited_summary
    return sentences, []


def sections_from_blocks(
    blocks: list[TextBlock],
    atoms: list[ProfileAtom],
    analysis: JobAnalysis,
    *,
    document_id: str,
    filename: str,
) -> SourceResume:
    """Split the original blocks. Frozen lines must match a confirmed atom."""

    by_statement = _by_statement(atoms, document_id)
    skill_atoms = _skill_index(atoms, document_id)
    sections: list[SourceSection] = []
    sentences: list[ResumeSentence] = []
    role = "preamble"
    heading = ""
    lines: list[SourceLine] = []
    skill_rows: list[_SkillRow] = []
    dropped: list[str] = []
    skill_heading = ""
    skill_at: int | None = None
    skill_sentence_at: int | None = None

    def flush() -> None:
        if role == "skills" or (not heading and not lines):
            return
        sections.append(SourceSection(heading, role, tuple(lines)))

    for block in blocks:
        if block.kind != BlockKind.bullet and classify_heading(block.text) is not None:
            flush()
            classified = classify_heading(block.text)
            role = _role(classified)
            heading = block.text
            lines = []
            if role == "skills":
                skill_heading = block.text
                skill_at = len(sections)
                skill_sentence_at = len(sentences)
            continue
        if role == "skills":
            row, missing = _skill_row(block, skill_atoms)
            dropped.extend(missing)
            if row.atoms:
                skill_rows.append(row)
            continue
        atom = by_statement.get(block.text.strip())
        if atom is None:
            if _is_label(block.text):
                lines.append(SourceLine(text=block.text.strip(), bullet=block.kind == BlockKind.bullet))
                continue
            raise CareerCopilotError("The original CV has a line that is not a confirmed fact.")
        kind = _sentence_kind(atom, role)
        sentences.append(ResumeSentence(text=atom.statement, evidence_ids=[atom.evidence_id], kind=kind))
        lines.append(
            SourceLine(
                text=atom.statement,
                bullet=block.kind == BlockKind.bullet,
                style=_style(kind),
            )
        )
    flush()

    skills_are_bullets = bool(skill_rows) and all(row.bullet for row in skill_rows)
    skill_lines, skill_sentences, skills_changed = _align_skills(skill_rows, analysis)
    if skill_sentence_at is not None:
        sentences[skill_sentence_at:skill_sentence_at] = skill_sentences
    if skill_heading:
        body = tuple(SourceLine(text=line, bullet=skills_are_bullets) for line in skill_lines)
        sections.insert(skill_at if skill_at is not None else len(sections), SourceSection(skill_heading, "skills", body))
    summary_text = "\n\n".join(sentence.text for sentence in sentences if sentence.kind == SentenceKind.summary)
    skills_text = "\n".join(skill_lines)
    allowed = tuple(dict.fromkeys(atom.statement for row in skill_rows for atom in row.atoms))
    return SourceResume(
        filename=filename,
        sections=tuple(sections),
        sentences=sentences,
        summary_text=summary_text,
        skills_text=skills_text,
        skills_are_bullets=skills_are_bullets,
        diff_markdown=_diff(analysis, atoms, dropped, skills_changed),
        allowed_skills=allowed,
        source_summary=summary_text,
    )


@dataclass
class _SkillRow:
    original_text: str
    bullet: bool
    atoms: list[ProfileAtom]
    dropped: list[str]


def _skill_row(block: TextBlock, skill_atoms: dict[str, ProfileAtom]) -> tuple[_SkillRow, list[str]]:
    items = split_skill_items(block.text) or [block.text.strip()]
    kept: list[ProfileAtom] = []
    missing: list[str] = []
    seen: set[str] = set()
    for item in items:
        atom = skill_atoms.get(item.casefold())
        if atom is None or atom.evidence_id in seen:
            missing.append(item)
            continue
        seen.add(atom.evidence_id)
        kept.append(atom)
    return _SkillRow(block.text.strip(), block.kind == BlockKind.bullet, kept, missing), missing


def _align_skills(
    rows: list[_SkillRow],
    analysis: JobAnalysis,
) -> tuple[list[str], list[ResumeSentence], bool]:
    flat = [atom for row in rows for atom in row.atoms]
    terms = _preferred_terms(analysis)
    texts = [item.text for item in analysis.requirements if item.status in {RequirementStatus.met, RequirementStatus.partial}]
    ordered = sorted(enumerate(flat), key=lambda pair: (not _matches(pair[1].statement, terms, texts), pair[0]))
    ordered_atoms = [atom for _index, atom in ordered]
    changed = any(row.dropped for row in rows) or [atom.evidence_id for atom in ordered_atoms] != [
        atom.evidence_id for atom in flat
    ]
    lines: list[str] = []
    sentences: list[ResumeSentence] = []
    cursor = 0
    for row in rows:
        count = len(row.atoms)
        if count == 0:
            continue
        chunk = ordered_atoms[cursor : cursor + count]
        cursor += count
        same = [atom.evidence_id for atom in chunk] == [atom.evidence_id for atom in row.atoms] and not row.dropped
        lines.append(row.original_text if same else ", ".join(atom.statement for atom in chunk))
        for atom in chunk:
            sentences.append(ResumeSentence(text=atom.statement, evidence_ids=[atom.evidence_id], kind=SentenceKind.skill))
    return lines, sentences, changed


def _preferred_terms(analysis: JobAnalysis) -> list[str]:
    phrases = list(SKILL_LEXICON)
    for group in SKILL_SYNONYMS:
        phrases.extend(group)
    terms: list[str] = []
    seen: set[str] = set()
    for item in analysis.requirements:
        if item.status not in {RequirementStatus.met, RequirementStatus.partial}:
            continue
        for term in skills_in_text(item.text, phrases):
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def _matches(item: str, terms: list[str], requirement_texts: list[str]) -> bool:
    if any(skill_named_in(item, text) for text in requirement_texts):
        return True
    for term in terms:
        if term.casefold() == item.casefold() or skill_named_in(term, item):
            return True
    return False


def _edited_summary(
    sentences: list[ResumeSentence],
    summary: str,
) -> tuple[list[ResumeSentence], list[Violation]]:
    text = summary.strip()
    if not sentences:
        if text:
            return [], [_violation(text, "rewrite", "The original CV has no summary to change.")]
        return [], []
    if len(sentences) == 1:
        if not text:
            return [], [_violation(sentences[0].text, "rewrite", "The summary is empty.")]
        return [sentences[0].model_copy(update={"text": text})], []
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    if len(parts) != len(sentences):
        return [], [_violation(text or sentences[0].text, "rewrite", "The summary has a different number of lines.")]
    return [sentence.model_copy(update={"text": part}) for sentence, part in zip(sentences, parts, strict=True)], []


def _edited_skills(source: SourceResume, skills: str) -> tuple[list[ResumeSentence], list[Violation]]:
    items = _editor_items(skills)
    allowed = {item.casefold(): item for item in source.allowed_skills}
    originals = [sentence for sentence in source.sentences if sentence.kind == SentenceKind.skill]
    by_text: dict[str, list[ResumeSentence]] = {}
    for sentence in originals:
        by_text.setdefault(sentence.text.casefold(), []).append(sentence)
    used: set[str] = set()
    chosen: list[ResumeSentence] = []
    violations: list[Violation] = []
    for item in items:
        key = item.casefold()
        if key not in allowed:
            violations.append(_violation(item, "skill", "That skill is not in the original CV."))
            continue
        pool = by_text.get(key, [])
        match = next((sentence for sentence in pool if sentence.evidence_ids[0] not in used), None)
        if match is None:
            violations.append(_violation(item, "skill", "That skill is not in the original CV."))
            continue
        used.add(match.evidence_ids[0])
        chosen.append(match.model_copy(update={"text": allowed[key]}))
    return chosen, violations


def _editor_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        cleaned = _skill_line(line)
        if cleaned:
            items.extend(split_skill_items(cleaned) or [cleaned])
    return items


def _skill_line(text: str) -> str:
    return re.sub(r"^\s*[-•]\s+", "", text).strip()


def _paragraphs(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    parts = [part.strip() for part in re.split(r"\n\s*\n", stripped) if part.strip()]
    return parts or [stripped]


def _same_frozen(local: list[ResumeSentence], proposed: list[ResumeSentence]) -> bool:
    return _frozen_key(local) == _frozen_key(proposed)


def _frozen_key(sentences: list[ResumeSentence]) -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        (sentence.kind.value, sentence.text, tuple(sentence.evidence_ids))
        for sentence in sentences
        if sentence.kind not in _REPLACEABLE
    ]


def _skills_allowed(sentences: list[ResumeSentence], allowed: tuple[str, ...]) -> bool:
    pool = {item.casefold() for item in allowed}
    seen: set[str] = set()
    for sentence in sentences:
        if sentence.kind != SentenceKind.skill:
            continue
        key = sentence.text.casefold()
        if key not in pool or sentence.evidence_ids[0] in seen:
            return False
        seen.add(sentence.evidence_ids[0])
    return True


def _summary_uses_cv_skills(
    sentences: list[ResumeSentence],
    source_summary: str,
    allowed: tuple[str, ...],
) -> bool:
    blob = "\n".join((source_summary, *allowed))
    phrases = list(SKILL_LEXICON)
    for group in SKILL_SYNONYMS:
        phrases.extend(group)
    for sentence in sentences:
        if sentence.kind != SentenceKind.summary:
            continue
        for term in skills_in_text(sentence.text, phrases):
            if not skill_named_in(term, blob):
                return False
    return True


def _by_statement(atoms: list[ProfileAtom], document_id: str) -> dict[str, ProfileAtom]:
    found: dict[str, ProfileAtom] = {}
    for atom in atoms:
        if atom.confirmation != ConfirmationState.confirmed:
            continue
        key = atom.statement.strip()
        current = found.get(key)
        if current is None or _prefer(atom, current, document_id):
            found[key] = atom
    return found


def _skill_index(atoms: list[ProfileAtom], document_id: str) -> dict[str, ProfileAtom]:
    found: dict[str, ProfileAtom] = {}
    for atom in atoms:
        if atom.confirmation != ConfirmationState.confirmed or not _is_skill_atom(atom):
            continue
        key = atom.statement.strip().casefold()
        current = found.get(key)
        if current is None or _prefer(atom, current, document_id):
            found[key] = atom
    return found


def _prefer(candidate: ProfileAtom, current: ProfileAtom, document_id: str) -> bool:
    return current.source_document_id != document_id and candidate.source_document_id == document_id


def _role(section: Section | None) -> str:
    if section == Section.summary:
        return "summary"
    if section == Section.skills:
        return "skills"
    return "frozen"


def _sentence_kind(atom: ProfileAtom, role: str) -> SentenceKind:
    if role == "summary":
        return SentenceKind.summary
    if role == "preamble":
        if "@" in atom.statement:
            return SentenceKind.contact
        return SentenceKind.name
    if _is_role_header(atom):
        return SentenceKind.role
    if atom.category == EvidenceCategory.coursework:
        return SentenceKind.education
    if is_publication(atom):
        return SentenceKind.publication
    if atom.category == EvidenceCategory.project:
        return SentenceKind.project
    if _is_language(atom):
        return SentenceKind.language
    if atom.category == EvidenceCategory.used_in_job:
        return SentenceKind.bullet
    return SentenceKind.bullet


def _style(kind: SentenceKind) -> str:
    if kind == SentenceKind.name:
        return "name"
    if kind == SentenceKind.role:
        return "role"
    return "text"


def _is_label(text: str) -> bool:
    words = text.split()
    if not 1 <= len(words) <= 6:
        return False
    if any(char.isdigit() for char in text):
        return False
    if any(char in text for char in ",;|@%"):
        return False
    if text.endswith((".", "!", "?")):
        return False
    if skills_in_text(text, list(SKILL_LEXICON)):
        return False
    return True


def _diff(analysis: JobAnalysis, atoms: list[ProfileAtom], dropped: list[str], skills_changed: bool) -> str:
    lines = [
        "# Resume diff",
        "",
        f"The resume starts from {ORIGINAL_CV_NAME}.",
        "Only the summary and Technical Skills may change.",
        "Roles, bullets, dates, employers, education, publications, and every other section stay as they are in that file.",
        "",
    ]
    if skills_changed:
        lines.extend(
            [
                "## Changed",
                "",
                "- Technical Skills were aligned with the job. No new skill was added.",
                "",
            ]
        )
    if dropped:
        lines.extend(["## Omitted", ""])
        for item in dropped:
            lines.append(f"- {item}. It is not a confirmed fact, so it was left out of Technical Skills.")
        lines.append("")
    lines.extend(
        [
            "## Kept",
            "",
            "- Every section other than the summary and Technical Skills is unchanged.",
            "",
        ]
    )
    lines.extend(_refused(analysis, atoms))
    return "\n".join(lines).rstrip() + "\n"


def _violation(sentence: str, code: str, detail: str) -> Violation:
    shown = sentence.strip() or "sentence"
    return Violation(code=code, sentence=shown, detail=detail)  # type: ignore[arg-type]
