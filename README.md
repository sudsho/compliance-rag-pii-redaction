# compliance-rag-pii-redaction

Compliance-aware retrieval augmented generation for regulated data.

The idea: a health payer wants to build a clinical guideline chatbot for
member-services agents. Rules of the road:

- No PHI ever hits the LLM in the clear (18 HIPAA safe harbor identifiers
  are stripped or pseudonymized before any prompt is built).
- Every retrieval is filtered by the caller's role, org, and consented
  patient IDs.
- Every response is source-cited.
- Every request produces a tamper-evident audit record.

Backing bits: Presidio for PII/PHI recognition, AWS Bedrock (Claude 3.5
Sonnet) for generation with Bedrock Guardrails, ChromaDB with ACL
metadata, hash-chained Postgres audit log, OpenTelemetry spans following
the GenAI semantic conventions.

## Status

Work in progress. Not for production.

## Layout (target)

```
src/
  ingest.py              PDF/DOCX/MD ingestion with page provenance
  presidio_redact.py     PII/PHI redaction, 18 HIPAA identifiers
  embed.py               Bedrock Titan embeddings with local fallback
  store.py               ChromaDB with per-doc ACL metadata
  retrieve.py            Retrieval with ACL filter, hybrid BM25 + dense
  generate.py            Bedrock Claude 3.5 with cited answers
  guardrails.py          Bedrock Guardrails wrapper
  audit.py               Hash-chained audit log
  otel.py                OpenTelemetry GenAI spans
  api/main.py            FastAPI /ask /ingest /audit /health
configs/                 default.yaml, hipaa_recognizers.yaml
tests/                   redact, ACL, audit, guardrails, api, retrieve
data/sample_policies/    Synthetic (only) clinical policies for demo
```

## Setup (rough)

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.api.main:app --reload
```

## Notes

Only synthetic policy text is committed. Any names, MRNs, phone numbers,
addresses in the sample data are fake and are there to exercise the
redactor.
