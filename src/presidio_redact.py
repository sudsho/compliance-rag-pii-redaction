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

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


@dataclass
class RedactionResult:
    text: str
    entities: list[dict]
    stats: dict


class Redactor:
    """Wraps presidio with our custom recognizers + operators."""

    def __init__(self, language: str = "en") -> None:
        self.analyzer = AnalyzerEngine(default_score_threshold=0.35)
        self.anonymizer = AnonymizerEngine()
        self.language = language

    def redact(self, text: str) -> RedactionResult:
        results: list[RecognizerResult] = self.analyzer.analyze(
            text=text, language=self.language
        )
        anon = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})},
        )
        entities = [
            {"type": r.entity_type, "start": r.start, "end": r.end, "score": r.score}
            for r in results
        ]
        stats = _summarize(results)
        return RedactionResult(text=anon.text, entities=entities, stats=stats)


def _summarize(results: list[RecognizerResult]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return {"n": len(results), "by_type": counts}
