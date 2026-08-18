"""fastapi surface: /ask, /ingest, /audit, /health.

Auth: bearer JWT. Roles + org_id + consented patient ids read from claims.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import __version__
from src.api.schemas import (
    AskRequest,
    AskResponse,
    AuditQueryResponse,
    AuditRowOut,
    Citation,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    RetrievedChunk,
)
from src.audit import AuditLog, hash_question
from src.generate import BedrockGenerator, make_generator
from src.guardrails import BedrockGuardrails, GuardrailVerdict
from src.identity import AuthError, identity_from_token
from src.ingest import ingest_file
from src.otel import init_tracing, llm_span, rag_span, record_llm_result
from src.presidio_redact import Redactor
from src.pseudonym import load_config_from_env
from src.retrieve import Retriever
from src.store import DocACL, Store


log = logging.getLogger("compliance-rag")
security = HTTPBearer(auto_error=True)


app = FastAPI(title="compliance-rag", version=__version__)


class _Deps:
    def __init__(self) -> None:
        self.redactor: Redactor | None = None
        self.store: Store | None = None
        self.retriever: Retriever | None = None
        self.generator: BedrockGenerator | object | None = None
        self.guardrails: BedrockGuardrails | None = None
        self.audit: AuditLog | None = None


_deps = _Deps()


@app.on_event("startup")
def startup() -> None:
    init_tracing()
    try:
        pseudo = load_config_from_env()
    except RuntimeError:
        pseudo = None
        log.warning("running without pseudonym config (dev only)")
    _deps.redactor = Redactor(
        recognizer_config=Path("configs/hipaa_recognizers.yaml"),
        pseudonym_config=pseudo,
    )
    _deps.store = Store()
    _deps.retriever = Retriever(_deps.store)
    _deps.generator = make_generator()
    _deps.guardrails = BedrockGuardrails()
    _deps.audit = AuditLog()


def get_identity(creds: HTTPAuthorizationCredentials = Depends(security)):
    try:
        return identity_from_token(creds.credentials)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@app.get("/health", response_model=HealthResponse)
def health():
    count = _deps.store.count() if _deps.store else 0
    return HealthResponse(status="ok", version=__version__, store_count=count)


@app.post("/redact/preview")
def redact_preview(payload: dict, ident=Depends(get_identity)):
    """Return the redacted form of a text plus per-entity stats.

    UI convenience so the operator can eyeball what the LLM will see
    before hitting /ask.
    """
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    r = _deps.redactor.redact(text)
    return {
        "redacted_text": r.text,
        "entities": r.entities,
        "stats": r.stats,
    }


@app.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, ident=Depends(get_identity)):
    if "admin" not in ident.roles:
        raise HTTPException(status_code=403, detail="admin role required for ingest")
    n_files = 0
    n_chunks = 0
    for spec in req.files:
        chunks = ingest_file(Path(spec.path), source_id=spec.source_id)
        if not chunks:
            continue
        acl = DocACL(
            allowed_roles=spec.allowed_roles or ["nurse", "case_manager", "admin"],
            allowed_org_ids=spec.allowed_org_ids or [ident.org_id],
            allowed_patient_ids=spec.allowed_patient_ids,
            sensitivity=spec.sensitivity,
        )
        _deps.store.add(chunks, acl)
        n_files += 1
        n_chunks += len(chunks)
    return IngestResponse(ingested=n_files, chunks=n_chunks)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, ident=Depends(get_identity)):
    request_id = str(uuid.uuid4())

    # 1. redact the incoming question
    with rag_span("rag.redact", **{"rag.acl.user_id": ident.user_id}):
        redacted = _deps.redactor.redact(req.question)

    # 2. guardrail the (redacted) prompt
    with rag_span("rag.guardrail.prompt"):
        gp = _deps.guardrails.check_prompt(redacted.text)
    if gp.verdict is GuardrailVerdict.DENY:
        _write_audit(request_id, ident, redacted, [], gp.action, "N/A", gp.reason, "", 0, 0, [])
        return _refuse(request_id, _deps.guardrails.render_refusal(gp), gp.action, "N/A", redacted)

    # 3. retrieve
    with rag_span("rag.retrieve", **{"rag.retrieve.k": req.top_k}):
        _deps.retriever.final_k = req.top_k
        hits = _deps.retriever.retrieve(redacted.text, ident)

    if not hits:
        answer = "I don't have that policy on file."
        _write_audit(
            request_id, ident, redacted, [], gp.action, "ALLOW", gp.reason, "", 0, 0, []
        )
        return AskResponse(
            request_id=request_id,
            answer=answer,
            citations=[],
            retrieved=[],
            guardrail_prompt=gp.verdict.value,
            guardrail_response="ALLOW",
            redaction_n=redacted.stats["n"],
        )

    # 4. generate
    with llm_span(
        _deps.generator.model_id,
        _deps.generator.max_tokens,
        _deps.generator.temperature,
    ) as span:
        gen = _deps.generator.generate(redacted.text, hits)
        record_llm_result(span, gen.input_tokens, gen.output_tokens, gen.stop_reason)

    # 5. guardrail the response
    with rag_span("rag.guardrail.response"):
        gr = _deps.guardrails.check_response(gen.text)
    if gr.verdict is GuardrailVerdict.DENY:
        _write_audit(
            request_id, ident, redacted, hits, gp.action, gr.action, gr.reason,
            _deps.generator.model_id, gen.input_tokens, gen.output_tokens, [],
        )
        return _refuse(request_id, _deps.guardrails.render_refusal(gr), gp.action, gr.action, redacted)

    _write_audit(
        request_id, ident, redacted, hits, gp.action, gr.action, gr.reason,
        _deps.generator.model_id, gen.input_tokens, gen.output_tokens, gen.citations,
    )

    return AskResponse(
        request_id=request_id,
        answer=gen.text,
        citations=[Citation(**c) for c in gen.citations],
        retrieved=[
            RetrievedChunk(
                chunk_id=h.chunk_id, source_id=h.source_id,
                page=h.page, chunk_index=h.chunk_index, score=h.score,
            )
            for h in hits
        ],
        guardrail_prompt=gp.verdict.value,
        guardrail_response=gr.verdict.value,
        redaction_n=redacted.stats["n"],
        input_tokens=gen.input_tokens,
        output_tokens=gen.output_tokens,
    )


@app.get("/audit", response_model=AuditQueryResponse)
def audit(ident=Depends(get_identity)):
    if "auditor" not in ident.roles and "admin" not in ident.roles:
        raise HTTPException(status_code=403, detail="auditor or admin role required")
    from sqlalchemy import select
    from src.audit import AuditRow
    from sqlalchemy.orm import Session as _Session

    ok, at = _deps.audit.verify_chain()
    with _Session(_deps.audit.engine) as s:
        rows = list(s.execute(select(AuditRow).order_by(AuditRow.id.desc()).limit(200)).scalars())
    return AuditQueryResponse(
        rows=[
            AuditRowOut(
                id=r.id, request_id=r.request_id, ts=r.ts,
                user_id=r.user_id, org_id=r.org_id, question_hash=r.question_hash,
                retrieved_chunk_ids=json.loads(r.retrieved_chunk_ids) if r.retrieved_chunk_ids.startswith("[") else [],
                guardrail_prompt=r.guardrail_prompt, guardrail_response=r.guardrail_response,
                row_hash=r.row_hash, prev_hash=r.prev_hash,
            )
            for r in rows
        ],
        chain_ok=ok,
        chain_broken_at=at,
    )


def _refuse(request_id: str, msg: str, gp: str, gr: str, redacted) -> AskResponse:
    return AskResponse(
        request_id=request_id, answer=msg, citations=[], retrieved=[],
        guardrail_prompt="DENY" if gp == "GUARDRAIL_INTERVENED" else "ALLOW",
        guardrail_response="DENY" if gr == "GUARDRAIL_INTERVENED" else ("ALLOW" if gr != "N/A" else "ALLOW"),
        redaction_n=redacted.stats["n"],
    )


def _write_audit(
    request_id, ident, redacted, hits, gp_action, gr_action, gr_reason,
    model, in_tok, out_tok, citations,
) -> None:
    _deps.audit.write(
        request_id=request_id,
        user_id=ident.user_id, roles=list(ident.roles), org_id=ident.org_id,
        question_hash=hash_question(redacted.text),
        retrieved_chunk_ids=[h.chunk_id for h in hits],
        redaction_stats=redacted.stats,
        guardrail_prompt="DENY" if gp_action == "GUARDRAIL_INTERVENED" else "ALLOW",
        guardrail_response="DENY" if gr_action == "GUARDRAIL_INTERVENED" else "ALLOW",
        guardrail_reason=gr_reason,
        llm_model=model, input_tokens=in_tok, output_tokens=out_tok,
        citations=citations,
    )
