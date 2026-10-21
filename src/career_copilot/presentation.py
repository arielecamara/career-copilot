"""Labels, grouping, and text cleanup shared by the page and the DOCX."""

from __future__ import annotations

import re
import unicodedata

from career_copilot.models import (
    AreaChoice,
    ConfirmationState,
    ConstraintKind,
    ConstraintResult,
    EvidenceCategory,
    ProfileAtom,
    ScheduleChoice,
    SectorPreference,
    SentenceKind,
    WorkMode,
)

COVER_LEAD = "What this background contributes: "
PUBLICATION_NOTE = "Full list available upon request."
_ONE_PAGE_WORDS = 320
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+\d|\(\d)[\d\s().-]{6,}\d")
_PUBLICATION = re.compile(
    r"\b(publication|published|journal|conference|proceedings|doi|thesis|paper)\b",
    re.IGNORECASE,
)
_WORD_LIMIT = re.compile(
    r"(?:cover letter|letter).{0,60}?(\d{2,4})\s+words?|(\d{2,4})\s+words?.{0,60}?(?:cover letter|letter)",
    re.IGNORECASE,
)
_PAGE_LIMIT = re.compile(
    r"(?:cover letter|letter).{0,40}?\b(one|1|two|2)\s+pages?",
    re.IGNORECASE,
)
_CIRCUMFLEX = {
    "a": "â", "A": "Â", "e": "ê", "E": "Ê", "i": "î", "I": "Î",
    "o": "ô", "O": "Ô", "u": "û", "U": "Û", "ı": "î", "İ": "Î",
}
_ACUTE = {
    "a": "á", "A": "Á", "e": "é", "E": "É", "i": "í", "I": "Í",
    "o": "ó", "O": "Ó", "u": "ú", "U": "Ú", "ı": "í", "İ": "Í",
}
_GRAVE = {
    "a": "à", "A": "À", "e": "è", "E": "È", "i": "ì", "I": "Ì",
    "o": "ò", "O": "Ò", "u": "ù", "U": "Ù", "ı": "ì", "İ": "Ì",
}
_TILDE = {"a": "ã", "A": "Ã", "o": "õ", "O": "Õ", "n": "ñ", "N": "Ñ"}
_DIAERESIS = {
    "a": "ä", "A": "Ä", "e": "ë", "E": "Ë", "i": "ï", "I": "Ï",
    "o": "ö", "O": "Ö", "u": "ü", "U": "Ü", "ı": "ï", "İ": "Ï",
}
_MARK_TABLES = {"ˆ": _CIRCUMFLEX, "´": _ACUTE, "`": _GRAVE, "˜": _TILDE, "¨": _DIAERESIS}


