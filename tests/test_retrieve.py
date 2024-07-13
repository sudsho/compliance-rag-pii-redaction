"""retrieval: rrf fusion of dense + bm25 rerank, acl visibility.

Uses a Store mock so these run without a live ChromaDB.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.retrieve import CallerIdentity, Retriever
from src.store import ACL_DELIM


def _store(ids, docs, dists):
    s = MagicMock()
    s.query.return_value = {
        "ids": [ids],
        "documents": [docs],
        "metadatas": [
            [{"source_id": f"s{i}", "page": 1, "chunk_index": i, "allowed_patient_ids": ACL_DELIM}
             for i in range(len(ids))]
        ],
        "distances": [dists],
    }
    return s


IDENT = CallerIdentity(user_id="u1", roles=("nurse",), org_id="acme")


def test_rrf_promotes_items_in_both_lists():
    ids = ["a", "b", "c", "d"]
    docs = [
        "prior authorization for lumbar mri and other imaging",  # a, top of both
        "diabetes cgm coverage rules",
        "member wellness benefits",
        "prior auth mri notes for weekend on-call",              # d, mri-heavy
    ]
    dists = [0.05, 0.20, 0.30, 0.10]                             # dense: a, d, b, c
    r = Retriever(_store(ids, docs, dists), k_dense=4, k_bm25=4, final_k=4)
    hits = r.retrieve("prior authorization mri", IDENT)
    top_ids = [h.chunk_id for h in hits]
    assert top_ids[0] == "a"                                     # a wins both
    assert "d" in top_ids[:3]                                    # d wins bm25


def test_final_k_caps_results():
    r = Retriever(_store(["a", "b"], ["x", "y"], [0.1, 0.2]), final_k=1)
    hits = r.retrieve("q", IDENT)
    assert len(hits) == 1


def test_scores_are_monotone_nonincreasing():
    ids = ["a", "b", "c", "d"]
    r = Retriever(
        _store(ids, ["policy a", "policy b", "policy c", "policy d"], [0.1, 0.2, 0.3, 0.4]),
        final_k=4,
    )
    hits = r.retrieve("policy", IDENT)
    for i in range(1, len(hits)):
        assert hits[i - 1].score >= hits[i].score
