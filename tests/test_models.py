from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from career_copilot.models import (
    DEFAULT_DATA_NOTICE,
    ConfirmationState,
    EvidenceCategory,
    ExactDate,
    MasterProfile,
)
from tests.factories import atom


def test_atom_requires_evidence_id_shape():
    with pytest.raises(ValidationError):
        atom(evidence_id="role-1")


def test_unknown_category_is_rejected():
    with pytest.raises(ValidationError):
        atom(category="hands_on")


def test_skill_must_be_named_in_the_excerpt():
    with pytest.raises(ValidationError):
        atom(
            statement="Used Docker in production.",
            source_excerpt="Used Docker in production.",
            skills=["Kubernetes"],
        )


def test_sql_inside_postgresql_is_not_a_named_skill():
    with pytest.raises(ValidationError):
        atom(
            statement="Used PostgreSQL.",
            source_excerpt="Used PostgreSQL.",
            skills=["SQL"],
            employer=None,
            title=None,
            start_date=None,
            end_date=None,
            category=EvidenceCategory.mentioned_only,
        )


def test_metric_must_be_an_exact_copy():
    with pytest.raises(ValidationError):
        atom(
            statement="Cut latency by 20%.",
            source_excerpt="Cut latency by 20%.",
            metrics=["21%"],
            employer=None,
            title=None,
            start_date=None,
            end_date=None,
        )


def test_ownership_verb_outside_the_table_is_rejected():
    with pytest.raises(ValidationError):
        atom(
            statement="Managed the migration.",
            source_excerpt="Managed the migration.",
            ownership_verb="managed",
            employer=None,
            title=None,
            start_date=None,
            end_date=None,
        )


def test_ownership_verb_must_be_written_in_the_excerpt():
    with pytest.raises(ValidationError):
        atom(
            statement="Joined the migration.",
            source_excerpt="Joined the migration.",
            ownership_verb="led",
            employer=None,
            title=None,
            start_date=None,
            end_date=None,
        )


def test_statement_cannot_introduce_a_number():
    with pytest.raises(ValidationError):
        atom(
            statement="Cut latency by 50%.",
            source_excerpt="Cut latency by 20%.",
            employer=None,
            title=None,
            start_date=None,
            end_date=None,
        )


def test_open_ended_role_allows_a_null_end_date():
    created = atom(end_date=None, statement="Engineer | Acme 2020 – Present", source_excerpt="Engineer | Acme 2020 – Present")
    assert created.end_date is None
    assert created.confirmation == ConfirmationState.extracted


def test_day_requires_a_month():
    with pytest.raises(ValidationError):
        ExactDate(raw="2020", year=2020, day=2)


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        atom(
            start_date=ExactDate(raw="2022", year=2022),
            end_date=ExactDate(raw="2020", year=2020),
        )


def test_future_confirmation_value_is_in_the_schema():
    created = atom(confirmation=ConfirmationState.externally_verified)
    assert created.confirmation == ConfirmationState.externally_verified


def test_profile_round_trip_keeps_evidence_ids():
    created = atom()
    profile = MasterProfile(
        data_notice=DEFAULT_DATA_NOTICE,
        atoms=[created],
        updated_at=datetime.now(timezone.utc),
    )
    loaded = MasterProfile.model_validate_json(profile.model_dump_json())
    assert loaded.atoms[0].evidence_id == created.evidence_id
    assert loaded.data_notice.deletion == DEFAULT_DATA_NOTICE.deletion
