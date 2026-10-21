"""Model proposer. Python still accepts or rejects every id and every sentence.

A call is sent only when the model switch, the API key, and the daily quota all allow it.
The default is no call.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, ConfigDict, ValidationError

from career_copilot.job import analyze_job, atom_supports, checklist_tally, requested_documents
from career_copilot.models import (
    ChecklistItem,
    ConfirmationState,
    DocumentRequest,
    JobAnalysis,
    ModelMode,
    ProfileAtom,
    RequirementKind,
    RequirementStatus,
    MasterProfile,
    ResumeSentence,
    SentenceKind,
)
from career_copilot.resume import ResumeBundle
from career_copilot.spend import (
    DAILY_LIMIT_NOTE,
    api_key,
    gate,
    ledger_path,
    reserve,
    settle,
)
from career_copilot.validator import validate_resume

SCORE_MODEL = "grok-4.3"
DRAFT_MODEL = "grok-4.7"
_API_URL = "https://api.x.ai/v1/chat/completions"
_JOB_CHARS = 12_000
_REPLACEABLE = {SentenceKind.summary, SentenceKind.skill, SentenceKind.bullet}
_Transport = Callable[[dict[str, object], str, int], dict[str, object]]


class _ProposedRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    kind: RequirementKind
    status: RequirementStatus
    evidence_ids: list[str] = []


class _ProposedScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirements: list[_ProposedRequirement] = []
    requested_documents: list[str] = []


class _ProposedSentence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    evidence_ids: list[str] = []
    kind: SentenceKind


class _ProposedDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sentences: list[_ProposedSentence] = []


def read_job(
    text: str,
    atoms: list[ProfileAtom],
    *,
    as_of: date,
    transport: _Transport | None = None,
) -> JobAnalysis:
    """Read a pasted job. The local checklist is the result when no call is allowed."""

    local = analyze_job(text, atoms, as_of=as_of)
    allowed, note, estimate, max_out = gate("score", min(len(text), _JOB_CHARS))
    if not allowed:
        return local.model_copy(update={"model_mode": ModelMode.off, "model_note": note})
    path = ledger_path()
    if not reserve(estimate, path=path):
        return local.model_copy(update={"model_mode": ModelMode.off, "model_note": DAILY_LIMIT_NOTE})
    try:
        payload = _score_payload(text, atoms, max_out)
        body = (transport or post_chat)(payload, api_key(), 60)
    except (CareerCallError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeError):
        settle(estimate, None, path=path)
        return local.model_copy(
            update={
                "model_mode": ModelMode.off,
                "model_note": "The model call did not complete. The local checklist is shown.",
            }
        )
    settle(estimate, _cost(body), path=path)
    proposed = _parse_score(body)
    if proposed is None:
        return local.model_copy(
            update={
                "model_mode": ModelMode.off,
                "model_note": "The model reply could not be read. The local checklist is shown.",
            }
        )
    requirements = _accept_requirements(proposed.requirements, atoms)
    if not requirements:
        return local.model_copy(
            update={
                "model_mode": ModelMode.off,
                "model_note": "The model returned no usable requirements. The local checklist is shown.",
            }
        )
    documents = _documents(local.requested_documents, proposed.requested_documents, text)
    return local.model_copy(
        update={
            "requirements": requirements,
            "tally": checklist_tally(requirements),
            "requested_documents": documents,
            "model_mode": ModelMode.model,
            "model_note": "The model read this posting. Python checked every evidence id.",
        }
    )


def apply_model_draft(
    profile: MasterProfile,
    analysis: JobAnalysis,
    bundle: ResumeBundle,
    job_text: str,
    *,
    transport: _Transport | None = None,
    replaceable: set[SentenceKind] | None = None,
) -> ResumeBundle | None:
    """Replace summary, skills, and bullets only when the checker accepts the draft.

    `replaceable` narrows that set. The original CV allows summary and skills only.
    Returns None when no call is made or the draft is rejected. The caller keeps
    the confirmed-sentence resume.
    """

    if analysis.model_mode != ModelMode.model:
        return None
    allowed, _note, estimate, max_out = gate("draft", min(len(job_text), _JOB_CHARS) + 4000)
    if not allowed:
        return None
    path = ledger_path()
    if not reserve(estimate, path=path):
        return None
    try:
        payload = _draft_payload(job_text, profile.atoms, max_out)
        body = (transport or post_chat)(payload, api_key(), 90)
    except (CareerCallError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeError):
        settle(estimate, None, path=path)
        return None
    settle(estimate, _cost(body), path=path)
    proposed = _parse_draft(body)
    if proposed is None:
        return None
    kinds = _REPLACEABLE if replaceable is None else set(replaceable)
    spliced = _splice(bundle.sentences, proposed.sentences, profile, analysis, kinds)
    if spliced is None:
        return None
    diff = bundle.diff_markdown
    if not diff.endswith("\n"):
        diff += "\n"
    replaced = "summary and skills" if SentenceKind.bullet not in kinds else "summary, skills, and bullets"
    diff += f"\nThe model replaced the {replaced}. The checker passed.\n"
    return ResumeBundle(
        sentences=spliced,
        cover=bundle.cover,
        diff_markdown=diff,
        publication_note=bundle.publication_note,
    )


def post_chat(payload: dict[str, object], key: str, timeout: int) -> dict[str, object]:
    """Send one chat request. Callers reserve the worst case before this runs."""

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _API_URL,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CareerCallError("The model call was refused.") from exc
    if not isinstance(parsed, dict):
        raise CareerCallError("The model reply was not an object.")
    return parsed


class CareerCallError(Exception):
    """A model call that did not return a usable body. The message is not logged with the CV."""


def _score_payload(text: str, atoms: list[ProfileAtom], max_out: int) -> dict[str, object]:
    job = text[:_JOB_CHARS]
    return {
        "model": SCORE_MODEL,
        "max_tokens": max_out,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You propose a job checklist. You do not decide that a sentence is true. "
                    "Return a JSON object with keys requirements and requested_documents. "
                    "Each requirement has text, kind (mandatory or preferred), "
                    "status (met, partial, missing, or unknown), and evidence_ids. "
                    "Use only evidence ids from the profile. "
                    "requested_documents is a list drawn from cover_letter, portfolio, "
                    "writing_sample, and transcript."
                ),
            },
            {"role": "user", "content": json.dumps({"job": job, "profile": _card(atoms)}, ensure_ascii=False)},
        ],
    }


def _draft_payload(text: str, atoms: list[ProfileAtom], max_out: int) -> dict[str, object]:
    job = text[:_JOB_CHARS]
    return {
        "model": DRAFT_MODEL,
        "max_tokens": max_out,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You draft resume sentences from confirmed facts. "
                    "Return a JSON object with key sentences. "
                    "Each sentence has text, evidence_ids, and kind (summary, skill, or bullet). "
                    "Cite only the given evidence ids. Do not add an employer, a title, a date, "
                    "a number, or a skill that is not in the cited fact."
                ),
            },
            {"role": "user", "content": json.dumps({"job": job, "profile": _card(atoms)}, ensure_ascii=False)},
        ],
    }


def _card(atoms: list[ProfileAtom]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for atom in atoms:
        if atom.confirmation != ConfirmationState.confirmed:
            continue
        rows.append(
            {
                "evidence_id": atom.evidence_id,
                "statement": atom.statement[:500],
                "skills": atom.skills,
                "employer": atom.employer,
                "title": atom.title,
            }
        )
        if len(rows) == 60:
            break
    return rows


def _parse_score(body: dict[str, object]) -> _ProposedScore | None:
    content = _content(body)
    if content is None:
        return None
    try:
        return _ProposedScore.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError):
        return None


def _parse_draft(body: dict[str, object]) -> _ProposedDraft | None:
    content = _content(body)
    if content is None:
        return None
    try:
        return _ProposedDraft.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError):
        return None


def _content(body: dict[str, object]) -> str | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _cost(body: dict[str, object]) -> float | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    ticks = usage.get("cost_in_usd_ticks")
    if isinstance(ticks, int) and ticks >= 0:
        return ticks / 10_000_000_000
    return None


def _accept_requirements(items: list[_ProposedRequirement], atoms: list[ProfileAtom]) -> list[ChecklistItem]:
    confirmed = {
        atom.evidence_id: atom for atom in atoms if atom.confirmation == ConfirmationState.confirmed
    }
    accepted: list[ChecklistItem] = []
    seen: set[str] = set()
    for item in items[:40]:
        text = item.text.strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        ids: list[str] = []
        excerpt = ""
        for evidence_id in item.evidence_ids:
            atom = confirmed.get(evidence_id)
            if atom is None or not _supports(atom, text):
                continue
            ids.append(evidence_id)
            if not excerpt:
                excerpt = atom.source_excerpt
        status = item.status
        if status in {RequirementStatus.met, RequirementStatus.partial}:
            if not ids or not excerpt:
                status = RequirementStatus.missing
                ids = []
                excerpt = ""
        else:
            ids = []
            excerpt = ""
        accepted.append(
            ChecklistItem(
                text=text,
                kind=item.kind,
                status=status,
                evidence_ids=ids,
                excerpt=excerpt,
            )
        )
    return accepted


def _supports(atom: ProfileAtom, text: str) -> bool:
    if atom_supports(atom, text):
        return True
    folded = text.casefold()
    for skill in atom.skills:
        if len(skill) >= 2 and skill.casefold() in folded:
            return True
    for field in (atom.employer, atom.title):
        if field and len(field) >= 3 and field.casefold() in folded:
            return True
    return False


def _documents(
    local: list[DocumentRequest],
    proposed: list[str],
    text: str,
) -> list[DocumentRequest]:
    found = list(requested_documents(text))
    for item in local:
        if item not in found:
            found.append(item)
    allowed = {member.value: member for member in DocumentRequest}
    for name in proposed:
        kind = allowed.get(name)
        if kind is not None and kind not in found:
            found.append(kind)
    return found


def _splice(
    local: list[ResumeSentence],
    proposed: list[_ProposedSentence],
    profile: MasterProfile,
    analysis: JobAnalysis,
    replaceable: set[SentenceKind],
) -> list[ResumeSentence] | None:
    incoming: list[ResumeSentence] = []
    for item in proposed:
        if item.kind not in replaceable:
            return None
        try:
            sentence = ResumeSentence(
                text=item.text,
                evidence_ids=item.evidence_ids,
                kind=item.kind,
            )
        except ValidationError:
            return None
        incoming.append(sentence)
    if not incoming:
        return None
    proposed_kinds = {sentence.kind for sentence in incoming}
    grouped = {kind: [sentence for sentence in incoming if sentence.kind == kind] for kind in proposed_kinds}
    result: list[ResumeSentence] = []
    inserted: set[SentenceKind] = set()
    for sentence in local:
        if sentence.kind not in proposed_kinds:
            result.append(sentence)
            continue
        if sentence.kind not in inserted:
            inserted.add(sentence.kind)
            result.extend(grouped[sentence.kind])
    if validate_resume(result, profile, analysis):
        return None
    return result
