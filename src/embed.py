"""embeddings: prefer bedrock titan v2, fall back to local sentence-transformers.

Titan v2 (`amazon.titan-embed-text-v2:0`) returns 1024-dim vectors and is
the default in production. For offline dev and CI we swap in a local
sentence-transformers model (`intfloat/e5-small-v2`, 384-dim) via the
USE_LOCAL_EMBEDDINGS env flag.
"""

from __future__ import annotations

import json
import os
from typing import Protocol


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


def make_embedder() -> Embedder:
    if os.environ.get("USE_LOCAL_EMBEDDINGS", "false").lower() == "true":
        return LocalEmbedder()
    return BedrockTitanEmbedder()
