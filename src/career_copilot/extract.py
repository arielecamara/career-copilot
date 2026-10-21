"""Turn a stage-1 document into profile atoms.

Statements are copied from the source text. This step does not paraphrase,
does not add ownership verbs, and does not expand skill synonyms.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from career_copilot.dates import DateRange, find_date_range
from career_copilot.models import (
    BlockKind,
    ConfirmationState,
    DocumentKind,
    DocumentRecord,
    EvidenceCategory,
    ExactDate,
    ProfileAtom,
    TextBlock,
)
from career_copilot.ownership import find_ownership_verb
from career_copilot.vocab import Section, SKILL_LEXICON, classify_heading, looks_like_heading, skill_named_in

_METRIC_PATTERNS = (
    re.compile(r"[$€£]\s?\d+(?:,\d{3})*(?:\.\d+)?"),
    re.compile(r"\d+(?:\.\d+)?%"),
    re.compile(r"\d+(?:\.\d+)?x\b", re.IGNORECASE),
    re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"),
    re.compile(r"\d+\.\d+"),
    re.compile(r"\b\d+\s+years?\b", re.IGNORECASE),
)
_LANGUAGE = re.compile(r"^([^:\d][^:]{0,40}?):\s+\S")
_LABEL = re.compile(r"^([^:]{1,60}):\s+(.*)$")


@dataclass
class _Role:
    employer: str | None
    title: str | None
    start: ExactDate | None
    end: ExactDate | None


@dataclass
class _Draft:
    statement: str
    excerpt: str
    category: EvidenceCategory
    employer: str | None = None
    title: str | None = None
    start: ExactDate | None = None
    end: ExactDate | None = None
    metrics: list[str] | None = None
    skills: list[str] | None = None
    ownership_verb: str | None = None


def extract_atoms(document: DocumentRecord) -> list[ProfileAtom]:
    content = list(_content(document.blocks, document.kind))
    phrases = _link_phrases(_skill_items(content))
    drafts: list[_Draft] = []
    role: _Role | None = None
    pending: str | None = None
    section: Section | None = None

    def flush_pending() -> None:
        nonlocal pending, role
        if pending is None:
            return
        title, employer = _split_pipe(pending)
        drafts.append(
            _body_draft(
                pending,
                EvidenceCategory.used_in_job,
                phrases,
                employer=employer,
                title=title,
            )
        )
        role = _Role(employer, title, None, None)
        pending = None

    for next_section, block in content:
        if section is not None and next_section != section:
            flush_pending()
            role = None
        section = next_section
        if next_section == Section.skills:
            flush_pending()
            role = None
            drafts.extend(_skill_drafts(block.text))
            continue
        if next_section in {Section.projects, Section.education} and _is_section_label(block):
            continue
        if next_section == Section.experience:
            line_kind = _experience_line_kind(block)
            if line_kind == "pending":
                flush_pending()
                pending = block.text
                continue
            if line_kind == "role":
                date_range = find_date_range(block.text)
                if date_range is not None:
                    if pending is not None and _remainder(block.text, date_range) != "":
                        flush_pending()
                    drafts.append(_role_draft(block.text, date_range, pending, phrases))
                    title, employer, start, end, _excerpt = _role_fields(
                        block.text, date_range, pending
                    )
                    role = _Role(employer, title, start, end)
                    pending = None
                    continue
            flush_pending()
            inherited = role if role is not None else _Role(None, None, None, None)
            drafts.append(
                _body_draft(
                    block.text,
                    EvidenceCategory.used_in_job,
                    phrases,
                    employer=inherited.employer,
                    title=inherited.title,
                    start=inherited.start,
                    end=inherited.end,
                )
            )
            continue
        category = _category(next_section)
        date_range = None if block.kind == BlockKind.bullet else find_date_range(block.text)
        skills = skills_in_text(block.text, phrases)
        if next_section == Section.other:
            language = _language_name(block.text)
            if language and skill_named_in(language, block.text) and language not in skills:
                skills.append(language)
        drafts.append(
            _body_draft(
                block.text,
                category,
                phrases,
                skills=skills,
                start=date_range.start if date_range else None,
                end=date_range.end if date_range else None,
            )
        )
    flush_pending()
    return [_to_atom(document.document_id, ordinal, draft) for ordinal, draft in enumerate(drafts)]


def split_skill_items(text: str) -> list[str]:
    """Split a skills line on commas, keeping parenthetical names intact."""

    body = text.strip()
    labeled = _LABEL.match(body)
    if labeled and "," in labeled.group(2) and len(labeled.group(1).split()) <= 6:
        body = labeled.group(2).strip()
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current))
    cleaned: list[str] = []
    for item in items:
        value = re.sub(r"^(?:and|&)\s+", "", item.strip(), flags=re.IGNORECASE)
        value = value.strip(" .;")
        if value:
            cleaned.append(value)
    return cleaned


def find_metrics(text: str, *, skip_spans: list[tuple[int, int]] | None = None) -> list[str]:
    """Copy performance numbers exactly. Bare years and DOI fragments are left out."""

    blocked = skip_spans or []
    candidates: list[tuple[int, int, str]] = []
    for pattern in _METRIC_PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in blocked):
                continue
            before = text[match.start() - 1] if match.start() else ""
            after = text[match.end()] if match.end() < len(text) else ""
            if before == "/" or after == "/":
                continue
            value = match.group(0)
            if re.fullmatch(r"(?:19|20)\d{2}", value):
                continue
            candidates.append((match.start(), match.end(), value))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[str] = []
    cursor = -1
    for start, end, value in candidates:
        if start < cursor:
            continue
        chosen.append(value)
        cursor = end
    return chosen


def skills_in_text(text: str, phrases: list[str]) -> list[str]:
    """Return surface forms that are written in `text`. Longer terms win over shorter ones."""

    occupied = [False] * (len(text) + 1)
    found: list[str] = []
    for phrase in phrases:
        if not phrase.strip():
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(text):
            if any(occupied[match.start() : match.end()]):
                continue
            for index in range(match.start(), match.end()):
                occupied[index] = True
            found.append(match.group(0))
    return found


def _content(blocks: list[TextBlock], kind: DocumentKind):
    section = _initial_section(kind)
    for block in blocks:
        if _is_heading_block(block):
            classified = classify_heading(block.text)
            section = classified if classified is not None else Section.other
            continue
        yield section, block


def _is_heading_block(block: TextBlock) -> bool:
    if block.kind == BlockKind.bullet:
        return False
    if block.kind == BlockKind.heading:
        return True
    if find_date_range(block.text) is not None or "|" in block.text:
        return False
    return looks_like_heading(block.text)


def _initial_section(kind: DocumentKind) -> Section:
    if kind == DocumentKind.certificate:
        return Section.education
    if kind == DocumentKind.portfolio_note:
        return Section.projects
    return Section.preamble


def _category(section: Section) -> EvidenceCategory:
    if section == Section.experience:
        return EvidenceCategory.used_in_job
    if section == Section.projects:
        return EvidenceCategory.project
    if section == Section.education:
        return EvidenceCategory.coursework
    return EvidenceCategory.mentioned_only


def _skill_items(content: list[tuple[Section, TextBlock]]) -> list[str]:
    items: list[str] = []
    for section, block in content:
        if section != Section.skills:
            continue
        items.extend(split_skill_items(block.text))
    return items


def _link_phrases(skill_items: list[str]) -> list[str]:
    phrases: list[str] = []
    for item in skill_items:
        phrases.append(item)
        match = re.fullmatch(r"(.*?)\((.*)\)", item)
        if match:
            outer = match.group(1).strip()
            inner = match.group(2).strip()
            if outer:
                phrases.append(outer)
            if inner and "," not in inner:
                phrases.append(inner)
    phrases.extend(SKILL_LEXICON)
    unique = list(dict.fromkeys(phrase.strip() for phrase in phrases if phrase.strip()))
    unique.sort(key=len, reverse=True)
    return unique


def _skill_drafts(text: str) -> list[_Draft]:
    items = [item for item in split_skill_items(text) if skill_named_in(item, text)]
    if not items:
        return [_body_draft(text, EvidenceCategory.mentioned_only, [])]
    return [
        _Draft(
            statement=item,
            excerpt=item,
            category=EvidenceCategory.mentioned_only,
            skills=[item],
        )
        for item in items
    ]


def _experience_line_kind(block: TextBlock) -> str:
    if block.kind == BlockKind.bullet:
        return "bullet"
    date_range = find_date_range(block.text)
    if date_range is not None:
        return "role"
    if "|" in block.text:
        return "pending"
    return "prose"


def _role_draft(text: str, date_range: DateRange, pending: str | None, phrases: list[str]) -> _Draft:
    title, employer, start, end, excerpt = _role_fields(text, date_range, pending)
    return _body_draft(
        excerpt,
        EvidenceCategory.used_in_job,
        phrases,
        employer=employer,
        title=title,
        start=start,
        end=end,
        skip_spans=[(date_range.start_index, date_range.end_index)] if pending is None else None,
    )


def _role_fields(
    text: str,
    date_range: DateRange,
    pending: str | None,
) -> tuple[str | None, str | None, ExactDate, ExactDate | None, str]:
    if pending and _remainder(text, date_range) == "":
        excerpt = pending + "\n" + text
        title, employer = _split_pipe(pending)
        return title, employer, date_range.start, date_range.end, excerpt
    title, employer = _split_role(text, date_range)
    return title, employer, date_range.start, date_range.end, text


def _body_draft(
    excerpt: str,
    category: EvidenceCategory,
    phrases: list[str],
    *,
    employer: str | None = None,
    title: str | None = None,
    start: ExactDate | None = None,
    end: ExactDate | None = None,
    skills: list[str] | None = None,
    skip_spans: list[tuple[int, int]] | None = None,
) -> _Draft:
    named = skills if skills is not None else skills_in_text(excerpt, phrases)
    verb = find_ownership_verb(excerpt)
    return _Draft(
        statement=excerpt,
        excerpt=excerpt,
        category=category,
        employer=employer,
        title=title,
        start=start,
        end=end,
        metrics=find_metrics(excerpt, skip_spans=skip_spans),
        skills=named,
        ownership_verb=verb,
    )


def _split_role(text: str, date_range: DateRange) -> tuple[str | None, str | None]:
    return _split_pipe(_remainder(text, date_range))


def _remainder(text: str, date_range: DateRange) -> str:
    raw = text[: date_range.start_index] + " " + text[date_range.end_index :]
    return raw.strip(" |-–—,")


def _split_pipe(text: str) -> tuple[str | None, str | None]:
    parts = [part.strip(" ,") for part in text.split("|")]
    parts = [part for part in parts if part]
    if len(parts) != 2:
        return None, None
    title, employer = parts
    if title not in text or employer not in text:
        return None, None
    return title, employer


def _is_section_label(block: TextBlock) -> bool:
    text = block.text
    words = text.split()
    if not 1 <= len(words) <= 6:
        return False
    if "," in text or "|" in text or any(char.isdigit() for char in text):
        return False
    if text.endswith((".", "!", "?")):
        return False
    if find_date_range(text):
        return False
    return True


def _language_name(text: str) -> str | None:
    match = _LANGUAGE.match(text.strip())
    if match is None:
        return None
    name = match.group(1).strip()
    if " " in name and len(name.split()) > 3:
        return None
    return name or None


def _to_atom(document_id: str, ordinal: int, draft: _Draft) -> ProfileAtom:
    excerpt = draft.excerpt.strip()
    return ProfileAtom(
        evidence_id=_evidence_id(document_id, ordinal, excerpt),
        statement=draft.statement.strip(),
        category=draft.category,
        confirmation=ConfirmationState.extracted,
        source_document_id=document_id,
        source_excerpt=excerpt,
        employer=draft.employer,
        title=draft.title,
        start_date=draft.start,
        end_date=draft.end,
        metrics=draft.metrics or [],
        skills=draft.skills or [],
        ownership_verb=draft.ownership_verb,
    )


def _evidence_id(document_id: str, ordinal: int, excerpt: str) -> str:
    payload = f"{document_id}\n{ordinal}\n{excerpt}".encode()
    return "ev_" + hashlib.sha256(payload).hexdigest()[:16]