def polish(text: str) -> str:
    """Normalize split accents and spacing for display. Facts and numbers stay."""

    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _join_split_accents(cleaned)
    cleaned = unicodedata.normalize("NFC", cleaned)
    cleaned = cleaned.replace("\x00", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    cleaned = re.sub(r"(?<=[A-Za-z])!(?=[A-Za-z])", "ff", cleaned)
    cleaned = re.sub(r"\s*•\s*", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


def _join_split_accents(text: str) -> str:
    """Join a pdfplumber accent to the vowel it belongs to. ão stays on the a."""

    chars = list(text)
    out: list[str] = []
    index = 0
    while index < len(chars):
        char = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if char == "¸":
            if nxt in {"c", "C"}:
                out.append("ç" if nxt == "c" else "Ç")
                index += 2
                continue
            if out and out[-1] in {"c", "C"}:
                out[-1] = "ç" if out[-1] == "c" else "Ç"
                index += 1
                continue
        table = _MARK_TABLES.get(char)
        if table is not None:
            prev = out[-1] if out else ""
            if char == "˜" and prev in {"a", "A"} and nxt in {"o", "O"}:
                out[-1] = table[prev]
                index += 1
                continue
            if nxt in table:
                out.append(table[nxt])
                index += 2
                continue
            if prev in table:
                out[-1] = table[prev]
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def fit_feedback(analysis) -> tuple[str, str]:
    """A local signal from the checklist. It is not a score and adds no sentence."""

    tally = analysis.tally
    judged = tally.met + tally.partial + tally.missing
    if judged == 0:
        return "Negative", "Not enough information"
    if tally.missing > 0:
        return "Negative", "Missing key skills"
    failed = [item for item in analysis.hard_constraints if item.result == ConstraintResult.failed]
    if any(item.kind == ConstraintKind.minimum_years for item in failed):
        return "Negative", "Below the stated experience"
    if failed:
        return "Negative", "Does not meet a stated requirement"
    if tally.partial == 0 and tally.met > 0 and _far_above_minimum(analysis.hard_constraints):
        return "Negative", "Overqualified"
    if tally.partial > 0:
        return "Positive", "Mostly qualified"
    return "Positive", "Well qualified"


def _far_above_minimum(constraints) -> bool:
    """True when every passed year minimum is at least twice the asked length."""

    passed = [
        item
        for item in constraints
        if item.kind == ConstraintKind.minimum_years
        and item.result == ConstraintResult.passed
        and item.required_years
        and item.observed_days is not None
    ]
    if not passed:
        return False
    return all(item.observed_days + 1 >= item.required_years * 365.25 * 2 for item in passed)


def job_name(text: str) -> str:
    """A short label from the pasted job. Headings such as Requirements are skipped."""

    for line in text.splitlines():
        cleaned = polish(line)
        if not cleaned:
            continue
        if cleaned.endswith(":") and len(cleaned) < 40:
            continue
        if len(cleaned) > 120:
            return cleaned[:120].rstrip()
        return cleaned
    return "Job"


def cover_word_budget(job_text: str) -> int:
    """Words allowed in the cover body. A missing limit is one page."""

    match = _WORD_LIMIT.search(job_text)
    if match is not None:
        raw = next(group for group in match.groups() if group)
        return max(1, int(raw))
    pages = _PAGE_LIMIT.search(job_text)
    if pages is not None:
        count = 1 if pages.group(1).casefold() in {"1", "one"} else 2
        return count * _ONE_PAGE_WORDS
    return _ONE_PAGE_WORDS


def is_publication(atom: ProfileAtom) -> bool:
    return atom.category == EvidenceCategory.project and _PUBLICATION.search(atom.statement) is not None


def is_academic(profile) -> bool:
    """Academia alone uses the academic section order. Mixed choices stay industry-first."""

    preferences = profile.preferences
    if preferences is None or not preferences.areas:
        return False
    areas = {item.value for item in preferences.areas}
    return AreaChoice.academia.value in areas and AreaChoice.industry.value not in areas


def resume_header_name(profile, sentences) -> str | None:
    """A typed name is a heading only when no confirmed name atom was copied."""

    for sentence in sentences:
        if sentence.kind == SentenceKind.name:
            return None
    name = profile.display_name
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def header_contact(profile) -> str | None:
    residence = profile.residence
    if residence is None:
        return None
    parts = [part for part in (residence.contact, residence.city, residence.country) if part]
    if not parts:
        return None
    return " | ".join(parts)


def person_name(profile) -> str:
    if profile.display_name:
        return profile.display_name
    for atom in profile.atoms:
        if _is_name(atom):
            return atom.statement
    return "Profile"


def extracted_contacts(atoms: list[ProfileAtom]) -> dict[str, list[str]]:
    emails: list[str] = []
    phones: list[str] = []
    for atom in atoms:
        for email in _EMAIL.findall(atom.statement):
            if email not in emails:
                emails.append(email)
        for phone in _PHONE.findall(atom.statement):
            compact = " ".join(phone.split())
            if compact not in phones:
                phones.append(compact)
    return {"emails": emails, "phones": phones}


def fact_groups(atoms: list[ProfileAtom]) -> list[tuple[str, list[ProfileAtom]]]:
    """Facts for the profile table. Role order inside each group stays as stored."""

    experience: list[ProfileAtom] = []
    education: list[ProfileAtom] = []
    skills: list[ProfileAtom] = []
    publications: list[ProfileAtom] = []
    for atom in atoms:
        if atom.category == EvidenceCategory.used_in_job:
            experience.append(atom)
        elif atom.category == EvidenceCategory.coursework:
            education.append(atom)
        elif is_publication(atom):
            publications.append(atom)
        elif _is_skill(atom):
            skills.append(atom)
    groups = [
        ("Experience", experience),
        ("Education", education),
        ("Skills", skills),
        ("Publications", publications),
    ]
    return [(label, rows) for label, rows in groups if rows]


def selected_modes(preferences) -> list[str]:
    if preferences is None:
        return [mode.value for mode in WorkMode]
    if preferences.job_types:
        return [item.value for item in preferences.job_types]
    return [
        item.mode.value
        for item in preferences.work_modes
        if item.priority.value in {"high", "medium"}
    ]


def selected_areas(preferences) -> list[str]:
    if preferences is None or not preferences.areas:
        if preferences is not None and preferences.sector == SectorPreference.academia:
            return [AreaChoice.academia.value]
        if preferences is not None and preferences.sector == SectorPreference.industry:
            return [AreaChoice.industry.value]
        if preferences is not None and preferences.sector == SectorPreference.both:
            return [AreaChoice.academia.value, AreaChoice.industry.value]
        return [AreaChoice.industry.value]
    return [item.value for item in preferences.areas]


def selected_schedules(preferences) -> list[str]:
    if preferences is None or not preferences.schedules:
        return [ScheduleChoice.full_time.value]
    return [item.value for item in preferences.schedules]


def _is_skill(atom: ProfileAtom) -> bool:
    return (
        atom.category == EvidenceCategory.mentioned_only
        and len(atom.skills) == 1
        and atom.statement.strip().casefold() == atom.skills[0].strip().casefold()
    )


def _is_name(atom: ProfileAtom) -> bool:
    if atom.confirmation not in {ConfirmationState.confirmed, ConfirmationState.extracted}:
        return False
    if atom.category != EvidenceCategory.mentioned_only or _is_skill(atom):
        return False
    if "@" in atom.statement or len(atom.statement) > 80 or atom.statement.rstrip().endswith("."):
        return False
    return ":" not in atom.statement
