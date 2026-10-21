"""Grounding checks for real CVs.

Drop DOCX or single-column text PDFs into golden/ when you have permission to
store them. The harness does not invent resumes. One sample CV already lives
next to SPEC.md and is checked when that file is present.
"""

from pathlib import Path

import pytest

from career_copilot.extract import extract_atoms
from career_copilot.grounding import grounding_problems
from career_copilot.models import DocumentKind
from career_copilot.parsers import parse_document
from tests.factories import profile_for

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden"
SAMPLE = ROOT / "sample-cv.pdf"


def _documents():
    paths = []
    if GOLDEN.exists():
        paths.extend(sorted(GOLDEN.glob("*.docx")))
        paths.extend(sorted(GOLDEN.glob("*.pdf")))
    if SAMPLE.exists():
        paths.append(SAMPLE)
    return paths


def test_real_documents_quote_their_source_and_stay_unconfirmed():
    paths = _documents()
    if not paths:
        pytest.skip("No real CVs are available.")
    for path in paths:
        record = parse_document(path, DocumentKind.cv)
        atoms = extract_atoms(record)
        problems = grounding_problems(profile_for(record, atoms))
        assert problems == [], f"{path.name}: {problems}"
        assert atoms, path.name
        assert all(atom.evidence_id.startswith("ev_") for atom in atoms)
        assert all(atom.confirmation.value == "extracted" for atom in atoms)
        again = extract_atoms(parse_document(path, DocumentKind.cv))
        assert [atom.evidence_id for atom in atoms] == [atom.evidence_id for atom in again]


def test_sample_cv_finds_roles_and_skills():
    if not SAMPLE.exists():
        pytest.skip("Sample CV is not in the workspace.")
    record = parse_document(SAMPLE, DocumentKind.cv)
    atoms = extract_atoms(record)
    assert any(atom.category.value == "used_in_job" and atom.start_date is not None for atom in atoms)
    assert any(atom.skills for atom in atoms)
    assert any(atom.employer for atom in atoms)
