"""ingest data/sample_policies into the vector store with sensible acls.

Wipes the collection first. Never run this on prod.
"""

from __future__ import annotations

from pathlib import Path

from src.ingest import ingest_file
from src.store import DocACL, Store


ACL_BY_SOURCE = {
    "coverage_policy_diabetes_supplies": DocACL(
        allowed_roles=["nurse", "case_manager", "admin", "member_services"],
        allowed_org_ids=["acme-payer"],
        allowed_patient_ids=[],
        sensitivity="internal",
    ),
    "prior_auth_mri_lumbar": DocACL(
        allowed_roles=["nurse", "case_manager", "admin", "member_services", "reviewer"],
        allowed_org_ids=["acme-payer"],
        allowed_patient_ids=[],
        sensitivity="internal",
    ),
    "formulary_glp1_step_therapy": DocACL(
        allowed_roles=["nurse", "case_manager", "admin", "member_services", "pharmacist"],
        allowed_org_ids=["acme-payer"],
        allowed_patient_ids=[],
        sensitivity="internal",
    ),
    "care_management_hf_stage3": DocACL(
        allowed_roles=["nurse", "case_manager", "admin"],
        allowed_org_ids=["acme-payer"],
        allowed_patient_ids=[],
        sensitivity="restricted",
    ),
}


def main() -> None:
    store = Store()
    store.reset()
    root = Path("data/sample_policies")
    total = 0
    for path in sorted(root.glob("*.md")):
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
        print(f"ingested {path.name} ({len(chunks)} chunks)")
    print(f"done. total chunks: {total}")


if __name__ == "__main__":
    main()
