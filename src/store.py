"""chromadb vector store with per-doc ACL metadata.

Every chunk carries:
  - allowed_roles     list[str] (e.g. ["nurse","case_manager"])
  - allowed_org_ids   list[str] (payer org / plan / market)
  - allowed_patient_ids list[str] (empty = policy-level, not patient-linked)
  - sensitivity       str  ("public"|"internal"|"restricted")

Chroma <=0.5 stores each metadata value as a scalar, so lists are joined
with a `|` delimiter and matched via `$contains`-style predicates in
retrieve.py. Ugly but portable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

import chromadb
from chromadb.config import Settings

from src.embed import Embedder, make_embedder
from src.ingest import Chunk


ACL_DELIM = "|"


def _pack_list(xs: Iterable[str]) -> str:
    return ACL_DELIM + ACL_DELIM.join(sorted(set(xs))) + ACL_DELIM if xs else ACL_DELIM


def _unpack_list(s: str) -> list[str]:
    if not s or s == ACL_DELIM:
        return []
    return [x for x in s.strip(ACL_DELIM).split(ACL_DELIM) if x]


@dataclass
class DocACL:
    allowed_roles: list[str]
    allowed_org_ids: list[str]
    allowed_patient_ids: list[str]
    sensitivity: str = "internal"

    def as_metadata(self) -> dict:
        return {
            "allowed_roles": _pack_list(self.allowed_roles),
            "allowed_org_ids": _pack_list(self.allowed_org_ids),
            "allowed_patient_ids": _pack_list(self.allowed_patient_ids),
            "sensitivity": self.sensitivity,
        }


class Store:
    def __init__(
        self,
        collection: str | None = None,
        host: str | None = None,
        port: int | None = None,
        embedder: Embedder | None = None,
        persist_dir: str = ".chroma",
    ) -> None:
        self.embedder = embedder or make_embedder()
        self.collection_name = collection or os.environ.get("CHROMA_COLLECTION", "policies")
        if host:
            self.client = chromadb.HttpClient(
                host=host,
                port=port or int(os.environ.get("CHROMA_PORT", "8000")),
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            self.client = chromadb.PersistentClient(
                path=persist_dir, settings=Settings(anonymized_telemetry=False)
            )
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, chunks: list[Chunk], acl: DocACL) -> None:
        if not chunks:
            return
        vectors = self.embedder.encode([c.text for c in chunks])
        acl_md = acl.as_metadata()
        metadatas = [
            {
                **acl_md,
                "source_id": c.source_id,
                "source_path": c.source_path,
                "page": c.page,
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ]
        self.collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=metadatas,
        )

    def query(
        self,
        text: str | None = None,
        embedding: list[float] | None = None,
        where: dict | None = None,
        k: int = 8,
    ) -> dict:
        if embedding is None:
            if text is None:
                raise ValueError("provide text or embedding")
            embedding = self.embedder.encode([text])[0]
        return self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw:space": "cosine"}
        )
