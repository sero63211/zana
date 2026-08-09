"""Minimal JSON Schema subset evaluator for proving schema/runtime parity."""

from __future__ import annotations

import re
from typing import Any


def _type_ok(expected: Any, value: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(item, value) for item in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def validate_schema(schema: dict[str, Any], instance: Any) -> list[str]:
    """Return a list of schema violations; empty means the instance is valid."""
    return _validate(schema, instance, "#", schema)


def _validate(
    schema: dict[str, Any],
    instance: Any,
    path: str,
    root: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            errors.append(f"{path}: unsupported external $ref {ref!r}")
            return errors
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _validate(target, instance, path, root)
    if "type" in schema and not _type_ok(schema["type"], instance):
        errors.append(f"{path}: expected type {schema['type']!r}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value not in enum")
    if "minLength" in schema and isinstance(instance, str) and len(instance) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if "maxLength" in schema and isinstance(instance, str) and len(instance) > schema["maxLength"]:
        errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
    if (
        "pattern" in schema
        and isinstance(instance, str)
        and not re.search(schema["pattern"], instance)
    ):
        errors.append(f"{path}: pattern mismatch")
    if (
        "minimum" in schema
        and isinstance(instance, int | float)
        and not isinstance(instance, bool)
        and instance < schema["minimum"]
    ):
        errors.append(f"{path}: below minimum {schema['minimum']}")
    if (
        "maximum" in schema
        and isinstance(instance, int | float)
        and not isinstance(instance, bool)
        and instance > schema["maximum"]
    ):
        errors.append(f"{path}: above maximum {schema['maximum']}")
    if "minItems" in schema and isinstance(instance, list) and len(instance) < schema["minItems"]:
        errors.append(f"{path}: fewer than minItems {schema['minItems']}")
    if "required" in schema:
        if not isinstance(instance, dict):
            errors.append(f"{path}: expected object for required check")
        else:
            for key in schema["required"]:
                if key not in instance:
                    errors.append(f"{path}: missing required {key!r}")
    if "properties" in schema and isinstance(instance, dict):
        for key, subschema in schema["properties"].items():
            if key in instance:
                errors.extend(_validate(subschema, instance[key], f"{path}.{key}", root))
    if (
        "additionalProperties" in schema
        and isinstance(instance, dict)
        and schema["additionalProperties"] is False
    ):
        allowed = set(schema.get("properties", {}))
        for key in instance:
            if key not in allowed:
                errors.append(f"{path}: additional property {key!r} not allowed")
    if "items" in schema and isinstance(instance, list):
        for index, item in enumerate(instance):
            errors.extend(_validate(schema["items"], item, f"{path}[{index}]", root))
    if "allOf" in schema:
        for index, subschema in enumerate(schema["allOf"]):
            errors.extend(_validate(subschema, instance, f"{path}.allOf[{index}]", root))
    if "if" in schema:
        if_error = _validate(schema["if"], instance, path, root)
        if not if_error and "then" in schema:
            errors.extend(_validate(schema["then"], instance, path, root))
    return errors
