"""bedrock claude 3.5 sonnet generation with cited answers.

Uses the messages API through boto3's `bedrock-runtime`. Guardrails are
applied inline via `guardrailIdentifier` + `guardrailVersion` so the
prompt + response are both scanned before we ever see the tokens.
"""

from __future__ import annotations

import json
import os
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
