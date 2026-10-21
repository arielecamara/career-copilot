"""The model proposer stays off unless the switch, key, and quota all allow a call."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from career_copilot.cli import main
from career_copilot.job import requested_documents
from career_copilot.llm import apply_model_draft, read_job
from career_copilot.models import ConfirmationState, DocumentRequest, EvidenceCategory, ModelMode
from career_copilot.profile import save_profile
from career_copilot.resume import build_resume
from career_copilot.spend import spent_today
from tests.factories import atom, document, profile_for

AS_OF = date(2026, 10, 19)


def _python():
    return atom(
        evidence_id="ev_" + "1" * 16,
        statement="Used Python to reduce latency by 20%.",
        source_excerpt="Used Python to reduce latency by 20%.",
        confirmation=ConfirmationState.confirmed,
        category=EvidenceCategory.used_in_job,
        skills=["Python"],
        ownership_verb=None,
        employer="Acme",
        title="Engineer",
    )


def _arm(monkeypatch: pytest.MonkeyPatch, ledger: Path, quota: str = "1") -> None:
    monkeypatch.setenv("CAREER_COPILOT_MODEL", "on")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("CAREER_COPILOT_QUOTA_USD", quota)
    monkeypatch.setenv("CAREER_COPILOT_LEDGER", str(ledger))


def _reply(payload: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"cost_in_usd_ticks": 100_000_000},
    }


def test_a_pasted_job_makes_no_call_when_the_model_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CAREER_COPILOT_MODEL", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("CAREER_COPILOT_LEDGER", str(tmp_path / "ledger.json"))
    calls: list[object] = []

    def transport(payload, key, timeout):
        calls.append(payload)
        raise AssertionError("no call")

    analysis = read_job("Requirements:\n- Python", [_python()], as_of=AS_OF, transport=transport)
    assert calls == []
    assert analysis.model_mode == ModelMode.off
    assert analysis.model_note == "Model is off. No call was made."
    assert not (tmp_path / "ledger.json").exists()


def test_requested_documents_are_named_without_a_model():
    found = requested_documents(
        "Send a cover letter, a writing sample, and your academic transcript. Portfolio optional."
    )
    assert found == [
        DocumentRequest.cover_letter,
        DocumentRequest.portfolio,
        DocumentRequest.writing_sample,
        DocumentRequest.transcript,
    ]


def test_python_drops_an_evidence_id_the_profile_does_not_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _arm(monkeypatch, tmp_path / "ledger.json")
    seen: list[str] = []

    def transport(payload, key, timeout):
        assert key == "test-key"
        assert payload["model"] == "grok-4.3"
        seen.append("called")
        return _reply(
            {
                "requirements": [
                    {
                        "text": "Python",
                        "kind": "mandatory",
                        "status": "met",
                        "evidence_ids": ["ev_" + "1" * 16],
                    },
                    {
                        "text": "Kubernetes",
                        "kind": "mandatory",
                        "status": "met",
                        "evidence_ids": ["ev_" + "1" * 16, "ev_" + "9" * 16],
                    },
                ],
                "requested_documents": ["cover_letter", "portfolio"],
            }
        )

    analysis = read_job(
        "Requirements:\n- Python\n- Kubernetes\nA cover letter is required.",
        [_python()],
        as_of=AS_OF,
        transport=transport,
    )
    assert seen == ["called"]
    assert analysis.model_mode == ModelMode.model
    by_text = {item.text: item for item in analysis.requirements}
    assert by_text["Python"].status.value == "met"
    assert by_text["Python"].evidence_ids == ["ev_" + "1" * 16]
    assert by_text["Kubernetes"].status.value == "missing"
    assert by_text["Kubernetes"].evidence_ids == []
    assert DocumentRequest.cover_letter in analysis.requested_documents
    assert DocumentRequest.portfolio in analysis.requested_documents
    assert spent_today(tmp_path / "ledger.json", today=date.today()) == pytest.approx(0.01)


def test_the_daily_limit_stops_the_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ledger = tmp_path / "ledger.json"
    _arm(monkeypatch, ledger, quota="0.01")
    ledger.write_text(json.dumps({"day": date.today().isoformat(), "usd": 0.001}) + "\n", encoding="utf-8")

    def transport(payload, key, timeout):
        raise AssertionError("no call")

    analysis = read_job("Requirements:\n- Python", [_python()], as_of=AS_OF, transport=transport)
    assert analysis.model_note == "Stopped. Reached the daily limit."
    assert analysis.model_mode == ModelMode.off
    assert spent_today(ledger, today=date.today()) == pytest.approx(0.001)


def test_a_failed_call_releases_the_hold_and_keeps_the_local_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ledger = tmp_path / "ledger.json"
    _arm(monkeypatch, ledger)

    def transport(payload, key, timeout):
        raise TimeoutError("slow")

    analysis = read_job("Requirements:\n- Python", [_python()], as_of=AS_OF, transport=transport)
    assert analysis.model_mode == ModelMode.off
    assert "did not complete" in analysis.model_note
    assert any(item.text == "Python" and item.status.value == "met" for item in analysis.requirements)
    assert spent_today(ledger, today=date.today()) == 0


def test_a_model_draft_that_adds_kubernetes_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _arm(monkeypatch, tmp_path / "ledger.json", quota="10")
    profile = profile_for(document([("paragraph", "Ada")]), [_python()])
    analysis = read_job("Requirements:\n- Python", profile.atoms, as_of=AS_OF, transport=lambda *_args: _reply({"requirements": []}))
    # The empty proposal keeps the local checklist and does not arm a draft.
    assert analysis.model_mode == ModelMode.off
    local = build_resume(profile.atoms, analysis, job_text="Requirements:\n- Python")
    armed = analysis.model_copy(update={"model_mode": ModelMode.model, "model_note": "The model read this posting. Python checked every evidence id."})

    def transport(payload, key, timeout):
        assert payload["model"] == "grok-4.7"
        return _reply(
            {
                "sentences": [
                    {
                        "text": "Used Kubernetes to reduce latency by 20%.",
                        "evidence_ids": ["ev_" + "1" * 16],
                        "kind": "bullet",
                    }
                ]
            }
        )

    assert apply_model_draft(profile, armed, local, "Requirements:\n- Python", transport=transport) is None
    assert all("Kubernetes" not in sentence.text for sentence in local.sentences)


def test_cover_letter_is_refused_when_the_posting_does_not_ask(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    profile_path = tmp_path / "profile.json"
    save_profile(profile_path, profile_for(document([("paragraph", "Ada")]), [_python()]))
    out = tmp_path / "out.docx"
    code = main(
        [
            "resume",
            "--text",
            "Requirements:\n- Python",
            "--profile",
            str(profile_path),
            "--as-of",
            "2026-10-19",
            "--out",
            str(out),
            "--cover-letter",
            str(tmp_path / "letter.docx"),
            "--confirm",
            "resume,cover",
        ]
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert out.is_file()
    assert not (tmp_path / "letter.docx").exists()
    assert "The posting does not ask for a cover letter." in captured
    assert "Model is off. No call was made." in captured
