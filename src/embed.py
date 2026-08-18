"""embeddings: prefer bedrock titan v2, fall back to a local path offline.

Titan v2 (`amazon.titan-embed-text-v2:0`) returns 1024-dim vectors and is
the default in production. When no AWS credentials are present we take a
local path so the whole pipeline runs on a CPU box with no cloud calls:

  * `LocalEmbedder`   sentence-transformers (`intfloat/e5-small-v2`, 384-dim).
                      Best quality offline, but pulls a model on first use.
  * `HashingEmbedder` deterministic feature-hashing vectorizer (256-dim,
                      pure numpy + hashlib). Zero downloads, zero model
                      state, fully reproducible. This is the default for
                      the offline demo and CI so nothing has to be fetched.

Backend selection (`EMBEDDINGS_BACKEND` wins, else auto):
    bedrock  -> BedrockTitanEmbedder (needs AWS creds)
    local    -> LocalEmbedder (sentence-transformers)
    hash     -> HashingEmbedder (offline default)

Auto: use bedrock when AWS creds are visible, otherwise hash. Setting the
legacy flag `USE_LOCAL_EMBEDDINGS=true` keeps its old meaning and selects
sentence-transformers (falling back to hashing if the model can not load).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class BedrockTitanEmbedder:
    dim = 1024

    def __init__(self, model_id: str | None = None, region: str | None = None) -> None:
        import boto3

        self.model_id = model_id or os.environ.get(
            "BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"
        )
        self.client = boto3.client(
            "bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "us-east-1")
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        # titan v2 has no batch endpoint yet; loop
        for t in texts:
            body = json.dumps({"inputText": t, "dimensions": self.dim, "normalize": True})
            resp = self.client.invoke_model(modelId=self.model_id, body=body)
            payload = json.loads(resp["body"].read())
            out.append(payload["embedding"])
        return out


class LocalEmbedder:
    def __init__(self, model_name: str = "intfloat/e5-small-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        # e5 wants "passage:" prefix
        prefixed = [f"passage: {t}" for t in texts]
        vecs = self.model.encode(prefixed, normalize_embeddings=True, convert_to_numpy=True)
        return vecs.tolist()


class HashingEmbedder:
    """Deterministic feature-hashing embedder, no model download.

    Tokens are hashed into `dim` buckets (the hashing trick), counts are
    accumulated with sublinear tf, then the vector is L2-normalized so
    cosine distance behaves. It is not competitive with a trained encoder
    on hard queries, but on a small policy corpus the BM25 rerank does the
    heavy lifting and this keeps retrieval running with zero network use.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _token_bucket(self, token: str) -> int:
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(h, "big") % self.dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                vec[self._token_bucket(tok)] += 1.0
            # sublinear term frequency dampening
            np.log1p(vec, out=vec)
            norm = float(np.linalg.norm(vec))
            if norm > 0.0:
                vec /= norm
            out.append(vec.tolist())
        return out


def _has_aws_credentials() -> bool:
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    if os.environ.get("AWS_PROFILE"):
        return True
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def make_embedder() -> Embedder:
    backend = os.environ.get("EMBEDDINGS_BACKEND", "").lower()
    if not backend:
        # legacy flag keeps working
        if os.environ.get("USE_LOCAL_EMBEDDINGS", "false").lower() == "true":
            backend = "local"
        elif _has_aws_credentials():
            backend = "bedrock"
        else:
            backend = "hash"

    if backend == "bedrock":
        return BedrockTitanEmbedder()
    if backend == "local":
        try:
            return LocalEmbedder()
        except Exception:
            # offline and the model is not cached: degrade to hashing
            return HashingEmbedder()
    return HashingEmbedder()
