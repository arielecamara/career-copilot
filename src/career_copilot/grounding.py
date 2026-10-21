"""Checks that stored atoms still quote their source documents."""

from __future__ import annotations

from career_copilot.models import MasterProfile


def grounding_problems(profile: MasterProfile) -> list[str]:
    """Return human-readable problems. An empty list means the profile quotes its sources."""

    problems: list[str] = []
    documents = {record.document_id: record for record in profile.documents}
    seen: set[str] = set()
    for atom in profile.atoms:
        if atom.evidence_id in seen:
            problems.append(f"{atom.evidence_id} is duplicated")
        seen.add(atom.evidence_id)
        document = documents.get(atom.source_document_id)
        if document is None:
            problems.append(f"{atom.evidence_id} cites a missing document")
            continue
        source = document.text
        if atom.source_excerpt not in source:
            problems.append(f"{atom.evidence_id} excerpt is not a verbatim span of its document")
        if atom.employer is not None and atom.employer not in source:
            problems.append(f"{atom.evidence_id} employer is not copied from its document")
        if atom.title is not None and atom.title not in source:
            problems.append(f"{atom.evidence_id} title is not copied from its document")
        if atom.start_date is not None and atom.start_date.raw not in source:
            problems.append(f"{atom.evidence_id} start date is not copied from its document")
        if atom.end_date is not None and atom.end_date.raw not in source:
            problems.append(f"{atom.evidence_id} end date is not copied from its document")

    for conflict in profile.conflicts:
        atoms = []
        for evidence_id in conflict.evidence_ids:
            match = profile.atom(evidence_id)
            if match is None:
                problems.append(f"{conflict.conflict_id} cites missing atom {evidence_id}")
                continue
            atoms.append(match)
        if len(atoms) == 2 and atoms[0].source_document_id == atoms[1].source_document_id:
            problems.append(f"{conflict.conflict_id} is inside a single document")
    return problems
