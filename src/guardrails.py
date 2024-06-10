"""bedrock guardrails wrapper for prompt+response scanning.

Bedrock Guardrails (GA April 2024) provide content, denied-topic, word,
sensitive-info, and prompt-attack policies. We call the standalone
`ApplyGuardrail` API for the prompt BEFORE the model, and for the
response AFTER the model. Fail-closed: any INTERVENED action denies.

Escalation paths:
  * PROMPT_ATTACK  -> deny, log, notify security (queue)
  * PII_LEAK       -> deny, log, notify compliance (queue)
  * DENIED_TOPIC   -> return canned refusal
  * CONTENT_HIGH   -> deny, log
  * default INTERVENED -> deny
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)


class GuardrailVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class Escalation(str, Enum):
    NONE = "NONE"
    SECURITY = "SECURITY"
    COMPLIANCE = "COMPLIANCE"


@dataclass
class GuardrailResult:
    verdict: GuardrailVerdict
    action: str                # bedrock action string
    reason: str
    escalation: Escalation
    output_text: str | None    # rewritten text if guardrail modified it
    raw: dict


class BedrockGuardrails:
    def __init__(
        self,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        region: str | None = None,
        fail_closed: bool = True,
    ) -> None:
        import boto3

        self.guardrail_id = guardrail_id or os.environ.get("BEDROCK_GUARDRAIL_ID") or ""
        self.guardrail_version = guardrail_version or os.environ.get(
            "BEDROCK_GUARDRAIL_VERSION", "DRAFT"
        )
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
        )
        self.fail_closed = fail_closed
        self.enabled = bool(self.guardrail_id)

    def _apply(self, text: str, source: str) -> GuardrailResult:
        if not self.enabled:
            return GuardrailResult(
                verdict=GuardrailVerdict.ALLOW,
                action="DISABLED",
                reason="no guardrail id configured",
                escalation=Escalation.NONE,
                output_text=None,
                raw={},
            )
        resp = self.client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.guardrail_version,
            source=source,          # "INPUT" | "OUTPUT"
            content=[{"text": {"text": text}}],
        )
        action = resp.get("action", "NONE")
        assessments = resp.get("assessments", []) or []
        reasons: list[str] = []
        escalation = Escalation.NONE

        for a in assessments:
            if "topicPolicy" in a:
                for t in a["topicPolicy"].get("topics", []):
                    reasons.append(f"topic:{t.get('name')}")
            if "contentPolicy" in a:
                for f in a["contentPolicy"].get("filters", []):
                    conf = f.get("confidence", "LOW")
                    if conf in {"HIGH", "MEDIUM"}:
                        reasons.append(f"content:{f.get('type')}:{conf}")
                        if conf == "HIGH":
                            escalation = Escalation.SECURITY
            if "sensitiveInformationPolicy" in a:
                for pi in a["sensitiveInformationPolicy"].get("piiEntities", []):
                    reasons.append(f"pii:{pi.get('type')}")
                    escalation = Escalation.COMPLIANCE
            if "wordPolicy" in a:
                for w in a["wordPolicy"].get("customWords", []):
                    reasons.append(f"word:{w.get('match')}")
            if a.get("invocationMetrics", {}).get("guardrailCoverage", {}).get(
                "textCharacters", {}
            ).get("guarded", 0):
                pass  # coverage stat, non-blocking

        verdict = (
            GuardrailVerdict.DENY
            if action == "GUARDRAIL_INTERVENED" and self.fail_closed
            else GuardrailVerdict.ALLOW
        )

        outputs = resp.get("outputs", []) or []
        output_text = outputs[0].get("text") if outputs else None

        if verdict is GuardrailVerdict.DENY:
            log.warning("guardrail deny source=%s reasons=%s", source, reasons)

        return GuardrailResult(
            verdict=verdict,
            action=action,
            reason=",".join(reasons) or "clean",
            escalation=escalation,
            output_text=output_text,
            raw=resp,
        )

    def check_prompt(self, text: str) -> GuardrailResult:
        return self._apply(text, source="INPUT")

    def check_response(self, text: str) -> GuardrailResult:
        return self._apply(text, source="OUTPUT")

    def render_refusal(self, res: GuardrailResult) -> str:
        if "topic:" in res.reason:
            return "I can only answer plan policy questions. Please rephrase."
        if res.escalation is Escalation.COMPLIANCE:
            return "That request may involve protected information; a compliance reviewer has been notified."
        if res.escalation is Escalation.SECURITY:
            return "That request was blocked by security policy. If this was in error, contact your administrator."
        return "That request was blocked by policy."
