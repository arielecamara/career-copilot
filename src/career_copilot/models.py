"""Pydantic boundaries for the master profile.

A valid model fixes the shape of a fact. It does not make the fact true.
Confirmation stays `extracted` until the candidate confirms it.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from career_copilot.ownership import OWNERSHIP_VERBS, verb_appears
from career_copilot.vocab import skill_named_in

EVIDENCE_ID = re.compile(r"^ev_[0-9a-f]{16}$")
DOCUMENT_ID = re.compile(r"^doc_[0-9a-f]{16}$")
CONFLICT_ID = re.compile(r"^cf_[0-9a-f]{16}$")
PROFILE_ID = re.compile(r"^pf_[0-9a-f]{16}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATEMENT_NUMBER = re.compile(r"\d+(?:[.,]\d+)?%?")


class EvidenceCategory(str, Enum):
    used_in_job = "used_in_job"
    project = "project"
    coursework = "coursework"
    mentioned_only = "mentioned_only"


class ConfirmationState(str, Enum):
    extracted = "extracted"
    confirmed = "confirmed"
    # Future work from SPEC.md. Profile commands refuse to set this.
    externally_verified = "externally_verified"


class DocumentKind(str, Enum):
    cv = "cv"
    certificate = "certificate"
    portfolio_note = "portfolio_note"


class BlockKind(str, Enum):
    heading = "heading"
    bullet = "bullet"
    paragraph = "paragraph"


class ConflictKind(str, Enum):
    employer = "employer"
    title = "title"
    dates = "dates"
    metric = "metric"
    skill_category = "skill_category"


class ExactDate(BaseModel):
    """A date copied from the source. Missing month or day stays missing."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    year: int
    month: int | None = None
    day: int | None = None

    @model_validator(mode="after")
    def check_parts(self) -> ExactDate:
        if not self.raw.strip():
            raise ValueError("date text is empty")
        if self.day is not None and self.month is None:
            raise ValueError("a day requires a month")
        if self.month is not None and not 1 <= self.month <= 12:
            raise ValueError("month must be from 1 to 12")
        if self.day is not None and not 1 <= self.day <= 31:
            raise ValueError("day must be from 1 to 31")
        if not 1900 <= self.year <= 2100:
            raise ValueError("year must be from 1900 to 2100")
        return self


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: BlockKind

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("block text is empty")
        return value.strip()


