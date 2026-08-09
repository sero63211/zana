"""Real pure scorers for deterministic ZANA evaluation."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from zana_core.evaluation.models import ScorerConfig, ScorerResult, ScorerType


@dataclass(frozen=True, slots=True)
class ScorerInput:
    """Raw inputs handed to a scorer without any runtime invocation."""

    raw_output: str
    citations: list[str] | None = None
    source_ids: list[str] | None = None
    metadata: dict[str, Any] | None = None


def _failure(result_type: ScorerType, case_id: str, raw_output: str, reason: str) -> ScorerResult:
    return ScorerResult(
        case_id=case_id,
        scorer_type=result_type,
        passed=False,
        score=0.0,
        raw_output=raw_output,
        failure_reason=reason,
    )


def _pass(
    result_type: ScorerType,
    case_id: str,
    raw_output: str,
    *,
    details: dict[str, Any] | None = None,
) -> ScorerResult:
    return ScorerResult(
        case_id=case_id,
        scorer_type=result_type,
        passed=True,
        score=1.0,
        raw_output=raw_output,
        details=details or {},
    )


class _JsonSchemaValidator:
    """Tiny Draft-2020-12 subset validator using only Python stdlib.

    Keeps ZANA independent of the `jsonschema` package while still rejecting
    malformed schemas and validating common scalar/array/object constraints.
    """

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema
        self._validate_schema(schema)

    @staticmethod
    def _validate_schema(schema: Any) -> None:
        if not isinstance(schema, dict):
            raise ValueError("JSON Schema must be an object")
        allowed = {
            "$schema",
            "type",
            "properties",
            "required",
            "items",
            "additionalProperties",
            "enum",
            "const",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
            "pattern",
        }
        unknown = set(schema) - allowed
        if unknown:
            raise ValueError("Unsupported JSON Schema keyword(s): " + ", ".join(sorted(unknown)))
        if "type" in schema and schema["type"] not in {
            "object",
            "array",
            "string",
            "number",
            "integer",
            "boolean",
            "null",
        }:
            raise ValueError(f"Unsupported JSON Schema type {schema['type']!r}")

    def validate(self, instance: Any) -> None:
        errors = list(self._iter_errors(instance, self.schema))
        if errors:
            raise ValueError("; ".join(errors))

    def _iter_errors(self, instance: Any, schema: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        expected_type = schema.get("type")
        if expected_type is not None and not self._matches_type(instance, expected_type):
            return [f"expected type {expected_type}, got {type(instance).__name__}"]
        if "enum" in schema and instance not in schema["enum"]:
            errors.append("value not in enum")
        if "const" in schema and instance != schema["const"]:
            errors.append("value does not equal const")
        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append("string shorter than minLength")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                errors.append("string longer than maxLength")
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                errors.append("string does not match pattern")
        if isinstance(instance, int | float) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append("number below minimum")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append("number above maximum")
        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append("array shorter than minItems")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append("array longer than maxItems")
            if "items" in schema:
                for index, item in enumerate(instance):
                    nested = self._iter_errors(item, schema["items"])
                    errors.extend(f"[{index}]{err}" for err in nested)
        if isinstance(instance, dict):
            for key, value in instance.items():
                if key in schema.get("properties", {}):
                    nested = self._iter_errors(value, schema["properties"][key])
                    errors.extend(f"{key}: {err}" for err in nested)
            if schema.get("additionalProperties") is False:
                for key in instance:
                    if key not in schema.get("properties", {}):
                        errors.append(f"additional property {key!r}")
            for key in schema.get("required", []):
                if key not in instance:
                    errors.append(f"missing required property {key!r}")
        return errors

    @staticmethod
    def _matches_type(instance: Any, expected: str) -> bool:
        if expected == "object":
            return isinstance(instance, dict)
        if expected == "array":
            return isinstance(instance, list)
        if expected == "string":
            return isinstance(instance, str)
        if expected == "number":
            return isinstance(instance, int | float) and not isinstance(instance, bool)
        if expected == "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        if expected == "boolean":
            return isinstance(instance, bool)
        if expected == "null":
            return instance is None
        return False


class ScorerRegistry:
    """Registry dispatching built-in scorer types."""

    def score(self, config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        raw = data.raw_output or ""
        dispatch = {
            ScorerType.EXACT_STRING: self._exact_string,
            ScorerType.CASE_NORMALIZED_EXACT: self._case_normalized_exact,
            ScorerType.NUMERIC_EXACT: self._numeric_exact,
            ScorerType.NUMERIC_TOLERANCE: self._numeric_tolerance,
            ScorerType.REGEX: self._regex,
            ScorerType.CONTAINS_ALL: self._contains_all,
            ScorerType.JSON_SCHEMA_VALID: self._json_schema_valid,
            ScorerType.CLASSIFICATION_LABEL: self._classification_label,
            ScorerType.CITATION_REQUIRED: self._citation_required,
            ScorerType.SOURCE_GROUNDING: self._source_grounding,
        }
        handler = dispatch.get(config.type)
        if handler is None:
            return _failure(config.type, case_id, raw, "unsupported scorer type")
        return handler(config, case_id, data)

    @staticmethod
    def _exact_string(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        expected = config.expected
        if not isinstance(expected, str):
            return _failure(config.type, case_id, data.raw_output, "expected must be a string")
        if data.raw_output == expected:
            return _pass(config.type, case_id, data.raw_output)
        return _failure(
            config.type, case_id, data.raw_output, "output does not match expected string"
        )

    @staticmethod
    def _case_normalized_exact(
        config: ScorerConfig, case_id: str, data: ScorerInput
    ) -> ScorerResult:
        expected = config.expected
        if not isinstance(expected, str):
            return _failure(config.type, case_id, data.raw_output, "expected must be a string")
        normalized = " ".join(data.raw_output.strip().lower().replace(",", "").split())
        expected_norm = " ".join(expected.strip().lower().replace(",", "").split())
        if normalized == expected_norm:
            return _pass(config.type, case_id, data.raw_output)
        return _failure(config.type, case_id, data.raw_output, "normalized output does not match")

    @staticmethod
    def _parse_number(raw: str) -> float | None:
        try:
            return float(raw.strip())
        except ValueError:
            return None

    @classmethod
    def _numeric_exact(cls, config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        expected = config.expected
        if not isinstance(expected, int | float) or isinstance(expected, bool):
            return _failure(config.type, case_id, data.raw_output, "expected must be a number")
        actual = cls._parse_number(data.raw_output)
        if actual is None:
            return _failure(config.type, case_id, data.raw_output, "output is not numeric")
        if math.isclose(actual, float(expected), rel_tol=0.0, abs_tol=1e-12):
            return _pass(config.type, case_id, data.raw_output)
        return _failure(config.type, case_id, data.raw_output, "numeric value differs")

    @classmethod
    def _numeric_tolerance(
        cls, config: ScorerConfig, case_id: str, data: ScorerInput
    ) -> ScorerResult:
        expected = config.expected
        tolerance = config.tolerance
        if not isinstance(expected, int | float) or isinstance(expected, bool):
            return _failure(config.type, case_id, data.raw_output, "expected must be a number")
        if tolerance is None or tolerance < 0:
            return _failure(config.type, case_id, data.raw_output, "tolerance must be non-negative")
        actual = cls._parse_number(data.raw_output)
        if actual is None:
            return _failure(config.type, case_id, data.raw_output, "output is not numeric")
        if abs(actual - float(expected)) <= tolerance:
            return _pass(config.type, case_id, data.raw_output)
        return _failure(config.type, case_id, data.raw_output, "numeric value outside tolerance")

    @staticmethod
    def _regex(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        expected = config.expected
        if not isinstance(expected, str):
            return _failure(
                config.type, case_id, data.raw_output, "expected must be a regex string"
            )
        try:
            compiled = re.compile(expected)
        except re.error as error:
            return _failure(config.type, case_id, data.raw_output, f"invalid regex: {error}")
        if compiled.search(data.raw_output):
            return _pass(config.type, case_id, data.raw_output)
        return _failure(config.type, case_id, data.raw_output, "regex did not match output")

    @staticmethod
    def _contains_all(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        expected = config.expected
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) for item in expected)
        ):
            return _failure(
                config.type, case_id, data.raw_output, "expected must be a list of strings"
            )
        missing = [item for item in expected if item not in data.raw_output]
        if missing:
            return _failure(
                config.type,
                case_id,
                data.raw_output,
                "missing required keywords: " + ", ".join(missing),
            )
        return _pass(config.type, case_id, data.raw_output)

    @staticmethod
    def _json_schema_valid(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        schema = config.json_schema
        if not isinstance(schema, dict):
            return _failure(config.type, case_id, data.raw_output, "schema must be an object")
        try:
            instance = json.loads(data.raw_output)
        except json.JSONDecodeError as error:
            return _failure(
                config.type, case_id, data.raw_output, f"output is not valid JSON: {error}"
            )
        try:
            validator = _JsonSchemaValidator(schema)
            validator.validate(instance)
        except ValueError as error:
            return _failure(
                config.type,
                case_id,
                data.raw_output,
                f"JSON Schema validation failed: {error}",
            )
        return _pass(config.type, case_id, data.raw_output)

    @staticmethod
    def _classification_label(
        config: ScorerConfig, case_id: str, data: ScorerInput
    ) -> ScorerResult:
        expected = config.expected
        if not isinstance(expected, str):
            return _failure(
                config.type, case_id, data.raw_output, "expected must be a label string"
            )
        label = " ".join(data.raw_output.strip().lower().split())
        if label == expected.strip().lower():
            return _pass(config.type, case_id, data.raw_output)
        return _failure(config.type, case_id, data.raw_output, "classification label differs")

    @staticmethod
    def _citation_required(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        if not data.citations:
            return _failure(
                config.type,
                case_id,
                data.raw_output,
                "citation is required but none were provided",
            )
        return _pass(config.type, case_id, data.raw_output)

    @staticmethod
    def _source_grounding(config: ScorerConfig, case_id: str, data: ScorerInput) -> ScorerResult:
        expected = config.expected
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) for item in expected)
        ):
            return _failure(
                config.type, case_id, data.raw_output, "expected must be a list of source ids"
            )
        known = set(data.source_ids or [])
        missing = [source for source in expected if source not in known]
        if missing:
            return _failure(
                config.type,
                case_id,
                data.raw_output,
                "grounding missing source ids: " + ", ".join(missing),
            )
        citations = data.citations or []
        for citation in citations:
            if citation not in known:
                return _failure(
                    config.type,
                    case_id,
                    data.raw_output,
                    f"citation references unknown source id {citation!r}",
                )
        if not citations:
            return _failure(
                config.type,
                case_id,
                data.raw_output,
                "source grounding requires at least one citation",
            )
        return _pass(config.type, case_id, data.raw_output)


def score_case(
    case_id: str,
    raw_output: str,
    config: ScorerConfig,
    *,
    citations: list[str] | None = None,
    source_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ScorerResult:
    """Score one raw output against a scorer configuration."""
    return ScorerRegistry().score(
        config,
        case_id,
        ScorerInput(
            raw_output=raw_output,
            citations=citations,
            source_ids=source_ids,
            metadata=metadata,
        ),
    )
