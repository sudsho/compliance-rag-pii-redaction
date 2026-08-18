"""answer generation with cited answers.

Two backends:

  * `BedrockGenerator`  calls Claude 3.5 Sonnet through boto3's
    `bedrock-runtime`. Guardrails are applied inline via
    `guardrailIdentifier` + `guardrailVersion` so the prompt + response
    are both scanned before we ever see the tokens.
  * `LocalExtractiveGenerator` needs no cloud. It stitches the most
    relevant sentences from the top retrieved chunks into a short,
    fully-cited answer. It is deterministic, which makes the offline
    demo and CI reproducible.

`make_generator()` picks Bedrock when AWS credentials are visible (or when
`GENERATOR_BACKEND=bedrock`), otherwise the local extractive path.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from src.retrieve import Hit


@dataclass
class GenerationResult:
    text: str
    citations: list[dict]
    guardrail_action: str          # "NONE" | "INTERVENED"
    guardrail_topics: list[str]
    stop_reason: str
    input_tokens: int
    output_tokens: int


def _render_context(hits: list[Hit]) -> str:
    lines = ["<CONTEXT>"]
    for h in hits:
        lines.append(f"[{h.source_id}:p{h.page}#{h.chunk_index}]")
        lines.append(h.text.strip())
        lines.append("")
    lines.append("</CONTEXT>")
    return "\n".join(lines)


def _extract_citations(text: str, hits: list[Hit]) -> list[dict]:
    import re

    allowed = {(h.source_id, h.page, h.chunk_index) for h in hits}
    cits: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    # tolerate optional trailing period, comma, semicolon before the closing ']'
    # and interior whitespace claude sometimes emits
    pattern = re.compile(
        r"\[\s*([A-Za-z0-9_.-]+)\s*:\s*p\s*(\d+)\s*#\s*(\d+)\s*[.,;]?\s*\]"
    )
    for m in pattern.finditer(text):
        key = (m.group(1), int(m.group(2)), int(m.group(3)))
        if key in allowed and key not in seen:
            cits.append({"source_id": key[0], "page": key[1], "chunk_index": key[2]})
            seen.add(key)
    return cits


class BedrockGenerator:
    def __init__(
        self,
        model_id: str | None = None,
        region: str | None = None,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        system_prompt_file: str | Path = "configs/system_prompt.md",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        import boto3

        self.model_id = model_id or os.environ.get(
            "BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0"
        )
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
        )
        self.guardrail_id = guardrail_id or os.environ.get("BEDROCK_GUARDRAIL_ID") or ""
        self.guardrail_version = guardrail_version or os.environ.get(
            "BEDROCK_GUARDRAIL_VERSION", "DRAFT"
        )
        self.system_prompt = Path(system_prompt_file).read_text(encoding="utf-8")
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, question: str, hits: list[Hit]) -> GenerationResult:
        user_msg = (
            f"{_render_context(hits)}\n\n"
            f"Question: {question}\n\n"
            "Respond in under 300 words. Cite every claim."
        )
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": self.system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        }
        kwargs: dict = {"modelId": self.model_id, "body": json.dumps(body)}
        if self.guardrail_id:
            kwargs["guardrailIdentifier"] = self.guardrail_id
            kwargs["guardrailVersion"] = self.guardrail_version
        resp = self.client.invoke_model(**kwargs)
        payload = json.loads(resp["body"].read())

        # anthropic response
        text = "".join(
            b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
        )
        stop_reason = payload.get("stop_reason", "?")
        usage = payload.get("usage", {})
        guardrail = payload.get("amazon-bedrock-trace", {}).get("guardrail", {})
        action = "INTERVENED" if guardrail.get("action") == "GUARDRAIL_INTERVENED" else "NONE"
        topics = [t["name"] for t in guardrail.get("topicPolicy", {}).get("topics", [])]

        return GenerationResult(
            text=text,
            citations=_extract_citations(text, hits),
            guardrail_action=action,
            guardrail_topics=topics,
            stop_reason=stop_reason,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 20]


class LocalExtractiveGenerator:
    """Offline, deterministic, citation-first generator.

    For each retrieved chunk we pick the sentence with the highest lexical
    overlap with the question and emit it followed by the chunk's citation
    tag `[source_id:pN#chunk]`. No model weights, no network. The system
    prompt file is still read so the offline path mirrors the cloud path.
    """

    model_id = "local-extractive-v1"

    def __init__(
        self,
        system_prompt_file: str | Path = "configs/system_prompt.md",
        max_sentences: int = 4,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self.system_prompt = (
            Path(system_prompt_file).read_text(encoding="utf-8")
            if Path(system_prompt_file).exists()
            else ""
        )
        self.max_sentences = max_sentences
        self.max_tokens = max_tokens
        self.temperature = temperature

    @staticmethod
    def _tokens(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", s.lower()))

    def _best_sentence(self, question_tokens: set[str], chunk_text: str) -> str:
        sentences = _split_sentences(chunk_text) or [chunk_text.strip()]
        best = sentences[0]
        best_score = -1.0
        for sent in sentences:
            overlap = len(question_tokens & self._tokens(sent))
            score = overlap / (1.0 + 0.01 * len(sent))
            if score > best_score:
                best_score = score
                best = sent
        return best.strip()

    def generate(self, question: str, hits: list[Hit]) -> GenerationResult:
        q_tokens = self._tokens(question)
        lines: list[str] = []
        citations: list[dict] = []
        seen: set[tuple[str, int, int]] = set()
        for h in hits[: self.max_sentences]:
            sentence = self._best_sentence(q_tokens, h.text).lstrip("-*# ").strip()
            tag = f"[{h.source_id}:p{h.page}#{h.chunk_index}]"
            lines.append(f"{sentence} {tag}")
            key = (h.source_id, h.page, h.chunk_index)
            if key not in seen:
                citations.append(
                    {"source_id": h.source_id, "page": h.page, "chunk_index": h.chunk_index}
                )
                seen.add(key)
        if lines:
            body = "Based on the retrieved plan policy documents:\n\n" + "\n".join(
                f"- {ln}" for ln in lines
            )
        else:
            body = "I don't have that policy on file."

        # rough token accounting for the audit row (chars/4 heuristic)
        prompt_chars = len(question) + sum(len(h.text) for h in hits)
        return GenerationResult(
            text=body,
            citations=citations,
            guardrail_action="NONE",
            guardrail_topics=[],
            stop_reason="end_turn",
            input_tokens=prompt_chars // 4,
            output_tokens=len(body) // 4,
        )


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


def make_generator():
    backend = os.environ.get("GENERATOR_BACKEND", "").lower()
    if not backend:
        backend = "bedrock" if _has_aws_credentials() else "local"
    if backend == "bedrock":
        return BedrockGenerator()
    return LocalExtractiveGenerator()
