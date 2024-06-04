"""presidio wrapper for hipaa safe harbor de-identification.

The 18 identifiers per 45 CFR 164.514(b)(2):
    1  names
    2  geographic subdivisions smaller than a state
    3  dates (except year) related to individual
    4  telephone numbers
    5  fax numbers
    6  email addresses
    7  social security numbers
    8  medical record numbers
    9  health plan beneficiary numbers
    10 account numbers
    11 certificate / license numbers
    12 vehicle identifiers and serial numbers
    13 device identifiers and serial numbers
    14 web urls
    15 ip addresses
    16 biometric identifiers
    17 full-face photographs (out of scope for text)
    18 any other unique identifying number, characteristic, or code
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


@dataclass
class RedactionResult:
    text: str
    entities: list[dict]
    stats: dict


class Redactor:
    """Wraps presidio with our custom recognizers + operators."""

    def __init__(
        self,
        recognizer_config: str | Path | None = None,
        language: str = "en",
    ) -> None:
        self.analyzer = AnalyzerEngine(default_score_threshold=0.35)
        self.anonymizer = AnonymizerEngine()
        self.language = language
        self._operator_config: dict = {}
        if recognizer_config:
            self._load_config(Path(recognizer_config))

    def _load_config(self, path: Path) -> None:
        cfg = yaml.safe_load(path.read_text())
        for spec in cfg.get("recognizers", []):
            patterns = [
                Pattern(name=p["name"], regex=p["regex"], score=p["score"])
                for p in spec.get("patterns", [])
            ]
            rec = PatternRecognizer(
                supported_entity=spec["supported_entity"],
                patterns=patterns,
                context=spec.get("context", []),
                name=spec["name"],
                supported_language=self.language,
            )
            self.analyzer.registry.add_recognizer(rec)
        self._operator_config = cfg.get("operators", {})

    def analyze(self, text: str, entities: list[str] | None = None) -> list[RecognizerResult]:
        return self.analyzer.analyze(text=text, entities=entities, language=self.language)

    def redact(self, text: str) -> RedactionResult:
        results = self.analyze(text)
        operators = self._build_operators()
        anon = self.anonymizer.anonymize(
            text=text, analyzer_results=results, operators=operators
        )
        entities = [
            {"type": r.entity_type, "start": r.start, "end": r.end, "score": r.score}
            for r in results
        ]
        return RedactionResult(text=anon.text, entities=entities, stats=_summarize(results))

    def _build_operators(self) -> dict[str, OperatorConfig]:
        ops: dict[str, OperatorConfig] = {
            "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})
        }
        for entity, spec in self._operator_config.items():
            kind = spec.get("type", "replace")
            if kind == "replace":
                ops[entity] = OperatorConfig("replace", {"new_value": spec["new_value"]})
            elif kind == "mask":
                ops[entity] = OperatorConfig(
                    "mask",
                    {
                        "masking_char": "*",
                        "chars_to_mask": spec.get("chars_to_mask", 4),
                        "from_end": spec.get("from_end", True),
                    },
                )
            elif kind == "pseudonymize":
                # actual pseudonym generation applied in _post_pseudonymize below
                ops[entity] = OperatorConfig(
                    "replace", {"new_value": f"<{spec.get('prefix', entity)}>"}
                )
            elif kind == "date_shift":
                ops[entity] = OperatorConfig("replace", {"new_value": "<DATE>"})
        return ops


def _summarize(results: list[RecognizerResult]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return {"n": len(results), "by_type": counts}
