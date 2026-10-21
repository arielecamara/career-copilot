"""Write one job's CV and cover letter on this computer.

The CV is an Amsterdam PDF: A4, one column, no photo, and no table.
Experience, education, publications, languages, and contact stay word for word.
Only the summary and Technical Skills may change, and only with facts already
in the CV saved on the User page. Nothing is submitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from career_copilot.errors import CareerCopilotError
from career_copilot.extract import skills_in_text
from career_copilot.roles import child_env, grok_argv
from career_copilot.vocab import SKILL_LEXICON, Section, classify_heading

_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)
_LOCKED_TOPIC = (
    r"experience|work history|employment|education|publications?|languages?|"
    r"contact details|employers?|job titles?|dates?|phone|e-mail|email address"
)
_CHANGE_VERB = r"change|edit|rewrite|update|add|remove|delete|alter|fix|shorten|lengthen|move|correct"
_ACRONYM = re.compile(r"\b(?:[A-Z]{2,}|PhD|Ph\.D\.)\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")
_GROK_TIMEOUT_SECONDS = 180
_LEFT_AS_WRITTEN = "The summary and the skills were left as written in your CV."


@dataclass(frozen=True)
class CvPart:
    heading: str
    role: str
    body: str


def document_folder(key: str, root: Path | None = None) -> Path:
    token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    base = Path.cwd() if root is None else root
    return base / ".career-copilot" / "documents" / token


def split_cv(text: str) -> list[CvPart]:
    parts: list[CvPart] = []
    heading = ""
    role = "preamble"
    lines: list[str] = []
    seen_summary = False
    seen_skills = False

    def flush() -> None:
        nonlocal lines
        body = "\n".join(lines).strip("\n")
        if heading or body.strip():
            parts.append(CvPart(heading, role, body))
        lines = []

    for line in text.splitlines():
        stripped = line.strip()
        kind = classify_heading(stripped) if stripped and len(stripped) <= 80 else None
        if kind is None:
            lines.append(line)
            continue
        flush()
        heading = stripped
        if kind == Section.summary and not seen_summary:
            role = "summary"
            seen_summary = True
        elif kind == Section.skills and not seen_skills:
            role = "skills"
            seen_skills = True
        else:
            role = "locked"
    flush()
    return parts


def note_is_locked(note: str) -> bool:
    text = " ".join(note.casefold().split())
    if not text:
        return False
    verb_then_topic = re.search(rf"\b(?:{_CHANGE_VERB})\b.{{0,60}}\b(?:{_LOCKED_TOPIC})\b", text)
    topic_section = re.search(rf"\b(?:{_LOCKED_TOPIC})\b.{{0,30}}\b(?:section|block)\b", text)
    topic_then_verb = re.search(rf"\b(?:{_LOCKED_TOPIC})\b.{{0,40}}\b(?:{_CHANGE_VERB})\b", text)
    return any(match is not None for match in (verb_then_topic, topic_section, topic_then_verb))


def grounded_sentence(sentence: str, cv_text: str) -> bool:
    cleaned = " ".join(sentence.split())
    if not cleaned:
        return False
    cv_folded = cv_text.casefold()
    for number in _NUMBER.findall(cleaned):
        plain = number.replace(",", "")
        if number not in cv_text and plain not in cv_text:
            return False
    for month in _MONTHS:
        if re.search(rf"\b{month}\b", cleaned.casefold()) and month not in cv_folded:
            return False
    cv_skills = {skill.casefold() for skill in skills_in_text(cv_text, list(SKILL_LEXICON))}
    for skill in skills_in_text(cleaned, list(SKILL_LEXICON)):
        if skill.casefold() not in cv_skills:
            return False
    for token in _ACRONYM.findall(cleaned):
        if token.casefold() not in cv_folded:
            return False
    for phrase in _PROPER.findall(cleaned):
        if phrase.casefold() not in cv_folded and not _same_skill(phrase, cv_text):
            return False
    for token in _CAMEL.findall(cleaned):
        if token.casefold() not in cv_folded:
            return False
    return True


def keep_summary(proposed: str, cv_text: str) -> str:
    kept = [sentence for sentence in _sentences(proposed) if grounded_sentence(sentence, cv_text)]
    return " ".join(kept).strip()


def keep_skills(proposed: str, cv_text: str) -> str:
    kept: list[str] = []
    seen: set[str] = set()
    for item in _skill_items(proposed):
        if not _skill_allowed(item, cv_text):
            continue
        key = _skill_key(item)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return "\n".join(kept)


def _skill_allowed(item: str, cv_text: str) -> bool:
    if item.casefold() in cv_text.casefold():
        return grounded_sentence(item, cv_text)
    return _same_skill(item, cv_text) and _variant_is_grounded(item, cv_text)


def _variant_is_grounded(item: str, cv_text: str) -> bool:
    cleaned = " ".join(item.split())
    cv_folded = cv_text.casefold()
    for number in _NUMBER.findall(cleaned):
        plain = number.replace(",", "")
        if number not in cv_text and plain not in cv_text:
            return False
    for month in _MONTHS:
        if re.search(rf"\b{month}\b", cleaned.casefold()) and month not in cv_folded:
            return False
    cv_skills = {skill.casefold() for skill in skills_in_text(cv_text, list(SKILL_LEXICON))}
    for skill in skills_in_text(cleaned, list(SKILL_LEXICON)):
        if skill.casefold() not in cv_skills and not _same_skill(skill, cv_text):
            return False
    for token in _ACRONYM.findall(cleaned):
        if token.casefold() not in cv_folded and not _same_skill(token, cv_text):
            return False
    for token in _CAMEL.findall(cleaned):
        if token.casefold() not in cv_folded:
            return False
    return True


def _same_skill(item: str, cv_text: str) -> bool:
    wanted = _fold_phrase(item)
    if not wanted:
        return False
    haystack = _fold_phrase(cv_text)
    width = len(wanted)
    for index in range(len(haystack) - width + 1):
        if haystack[index : index + width] == wanted:
            return True
    return False


def _skill_key(item: str) -> str:
    return " ".join(_fold_phrase(item))


def _fold_phrase(text: str) -> list[str]:
    return [_fold_token(word) for word in re.findall(r"[a-z0-9+#]+", text.casefold())]


def _fold_token(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def assemble_cv(cv_text: str, summary: str, skills: str) -> str:
    parts = split_cv(cv_text)
    if not parts:
        raise CareerCopilotError("Read the CV on the User page first.")
    blocks: list[str] = []
    for part in parts:
        body = part.body
        if part.role == "summary" and summary.strip():
            body = summary.strip()
        elif part.role == "skills" and skills.strip():
            body = skills.strip()
        chunk = part.heading
        if body.strip():
            chunk = f"{chunk}\n{body.strip()}" if chunk else body.strip()
        if chunk.strip():
            blocks.append(chunk.strip())
    preview = "\n\n".join(blocks).strip() + "\n"
    for part in parts:
        if part.role in {"summary", "skills"}:
            continue
        if part.heading and part.heading not in preview:
            raise CareerCopilotError("The CV could not be written.")
        if part.body.strip() and part.body.strip() not in preview:
            raise CareerCopilotError("The CV could not be written.")
    return preview


def generate_job_cv(
    cv_text: str,
    posting: str,
    key: str,
    *,
    propose: Callable[[str, str, str], tuple[str, str]],
    root: Path | None = None,
) -> Path:
    original = cv_text.strip()
    if not original:
        raise CareerCopilotError("Read the CV on the User page first.")
    parts = split_cv(original)
    notice = ""
    try:
        summary, skills = _proposed(original, posting, "", propose)
    except CareerCopilotError as exc:
        summary, skills = "", ""
        notice = _notice_for(exc)
    kept_summary = keep_summary(summary, original)
    kept_skills = keep_skills(skills, original)
    if not kept_summary.strip() and not kept_skills.strip():
        notice = notice or _LEFT_AS_WRITTEN
    summary = kept_summary or _original_body(parts, "summary")
    skills = kept_skills or _original_body(parts, "skills")
    preview = assemble_cv(original, summary, skills)
    path = _store_cv(key, preview, summary, skills, root)
    _replace_text(path.with_name("cv-notice.txt"), f"{notice.strip()}\n" if notice.strip() else "")
    return path


def apply_cv_note(
    cv_text: str,
    posting: str,
    key: str,
    note: str,
    *,
    propose: Callable[[str, str, str], tuple[str, str]],
    root: Path | None = None,
) -> Path:
    original = cv_text.strip()
    folder = document_folder(key, root)
    if not (folder / "cv.pdf").is_file():
        raise CareerCopilotError("Generate the CV before writing a note.")
    cleaned = " ".join(note.split())
    if not cleaned:
        raise CareerCopilotError("Write a note before applying it.")
    if len(cleaned) > 1000:
        raise CareerCopilotError("That note is too long. The file was not changed.")
    if note_is_locked(cleaned):
        raise CareerCopilotError("That note asks to change a locked section. The file was not changed.")
    if not any(part.role in {"summary", "skills"} for part in split_cv(original)):
        raise CareerCopilotError("This CV has no summary or technical skills section to change. The file was not changed.")
    current = _saved_sections(folder)
    guidance = (
        f"{cleaned}\n"
        f"Current summary:\n{current[0]}\n"
        f"Current skills:\n{current[1]}"
    )
    summary, skills = _proposed(original, posting, guidance, propose)
    summary = keep_summary(summary, original) or current[0] or _original_body(split_cv(original), "summary")
    skills = keep_skills(skills, original) or current[1] or _original_body(split_cv(original), "skills")
    before = (folder / "cv-preview.txt").read_text(encoding="utf-8")
    preview = assemble_cv(original, summary, skills)
    if preview == before:
        raise CareerCopilotError("The note did not change the file.")
    path = _store_cv(key, preview, summary, skills, root)
    _replace_text(path.with_name("cv-notice.txt"), "")
    return path


def generate_cover_letter(
    cv_text: str,
    posting: str,
    key: str,
    *,
    propose: Callable[[str, str], str],
    root: Path | None = None,
) -> Path:
    original = cv_text.strip()
    if not original:
        raise CareerCopilotError("Read the CV on the User page first.")
    try:
        draft = propose(original, posting)
    except CareerCopilotError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CareerCopilotError("The cover letter could not be written.") from exc
    if not isinstance(draft, str):
        raise CareerCopilotError("The cover letter could not be written.")
    kept = [sentence for sentence in _sentences(draft) if grounded_sentence(sentence, original)]
    if not kept:
        raise CareerCopilotError("The cover letter could not be written.")
    preview = " ".join(kept).strip() + "\n"
    folder = document_folder(key, root)
    folder.mkdir(parents=True, exist_ok=True)
    pdf_path = folder / "cover.pdf"
    text_path = folder / "cover-preview.txt"
    _replace_text(text_path, preview)
    write_amsterdam_pdf(pdf_path, preview, title="Cover letter")
    return pdf_path


def propose_cv_sections(cv_text: str, posting: str, note: str) -> tuple[str, str]:
    payload = _ask_grok(_cv_prompt(cv_text, posting, note))
    summary = payload.get("summary")
    skills = payload.get("skills")
    if not isinstance(summary, str) or not isinstance(skills, str):
        raise CareerCopilotError("The CV could not be written.")
    return summary, skills


def propose_cover_letter(cv_text: str, posting: str) -> str:
    payload = _ask_grok(_letter_prompt(cv_text, posting), missing="The cover letter could not be written.")
    letter = payload.get("letter")
    if not isinstance(letter, str) or not letter.strip():
        raise CareerCopilotError("The cover letter could not be written.")
    return letter


def _notice_for(exc: CareerCopilotError) -> str:
    text = str(exc).casefold()
    if "too long" in text:
        return f"Grok took too long. {_LEFT_AS_WRITTEN}"
    if "not available" in text:
        return f"Grok is not available in the terminal. Run grok login, then try again. {_LEFT_AS_WRITTEN}"
    return _LEFT_AS_WRITTEN


def write_amsterdam_pdf(path: Path, text: str, *, title: str) -> None:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    regular, bold = _fonts()
    body = ParagraphStyle(
        "AmsterdamBody",
        fontName=regular,
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    heading = ParagraphStyle(
        "AmsterdamHeading",
        fontName=bold,
        fontSize=12,
        leading=15,
        spaceBefore=8,
        spaceAfter=3,
    )
    name = ParagraphStyle(
        "AmsterdamName",
        fontName=bold,
        fontSize=16,
        leading=20,
        spaceAfter=4,
    )
    bullet = ParagraphStyle(
        "AmsterdamBullet",
        fontName=regular,
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        leftIndent=12,
        spaceAfter=2,
    )
    story = []
    lines = [line.rstrip() for line in text.splitlines()]
    first_written = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 4))
            continue
        is_heading = classify_heading(stripped) is not None and len(stripped) <= 80
        if (
            title == "CV"
            and not first_written
            and not is_heading
            and len(stripped) <= 60
            and "@" not in stripped
            and not any(char.isdigit() for char in stripped)
        ):
            story.append(Paragraph(_xml(stripped), name))
            first_written = True
            continue
        first_written = True
        if is_heading:
            story.append(Paragraph(_xml(stripped), heading))
            continue
        story.append(Paragraph(_xml(stripped), bullet if _bullet(stripped) else body))
    if not story:
        raise CareerCopilotError("The CV could not be written.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pdf.tmp")
    document = SimpleDocTemplate(
        str(temporary),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title or "CV",
        author="",
    )
    document.build(story)
    temporary.replace(path)


def _proposed(
    cv_text: str,
    posting: str,
    note: str,
    propose: Callable[[str, str, str], tuple[str, str]],
) -> tuple[str, str]:
    try:
        summary, skills = propose(cv_text, posting, note)
    except CareerCopilotError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CareerCopilotError("The CV could not be written.") from exc
    if not isinstance(summary, str) or not isinstance(skills, str):
        raise CareerCopilotError("The CV could not be written.")
    return summary[:2000], skills[:2000]


def _store_cv(key: str, preview: str, summary: str, skills: str, root: Path | None) -> Path:
    folder = document_folder(key, root)
    folder.mkdir(parents=True, exist_ok=True)
    _replace_text(folder / "cv-preview.txt", preview)
    _replace_text(
        folder / "cv-sections.json",
        json.dumps({"summary": summary, "skills": skills}, indent=2) + "\n",
    )
    write_amsterdam_pdf(folder / "cv.pdf", preview, title="CV")
    return folder / "cv.pdf"


def _saved_sections(folder: Path) -> tuple[str, str]:
    path = folder / "cv-sections.json"
    if not path.is_file():
        return "", ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    summary = payload.get("summary")
    skills = payload.get("skills")
    return (summary if isinstance(summary, str) else "", skills if isinstance(skills, str) else "")


def _original_body(parts: list[CvPart], role: str) -> str:
    for part in parts:
        if part.role == role:
            return part.body.strip()
    return ""


def _sentences(text: str) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _skill_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        for piece in line.split(","):
            value = piece.strip(" -•*\t")
            if value:
                items.append(value)
    return items


def _bullet(text: str) -> bool:
    return text.startswith(("- ", "* ", "• "))


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular = Path(r"C:\Windows\Fonts\arial.ttf")
    bold = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if regular.is_file() and bold.is_file():
        if "Amsterdam" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Amsterdam", str(regular)))
            pdfmetrics.registerFont(TTFont("Amsterdam-Bold", str(bold)))
        return "Amsterdam", "Amsterdam-Bold"
    return "Times-Roman", "Times-Bold"


def _ask_grok(
    prompt: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    *,
    missing: str = "The CV could not be written.",
) -> dict[str, object]:
    executable = shutil.which("grok")
    if executable is None:
        raise CareerCopilotError("Grok is not available in the terminal. Run grok login, then try again.")
    call = runner or subprocess.run
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        try:
            completed = call(
                grok_argv(executable, prompt_file, directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_GROK_TIMEOUT_SECONDS,
                env=child_env(os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CareerCopilotError("Grok took too long. Nothing was saved.") from exc
    if completed.returncode != 0:
        raise CareerCopilotError(missing)
    return _unwrap(_json_object(completed.stdout or "", missing), missing)


def _unwrap(payload: dict[str, object], missing: str) -> dict[str, object]:
    text = payload.get("text")
    if not isinstance(text, str):
        return payload
    if any(key in payload for key in ("summary", "skills", "letter")):
        return payload
    return _json_object(text, missing)


def _json_object(text: str, missing: str = "The CV could not be written.") -> dict[str, object]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise CareerCopilotError(missing)
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CareerCopilotError(missing) from exc
    if not isinstance(payload, dict):
        raise CareerCopilotError(missing)
    return payload


def _cv_prompt(cv_text: str, posting: str, note: str) -> str:
    guidance = note.strip() or "Fit the summary and the skills to this job."
    return (
        "Propose a professional summary and a technical skills block for this job.\n"
        "Return only JSON with this shape:\n"
        '{"summary":"one or two sentences","skills":"Python\\nSQL"}\n'
        "Rules:\n"
        "- Use only facts already written in the CV.\n"
        "- Lead with the CV facts that this job asks about.\n"
        "- When the CV already contains a skill, write it with the job's wording.\n"
        "- A plural or singular form of that same skill is allowed.\n"
        "- Do not add an employer, title, date, number, or skill that the CV does not contain.\n"
        "- A related idea is not the same skill.\n"
        "- Do not write experience, education, publications, languages, or contact details.\n"
        "- The note may ask for a change to the summary or the skills only.\n"
        f"Note:\n{guidance}\n"
        f"Job:\n{posting.strip()[:4000]}\n"
        f"CV:\n{cv_text.strip()[:20000]}\n"
    )


def _letter_prompt(cv_text: str, posting: str) -> str:
    return (
        "Write a short cover letter for this job.\n"
        "Return only JSON with this shape:\n"
        '{"letter":"two or three sentences"}\n'
        "Rules:\n"
        "- Use only facts already written in the CV.\n"
        "- Do not add an employer, title, date, number, or skill that the CV does not contain.\n"
        "- Do not invent a postal address or a phone number.\n"
        f"Job:\n{posting.strip()[:4000]}\n"
        f"CV:\n{cv_text.strip()[:20000]}\n"
    )