class DocumentRecord(BaseModel):
    """Stage-1 record. `blocks` are the text the atoms quote."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    media_type: Literal["docx", "pdf"]
    kind: DocumentKind
    sha256: str
    single_column: Literal[True] = True
    blocks: list[TextBlock]
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    @field_validator("document_id")
    @classmethod
    def check_document_id(cls, value: str) -> str:
        if not DOCUMENT_ID.match(value):
            raise ValueError("document_id must look like doc_ plus 16 hex characters")
        return value

    @field_validator("sha256")
    @classmethod
    def check_sha256(cls, value: str) -> str:
        if not SHA256.match(value):
            raise ValueError("sha256 must be 64 hex characters")
        return value

    @model_validator(mode="after")
    def require_text(self) -> DocumentRecord:
        if not self.blocks:
            raise ValueError("a document needs at least one text block")
        return self


class ProfileAtom(BaseModel):
    """One factual claim. Later generation may cite `evidence_id` only after confirmation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    statement: str
    category: EvidenceCategory
    confirmation: ConfirmationState
    source_document_id: str
    source_excerpt: str
    employer: str | None = None
    title: str | None = None
    start_date: ExactDate | None = None
    end_date: ExactDate | None = None
    metrics: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    ownership_verb: str | None = None

    @field_validator("evidence_id")
    @classmethod
    def check_evidence_id(cls, value: str) -> str:
        if not EVIDENCE_ID.match(value):
            raise ValueError("evidence_id must look like ev_ plus 16 hex characters")
        return value

    @field_validator("source_document_id")
    @classmethod
    def check_source_document_id(cls, value: str) -> str:
        if not DOCUMENT_ID.match(value):
            raise ValueError("source_document_id must look like doc_ plus 16 hex characters")
        return value

    @field_validator("statement", "source_excerpt")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("statement and source_excerpt are required")
        return stripped

    @field_validator("employer", "title", "ownership_verb")
    @classmethod
    def blank_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("skills", "metrics")
    @classmethod
    def clean_list(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            stripped = item.strip()
            if not stripped:
                raise ValueError("skills and metrics cannot be empty strings")
            if stripped not in seen:
                seen.add(stripped)
                cleaned.append(stripped)
        return cleaned

    @model_validator(mode="after")
    def check_grounding(self) -> ProfileAtom:
        excerpt = self.source_excerpt
        for skill in self.skills:
            if not skill_named_in(skill, excerpt):
                raise ValueError(f"skill {skill!r} is not named in the source excerpt")
        for metric in self.metrics:
            if metric not in excerpt:
                raise ValueError(f"metric {metric!r} is not an exact copy from the source excerpt")
        if self.ownership_verb is not None:
            allowed = {verb.casefold() for verb in OWNERSHIP_VERBS}
            if self.ownership_verb.casefold() not in allowed:
                raise ValueError(
                    f"ownership verb {self.ownership_verb!r} is not in the SPEC.md ownership table"
                )
            if not verb_appears(self.ownership_verb, excerpt):
                raise ValueError("ownership verb is not written in the source excerpt")
        for number in STATEMENT_NUMBER.findall(self.statement):
            if number not in excerpt:
                raise ValueError(
                    f"statement number {number!r} does not appear in the source excerpt"
                )
        if self.start_date is not None and self.end_date is not None:
            if _date_order(self.end_date) < _date_order(self.start_date):
                raise ValueError("end_date is earlier than start_date")
        return self


class DocumentConflict(BaseModel):
    """A disagreement between two documents. Resolving it does not merge or delete atoms."""

    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    kind: ConflictKind
    summary: str
    evidence_ids: list[str] = Field(min_length=2, max_length=2)
    left_value: str
    right_value: str
    resolved: bool = False
    resolution_note: str | None = None

    @field_validator("conflict_id")
    @classmethod
    def check_conflict_id(cls, value: str) -> str:
        if not CONFLICT_ID.match(value):
            raise ValueError("conflict_id must look like cf_ plus 16 hex characters")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def check_evidence_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not EVIDENCE_ID.match(item):
                raise ValueError("conflict evidence_ids must be evidence ids")
        if value[0] == value[1]:
            raise ValueError("a conflict needs two different atoms")
        return value

    @model_validator(mode="after")
    def note_when_resolved(self) -> DocumentConflict:
        if self.resolved and not (self.resolution_note and self.resolution_note.strip()):
            raise ValueError("a resolved conflict needs a note. Atoms are not merged.")
        if self.resolution_note is not None:
            stripped = self.resolution_note.strip()
            self.resolution_note = stripped or None
        return self


class RequirementKind(str, Enum):
    mandatory = "mandatory"
    preferred = "preferred"


class RequirementStatus(str, Enum):
    met = "met"
    partial = "partial"
    missing = "missing"
    unknown = "unknown"


class ConstraintKind(str, Enum):
    citizenship = "citizenship"
    work_authorization = "work_authorization"
    license = "license"
    minimum_years = "minimum_years"


class ConstraintResult(str, Enum):
    passed = "pass"
    failed = "fail"
    unknown = "unknown"


class ChecklistItem(BaseModel):
    """One requirement copied from a job description, judged only by confirmed atoms."""

    model_config = ConfigDict(extra="forbid")

    text: str
    kind: RequirementKind
    status: RequirementStatus
    evidence_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement text is empty")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def check_evidence_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not EVIDENCE_ID.match(item):
                raise ValueError("requirement evidence_ids must be evidence ids")
        return value

    @model_validator(mode="after")
    def evidence_follows_status(self) -> ChecklistItem:
        supported = self.status in {RequirementStatus.met, RequirementStatus.partial}
        if supported and not self.evidence_ids:
            raise ValueError("met and partial requirements need an evidence id")
        if supported and not self.excerpt.strip():
            raise ValueError("met and partial requirements need an excerpt")
        if not supported and (self.evidence_ids or self.excerpt.strip()):
            raise ValueError("missing and unknown requirements have no evidence")
        return self


class HardConstraint(BaseModel):
    """A boolean filter. The result is pass, fail, or unknown. Never a score."""

    model_config = ConfigDict(extra="forbid")

    text: str
    kind: ConstraintKind
    result: ConstraintResult
    required_years: int | None = None
    observed_days: int | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("constraint text is empty")
        return value.strip()

    @model_validator(mode="after")
    def years_fields(self) -> HardConstraint:
        if self.kind == ConstraintKind.minimum_years and self.required_years is None:
            raise ValueError("a minimum-years constraint needs required_years")
        if self.kind != ConstraintKind.minimum_years and (
            self.required_years is not None or self.observed_days is not None
        ):
            raise ValueError("only a minimum-years constraint records a year count")
        if self.required_years is not None and self.required_years < 0:
            raise ValueError("required_years cannot be negative")
        if self.observed_days is not None and self.observed_days < 0:
            raise ValueError("observed_days cannot be negative")
        return self


class ExperienceYears(BaseModel):
    """Length of the merged employment timeline. Overlaps are not added."""

    model_config = ConfigDict(extra="forbid")

    days: int
    as_of: str
    complete: bool
    intervals: list[str] = Field(default_factory=list)

    @field_validator("days")
    @classmethod
    def days_not_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("days cannot be negative")
        return value


class DocumentRequest(str, Enum):
    """A document named by the posting. Only a cover letter may later be drafted."""

    cover_letter = "cover_letter"
    portfolio = "portfolio"
    writing_sample = "writing_sample"
    transcript = "transcript"


class ModelMode(str, Enum):
    """Whether a model call produced the checklist. Off means no call was made."""

    off = "off"
    model = "model"


class ChecklistTally(BaseModel):
    """A count of checklist statuses. This is not a similarity score."""

    model_config = ConfigDict(extra="forbid")

    met: int = Field(ge=0)
    partial: int = Field(ge=0)
    missing: int = Field(ge=0)
    unknown: int = Field(ge=0)
    percent: float | None = None

    @model_validator(mode="after")
    def percent_matches_counts(self) -> ChecklistTally:
        judged = self.met + self.partial + self.missing
        if judged == 0:
            if self.percent is not None:
                raise ValueError("percent is empty when no requirement is met, partial, or missing")
            return self
        expected = round((self.met + 0.5 * self.partial) / judged * 100, 1)
        if self.percent is None or round(self.percent, 1) != expected:
            raise ValueError("percent must equal the checklist tally")
        self.percent = expected
        return self


class JobAnalysis(BaseModel):
    """Requirements, hard constraints, the years calculation, and a checklist tally."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[ChecklistItem] = Field(default_factory=list)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    years: ExperienceYears
    tally: ChecklistTally
    model_mode: ModelMode = ModelMode.off
    model_note: str = "Model is off. No call was made."
    requested_documents: list[DocumentRequest] = Field(default_factory=list)

    @field_validator("model_note")
    @classmethod
    def note_is_short(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("model note is empty")
        if len(stripped) > 400:
            raise ValueError("model note is too long")
        return stripped

    @field_validator("requested_documents")
    @classmethod
    def documents_once(cls, value: list[DocumentRequest]) -> list[DocumentRequest]:
        seen: list[DocumentRequest] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen


class SentenceKind(str, Enum):
    name = "name"
    contact = "contact"
    summary = "summary"
    skill = "skill"
    role = "role"
    bullet = "bullet"
    education = "education"
    project = "project"
    publication = "publication"
    language = "language"
    cover = "cover"


class ResumeSentence(BaseModel):
    """One resume or cover-letter sentence. It must cite a confirmed atom."""

    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str] = Field(min_length=1)
    kind: SentenceKind

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sentence text is empty")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def check_evidence_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not EVIDENCE_ID.match(item):
                raise ValueError("sentence evidence_ids must be evidence ids")
        return value


class Violation(BaseModel):
    """A deterministic rejection. This is not a match score."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "evidence",
        "confirmation",
        "employer",
        "title",
        "dates",
        "number",
        "ownership",
        "skill",
        "absent_skill",
        "rewrite",
    ]
    sentence: str
    detail: str


class DataNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    retention: str
    deletion: str


class PriorityLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    only_if_close = "only_if_close"


class WorkMode(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"


class SectorPreference(str, Enum):
    academia = "academia"
    industry = "industry"
    both = "both"


class AreaChoice(str, Enum):
    academia = "academia"
    industry = "industry"
    other = "other"


class ScheduleChoice(str, Enum):
    part_time = "part_time"
    full_time = "full_time"
    other = "other"


class WorkModePreference(BaseModel):
    """How strongly the person wants one way of working."""

    model_config = ConfigDict(extra="forbid")

    mode: WorkMode
    priority: PriorityLevel


MAX_PREFERENCE_CHARS = 200


class UserPreferences(BaseModel):
    """Answers stored on one profile. They do not search for jobs."""

    model_config = ConfigDict(extra="forbid")

    work_modes: list[WorkModePreference] = Field(min_length=3, max_length=3)
    onsite_location: str
    sector: SectorPreference
    time_availability: str | None = None
    job_types: list[WorkMode] = Field(default_factory=list)
    areas: list[AreaChoice] = Field(default_factory=list)
    schedules: list[ScheduleChoice] = Field(default_factory=list)

    @field_validator("onsite_location")
    @classmethod
    def location_not_blank(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("onsite location contains a null character")
        stripped = value.strip()
        if not stripped:
            raise ValueError("onsite location is empty")
        if len(stripped) > MAX_PREFERENCE_CHARS:
            raise ValueError("onsite location is too long")
        return stripped

    @field_validator("time_availability")
    @classmethod
    def availability_blank_is_missing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("time availability contains a null character")
        stripped = value.strip()
        if len(stripped) > MAX_PREFERENCE_CHARS:
            raise ValueError("time availability is too long")
        return stripped or None

    @field_validator("job_types", "areas", "schedules")
    @classmethod
    def unique_choices(cls, value: list) -> list:
        seen: list = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen

    @model_validator(mode="after")
    def each_mode_once(self) -> UserPreferences:
        modes = [item.mode for item in self.work_modes]
        if sorted(mode.value for mode in modes) != ["hybrid", "onsite", "remote"]:
            raise ValueError("preferences need remote, hybrid, and onsite exactly once")
        return self


DEFAULT_DATA_NOTICE = DataNotice(
    purpose=(
        "Build a local master profile so you can confirm facts before any "
        "resume or cover letter is generated."
    ),
    retention="The profile and source excerpts stay in this local file until you delete it.",
    deletion=(
        "Delete the profile file and your source documents to remove this data. "
        "Nothing is uploaded."
    ),
)


class Residence(BaseModel):
    """Contact the person added. It is not a work claim."""

    model_config = ConfigDict(extra="forbid")

    contact: str | None = None
    city: str | None = None
    country: str | None = None

    @field_validator("contact", "city", "country")
    @classmethod
    def clean_place(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("contact text contains a null character")
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > MAX_PREFERENCE_CHARS:
            raise ValueError("contact text is too long")
        return stripped


class MasterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    profile_id: str | None = None
    display_name: str | None = None
    residence: Residence | None = None
    data_notice: DataNotice
    documents: list[DocumentRecord] = Field(default_factory=list)
    atoms: list[ProfileAtom] = Field(default_factory=list)
    conflicts: list[DocumentConflict] = Field(default_factory=list)
    preferences: UserPreferences | None = None
    updated_at: datetime

    @field_validator("profile_id")
    @classmethod
    def check_profile_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not PROFILE_ID.match(value):
            raise ValueError("profile_id must look like pf_ plus 16 hex characters")
        return value

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("name contains a null character")
        stripped = value.strip()
        if not stripped:
            raise ValueError("name is empty")
        if len(stripped) > MAX_PREFERENCE_CHARS:
            raise ValueError("name is too long")
        return stripped

    def document(self, document_id: str) -> DocumentRecord | None:
        for record in self.documents:
            if record.document_id == document_id:
                return record
        return None

    def atom(self, evidence_id: str) -> ProfileAtom | None:
        for atom in self.atoms:
            if atom.evidence_id == evidence_id:
                return atom
        return None


def _date_order(value: ExactDate) -> tuple[int, int, int]:
    return (value.year, value.month or 1, value.day or 1)
