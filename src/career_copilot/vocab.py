"""Heading names and skill surface forms.

Skill detection copies a term only when that exact term is written in the excerpt.
Postgres, PostgreSQL, and SQL stay different terms. Docker does not become Kubernetes.
"""

from __future__ import annotations

import re
from enum import Enum

class Section(str, Enum):
    preamble = "preamble"
    summary = "summary"
    skills = "skills"
    experience = "experience"
    projects = "projects"
    education = "education"
    other = "other"


# Keys are normalize_heading() output.
HEADINGS: dict[str, Section] = {
    "summary": Section.summary,
    "professional summary": Section.summary,
    "profile": Section.summary,
    "about": Section.summary,
    "objective": Section.summary,
    "resumo": Section.summary,
    "resumo profissional": Section.summary,
    "perfil": Section.summary,
    "skills": Section.skills,
    "technical skills": Section.skills,
    "core skills": Section.skills,
    "core competencies": Section.skills,
    "competencies": Section.skills,
    "competences": Section.skills,
    "technologies": Section.skills,
    "tecnologias": Section.skills,
    "competencias": Section.skills,
    "competências": Section.skills,
    "habilidades": Section.skills,
    "experience": Section.experience,
    "work experience": Section.experience,
    "professional experience": Section.experience,
    "employment": Section.experience,
    "employment history": Section.experience,
    "work history": Section.experience,
    "career history": Section.experience,
    "experiencia": Section.experience,
    "experiência": Section.experience,
    "experiencia profissional": Section.experience,
    "experiência profissional": Section.experience,
    "projects": Section.projects,
    "personal projects": Section.projects,
    "portfolio": Section.projects,
    "projects and publications": Section.projects,
    "publications": Section.projects,
    "projetos": Section.projects,
    "education": Section.education,
    "academic background": Section.education,
    "formation": Section.education,
    "formação": Section.education,
    "formacao": Section.education,
    "certifications": Section.education,
    "certificates": Section.education,
    "certificacoes": Section.education,
    "certificações": Section.education,
    "courses": Section.education,
    "coursework": Section.education,
    "training": Section.education,
    "cursos": Section.education,
    "languages": Section.other,
    "language": Section.other,
    "idiomas": Section.other,
    "awards": Section.other,
    "honors": Section.other,
    "honours": Section.other,
    "volunteer": Section.other,
    "volunteering": Section.other,
    "interests": Section.other,
    "hobbies": Section.other,
    "references": Section.other,
}


def normalize_heading(text: str) -> str:
    folded = text.casefold().replace("&", " and ")
    chars = [char if char.isalnum() or char.isspace() else " " for char in folded]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def classify_heading(text: str) -> Section | None:
    return HEADINGS.get(normalize_heading(text))


def looks_like_heading(text: str) -> bool:
    """True only for known section titles. All-caps names are not headings."""

    return classify_heading(text) is not None


# A closed detector. A term is emitted only when the excerpt contains it.
# Short ambiguous names (Go, R, C) are omitted on purpose.
SKILL_LEXICON: tuple[str, ...] = (
    "Airflow",
    "Ansible",
    "AWS",
    "Azure",
    "C#",
    "C++",
    "CSS",
    "Cypher",
    "Django",
    "Docker",
    "FastAPI",
    "Flask",
    "GCP",
    "Git",
    "Golang",
    "GraphQL",
    "HTML",
    "Java",
    "JavaScript",
    "Kafka",
    "Kotlin",
    "Kubernetes",
    "Linux",
    "MongoDB",
    "MySQL",
    "Neo4j",
    "Node.js",
    "pandas",
    "PHP",
    "Postgres",
    "PostgreSQL",
    "PyTorch",
    "Python",
    "React",
    "Redis",
    "Ruby",
    "Rust",
    "Scala",
    "scikit-learn",
    "Spark",
    "SQL",
    "SQLite",
    "Swift",
    "Tableau",
    "TensorFlow",
    "Terraform",
    "TypeScript",
)


def skill_named_in(skill: str, text: str) -> bool:
    """True when `skill` is written as its own term, not as part of a longer word."""

    stripped = skill.strip()
    if not stripped:
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(stripped) + r"(?!\w)", re.IGNORECASE)
    return pattern.search(text) is not None
