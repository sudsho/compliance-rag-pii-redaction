"""end-to-end offline smoke test for the compliance rag pipeline.

Runs the whole thing on a CPU box with NO AWS credentials, NO API keys and
NO large downloads:

  1. ingest the bundled synthetic policy docs into a fresh ChromaDB store
  2. ask a policy question as an authorized user -> cited answer
  3. redact a PII-laden query -> show before / after
  4. ACL check -> an unauthorized role is denied a restricted document
  5. append audit rows, verify the hash chain, tamper with a row, prove the
     chain now fails and points at the mutated row

Embeddings use the deterministic hashing backend and generation uses the
local extractive generator, so the output is reproducible. Set
`EMBEDDINGS_BACKEND=local` (sentence-transformers) or provide AWS creds to
exercise the stronger / cloud paths.

Run:  python -m examples.smoke
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# offline-by-default configuration (set before importing src.*)
os.environ.setdefault("EMBEDDINGS_BACKEND", "hash")
os.environ.setdefault("GENERATOR_BACKEND", "local")
os.environ.setdefault("PSEUDONYM_SALT", "00" * 32)
os.environ.setdefault("PSEUDONYM_SALT_ROTATION_DAYS", "30")

from scripts.reindex_sample_policies import ACL_BY_SOURCE  # noqa: E402
from src.audit import AuditLog, hash_question  # noqa: E402
from src.generate import make_generator  # noqa: E402
from src.ingest import ingest_file  # noqa: E402
from src.presidio_redact import Redactor  # noqa: E402
from src.pseudonym import load_config_from_env  # noqa: E402
from src.retrieve import CallerIdentity, Retriever  # noqa: E402
from src.store import DocACL, Store  # noqa: E402


POLICY_DIR = Path("data/sample_policies")
RULE = "=" * 68


def _hr(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def ingest_policies(store: Store) -> int:
    _hr("1. INGEST synthetic policy documents")
    total = 0
    for path in sorted(POLICY_DIR.glob("*.md")):
        if path.stem == "README":
            continue
        acl = ACL_BY_SOURCE.get(
            path.stem,
            DocACL(
                allowed_roles=["nurse", "case_manager", "admin"],
                allowed_org_ids=["acme-payer"],
                allowed_patient_ids=[],
            ),
        )
        chunks = ingest_file(path, source_id=path.stem)
        store.add(chunks, acl)
        total += len(chunks)
        print(f"  ingested {path.name:<42} {len(chunks):>2} chunks  "
              f"[roles={','.join(acl.allowed_roles)} sensitivity={acl.sensitivity}]")
    print(f"  ---\n  total chunks in store: {store.count()}")
    return total


def ask(retriever: Retriever, generator, identity: CallerIdentity, question: str) -> None:
    _hr("2. ASK a policy question (authorized nurse) -> cited answer")
    print(f"  user   : {identity.user_id} roles={list(identity.roles)} org={identity.org_id}")
    print(f"  question: {question}\n")
    hits = retriever.retrieve(question, identity)
    gen = generator.generate(question, hits)
    print("  answer:")
    for line in gen.text.splitlines():
        print(f"    {line}")
    print("\n  citations:")
    for c in gen.citations:
        print(f"    - {c['source_id']} p{c['page']} #chunk{c['chunk_index']}")
    print(f"\n  retrieved {len(hits)} chunks from: "
          f"{sorted({h.source_id for h in hits})}")


def redact_demo(redactor: Redactor) -> None:
    _hr("3. REDACT a PII-laden query -> before / after")
    query = (
        "Patient John Smith (MRN: 000123456, SSN 412-34-5678, "
        "phone 617-555-0134, email john.smith@example.org) is asking whether "
        "his lumbar MRI needs prior authorization."
    )
    result = redactor.redact(query)
    print("  BEFORE:")
    print(f"    {query}")
    print("\n  AFTER (what the model actually sees):")
    print(f"    {result.text}")
    print(f"\n  entities detected: {result.stats['n']}  by_type={result.stats['by_type']}")


def acl_demo(retriever: Retriever) -> None:
    _hr("4. ACL enforcement -> unauthorized role is denied a restricted doc")
    restricted = "care_management_hf_stage3"
    question = "heart failure stage 3 care management enrollment criteria"

    nurse = CallerIdentity(user_id="nurse-1", roles=("nurse",), org_id="acme-payer")
    member_services = CallerIdentity(
        user_id="ms-7", roles=("member_services",), org_id="acme-payer"
    )

    nurse_hits = retriever.retrieve(question, nurse)
    ms_hits = retriever.retrieve(question, member_services)
    nurse_sources = sorted({h.source_id for h in nurse_hits})
    ms_sources = sorted({h.source_id for h in ms_hits})

    print(f"  restricted doc     : {restricted}  (allowed_roles=nurse,case_manager,admin)")
    print(f"  query              : {question}\n")
    print(f"  nurse           sees: {nurse_sources}")
    print(f"  member_services sees: {ms_sources}\n")
    assert restricted in nurse_sources, "authorized nurse should see the restricted doc"
    assert restricted not in ms_sources, "member_services must NOT see the restricted doc"
    print(f"  OK: '{restricted}' visible to nurse, DENIED to member_services")


def audit_demo(db_url: str) -> None:
    _hr("5. AUDIT hash-chain -> append, verify, tamper, detect")
    audit = AuditLog(db_url=db_url)
    for q in ["one", "two", "three"]:
        audit.write(
            user_id="nurse-1",
            roles=["nurse"],
            org_id="acme-payer",
            question_hash=hash_question(q),
            retrieved_chunk_ids=["c1", "c2"],
            redaction_stats={"n": 0, "by_type": {}},
            guardrail_prompt="ALLOW",
            guardrail_response="ALLOW",
            llm_model="local-extractive-v1",
            input_tokens=10,
            output_tokens=20,
            citations=[{"source_id": "pa-mri", "page": 1, "chunk_index": 0}],
        )
    ok, at = audit.verify_chain()
    print(f"  appended 3 rows. verify_chain() -> ok={ok} broken_at={at}")
    assert ok and at == 0

    with audit.engine.connect() as conn:
        conn.exec_driver_sql("UPDATE audit_log SET roles='admin' WHERE id = 2")
        conn.commit()
    print("  tampered: UPDATE audit_log SET roles='admin' WHERE id = 2")

    ok2, at2 = audit.verify_chain()
    print(f"  verify_chain() -> ok={ok2} broken_at={at2}")
    assert ok2 is False and at2 == 2
    print("  OK: tamper detected at row 2")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="compliance_rag_smoke_"))
    chroma_dir = str(workdir / "chroma")
    audit_db = f"sqlite+pysqlite:///{workdir / 'audit.db'}"

    print(f"offline smoke test  (workdir: {workdir})")
    print(f"embeddings backend : {os.environ['EMBEDDINGS_BACKEND']}")
    print(f"generator backend  : {os.environ['GENERATOR_BACKEND']}")

    try:
        store = Store(collection="smoke", persist_dir=chroma_dir)
        retriever = Retriever(store)
        generator = make_generator()
        redactor = Redactor(
            recognizer_config=Path("configs/hipaa_recognizers.yaml"),
            pseudonym_config=load_config_from_env(),
        )

        ingest_policies(store)
        nurse = CallerIdentity(user_id="nurse-1", roles=("nurse",), org_id="acme-payer")
        ask(retriever, generator, nurse,
            "When is prior authorization required for a lumbar MRI?")
        redact_demo(redactor)
        acl_demo(retriever)
        audit_demo(audit_db)

        _hr("SMOKE TEST PASSED")
        print("  ingest -> ask -> redact -> acl deny -> audit chain all green.\n")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
