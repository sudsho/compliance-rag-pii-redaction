"""retrieval with acl filtering + hybrid bm25 + dense.

Flow:
    1. Build a Chroma `where` clause from caller identity (roles, org,
       consented patient ids).
    2. Run dense retrieval against Chroma (already filtered by ACL).
    3. Run BM25 over the same filtered subset in memory.
    4. Fuse with reciprocal rank fusion.

Fail-closed: if the caller identity is missing roles OR org, we return
zero hits and log a compliance event.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from rank_bm25 import BM25Okapi

from src.store import ACL_DELIM, Store, _unpack_list

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallerIdentity:
    """Extracted from JWT claims."""
    user_id: str
    roles: tuple[str, ...]
    org_id: str
    consented_patient_ids: tuple[str, ...] = ()


@dataclass
class Hit:
    chunk_id: str
    text: str
    source_id: str
    page: int
    chunk_index: int
    score: float
    dense_rank: int | None = None
    bm25_rank: int | None = None
    metadata: dict | None = None


def _acl_where(identity: CallerIdentity) -> dict:
    """Chroma metadata predicate.

    We store packed lists with a `|` delimiter, so we use `$contains`
    (substring match) which is what Chroma exposes for strings.
    """
    if not identity.roles or not identity.org_id:
        # canary predicate that matches nothing; fail-closed
        return {"source_id": {"$eq": "__deny_all__"}}

    role_clauses = [
        {"allowed_roles": {"$contains": f"{ACL_DELIM}{r}{ACL_DELIM}"}}
        for r in identity.roles
    ]
    org_clause = {"allowed_org_ids": {"$contains": f"{ACL_DELIM}{identity.org_id}{ACL_DELIM}"}}
    return {"$and": [{"$or": role_clauses}, org_clause]}


def _tokenize(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _rrf(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)


class Retriever:
    def __init__(
        self,
        store: Store,
        k_dense: int = 8,
        k_bm25: int = 8,
        final_k: int = 6,
        rrf_k: int = 60,
    ) -> None:
        self.store = store
        self.k_dense = k_dense
        self.k_bm25 = k_bm25
        self.final_k = final_k
        self.rrf_k = rrf_k

    def _patient_filter(
        self, identity: CallerIdentity, metadatas: Sequence[dict]
    ) -> list[bool]:
        """Second-pass filter: enforce patient-level consent.

        If a doc has allowed_patient_ids that is non-empty, the caller
        must have consent for at least one of those patient ids.
        Policy-level docs (empty allowed_patient_ids) are always OK.
        """
        keep: list[bool] = []
        for md in metadatas:
            patients = _unpack_list(md.get("allowed_patient_ids", ""))
            if not patients:
                keep.append(True)
                continue
            keep.append(any(p in identity.consented_patient_ids for p in patients))
        return keep

    def retrieve(self, query: str, identity: CallerIdentity) -> list[Hit]:
        where = _acl_where(identity)
        raw = self.store.query(text=query, where=where, k=max(self.k_dense, self.k_bm25) * 2)
        ids = raw.get("ids", [[]])[0]
        docs = raw.get("documents", [[]])[0]
        mds = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]

        keep_mask = self._patient_filter(identity, mds)

        pool = [
            {
                "id": i, "text": d, "md": m, "dist": dist
            }
            for i, d, m, dist, k in zip(ids, docs, mds, dists, keep_mask) if k
        ]
        if not pool:
            log.info("retrieve.empty user=%s org=%s", identity.user_id, identity.org_id)
            return []

        # dense ranking (lower distance = better)
        dense_sorted = sorted(pool, key=lambda x: x["dist"])[: self.k_dense]

        # bm25 within filtered pool
        corpus = [_tokenize(x["text"]) for x in pool]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(_tokenize(query))
        bm25_order = sorted(range(len(pool)), key=lambda i: -scores[i])[: self.k_bm25]
        bm25_sorted = [pool[i] for i in bm25_order]

        # fuse
        rank_by_id: dict[str, dict] = {}
        for rank, item in enumerate(dense_sorted):
            slot = rank_by_id.setdefault(
                item["id"], {"item": item, "dense": None, "bm25": None, "rrf": 0.0}
            )
            slot["dense"] = rank
            slot["rrf"] += _rrf(rank, self.rrf_k)
        for rank, item in enumerate(bm25_sorted):
            slot = rank_by_id.setdefault(
                item["id"], {"item": item, "dense": None, "bm25": None, "rrf": 0.0}
            )
            slot["bm25"] = rank
            slot["rrf"] += _rrf(rank, self.rrf_k)

        fused = sorted(rank_by_id.values(), key=lambda s: -s["rrf"])[: self.final_k]

        out: list[Hit] = []
        for s in fused:
            it = s["item"]
            md = it["md"] or {}
            out.append(
                Hit(
                    chunk_id=it["id"],
                    text=it["text"],
                    source_id=md.get("source_id", "?"),
                    page=int(md.get("page", 0)),
                    chunk_index=int(md.get("chunk_index", 0)),
                    score=s["rrf"],
                    dense_rank=s["dense"],
                    bm25_rank=s["bm25"],
                    metadata=md,
                )
            )
        return out
