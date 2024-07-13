"""acl filter tests for retrieve.

We stub the Store to avoid a real chromadb dependency in unit tests.
Instead we test the where-clause builder + patient consent filter
directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.retrieve import CallerIdentity, Retriever, _acl_where
from src.store import ACL_DELIM


def test_where_denies_all_when_no_roles():
    ident = CallerIdentity(user_id="u1", roles=(), org_id="o1")
    w = _acl_where(ident)
    assert w == {"source_id": {"$eq": "__deny_all__"}}


def test_where_denies_all_when_no_org():
    ident = CallerIdentity(user_id="u1", roles=("nurse",), org_id="")
    w = _acl_where(ident)
    assert w == {"source_id": {"$eq": "__deny_all__"}}


def test_where_or_of_roles_and_and_org():
    ident = CallerIdentity(user_id="u1", roles=("nurse", "case_manager"), org_id="acme")
    w = _acl_where(ident)
    assert "$and" in w
    inner = w["$and"]
    role_clauses = inner[0]["$or"]
    assert {"allowed_roles": {"$contains": f"{ACL_DELIM}nurse{ACL_DELIM}"}} in role_clauses
    assert {"allowed_roles": {"$contains": f"{ACL_DELIM}case_manager{ACL_DELIM}"}} in role_clauses
    assert inner[1] == {"allowed_org_ids": {"$contains": f"{ACL_DELIM}acme{ACL_DELIM}"}}


def _mock_store(ids, docs, mds, dists):
    store = MagicMock()
    store.query.return_value = {
        "ids": [ids],
        "documents": [docs],
        "metadatas": [mds],
        "distances": [dists],
    }
    return store


def test_patient_scoped_doc_hidden_when_no_consent():
    ident = CallerIdentity(
        user_id="u1", roles=("nurse",), org_id="acme", consented_patient_ids=()
    )
    mds = [
        {   # policy-level doc, no patient scope -> visible
            "source_id": "cp-dme",
            "page": 1,
            "chunk_index": 0,
            "allowed_patient_ids": ACL_DELIM,
        },
        {   # patient-scoped doc; caller lacks consent -> hidden
            "source_id": "notes-p42",
            "page": 1,
            "chunk_index": 0,
            "allowed_patient_ids": f"{ACL_DELIM}p42{ACL_DELIM}",
        },
    ]
    store = _mock_store(["a", "b"], ["policy text one two", "note about patient p42"], mds, [0.1, 0.2])
    r = Retriever(store, k_dense=2, k_bm25=2, final_k=2)
    hits = r.retrieve("policy question", ident)
    assert {h.chunk_id for h in hits} == {"a"}


def test_patient_scoped_doc_visible_with_consent():
    ident = CallerIdentity(
        user_id="u1",
        roles=("nurse",),
        org_id="acme",
        consented_patient_ids=("p42",),
    )
    mds = [
        {"source_id": "notes-p42", "page": 1, "chunk_index": 0,
         "allowed_patient_ids": f"{ACL_DELIM}p42{ACL_DELIM}"},
    ]
    store = _mock_store(["b"], ["note about patient p42"], mds, [0.1])
    r = Retriever(store, k_dense=1, k_bm25=1, final_k=1)
    hits = r.retrieve("note", ident)
    assert len(hits) == 1
    assert hits[0].source_id == "notes-p42"


def test_missing_role_returns_empty_hits(caplog):
    ident = CallerIdentity(user_id="u1", roles=(), org_id="acme")
    store = _mock_store([], [], [], [])
    r = Retriever(store)
    hits = r.retrieve("anything", ident)
    assert hits == []
