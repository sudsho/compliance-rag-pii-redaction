"""fastapi surface tests with bedrock / chromadb mocked out."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt

os.environ.setdefault("JWT_SECRET", "test-secret-32-bytes-abcdefghijkl")
os.environ.setdefault("PSEUDONYM_SALT", "00" * 32)
os.environ.setdefault("AUDIT_DB_URL", "sqlite+pysqlite:///:memory:")

from src.api import main as api_main   # noqa: E402
from src.generate import GenerationResult   # noqa: E402
from src.guardrails import Escalation, GuardrailResult, GuardrailVerdict  # noqa: E402
from src.retrieve import Hit             # noqa: E402


def _tok(roles=("nurse",), org="acme"):
    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": "u1",
        "roles": list(roles),
        "org_id": org,
        "consented_patient_ids": [],
        "iss": "compliance-rag",
        "aud": "member-services",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256")


@pytest.fixture()
def client(monkeypatch):
    # patch all external calls
    monkeypatch.setattr(api_main, "init_tracing", lambda: None)

    fake_store = MagicMock()
    fake_store.count.return_value = 42

    fake_retriever = MagicMock()
    fake_retriever.retrieve.return_value = [
        Hit(chunk_id="c1", text="Prior auth is required for lumbar MRI.",
            source_id="pa-mri", page=1, chunk_index=0, score=0.9),
    ]

    fake_gen = MagicMock()
    fake_gen.model_id = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    fake_gen.max_tokens = 512
    fake_gen.temperature = 0.1
    fake_gen.generate.return_value = GenerationResult(
        text="Prior authorization is required. [pa-mri:p1#0]",
        citations=[{"source_id": "pa-mri", "page": 1, "chunk_index": 0}],
        guardrail_action="NONE",
        guardrail_topics=[],
        stop_reason="end_turn",
        input_tokens=100, output_tokens=20,
    )

    fake_gr = MagicMock()
    fake_gr.check_prompt.return_value = GuardrailResult(
        verdict=GuardrailVerdict.ALLOW, action="NONE", reason="clean",
        escalation=Escalation.NONE, output_text=None, raw={},
    )
    fake_gr.check_response.return_value = GuardrailResult(
        verdict=GuardrailVerdict.ALLOW, action="NONE", reason="clean",
        escalation=Escalation.NONE, output_text=None, raw={},
    )
    fake_gr.render_refusal.return_value = "blocked"

    from src.audit import AuditLog
    from src.presidio_redact import Redactor
    from src.pseudonym import PseudonymConfig
    from datetime import date

    def _startup():
        api_main._deps.redactor = Redactor(
            recognizer_config=Path("configs/hipaa_recognizers.yaml"),
            pseudonym_config=PseudonymConfig(
                salt=bytes.fromhex("00" * 32), epoch=date(2024, 6, 1), rotation_days=30
            ),
        )
        api_main._deps.store = fake_store
        api_main._deps.retriever = fake_retriever
        api_main._deps.generator = fake_gen
        api_main._deps.guardrails = fake_gr
        api_main._deps.audit = AuditLog(db_url="sqlite+pysqlite:///:memory:")

    _startup()
    return TestClient(api_main.app)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["store_count"] == 42


def test_ask_requires_jwt(client):
    r = client.post("/ask", json={"question": "when is prior auth required for lumbar MRI"})
    assert r.status_code == 403 or r.status_code == 401


def test_ask_returns_answer_with_citation(client):
    r = client.post(
        "/ask",
        json={"question": "when is prior auth required for lumbar MRI"},
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "[pa-mri:p1#0]" in body["answer"]
    assert body["citations"][0]["source_id"] == "pa-mri"
    assert body["guardrail_prompt"] == "ALLOW"


def test_ingest_admin_only(client):
    r = client.post(
        "/ingest",
        json={"files": []},
        headers={"Authorization": f"Bearer {_tok(roles=('nurse',))}"},
    )
    assert r.status_code == 403


def test_audit_requires_auditor(client):
    r = client.get("/audit", headers={"Authorization": f"Bearer {_tok(roles=('nurse',))}"})
    assert r.status_code == 403
    r = client.get("/audit", headers={"Authorization": f"Bearer {_tok(roles=('auditor',))}"})
    assert r.status_code == 200
