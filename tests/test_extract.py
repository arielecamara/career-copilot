from career_copilot.extract import extract_atoms, split_skill_items
from career_copilot.grounding import grounding_problems
from career_copilot.models import DocumentKind, EvidenceCategory
from tests.factories import document, profile_for


CV = [
    ("paragraph", "Ada Lovelace"),
    ("heading", "Professional Summary"),
    ("paragraph", "Engineer who uses Postgres."),
    ("heading", "Technical Skills"),
    ("bullet", "Applied AI: Machine Learning (Dataiku), Docker, and Python."),
    ("heading", "Professional Experience"),
    ("paragraph", "Engineer | Acme Jan 2020 – Present"),
    ("bullet", "Led the API migration and cut latency by 20% using Postgres."),
    ("bullet", "Managed the on-call rotation."),
    ("paragraph", "Analyst | Other Co"),
    ("paragraph", "2016 - 2018"),
    ("heading", "Education"),
    ("paragraph", "BSc Computer Science | Example University"),
    ("heading", "Projects"),
    ("paragraph", "Journal Articles"),
    ("paragraph", "Property graph prototype using Docker."),
    ("heading", "Languages"),
    ("bullet", "Portuguese: Native"),
]


def _atoms():
    record = document(CV)
    atoms = extract_atoms(record)
    return record, atoms


def test_extraction_copies_claims_and_does_not_confirm_them():
    record, atoms = _atoms()
    assert grounding_problems(profile_for(record, atoms)) == []
    assert atoms
    assert all(atom.confirmation.value == "extracted" for atom in atoms)
    assert all(atom.evidence_id.startswith("ev_") for atom in atoms)
    assert "Professional Experience" not in {atom.statement for atom in atoms}
    assert "Journal Articles" not in {atom.statement for atom in atoms}


def test_role_fields_dates_metrics_and_ownership_stay_exact():
    _record, atoms = _atoms()
    role = next(atom for atom in atoms if atom.employer == "Acme")
    assert role.title == "Engineer"
    assert role.start_date is not None and role.start_date.raw == "Jan 2020"
    assert role.end_date is None
    assert role.category == EvidenceCategory.used_in_job
    assert "2020" not in role.metrics

    bullet = next(atom for atom in atoms if "20%" in atom.statement)
    assert bullet.employer == "Acme"
    assert bullet.title == "Engineer"
    assert bullet.end_date is None
    assert bullet.metrics == ["20%"]
    assert bullet.ownership_verb == "Led"
    assert bullet.skills == ["Postgres"]
    assert "PostgreSQL" not in bullet.skills
    assert "SQL" not in bullet.skills
    assert "Kubernetes" not in bullet.skills

    managed = next(atom for atom in atoms if atom.statement.startswith("Managed"))
    assert managed.ownership_verb is None


def test_second_role_can_be_split_across_a_date_line():
    _record, atoms = _atoms()
    role = next(atom for atom in atoms if atom.employer == "Other Co")
    assert role.title == "Analyst"
    assert role.start_date is not None and role.start_date.year == 2016
    assert role.end_date is not None and role.end_date.year == 2018
    assert "\n" in role.source_excerpt


def test_skills_section_does_not_expand_synonyms_or_drop_parentheticals():
    items = split_skill_items("Applied AI: Machine Learning (Dataiku), Docker, and Python.")
    assert items == ["Machine Learning (Dataiku)", "Docker", "Python"]
    _record, atoms = _atoms()
    skills = [atom.statement for atom in atoms if atom.category == EvidenceCategory.mentioned_only and atom.skills]
    assert "Machine Learning (Dataiku)" in skills
    assert "Docker" in skills
    assert "PostgreSQL" not in skills
    assert "Kubernetes" not in skills


def test_education_is_coursework_and_not_an_employer():
    _record, atoms = _atoms()
    education = next(atom for atom in atoms if "Example University" in atom.statement)
    assert education.category == EvidenceCategory.coursework
    assert education.employer is None
    assert education.title is None


def test_project_and_language_categories():
    _record, atoms = _atoms()
    project = next(atom for atom in atoms if "Property graph" in atom.statement)
    assert project.category == EvidenceCategory.project
    assert project.skills == ["Docker"]
    language = next(atom for atom in atoms if atom.statement == "Portuguese: Native")
    assert language.category == EvidenceCategory.mentioned_only
    assert language.skills == ["Portuguese"]


def test_summary_stays_mentioned_only():
    _record, atoms = _atoms()
    summary = next(atom for atom in atoms if atom.statement == "Engineer who uses Postgres.")
    assert summary.category == EvidenceCategory.mentioned_only
    assert summary.skills == ["Postgres"]


def test_certificate_without_headings_is_coursework():
    record = document(
        [("paragraph", "Completed the Docker course in March 2021.")],
        kind=DocumentKind.certificate,
        document_id="doc_" + "c" * 16,
    )
    atoms = extract_atoms(record)
    assert len(atoms) == 1
    assert atoms[0].category == EvidenceCategory.coursework
    assert atoms[0].employer is None
    assert atoms[0].skills == ["Docker"]
    assert atoms[0].start_date is None


def test_evidence_ids_are_stable_and_unique():
    record = document(CV)
    first = [atom.evidence_id for atom in extract_atoms(record)]
    second = [atom.evidence_id for atom in extract_atoms(record)]
    assert first == second
    assert len(first) == len(set(first))
