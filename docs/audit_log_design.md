# Audit log design

The audit log is the single most load-bearing thing in this repo from a
compliance standpoint. Everything else can be reproduced; the audit
record is the record of what happened.

## Goals

1. Tamper-evident: if a row was edited after write, the chain no longer
   verifies. The chain is unkeyed SHA-256, so it is evidence of an
   edit, not a cryptographic signature; it is not non-repudiable.
2. Non-leaking: the raw question, raw retrieved chunk text, and raw
   model output never land in the audit table.
3. Queryable: an auditor should be able to reconstruct "what did user X
   see for question Y at time T" without paging through a haystack of
   log files.

## Row schema

See `migrations/versions/0001_audit_log.py` for the source of truth. Key
columns:

- `request_id` UUID4, unique.
- `ts` UTC, indexed. We serialize as ISO 8601.
- `user_id`, `roles`, `org_id`: caller identity from JWT claims.
- `question_hash`: sha256 of the redacted question. The plaintext
  question is never written.
- `retrieved_chunk_ids`: JSON list of chunk ids that were passed to the
  generator. We can regenerate what the model saw by dereferencing
  these against the vector store (as long as the store snapshot is
  archived; see "snapshot rules" below).
- `redaction_stats`: JSON `{n, by_type}` summarizing what Presidio
  removed. Useful for spotting shifts in PHI density over time.
- `guardrail_prompt`, `guardrail_response`: ALLOW / DENY / DISABLED.
- `guardrail_reason`: short comma-joined list of `topic:X`,
  `content:Y:Z`, `pii:W` tokens.
- `llm_model`, `input_tokens`, `output_tokens`: cost / capacity trail.
- `citations`: list of `{source_id, page, chunk_index}` the response
  actually cited.
- `prev_hash`, `row_hash`: hash chain.

## Hash chain

For row N:

    row_hash_N = sha256(canonical_body(row_N) || prev_hash_N)
    prev_hash_N = row_hash_{N-1}
    prev_hash_1 = 0 * 64  (GENESIS_HASH)

`canonical_body` is the JSON-serialized row body with keys sorted and
the `row_hash` field itself removed, so hashing is deterministic.

Any mutation to any past row invalidates two things:

1. The row's own `row_hash` (since the body changed, the hash of the
   body will not match the stored value).
2. Every subsequent row's `prev_hash` (since one link back is broken,
   nothing beyond it can be trusted).

`AuditLog.verify_chain()` walks the table in order and reports the
1-based offset of the first row where either invariant fails.

## Snapshot rules

The audit log by itself only stores hashes and ids; to reconstruct what
the model saw, we also need the vector store at the corresponding point
in time. Two supported patterns:

- **Copy-on-write ingest**: every `POST /ingest` writes a new versioned
  collection (`policies_20240612_1830`) and updates a pointer. Retention
  policy: keep at least 7 years of collections for PHI-scoped orgs.
- **Point-in-time export**: nightly export of the current Chroma
  collection to S3 (versioned bucket, immutable retention lock). Audit
  rows are timestamped and can be rounded to the nearest snapshot.

Both are documented in `docs/on_prem_deploy.md` for on-prem variants.

## Auditor workflow

1. Auditor logs into `/audit` with an `auditor` role JWT.
2. Response includes chain verification result. If broken, everything
   from `chain_broken_at` onward is suspect. Halt investigation and
   escalate to Security.
3. Auditor filters by `user_id`, `org_id`, or `ts` range.
4. For any row of interest, auditor pulls the referenced chunk ids from
   the appropriate snapshot to see the source material.
5. Question plaintext is not recoverable by design; only the redacted
   form + entity summary. This is intentional and matches HIPAA
   "minimum necessary" for internal investigation.

## What we deliberately do NOT do

- **No plaintext prompt in the audit row.** Even the redacted form is
  omitted; only its hash. Rationale: any store that can be read can be
  exfiltrated, and even redacted questions can be recovered word-by-word
  from a determined attacker with the right auxiliary data.
- **No response body in the audit row.** Same reason. Chain still binds
  the response through `input_tokens`, `output_tokens`, and `citations`.
- **No signing key rotation for the chain.** The chain uses no
  external secret; each `row_hash` is a plain sha256. Rotation would
  buy nothing (there's no key to rotate) and adds complexity.

## Failure modes handled

- Partial write / crash: SQLAlchemy commit is atomic per row; a crash
  either leaves N rows or N+1 rows, never a half row.
- Clock skew: we accept the timestamp the server (not the client) sees,
  in UTC. Chain integrity does not depend on wall-clock ordering
  matching insertion ordering; the `id` sequence is the source of truth.

## Not handled

- Concurrent writers. The write path opens a plain SQLAlchemy session
  with no isolation level, row lock, or advisory lock. Two writers
  under the default Postgres isolation can read the same `prev_hash`
  and both commit, forking the chain. Adding an advisory lock around
  the prev-hash read + insert (or moving to `SERIALIZABLE`) is required
  before running with more than one API replica.
