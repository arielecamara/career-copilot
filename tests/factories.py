"""In-memory documents and atoms for tests. These are synthetic, not real CVs."""

from __future__ import annotations

from datetime import datetime, timezone

from career_copilot.models import (
    DEFAULT_DATA_NOTICE,
    BlockKind,
    ConfirmationState,
    DocumentKind,
    DocumentRecord,
    EvidenceCategory,
    ExactDate,
    MasterProfile,
    ProfileAtom,
    TextBlock,
)


def document(
    blocks: list[tuple[str, str]],
    *,
    document_id: str = "doc_" + "a" * 16,
    kind: DocumentKind = DocumentKind.cv,
    filename: str = "fixture.docx",
) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        filename=filename,
        media_type="docx",
        kind=kind,
        sha256="b" * 64,
        single_column=True,
        blocks=[TextBlock(text=text, kind=BlockKind(block_kind)) for block_kind, text in blocks],
    )


def profile_for(record: DocumentRecord, atoms: list[ProfileAtom]) -> MasterProfile:
    return MasterProfile(
        data_notice=DEFAULT_DATA_NOTICE,
        documents=[record],
        atoms=atoms,
        updated_at=datetime.now(timezone.utc),
    )


def atom(**overrides: object) -> ProfileAtom:
    payload: dict[str, object] = {
        "evidence_id": "ev_" + "1" * 16,
        "statement": "Engineer | Acme 2020 – 2022",
        "category": EvidenceCategory.used_in_job,
        "confirmation": ConfirmationState.extracted,
        "source_document_id": "doc_" + "a" * 16,
        "source_excerpt": "Engineer | Acme 2020 – 2022",
        "employer": "Acme",
        "title": "Engineer",
        "start_date": ExactDate(raw="2020", year=2020),
        "end_date": ExactDate(raw="2022", year=2022),
    }
    payload.update(overrides)
    return ProfileAtom(**payload)  # type: ignore[arg-type]
