#!/usr/bin/env python3
"""Versioned Project DeepDive artifact validation and canonical JSON output.

The repository intentionally has no third-party runtime dependency.  This
module implements only the JSON Schema keywords used by ``schemas/v1``.  The
schema documents remain the public contract; this validator is the local,
deterministic enforcement path for scripts and CI.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = SKILL_ROOT / "schemas"
SUPPORTED_MAJOR = 1
ARTIFACT_KINDS = (
    "development-plan",
    "project-index",
    "stack-profile",
    "evidence",
    "coverage",
    "static-analysis",
    "knowledge-graph",
    "curriculum",
    "quality-report",
    "phase3-run",
    "claim-candidates",
    "claim-evidence",
    "claim-evidence-graph",
    "semantic-proposals",
    "prerequisite-candidates",
    "prerequisite-graph",
    "curriculum-candidates",
    "curriculum-run-plan",
    "curriculum-run-state",
    "chapter-facts",
    "answer-candidates",
    "exercise-bank",
    "chapter-draft-status",
    "chapter-review-session",
    "chapter-evidence-review",
    "chapter-beginner-review",
    "chapter-review-status",
    "chapter-repair-triage",
    "answer-review-session",
    "answer-evidence-review",
    "answer-beginner-review",
    "answer-review-status",
    "general-unit-facts",
    "general-unit-candidate",
    "general-learning-unit",
    "general-review-session",
    "general-factuality-review",
    "general-beginner-review",
    "general-review-status",
    "whole-book-assembly",
    "whole-book-consistency-audit",
)
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SCHEMA_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_SCHEMA_KEYWORDS = {
    "type",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "const",
    "enum",
    "pattern",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
}
_JSON_TYPES = {"object", "array", "string", "boolean", "integer", "number", "null"}
_SCHEMA_FILES = {
    "development-plan": {"1.0.0": "development-plan.schema.json"},
    "project-index": {"1.0.0": "project-index.schema.json", "1.1.0": "project-index.schema.json"},
    "stack-profile": {"1.0.0": "stack-profile.schema.json", "1.1.0": "stack-profile.schema.json"},
    "evidence": {
        "1.0.0": "evidence.schema.json",
        "1.1.0": "evidence.schema.json",
        "1.2.0": "evidence-v1.2.schema.json",
        "1.3.0": "evidence-v1.3.schema.json",
        "1.4.0": "evidence-v1.4.schema.json",
    },
    "coverage": {"1.0.0": "coverage.schema.json", "1.1.0": "coverage.schema.json"},
    "static-analysis": {
        "1.0.0": "static-analysis.schema.json",
        "1.1.0": "static-analysis-v1.1.schema.json",
        "1.2.0": "static-analysis-v1.2.schema.json",
        "1.3.0": "static-analysis-v1.3.schema.json",
        "1.4.0": "static-analysis-v1.4.schema.json",
        "1.5.0": "static-analysis-v1.5.schema.json",
    },
    "knowledge-graph": {
        "1.0.0": "knowledge-graph.schema.json",
        "1.1.0": "knowledge-graph-v1.1.schema.json",
        "1.2.0": "knowledge-graph-v1.2.schema.json",
    },
    "curriculum": {
        "1.0.0": "curriculum.schema.json",
        "1.1.0": "curriculum-v1.1.schema.json",
        "1.2.0": "curriculum-v1.2.schema.json",
    },
    "quality-report": {"1.0.0": "quality-report.schema.json"},
    "phase3-run": {
        "1.0.0": "phase3-run.schema.json",
        "1.1.0": "phase3-run-v1.1.schema.json",
    },
    "claim-candidates": {"1.0.0": "claim-candidates.schema.json", "1.1.0": "claim-candidates-v1.1.schema.json", "1.2.0": "claim-candidates-v1.2.schema.json"},
    "claim-evidence": {"1.0.0": "claim-evidence.schema.json", "1.1.0": "claim-evidence-v1.1.schema.json", "1.2.0": "claim-evidence-v1.2.schema.json"},
    "claim-evidence-graph": {"1.0.0": "claim-evidence-graph.schema.json", "1.1.0": "claim-evidence-graph-v1.1.schema.json"},
    "semantic-proposals": {
        "1.0.0": "semantic-proposals.schema.json",
        "1.1.0": "semantic-proposals-v1.1.schema.json",
    },
    "prerequisite-candidates": {
        "1.0.0": "prerequisite-candidates.schema.json",
        "1.1.0": "prerequisite-candidates-v1.1.schema.json",
    },
    "prerequisite-graph": {
        "1.0.0": "prerequisite-graph.schema.json",
        "1.1.0": "prerequisite-graph-v1.1.schema.json",
    },
    "curriculum-candidates": {
        "1.0.0": "curriculum-candidates.schema.json",
        "1.1.0": "curriculum-candidates-v1.1.schema.json",
    },
    "curriculum-run-plan": {"1.0.0": "curriculum-run-plan.schema.json"},
    "curriculum-run-state": {
        "1.0.0": "curriculum-run-state.schema.json",
        "1.1.0": "curriculum-run-state-v1.1.schema.json",
    },
    "chapter-facts": {"1.0.0": "chapter-facts.schema.json"},
    "answer-candidates": {"1.0.0": "answer-candidates.schema.json"},
    "exercise-bank": {
        "1.0.0": "exercise-bank.schema.json",
        "1.1.0": "exercise-bank-v1.1.schema.json",
    },
    "chapter-draft-status": {"1.0.0": "chapter-draft-status.schema.json"},
    "chapter-review-session": {"1.0.0": "chapter-review-session.schema.json"},
    "chapter-evidence-review": {"1.0.0": "chapter-evidence-review.schema.json"},
    "chapter-beginner-review": {"1.0.0": "chapter-beginner-review.schema.json"},
    "chapter-review-status": {"1.0.0": "chapter-review-status.schema.json"},
    "chapter-repair-triage": {"1.0.0": "chapter-repair-triage.schema.json"},
    "answer-review-session": {"1.0.0": "answer-review-session.schema.json"},
    "answer-evidence-review": {"1.0.0": "answer-evidence-review.schema.json"},
    "answer-beginner-review": {"1.0.0": "answer-beginner-review.schema.json"},
    "answer-review-status": {"1.0.0": "answer-review-status.schema.json"},
    "general-unit-facts": {"1.0.0": "general-unit-facts.schema.json"},
    "general-unit-candidate": {"1.0.0": "general-unit-candidate.schema.json"},
    "general-learning-unit": {
        "1.0.0": "general-learning-unit.schema.json",
        "1.1.0": "general-learning-unit-v1.1.schema.json",
    },
    "general-review-session": {"1.0.0": "general-review-session.schema.json"},
    "general-factuality-review": {"1.0.0": "general-factuality-review.schema.json"},
    "general-beginner-review": {"1.0.0": "general-beginner-review.schema.json"},
    "general-review-status": {"1.0.0": "general-review-status.schema.json"},
    "whole-book-assembly": {"1.0.0": "whole-book-assembly.schema.json"},
    "whole-book-consistency-audit": {"1.0.0": "whole-book-consistency-audit.schema.json"},
}


class ArtifactValidationError(ValueError):
    """An artifact failed registry or schema validation."""

    def __init__(self, errors: list[str] | tuple[str, ...] | str):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _major(version: Any) -> int:
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ArtifactValidationError("$.schema_version: expected semantic version MAJOR.MINOR.PATCH")
    return int(version.split(".", 1)[0])


def schema_path(kind: str, version: str) -> Path:
    """Return the schema path for a registered kind and supported version."""
    if kind not in ARTIFACT_KINDS:
        raise ArtifactValidationError(f"$.artifact_kind: unsupported artifact_kind: {kind!r}")
    major = _major(version)
    if major != SUPPORTED_MAJOR:
        raise ArtifactValidationError(f"$.schema_version: unsupported schema major version: {major}")
    filename = _SCHEMA_FILES.get(kind, {}).get(version)
    if filename is None:
        raise ArtifactValidationError(
            f"$.schema_version: unsupported {kind!r} schema version: {version!r}",
        )
    path = SCHEMA_ROOT / f"v{major}" / filename
    if not path.is_file():
        raise ArtifactValidationError(f"schema file is missing for {kind!r} version {version!r}: {path}")
    return path


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactValidationError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ArtifactValidationError(f"invalid JSON constant: {value}")


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_pairs_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _json_domain_errors(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: object keys must be strings (got {key!r})")
                continue
            _json_domain_errors(child, f"{path}.{key}", errors)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _json_domain_errors(child, f"{path}[{index}]", errors)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            errors.append(f"{path}: expected finite number")
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    errors.append(f"{path}: expected JSON value, got {type(value).__name__}")


def validate_schema_document(schema: Any) -> Mapping[str, Any]:
    """Fail closed if a schema uses constraints this validator cannot enforce."""
    json_errors: list[str] = []
    _json_domain_errors(schema, "$", json_errors)
    if json_errors:
        raise ArtifactValidationError(json_errors)

    errors: list[str] = []

    def walk(current: Any, path: str) -> None:
        if not isinstance(current, dict):
            errors.append(f"{path}: schema must be an object")
            return
        for keyword in current:
            if keyword not in _SCHEMA_ANNOTATIONS and keyword not in _SCHEMA_KEYWORDS:
                errors.append(f"{path}.{keyword}: unsupported schema keyword")

        declared_types = current.get("type")
        if declared_types is not None:
            values = declared_types if isinstance(declared_types, list) else [declared_types]
            if not values or any(item not in _JSON_TYPES for item in values):
                errors.append(f"{path}.type: unsupported JSON Schema type declaration")

        properties = current.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                errors.append(f"{path}.properties: expected object")
            else:
                for name, child in properties.items():
                    walk(child, f"{path}.properties.{name}")
        items = current.get("items")
        if items is not None:
            walk(items, f"{path}.items")
        additional = current.get("additionalProperties")
        if additional is not None and not isinstance(additional, bool):
            walk(additional, f"{path}.additionalProperties")

    walk(schema, "$")
    if errors:
        raise ArtifactValidationError(errors)
    return schema


@lru_cache(maxsize=None)
def _load_schema(kind: str, version: str) -> dict[str, Any]:
    path = schema_path(kind, version)
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"cannot load schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"schema root must be an object: {path}")
    validate_schema_document(value)
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return not isinstance(value, float) or math.isfinite(value)
    if expected == "null":
        return value is None
    raise ArtifactValidationError(f"schema uses unsupported type keyword: {expected!r}")


def _validate(schema: Mapping[str, Any], value: Any, path: str, errors: list[str]) -> None:
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        errors.append(f"{path}: expected constant {schema['const']!r}")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")
        return

    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in expected_types):
            label = " or ".join(expected_types)
            errors.append(f"{path}: expected {label}")
            return

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        additional = schema.get("additionalProperties", True)
        for name in value:
            if name not in properties:
                if additional is False:
                    errors.append(f"{path}.{name}: unexpected property")
                elif isinstance(additional, dict):
                    _validate(additional, value[name], f"{path}.{name}", errors)
        for name, child in properties.items():
            if name in value:
                _validate(child, value[name], f"{path}.{name}", errors)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items")
        child = schema.get("items")
        if child:
            for index, item in enumerate(value):
                _validate(child, item, f"{path}[{index}]", errors)

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: expected at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: expected at most {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path}: expected finite number")
            return
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")


def validate_artifact(data: Any) -> Mapping[str, Any]:
    """Validate an artifact and return the original mapping on success."""
    if not isinstance(data, dict):
        raise ArtifactValidationError("$: expected object")
    json_errors: list[str] = []
    _json_domain_errors(data, "$", json_errors)
    if json_errors:
        raise ArtifactValidationError(json_errors)
    kind = data.get("artifact_kind")
    if not isinstance(kind, str) or kind not in ARTIFACT_KINDS:
        raise ArtifactValidationError(f"$.artifact_kind: unsupported artifact_kind: {kind!r}")
    version = data.get("schema_version")
    major = _major(version)
    if major != SUPPORTED_MAJOR:
        raise ArtifactValidationError(f"$.schema_version: unsupported schema major version: {major}")

    schema = _load_schema(kind, version)
    errors: list[str] = []
    _validate(schema, data, "$", errors)
    if errors:
        raise ArtifactValidationError(errors)
    return data


def load_artifact(path: str | Path) -> Mapping[str, Any]:
    """Load and validate one UTF-8 JSON artifact."""
    source = Path(path)
    try:
        data = _strict_json_loads(source.read_text(encoding="utf-8"))
    except ArtifactValidationError as exc:
        raise ArtifactValidationError(f"cannot read JSON: {exc}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"{source}: cannot read JSON: {exc}") from exc
    return validate_artifact(data)


def dumps_artifact(data: Any) -> str:
    """Validate and serialize an artifact in its canonical text form."""
    validate_artifact(data)
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
