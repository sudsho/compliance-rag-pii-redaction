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

import os

import yaml
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from src.pseudonym import PseudonymConfig, pseudonym


# Presidio defaults to en_core_web_lg (~560 MB). For an offline / CPU box we
# use whichever spaCy model is actually installed, preferring larger models
# but happily running on en_core_web_sm (~12 MB). SPACY_MODEL overrides.
_SPACY_MODEL_PREFERENCE = ["en_core_web_lg", "en_core_web_md", "en_core_web_sm"]


def _pick_spacy_model() -> str | None:
    import spacy.util

    forced = os.environ.get("SPACY_MODEL")
    candidates = [forced] + _SPACY_MODEL_PREFERENCE if forced else _SPACY_MODEL_PREFERENCE
    for model in candidates:
        if model and spacy.util.is_package(model):
            return model
    return None


def _build_nlp_engine(language: str):
    model = _pick_spacy_model()
    if not model:
        return None
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": language, "model_name": model}],
        }
    )
    return provider.create_engine()


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
        pseudonym_config: PseudonymConfig | None = None,
    ) -> None:
        nlp_engine = _build_nlp_engine(language)
        if nlp_engine is not None:
            self.analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine, default_score_threshold=0.35
            )
        else:
            # no spaCy model installed; let presidio use its default config
            self.analyzer = AnalyzerEngine(default_score_threshold=0.35)
        self.anonymizer = AnonymizerEngine()
        self.language = language
        self.pseudonym_config = pseudonym_config
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
        operators = self._build_operators(text, results)
        anon = self.anonymizer.anonymize(
            text=text, analyzer_results=results, operators=operators
        )
        entities = [
            {"type": r.entity_type, "start": r.start, "end": r.end, "score": r.score}
            for r in results
        ]
        return RedactionResult(text=anon.text, entities=entities, stats=_summarize(results))

    def _build_operators(
        self, text: str, results: list[RecognizerResult]
    ) -> dict[str, OperatorConfig]:
        ops: dict[str, OperatorConfig] = {
            "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})
        }
        # per-instance pseudonym table so identical values collapse to same tag
        pseudo_by_slot: dict[tuple[str, str], str] = {}
        for r in results:
            surface = text[r.start : r.end]
            spec = self._operator_config.get(r.entity_type, {})
            if spec.get("type") == "pseudonymize" and self.pseudonym_config:
                pseudo_by_slot[(r.entity_type, surface)] = pseudonym(
                    surface, spec.get("prefix", r.entity_type), self.pseudonym_config
                )

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
                prefix = spec.get("prefix", entity)
                if self.pseudonym_config is None:
                    ops[entity] = OperatorConfig("replace", {"new_value": f"<{prefix}>"})
                else:
                    ops[entity] = OperatorConfig(
                        "custom",
                        {
                            "lambda": lambda x, _p=prefix: pseudonym(
                                x, _p, self.pseudonym_config
                            )
                        },
                    )
            elif kind == "date_shift":
                ops[entity] = OperatorConfig("replace", {"new_value": "<DATE>"})
        return ops


def _summarize(results: list[RecognizerResult]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return {"n": len(results), "by_type": counts}
