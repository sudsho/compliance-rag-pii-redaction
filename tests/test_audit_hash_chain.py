"""verify the audit chain: append works, tamper is caught."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.audit import AuditLog, AuditRow, compute_row_hash, hash_question


@pytest.fixture()
def log(tmp_path):
    db = f"sqlite+pysqlite:///{tmp_path/'audit.db'}"
    return AuditLog(db_url=db)


def _write(log: AuditLog, q: str = "hi", user: str = "u1", org: str = "o1"):
    return log.write(
        user_id=user,
        roles=["nurse"],
        org_id=org,
        question_hash=hash_question(q),
        retrieved_chunk_ids=["c1", "c2"],
        redaction_stats={"n": 0, "by_type": {}},
        guardrail_prompt="ALLOW",
        guardrail_response="ALLOW",
        llm_model="anthropic.claude-3-5-sonnet-20240620-v1:0",
        input_tokens=42,
        output_tokens=17,
        citations=[{"source_id": "cp-dme", "page": 1, "chunk_index": 0}],
    )


def test_chain_appends_cleanly(log):
    a = _write(log, "one")
    b = _write(log, "two")
    c = _write(log, "three")
    assert b.prev_hash == a.row_hash
    assert c.prev_hash == b.row_hash
    ok, at = log.verify_chain()
    assert ok is True and at == 0


def test_chain_detects_row_mutation(log):
    _write(log, "one")
    _write(log, "two")
    with log.engine.connect() as conn:
        # mutate roles field of row 1 -> row_hash no longer matches body
        conn.exec_driver_sql(
            "UPDATE audit_log SET roles='admin' WHERE id = 1"
        )
        conn.commit()
    ok, at = log.verify_chain()
    assert ok is False
    assert at == 1


def test_chain_detects_prev_hash_break(log):
    _write(log, "one")
    _write(log, "two")
    _write(log, "three")
    with log.engine.connect() as conn:
        conn.exec_driver_sql(
            "UPDATE audit_log SET prev_hash = '" + "f" * 64 + "' WHERE id = 3"
        )
        conn.commit()
    ok, at = log.verify_chain()
    assert ok is False
    assert at == 3


def test_question_is_never_stored_plaintext(log):
    q = "very sensitive question about member X"
    r = _write(log, q)
    assert r.question_hash == hash_question(q)
    assert q not in r.question_hash
