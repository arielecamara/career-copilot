"""Synonyms match. A named tool stays possible until the person answers."""

import json
from pathlib import Path

from career_copilot.esco import load_index
from career_copilot.job_list import real_search
from career_copilot.roles import WorkRole
from career_copilot.search_intent import clean_conditions
from test_job_list import _web_opening


def _roles():
    return [WorkRole(name="researcher", reason="The work is research.")]


def test_a_synonym_is_a_match_and_a_tool_is_possible(tmp_path: Path):
    index = tmp_path / "esco.json"
    index.write_text(
        json.dumps(
            [
                {
                    "id": "concept-1",
                    "labels": ["knowledge graph", "semantic network"],
                }
            ]
        ),
        encoding="utf-8",
    )
    load_index(index)
    try:
        page = _web_opening(
            "https://example.com/jobs/concept",
            "\n".join(
                [
                    "Requirements",
                    "Semantic network",
                    "Neo4j",
                    "Nice to have",
                    "Board certification",
                ]
            ),
            title="Analyst",
            workplace="Remote",
        )
        outcome = real_search(
            "knowledge graph researcher",
            _roles(),
            clean_conditions([], [], ["Remote"], "", ""),
            score=lambda _cv, _posting: (_ for _ in ()).throw(AssertionError("scored")),
            discover=lambda: [page],
            skills=["knowledge graph"],
            path=tmp_path / "jobs.json",
            require_grok=False,
        )
    finally:
        load_index(tmp_path / "missing.json")
    job = outcome.latest[0]
    assert "Semantic network" not in job.covered
    assert "Semantic network" not in job.missing
    assert "Neo4j" in [item.ask for item in job.assumed]
    assert "Neo4j" not in job.missing
    assert "Board certification" not in job.missing
    assert job.weights["Neo4j"] == 2
