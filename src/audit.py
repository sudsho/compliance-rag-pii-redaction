"""tamper-evident audit log for compliance rag.

Each request writes ONE audit row containing:
  * request id (uuid4)
  * timestamp (utc)
  * user id, roles, org id
  * question hash (sha256 of redacted question, never plaintext)
  * retrieved chunk ids (list)
  * redaction stats (n entities by type)
  * guardrail prompt verdict, response verdict, reason
  * llm model id, input/output token counts
  * response citation ids
  * prev_hash (sha256 of the previous row's row_hash)
  * row_hash (sha256 over serialized row + prev_hash)

Chain invariant: for row N, row_hash_N = sha256(row_body_N + row_hash_{N-1}).
Tampering with any row invalidates every subsequent row_hash. Auditor can
verify the entire chain in O(n).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool


GENESIS_HASH = "0" * 64


class Base(DeclarativeBase):
    pass


class AuditRow(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(36), unique=True, nullable=False, index=True)
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    roles = Column(String(512), nullable=False, default="")
    org_id = Column(String(64), nullable=False, index=True)
    question_hash = Column(String(64), nullable=False)
    retrieved_chunk_ids = Column(Text, nullable=False, default="[]")
    redaction_stats = Column(Text, nullable=False, default="{}")
    guardrail_prompt = Column(String(16), nullable=False, default="ALLOW")
    guardrail_response = Column(String(16), nullable=False, default="ALLOW")
    guardrail_reason = Column(Text, nullable=False, default="")
    llm_model = Column(String(128), nullable=False, default="")
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    citations = Column(Text, nullable=False, default="[]")
    prev_hash = Column(String(64), nullable=False, default=GENESIS_HASH)
    row_hash = Column(String(64), nullable=False, index=True)


@dataclass
class AuditRecord:
    request_id: str
    ts: str
    user_id: str
    roles: list[str]
    org_id: str
    question_hash: str
    retrieved_chunk_ids: list[str]
    redaction_stats: dict
    guardrail_prompt: str
    guardrail_response: str
    guardrail_reason: str
    llm_model: str
    input_tokens: int
    output_tokens: int
    citations: list[dict]
    prev_hash: str = GENESIS_HASH
    row_hash: str = ""


def hash_question(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_body(rec: AuditRecord) -> str:
    d = asdict(rec)
    d.pop("row_hash", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def compute_row_hash(rec: AuditRecord) -> str:
    return hashlib.sha256(_canonical_body(rec).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db_url: str | None = None, echo: bool = False) -> None:
        url = db_url or os.environ.get(
            "AUDIT_DB_URL", "sqlite+pysqlite:///audit.db"
        )
        engine_kwargs: dict = {"echo": echo, "future": True}
        # In-memory SQLite hands out a fresh empty database per connection, so
        # the schema created below would vanish on the next checkout. Pin a
        # single shared connection so the chain survives across sessions.
        if url.startswith("sqlite") and ":memory:" in url:
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **engine_kwargs)
        Base.metadata.create_all(self.engine)

    def _prev_hash(self, session: Session) -> str:
        row = session.execute(
            select(AuditRow).order_by(AuditRow.id.desc()).limit(1)
        ).scalar_one_or_none()
        return row.row_hash if row else GENESIS_HASH

    def write(self, **fields: Any) -> AuditRecord:
        # Normalize ts to a naive UTC isoformat string so the value we
        # hash matches the value we get back from SQLAlchemy's DateTime
        # column on any backend (SQLite drops tzinfo on read).
        ts_arg = fields.get("ts")
        if ts_arg:
            ts_dt = datetime.fromisoformat(ts_arg)
        else:
            ts_dt = datetime.now(tz=timezone.utc)
        if ts_dt.tzinfo is not None:
            ts_dt = ts_dt.astimezone(timezone.utc).replace(tzinfo=None)
        ts_str = ts_dt.isoformat()

        rec = AuditRecord(
            request_id=fields.get("request_id") or str(uuid.uuid4()),
            ts=ts_str,
            user_id=fields["user_id"],
            roles=list(fields.get("roles") or []),
            org_id=fields["org_id"],
            question_hash=fields["question_hash"],
            retrieved_chunk_ids=list(fields.get("retrieved_chunk_ids") or []),
            redaction_stats=dict(fields.get("redaction_stats") or {}),
            guardrail_prompt=fields.get("guardrail_prompt", "ALLOW"),
            guardrail_response=fields.get("guardrail_response", "ALLOW"),
            guardrail_reason=fields.get("guardrail_reason", ""),
            llm_model=fields.get("llm_model", ""),
            input_tokens=int(fields.get("input_tokens", 0)),
            output_tokens=int(fields.get("output_tokens", 0)),
            citations=list(fields.get("citations") or []),
        )
        with Session(self.engine) as session:
            rec.prev_hash = self._prev_hash(session)
            rec.row_hash = compute_row_hash(rec)
            row = AuditRow(
                request_id=rec.request_id,
                ts=ts_dt,
                user_id=rec.user_id,
                roles=",".join(rec.roles),
                org_id=rec.org_id,
                question_hash=rec.question_hash,
                retrieved_chunk_ids=json.dumps(rec.retrieved_chunk_ids),
                redaction_stats=json.dumps(rec.redaction_stats),
                guardrail_prompt=rec.guardrail_prompt,
                guardrail_response=rec.guardrail_response,
                guardrail_reason=rec.guardrail_reason,
                llm_model=rec.llm_model,
                input_tokens=rec.input_tokens,
                output_tokens=rec.output_tokens,
                citations=json.dumps(rec.citations),
                prev_hash=rec.prev_hash,
                row_hash=rec.row_hash,
            )
            session.add(row)
            session.commit()
        return rec

    def verify_chain(self) -> tuple[bool, int]:
        """Returns (ok, offset_of_first_broken_row). offset is 1-based."""
        with Session(self.engine) as session:
            prev = GENESIS_HASH
            i = 0
            for row in session.execute(select(AuditRow).order_by(AuditRow.id.asc())).scalars():
                i += 1
                if row.prev_hash != prev:
                    return False, i
                rec = AuditRecord(
                    request_id=row.request_id,
                    ts=row.ts.isoformat(),
                    user_id=row.user_id,
                    roles=row.roles.split(",") if row.roles else [],
                    org_id=row.org_id,
                    question_hash=row.question_hash,
                    retrieved_chunk_ids=json.loads(row.retrieved_chunk_ids),
                    redaction_stats=json.loads(row.redaction_stats),
                    guardrail_prompt=row.guardrail_prompt,
                    guardrail_response=row.guardrail_response,
                    guardrail_reason=row.guardrail_reason,
                    llm_model=row.llm_model,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    citations=json.loads(row.citations),
                    prev_hash=row.prev_hash,
                )
                expected = compute_row_hash(rec)
                if expected != row.row_hash:
                    return False, i
                prev = row.row_hash
        return True, 0
