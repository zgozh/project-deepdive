#!/usr/bin/env python3
"""Build opt-in frontend static-analysis facts from an audited Git snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from artifact_contract import (
    ArtifactValidationError,
    dumps_artifact,
    validate_artifact,
)
from file_classification import normalize_artifact_path
from frontend_static_analysis import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    FrontendAnalysisError,
    analyze_sources,
)
from stack_detection import (
    StackDetectionError,
    validate_evidence_references,
)


ANALYSIS_VERSION = "1.4.0"
ANALYSIS_VERSIONS = {"1.4.0", "1.5.0"}
_PARSER_PROTOCOL_VERSION = {"1.4.0": 1, "1.5.0": 2}
EXTRACTION_METHOD = "typescript-6.0.3-parse"
_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
_LANGUAGE_BY_EXTENSION = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}
_EXCLUDED_CLASSIFICATIONS = {"GENERATED", "VENDOR", "IGNORED_WITH_REASON"}
_SENSITIVE_LABEL = re.compile(
    r"(?:secret|token|password|passwd|credential|authorization|cookie|api[_-]?key)",
    re.IGNORECASE,
)
_UNRESOLVED_TARGETS = {
    "DEFINES": None,
    "IMPORTS": "frontend_import_target_redacted",
    "EXPORTS": "frontend_export_target_unresolved",
    "USES_API": "frontend_api_target_unresolved",
}
_LANGUAGE_LIMITATIONS = [
    {
        "code": "NO_ENDPOINT_RESOLUTION",
        "reason": "Frontend request candidates are not resolved to URLs or backend endpoints.",
    },
    {
        "code": "ROLE_SCOPE",
        "reason": "This slice identifies component and Hook candidates only.",
    },
]
_LANGUAGE_LIMITATIONS_V15 = [
    {
        "code": "NO_ENDPOINT_RESOLUTION",
        "reason": "Frontend request candidates are not resolved to URLs or backend endpoints.",
    },
    {
        "code": "ROLE_SCOPE",
        "reason": "Page, store, API-client, component, and Hook roles are narrow syntax candidates; page roles require a same-snapshot package.json Next dependency marker, while runtime registration and state lifecycle remain unverified.",
    },
]
_SKIP_REASONS = {
    "EXCLUDED_CLASSIFICATION": "Coverage classifies this path as generated, vendor, or ignored.",
    "UNKNOWN_CLASSIFICATION": "Coverage has not resolved this path to a source classification.",
    "BINARY_SOURCE": "Phase 2 identified this path as binary; it was not parsed.",
    "NOT_REGULAR_SOURCE": "This path is not a regular tracked Git blob.",
    "SOURCE_TOO_LARGE": "This source exceeds the one MiB per-file parser limit.",
    "UNSUPPORTED_ENCODING": "This source is not valid UTF-8 and was not parsed.",
    "UNSUPPORTED_CONTENT_KIND": "Phase 2 does not identify this path as supported text source.",
}
_PARTIAL_REASONS = {
    "SYNTAX_ERROR": "The parser reported a syntax error and emitted no facts for this file.",
    "AMBIGUOUS_DEFINITION_OWNER": "A declaration has no unique enclosing declaration for its DEFINES edge.",
    "AMBIGUOUS_ROLE_SYMBOL": "A component or Hook candidate has no unique matching declaration symbol.",
}
_SOURCE_SUMMARIES = {
    "module": "JavaScript or TypeScript source-file syntax was parsed.",
    "class": "JavaScript or TypeScript class declaration syntax was parsed.",
    "function": "JavaScript or TypeScript callable declaration syntax was parsed.",
    "method": "JavaScript or TypeScript method or accessor declaration syntax was parsed.",
    "constructor": "JavaScript or TypeScript constructor declaration syntax was parsed.",
    "variable": "A JavaScript or TypeScript variable declaration is present.",
    "DEFINES": "A source declaration is syntactically contained by this declaration.",
    "IMPORTS": "An import declaration is present; its target is redacted or unresolved.",
    "EXPORTS": "An export declaration is present; its target is unresolved.",
    "USES_API": "An allowlisted API call candidate is present; its target is unresolved.",
    "component_candidate": "The parser marked a component candidate by syntax convention.",
    "hook_candidate": "The parser marked a Hook candidate by naming convention.",
    "page_candidate": "A Next app/pages convention and exported component syntax identify a page candidate.",
    "store_candidate": "A direct imported Zustand create call initializes this variable candidate.",
    "api_client_candidate": "This function or module directly contains an HTTP call candidate.",
}


class FrontendSnapshotAnalysisError(ValueError):
    """Fixed-code error that never includes target source or child diagnostics."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:24]}"


def _evidence_id(fact_id: str) -> str:
    return "EVID-FRONTEND-" + hashlib.sha256(fact_id.encode("ascii")).hexdigest()[:32]


def _sha256(value: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(dumps_artifact(value).encode("utf-8")).hexdigest()
    except (ArtifactValidationError, TypeError, ValueError) as exc:
        raise FrontendSnapshotAnalysisError("INPUT_INVALID") from exc


def _source_metadata(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    g01_status: str,
    unknown_files: int,
) -> dict[str, Any]:
    actual_unknowns = coverage.get("unknown_count")
    if (
        type(unknown_files) is not int
        or unknown_files < 0
        or actual_unknowns != unknown_files
        or g01_status not in {"PASS", "PARTIAL"}
        or g01_status != ("PARTIAL" if unknown_files else "PASS")
    ):
        raise FrontendSnapshotAnalysisError("G01_INVALID")
    snapshot_kind = project_index.get("project", {}).get("snapshot_kind")
    if snapshot_kind not in {"git-tree", "worktree"}:
        raise FrontendSnapshotAnalysisError("SOURCE_METADATA_INVALID")
    return {
        "snapshot_kind": snapshot_kind,
        "g01_status": g01_status,
        "unknown_files": unknown_files,
        "project_index_sha256": _sha256(project_index),
        "coverage_sha256": _sha256(coverage),
    }


def _paths_by_key(rows: Any, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, list):
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise FrontendSnapshotAnalysisError("INPUT_INVALID")
        try:
            path = normalize_artifact_path(row.get("path"))
        except (TypeError, ValueError) as exc:
            raise FrontendSnapshotAnalysisError("UNSAFE_PATH") from exc
        if path in result:
            raise FrontendSnapshotAnalysisError("DUPLICATE_PATH")
        result[path] = row
    return result


def _validate_inputs(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    g01_status: str,
    unknown_files: int,
):
    for label, artifact, kind in (
        ("project-index", project_index, "project-index"),
        ("coverage", coverage, "coverage"),
        ("stack-profile", stack_profile, "stack-profile"),
        ("Phase 3A evidence", evidence, "evidence"),
    ):
        try:
            validate_artifact(artifact)
        except (ArtifactValidationError, KeyError, TypeError) as exc:
            raise FrontendSnapshotAnalysisError("INPUT_INVALID") from exc
        if artifact.get("artifact_kind") != kind:
            raise FrontendSnapshotAnalysisError("INPUT_INVALID")

    if stack_profile.get("schema_version") != "1.1.0" or evidence.get("schema_version") != "1.1.0":
        raise FrontendSnapshotAnalysisError("PHASE3A_VERSION_UNSUPPORTED")
    if project_index["repository_revision"] != coverage["repository_revision"]:
        raise FrontendSnapshotAnalysisError("REVISION_MISMATCH")
    if project_index["generated_at"] != coverage["generated_at"]:
        raise FrontendSnapshotAnalysisError("TIMESTAMP_MISMATCH")
    revision = project_index["repository_revision"]
    if stack_profile["repository_revision"] != revision or evidence["repository_revision"] != revision:
        raise FrontendSnapshotAnalysisError("REVISION_MISMATCH")
    if stack_profile["generated_at"] != evidence["generated_at"]:
        raise FrontendSnapshotAnalysisError("TIMESTAMP_MISMATCH")

    metadata = _source_metadata(project_index, coverage, g01_status, unknown_files)
    if (
        stack_profile.get("source_metadata") != metadata
        or evidence.get("source_metadata") != metadata
    ):
        raise FrontendSnapshotAnalysisError("SOURCE_METADATA_MISMATCH")
    try:
        validate_evidence_references(
            stack_profile,
            evidence,
            expected_source_metadata=metadata,
        )
    except (ArtifactValidationError, StackDetectionError, KeyError, TypeError) as exc:
        raise FrontendSnapshotAnalysisError("PHASE3A_PAIR_INVALID") from exc

    files_by_path = _paths_by_key(project_index.get("files"), "project-index")
    coverage_by_path = _paths_by_key(coverage.get("entries"), "coverage")
    if set(files_by_path) != set(coverage_by_path):
        raise FrontendSnapshotAnalysisError("PATH_SET_MISMATCH")
    if project_index.get("file_count") != len(files_by_path):
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")
    if coverage.get("tracked_file_count") != len(coverage_by_path):
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")

    original_evidence: dict[str, Mapping[str, Any]] = {}
    items = evidence.get("items")
    if not isinstance(items, list):
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")
    for item in items:
        evidence_id = item["id"]
        if evidence_id in original_evidence:
            raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
        original_evidence[evidence_id] = item
    return metadata, files_by_path, coverage_by_path, original_evidence


def _language(path: str) -> str:
    return _LANGUAGE_BY_EXTENSION[PurePosixPath(path).suffix.lower()]


def _is_next_page_path(path: str, package_root: str) -> bool:
    parts = PurePosixPath(path).parts
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in _EXTENSIONS or not parts:
        return False
    root_parts = () if package_root in {"", "."} else PurePosixPath(package_root).parts
    if parts[:len(root_parts)] != root_parts:
        return False
    relative_parts = parts[len(root_parts):]
    if not relative_parts:
        return False
    stem = PurePosixPath(relative_parts[-1]).stem
    if (
        stem == "page"
        and (
            relative_parts[:-1][:1] == ("app",)
            or relative_parts[:-1][:2] == ("src", "app")
        )
    ):
        return True
    page_prefix_length = (
        1 if relative_parts[:1] == ("pages",)
        else 2 if relative_parts[:2] == ("src", "pages")
        else 0
    )
    if page_prefix_length:
        route = relative_parts[page_prefix_length:]
        if not route:
            return False
        route_stem = PurePosixPath(route[0]).stem
        if route[0] == "api" or route_stem == "api":
            return False
        if stem.startswith("_"):
            return False
        return True
    return False


def _package_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate package.json key")
        result[key] = value
    return result


def _nearest_package_manifest(
    path: str,
    files_by_path: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str] | None:
    directory = PurePosixPath(path).parent
    while True:
        manifest_path = (directory / "package.json").as_posix()
        if manifest_path in files_by_path:
            return manifest_path, directory.as_posix()
        if directory == PurePosixPath("."):
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _manifest_declares_next(
    manifest_path: str,
    files_by_path: Mapping[str, Mapping[str, Any]],
    source_reader: Callable[[str, Mapping[str, Any]], bytes] | None,
    regular_source_paths: frozenset[str],
) -> bool:
    entry = files_by_path.get(manifest_path)
    if (
        entry is None
        or source_reader is None
        or manifest_path not in regular_source_paths
        or entry.get("content_kind") != "text"
        or type(entry.get("bytes")) is not int
        or entry["bytes"] < 0
        or entry["bytes"] > MAX_FILE_BYTES
    ):
        return False
    payload = _source_payload(source_reader, manifest_path, entry)
    try:
        manifest = json.loads(
            payload.decode("utf-8-sig", errors="strict"),
            object_pairs_hook=_package_json_object,
        )
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(manifest, Mapping):
        return False
    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        dependencies = manifest.get(field)
        if (
            isinstance(dependencies, Mapping)
            and isinstance(dependencies.get("next"), str)
            and dependencies["next"]
        ):
            return True
    return False


def _confirmed_next_page_paths(
    paths,
    files_by_path: Mapping[str, Mapping[str, Any]],
    source_reader: Callable[[str, Mapping[str, Any]], bytes] | None,
    regular_source_paths: frozenset[str],
) -> set[str]:
    confirmed: set[str] = set()
    manifest_cache: dict[str, bool] = {}
    for path in paths:
        nearest = _nearest_package_manifest(path, files_by_path)
        if nearest is None:
            continue
        manifest_path, package_root = nearest
        if not _is_next_page_path(path, package_root):
            continue
        if manifest_path not in manifest_cache:
            manifest_cache[manifest_path] = _manifest_declares_next(
                manifest_path,
                files_by_path,
                source_reader,
                regular_source_paths,
            )
        if manifest_cache[manifest_path]:
            confirmed.add(path)
    return confirmed


def _line_count(source_text: str) -> int:
    normalized = re.sub(r"\r\n?", "\n", source_text)
    return max(1, normalized.count("\n") + source_text.count("\u2028") + source_text.count("\u2029") + 1)


def _limitation(code: str, *, partial: bool = False) -> dict[str, str]:
    reasons = _PARTIAL_REASONS if partial else _SKIP_REASONS
    reason = reasons.get(code)
    if reason is None:
        raise FrontendSnapshotAnalysisError("INTERNAL_LIMITATION_CODE")
    return {"code": code, "reason": reason}


def _add_partial_limitation(file_record: dict[str, Any], code: str) -> None:
    limitation = _limitation(code, partial=True)
    if limitation not in file_record["limitations"]:
        file_record["limitations"].append(limitation)
    file_record["limitations"].sort(key=lambda item: item["code"])
    file_record["status"] = "PARTIAL"


def _skip_code(
    path: str,
    entry: Mapping[str, Any],
    coverage_entry: Mapping[str, Any],
    regular_source_paths: frozenset[str],
) -> str | None:
    classification = coverage_entry.get("classification")
    if classification in _EXCLUDED_CLASSIFICATIONS:
        return "EXCLUDED_CLASSIFICATION"
    if classification == "UNKNOWN":
        return "UNKNOWN_CLASSIFICATION"
    content_kind = entry.get("content_kind")
    if content_kind == "binary":
        return "BINARY_SOURCE"
    if content_kind in {"symlink", "gitlink"} or path not in regular_source_paths:
        return "NOT_REGULAR_SOURCE"
    if content_kind != "text":
        return "UNSUPPORTED_CONTENT_KIND"
    size = entry.get("bytes")
    if type(size) is not int or size < 0:
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")
    if size > MAX_FILE_BYTES:
        return "SOURCE_TOO_LARGE"
    return None


def _selected_frontend_paths(
    files_by_path: Mapping[str, Mapping[str, Any]],
    coverage_by_path: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    paths = []
    for path in files_by_path:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in _EXTENSIONS:
            continue
        coverage_entry = coverage_by_path[path]
        if (
            coverage_entry.get("surface") == "frontend"
            or "frontend" in coverage_entry.get("secondary_surfaces", [])
        ):
            paths.append(path)
    return sorted(paths)


def _source_payload(source_reader, path: str, entry: Mapping[str, Any]) -> bytes:
    try:
        payload = source_reader(path, entry)
    except Exception as exc:
        raise FrontendSnapshotAnalysisError("SNAPSHOT_SOURCE_READ_FAILED") from exc
    if not isinstance(payload, bytes):
        raise FrontendSnapshotAnalysisError("SNAPSHOT_SOURCE_INVALID")
    if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise FrontendSnapshotAnalysisError("SOURCE_DRIFT")
    return payload


def _batches(paths: list[str], files_by_path: Mapping[str, Mapping[str, Any]]):
    batch: list[str] = []
    batch_bytes = 0
    for path in paths:
        size = files_by_path[path]["bytes"]
        if size > MAX_FILE_BYTES:
            continue
        if batch and (len(batch) >= MAX_FILES or batch_bytes + size > MAX_TOTAL_BYTES):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(path)
        batch_bytes += size
        if len(batch) >= MAX_FILES or batch_bytes >= MAX_TOTAL_BYTES:
            yield batch
            batch = []
            batch_bytes = 0
    if batch:
        yield batch


def _stable_module_fact_id(path: str, digest: str, source_length: int) -> str:
    seed = "\0".join(("module", path, digest, "0", str(source_length)))
    return "FE_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _symbol_kind(fact: Mapping[str, Any]) -> str:
    if fact["kind"] == "SYMBOL_CLASS":
        return "class"
    if fact["kind"] == "STORE_DECLARATION_CANDIDATE":
        return "variable"
    return {
        "arrow": "function",
        "expression": "function",
        "declaration": "function",
        "method": "method",
        "constructor": "constructor",
        "accessor": "method",
    }[fact["function_kind"]]


def _enclosing_declaration(
    fact: Mapping[str, Any],
    declaration_facts: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, bool]:
    location = fact["location"]
    start = location["start_offset"]
    end = location["end_offset"]
    candidates = [
        other for other in declaration_facts
        if other["id"] != fact["id"]
        and other["location"]["start_offset"] <= start
        and other["location"]["end_offset"] >= end
        and (
            other["location"]["start_offset"] < start
            or other["location"]["end_offset"] > end
        )
    ]
    if not candidates:
        return None, False
    minimum = min(
        item["location"]["end_offset"] - item["location"]["start_offset"]
        for item in candidates
    )
    nearest = [
        item for item in candidates
        if item["location"]["end_offset"] - item["location"]["start_offset"] == minimum
    ]
    if len(nearest) != 1:
        return None, True
    return nearest[0], False


def _evidence_for_fact(
    fact: Mapping[str, Any],
    *,
    revision: str,
    digest: str,
) -> dict[str, Any]:
    return {
        "id": _evidence_id(fact["id"]),
        "level": "E1",
        "kind": "source",
        "summary": _SOURCE_SUMMARIES[fact["summary_kind"]],
        "confidence": 1.0,
        "locator": {
            "path": fact["path"],
            "symbol": fact["id"],
            "line_start": fact["line_start"],
            "line_end": fact["line_end"],
            "observation": f"revision={revision}; sha256={digest}",
        },
    }


def _make_fact(
    kind: str,
    *,
    prefix: str,
    revision: str,
    metadata: Mapping[str, Any],
    path: str,
    digest: str,
    language: str,
    fact_identity: str,
    line_start: int,
    line_end: int,
    fields: Mapping[str, Any],
    summary_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    identifier = _stable_id(
        prefix,
        revision,
        metadata,
        digest,
        language,
        kind,
        path,
        fact_identity,
    )
    fact = {
        "id": identifier,
        **fields,
        "language": language,
        "path": path,
        "extraction_method": EXTRACTION_METHOD,
        "certainty": fields.get("certainty", "VERIFIED"),
        "line_start": line_start,
        "line_end": line_end,
        "evidence_ids": [_evidence_id(identifier)],
    }
    evidence = _evidence_for_fact(
        {
            "id": identifier,
            "summary_kind": summary_kind,
            "path": path,
            "line_start": line_start,
            "line_end": line_end,
        },
        revision=revision,
        digest=digest,
    )
    return fact, evidence


def _add_symbol(
    symbols: list[dict[str, Any]],
    evidence_rows: dict[str, dict[str, Any]],
    *,
    revision: str,
    metadata: Mapping[str, Any],
    path: str,
    digest: str,
    language: str,
    fact_identity: str,
    kind: str,
    name: str,
    qualified_name: str,
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    fact, evidence = _make_fact(
        kind,
        prefix="SYM",
        revision=revision,
        metadata=metadata,
        path=path,
        digest=digest,
        language=language,
        fact_identity=fact_identity,
        line_start=line_start,
        line_end=line_end,
        fields={
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
        },
        summary_kind=kind,
    )
    symbols.append(fact)
    if evidence["id"] in evidence_rows:
        raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
    evidence_rows[evidence["id"]] = evidence
    return fact


def _add_relation(
    relations: list[dict[str, Any]],
    evidence_rows: dict[str, dict[str, Any]],
    *,
    revision: str,
    metadata: Mapping[str, Any],
    path: str,
    digest: str,
    language: str,
    fact_identity: str,
    kind: str,
    source_id: str,
    target_id: str | None,
    line_start: int,
    line_end: int,
    certainty: str,
) -> None:
    fact, evidence = _make_fact(
        kind,
        prefix="REL",
        revision=revision,
        metadata=metadata,
        path=path,
        digest=digest,
        language=language,
        fact_identity=fact_identity,
        line_start=line_start,
        line_end=line_end,
        fields={
            "kind": kind,
            "source_id": source_id,
            "target_id": target_id,
            "unresolved_target": _UNRESOLVED_TARGETS[kind],
            "certainty": certainty,
        },
        summary_kind=kind,
    )
    relations.append(fact)
    if evidence["id"] in evidence_rows:
        raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
    evidence_rows[evidence["id"]] = evidence


def _add_role(
    roles: list[dict[str, Any]],
    evidence_rows: dict[str, dict[str, Any]],
    *,
    revision: str,
    metadata: Mapping[str, Any],
    path: str,
    digest: str,
    language: str,
    fact_identity: str,
    kind: str,
    symbol_id: str,
    line_start: int,
    line_end: int,
) -> None:
    fact, evidence = _make_fact(
        kind,
        prefix="ROLE",
        revision=revision,
        metadata=metadata,
        path=path,
        digest=digest,
        language=language,
        fact_identity=fact_identity,
        line_start=line_start,
        line_end=line_end,
        fields={
            "kind": kind,
            "symbol_id": symbol_id,
            "certainty": "CANDIDATE",
        },
        summary_kind=kind,
    )
    roles.append(fact)
    if evidence["id"] in evidence_rows:
        raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
    evidence_rows[evidence["id"]] = evidence


def _fact_symbol_names(facts: list[Mapping[str, Any]]) -> dict[str, str]:
    return {
        fact["id"]: (
            "<redacted>"
            if _SENSITIVE_LABEL.search(fact["name"])
            else fact["name"]
        )
        for fact in facts
    }


def _convert_file_facts(
    parsed_file: Mapping[str, Any],
    *,
    revision: str,
    metadata: Mapping[str, Any],
    file_record: dict[str, Any],
    symbols: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    roles: list[dict[str, Any]],
    evidence_rows: dict[str, dict[str, Any]],
    analysis_version: str = ANALYSIS_VERSION,
    next_page_candidate: bool = False,
) -> None:
    path = parsed_file["path"]
    digest = parsed_file["sha256"]
    language = _language(path)
    facts = parsed_file["facts"]
    declarations = [
        fact for fact in facts
        if fact["kind"] in {"SYMBOL_FUNCTION", "SYMBOL_CLASS"}
        or analysis_version == "1.5.0"
        and fact["kind"] == "STORE_DECLARATION_CANDIDATE"
    ]
    source_length = max(
        (fact["location"]["end_offset"] for fact in facts),
        default=0,
    )
    module_fact_identity = _stable_module_fact_id(path, digest, source_length)
    module_name = PurePosixPath(path).stem or "source"
    if _SENSITIVE_LABEL.search(module_name):
        module_name = "<redacted>"
    module_qualified = PurePosixPath(path).with_suffix("").as_posix()
    module, module_evidence = _make_fact(
        "module",
        prefix="SYM",
        revision=revision,
        metadata=metadata,
        path=path,
        digest=digest,
        language=language,
        fact_identity=module_fact_identity,
        line_start=1,
        line_end=file_record["line_count"],
        fields={
            "kind": "module",
            "name": module_name,
            "qualified_name": module_qualified,
        },
        summary_kind="module",
    )
    symbols.append(module)
    if module_evidence["id"] in evidence_rows:
        raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
    evidence_rows[module_evidence["id"]] = module_evidence

    symbol_by_fact: dict[str, dict[str, Any]] = {}
    api_client_sources: dict[str, tuple[str, dict[str, Any]]] = {}
    declaration_owners: dict[str, Mapping[str, Any] | None] = {}
    ambiguous_owners: set[str] = set()
    names = _fact_symbol_names(declarations)
    base_name = PurePosixPath(path).with_suffix("").as_posix()

    for fact in declarations:
        owner, ambiguous = _enclosing_declaration(fact, declarations)
        declaration_owners[fact["id"]] = owner
        if ambiguous:
            ambiguous_owners.add(fact["id"])
        kind = _symbol_kind(fact)
        name = names[fact["id"]]
        name_chain = [name]
        seen_owners: set[str] = set()
        current = owner
        while current is not None and current["id"] not in seen_owners:
            seen_owners.add(current["id"])
            name_chain.append(names[current["id"]])
            if current["id"] in ambiguous_owners:
                break
            current = declaration_owners.get(current["id"])
        qualified_name = base_name + "." + ".".join(reversed(name_chain))
        location = fact["location"]
        symbol, citation = _make_fact(
            kind,
            prefix="SYM",
            revision=revision,
            metadata=metadata,
            path=path,
            digest=digest,
            language=language,
            fact_identity=fact["id"],
            line_start=location["start_line"],
            line_end=location["end_line"],
            fields={
                "kind": kind,
                "name": name,
                "qualified_name": qualified_name,
            },
            summary_kind=kind,
        )
        symbols.append(symbol)
        if citation["id"] in evidence_rows:
            raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
        evidence_rows[citation["id"]] = citation
        symbol_by_fact[fact["id"]] = symbol

    for fact in declarations:
        location = fact["location"]
        if fact["id"] in ambiguous_owners:
            _add_partial_limitation(file_record, "AMBIGUOUS_DEFINITION_OWNER")
            continue
        owner = declaration_owners[fact["id"]]
        source_symbol = symbol_by_fact[owner["id"]] if owner is not None else module
        target_symbol = symbol_by_fact[fact["id"]]
        _add_relation(
            relations,
            evidence_rows,
            revision=revision,
            metadata=metadata,
            path=path,
            digest=digest,
            language=language,
            fact_identity=f"defines:{fact['id']}",
            kind="DEFINES",
            source_id=source_symbol["id"],
            target_id=target_symbol["id"],
            line_start=location["start_line"],
            line_end=location["end_line"],
            certainty="VERIFIED",
        )

    for fact in facts:
        kind = fact["kind"]
        location = fact["location"]
        line_start, line_end = location["start_line"], location["end_line"]
        if kind == "IMPORT":
            _add_relation(
                relations,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=fact["id"],
                kind="IMPORTS",
                source_id=module["id"],
                target_id=None,
                line_start=line_start,
                line_end=line_end,
                certainty="UNRESOLVED",
            )
        elif kind == "EXPORT":
            _add_relation(
                relations,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=fact["id"],
                kind="EXPORTS",
                source_id=module["id"],
                target_id=None,
                line_start=line_start,
                line_end=line_end,
                certainty="UNRESOLVED",
            )
        elif kind == "API_CALL_CANDIDATE":
            candidates = [
                item for item in declarations
                if item["kind"] == "SYMBOL_FUNCTION"
                and item["location"]["start_offset"] <= location["start_offset"]
                and item["location"]["end_offset"] >= location["end_offset"]
            ]
            source_symbol = module
            direct_owner = not candidates
            if candidates:
                minimum = min(
                    item["location"]["end_offset"] - item["location"]["start_offset"]
                    for item in candidates
                )
                nearest = [
                    item for item in candidates
                    if item["location"]["end_offset"] - item["location"]["start_offset"] == minimum
                ]
                if len(nearest) == 1:
                    source_symbol = symbol_by_fact[nearest[0]["id"]]
                    direct_owner = True
                else:
                    direct_owner = False
            _add_relation(
                relations,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=fact["id"],
                kind="USES_API",
                source_id=source_symbol["id"],
                target_id=None,
                line_start=line_start,
                line_end=line_end,
                certainty="CANDIDATE",
            )
            if analysis_version == "1.5.0" and direct_owner:
                api_client_sources.setdefault(
                    source_symbol["id"], (fact["id"], source_symbol),
                )

    for fact in declarations:
        if fact.get("exported"):
            location = fact["location"]
            _add_relation(
                relations,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=f"exported:{fact['id']}",
                kind="EXPORTS",
                source_id=module["id"],
                target_id=None,
                line_start=location["start_line"],
                line_end=location["end_line"],
                certainty="UNRESOLVED",
            )

    for fact in facts:
        if fact["kind"] not in {
            "COMPONENT_CANDIDATE", "HOOK_CANDIDATE", "STORE_DECLARATION_CANDIDATE",
        }:
            continue
        if fact["kind"] == "STORE_DECLARATION_CANDIDATE":
            role_kind = "store_candidate"
            allowed_declarations = {"STORE_DECLARATION_CANDIDATE"}
        elif fact["kind"] == "COMPONENT_CANDIDATE":
            role_kind = "component_candidate"
            allowed_declarations = {"SYMBOL_FUNCTION", "SYMBOL_CLASS"}
        else:
            role_kind = "hook_candidate"
            allowed_declarations = {"SYMBOL_FUNCTION"}
        matching = [
            symbol_fact for symbol_fact in declarations
            if symbol_fact["kind"] in allowed_declarations
            and (
                symbol_fact["id"] == fact["id"]
                if role_kind == "store_candidate"
                else symbol_fact["name"] == fact["name"]
            )
            and symbol_fact["location"]["start_offset"] == fact["location"]["start_offset"]
            and symbol_fact["location"]["end_offset"] == fact["location"]["end_offset"]
        ]
        if len(matching) != 1:
            _add_partial_limitation(file_record, "AMBIGUOUS_ROLE_SYMBOL")
            continue
        location = fact["location"]
        _add_role(
            roles,
            evidence_rows,
            revision=revision,
            metadata=metadata,
            path=path,
            digest=digest,
            language=language,
            fact_identity=fact["id"],
            kind=role_kind,
            symbol_id=symbol_by_fact[matching[0]["id"]]["id"],
            line_start=location["start_line"],
            line_end=location["end_line"],
        )
        if (
            analysis_version == "1.5.0"
            and role_kind == "component_candidate"
            and next_page_candidate
            and matching[0].get("exported") is True
        ):
            _add_role(
                roles,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=f"page:{fact['id']}",
                kind="page_candidate",
                symbol_id=symbol_by_fact[matching[0]["id"]]["id"],
                line_start=location["start_line"],
                line_end=location["end_line"],
            )

    if analysis_version == "1.5.0":
        for symbol_id, (fact_id, source_symbol) in sorted(api_client_sources.items()):
            _add_role(
                roles,
                evidence_rows,
                revision=revision,
                metadata=metadata,
                path=path,
                digest=digest,
                language=language,
                fact_identity=f"api-client:{symbol_id}:{fact_id}",
                kind="api_client_candidate",
                symbol_id=symbol_id,
                line_start=source_symbol["line_start"],
                line_end=source_symbol["line_end"],
            )


def _file_records(
    paths: list[str],
    files_by_path: Mapping[str, Mapping[str, Any]],
    coverage_by_path: Mapping[str, Mapping[str, Any]],
    regular_source_paths: frozenset[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    records: dict[str, dict[str, Any]] = {}
    parse_paths: list[str] = []
    for path in paths:
        entry = files_by_path[path]
        coverage_entry = coverage_by_path[path]
        code = _skip_code(path, entry, coverage_entry, regular_source_paths)
        record = {
            "path": path,
            "sha256": entry["sha256"],
            "status": "SKIPPED" if code else "ANALYZED",
            "limitations": [_limitation(code)] if code else [],
        }
        records[path] = record
        if code is None:
            parse_paths.append(path)
    return records, parse_paths


def _analysis_status(g01_status: str, records: Mapping[str, Mapping[str, Any]]) -> str:
    return "PARTIAL" if (
        g01_status == "PARTIAL"
        or any(
            record["status"] != "ANALYZED" or record["limitations"]
            for record in records.values()
        )
    ) else "PASS"


def _language_records(
    records_by_path: Mapping[str, Mapping[str, Any]],
    g01_status: str,
    analysis_version: str = ANALYSIS_VERSION,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for path, record in records_by_path.items():
        grouped.setdefault(_language(path), []).append(record)
    output = []
    for language in sorted(grouped):
        language_files = sorted(grouped[language], key=lambda row: row["path"])
        status = _analysis_status(g01_status, {row["path"]: row for row in language_files})
        output.append({
            "language": language,
            "analysis_status": status,
            "limitations": [dict(item) for item in (
                _LANGUAGE_LIMITATIONS_V15
                if analysis_version == "1.5.0"
                else _LANGUAGE_LIMITATIONS
            )],
            "files": language_files,
        })
    return output


def analyze_frontend_artifacts(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    source_reader: Callable[[str, Mapping[str, Any]], bytes],
    regular_source_paths: frozenset[str],
    *,
    g01_status: str,
    unknown_files: int,
    analysis_version: str = ANALYSIS_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a versioned frontend bundle from exact, byte-verified snapshot sources."""
    if analysis_version not in ANALYSIS_VERSIONS:
        raise FrontendSnapshotAnalysisError("ANALYSIS_VERSION_UNSUPPORTED")
    metadata, files_by_path, coverage_by_path, original_evidence = _validate_inputs(
        project_index,
        coverage,
        stack_profile,
        evidence,
        g01_status,
        unknown_files,
    )
    selected_paths = _selected_frontend_paths(files_by_path, coverage_by_path)
    if not selected_paths:
        raise FrontendSnapshotAnalysisError("NO_FRONTEND_PATHS")
    records, parse_paths = _file_records(
        selected_paths,
        files_by_path,
        coverage_by_path,
        regular_source_paths,
    )
    next_page_paths = (
        _confirmed_next_page_paths(
            selected_paths,
            files_by_path,
            source_reader,
            regular_source_paths,
        )
        if analysis_version == "1.5.0"
        else set()
    )

    symbols: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    additions: dict[str, dict[str, Any]] = {}
    revision = project_index["repository_revision"]
    generated_at = stack_profile["generated_at"]

    for batch_paths in _batches(parse_paths, files_by_path):
        source_bytes: dict[str, bytes] = {}
        for path in batch_paths:
            entry = files_by_path[path]
            payload = _source_payload(source_reader, path, entry)
            try:
                source_text = payload.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError:
                records[path] = {
                    "path": path,
                    "sha256": entry["sha256"],
                    "status": "SKIPPED",
                    "limitations": [_limitation("UNSUPPORTED_ENCODING")],
                }
                continue
            records[path]["line_count"] = _line_count(source_text)
            source_bytes[path] = payload

        if not source_bytes:
            continue
        try:
            protocol_version = _PARSER_PROTOCOL_VERSION[analysis_version]
            if protocol_version == 1:
                report = analyze_sources(source_bytes)
            else:
                report = analyze_sources(source_bytes, protocol_version=protocol_version)
        except FrontendAnalysisError as exc:
            raise FrontendSnapshotAnalysisError(f"PARSER_{exc.code}") from exc
        parsed_files = report.get("files") if isinstance(report, Mapping) else None
        if not isinstance(parsed_files, list) or len(parsed_files) != len(source_bytes):
            raise FrontendSnapshotAnalysisError("PARSER_PROTOCOL_ERROR")
        expected = {
            path: hashlib.sha256(payload).hexdigest()
            for path, payload in source_bytes.items()
        }
        seen_paths: set[str] = set()
        for parsed_file in parsed_files:
            if not isinstance(parsed_file, Mapping):
                raise FrontendSnapshotAnalysisError("PARSER_PROTOCOL_ERROR")
            path = parsed_file.get("path")
            if (
                path not in expected
                or path in seen_paths
                or parsed_file.get("sha256") != expected[path]
                or parsed_file.get("script_kind") != {
                    ".js": "JS",
                    ".jsx": "JSX",
                    ".ts": "TS",
                    ".tsx": "TSX",
                }[PurePosixPath(path).suffix.lower()]
                or parsed_file.get("status") not in {"ANALYZED", "PARTIAL"}
            ):
                raise FrontendSnapshotAnalysisError("PARSER_PROTOCOL_ERROR")
            seen_paths.add(path)
            record = records[path]
            if parsed_file["status"] == "PARTIAL":
                record["status"] = "PARTIAL"
                record["limitations"] = [_limitation("SYNTAX_ERROR", partial=True)]
                if parsed_file.get("facts") != []:
                    raise FrontendSnapshotAnalysisError("PARSER_PROTOCOL_ERROR")
                continue
            record["status"] = "ANALYZED"
            _convert_file_facts(
                parsed_file,
                revision=revision,
                metadata=metadata,
                file_record=record,
                symbols=symbols,
                relations=relations,
                roles=roles,
                evidence_rows=additions,
                analysis_version=analysis_version,
                next_page_candidate=path in next_page_paths,
            )
        if seen_paths != set(expected):
            raise FrontendSnapshotAnalysisError("PARSER_PROTOCOL_ERROR")

    language_rows = _language_records(records, g01_status, analysis_version)
    analysis_status = _analysis_status(g01_status, records)
    analysis = {
        "artifact_kind": "static-analysis",
        "schema_version": analysis_version,
        "repository_revision": revision,
        "generated_at": generated_at,
        "source_metadata": metadata,
        "stack_profile_sha256": _sha256(stack_profile),
        "phase3a_evidence_sha256": _sha256(evidence),
        "analysis_status": analysis_status,
        "languages": language_rows,
        "symbols": sorted(symbols, key=lambda row: row["id"]),
        "relations": sorted(relations, key=lambda row: row["id"]),
        "roles": sorted(roles, key=lambda row: row["id"]),
    }
    merged_evidence = {
        "artifact_kind": "evidence",
        "schema_version": evidence["schema_version"],
        "repository_revision": revision,
        "generated_at": generated_at,
        "source_metadata": metadata,
        "items": sorted(
            [*evidence["items"], *additions.values()],
            key=lambda row: row["id"],
        ),
    }
    validate_frontend_analysis_bundle(
        project_index,
        coverage,
        stack_profile,
        evidence,
        merged_evidence,
        analysis,
        g01_status=g01_status,
        unknown_files=unknown_files,
        regular_source_paths=regular_source_paths,
        source_reader=source_reader,
    )
    return analysis, merged_evidence


def validate_frontend_analysis_bundle(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    original_evidence: Mapping[str, Any],
    merged_evidence: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    g01_status: str,
    unknown_files: int,
    regular_source_paths: frozenset[str],
    source_reader: Callable[[str, Mapping[str, Any]], bytes] | None = None,
) -> None:
    """Check schemas and same-snapshot identity, IDs, ownership and E1 references."""
    metadata, files_by_path, coverage_by_path, original_by_id = _validate_inputs(
        project_index,
        coverage,
        stack_profile,
        original_evidence,
        g01_status,
        unknown_files,
    )
    try:
        validate_artifact(analysis)
        validate_artifact(merged_evidence)
    except (ArtifactValidationError, KeyError, TypeError) as exc:
        raise FrontendSnapshotAnalysisError("OUTPUT_SCHEMA_INVALID") from exc
    revision = project_index["repository_revision"]
    timestamp = stack_profile["generated_at"]
    analysis_version = analysis.get("schema_version")
    if (
        analysis_version not in ANALYSIS_VERSIONS
        or analysis.get("repository_revision") != revision
        or analysis.get("generated_at") != timestamp
        or analysis.get("source_metadata") != metadata
        or analysis.get("stack_profile_sha256") != _sha256(stack_profile)
        or analysis.get("phase3a_evidence_sha256") != _sha256(original_evidence)
    ):
        raise FrontendSnapshotAnalysisError("OUTPUT_IDENTITY_MISMATCH")
    if (
        merged_evidence.get("schema_version") != original_evidence.get("schema_version")
        or merged_evidence.get("repository_revision") != revision
        or merged_evidence.get("generated_at") != timestamp
        or merged_evidence.get("source_metadata") != metadata
    ):
        raise FrontendSnapshotAnalysisError("OUTPUT_IDENTITY_MISMATCH")
    try:
        validate_evidence_references(
            stack_profile,
            merged_evidence,
            expected_source_metadata=metadata,
        )
    except (ArtifactValidationError, StackDetectionError, KeyError, TypeError) as exc:
        raise FrontendSnapshotAnalysisError("OUTPUT_EVIDENCE_INVALID") from exc

    selected_paths = _selected_frontend_paths(files_by_path, coverage_by_path)
    if not selected_paths:
        raise FrontendSnapshotAnalysisError("NO_FRONTEND_PATHS")
    expected_records, _parse_paths = _file_records(
        selected_paths,
        files_by_path,
        coverage_by_path,
        regular_source_paths,
    )
    actual_records: dict[str, Mapping[str, Any]] = {}
    for language_row in analysis["languages"]:
        for record in language_row["files"]:
            path = record["path"]
            if path in actual_records:
                raise FrontendSnapshotAnalysisError("DUPLICATE_PATH")
            actual_records[path] = record
    if set(actual_records) != set(expected_records):
        raise FrontendSnapshotAnalysisError("FILE_SET_MISMATCH")
    for path, record in actual_records.items():
        expected = expected_records[path]
        if record["sha256"] != files_by_path[path]["sha256"]:
            raise FrontendSnapshotAnalysisError("SOURCE_DRIFT")
        if expected["status"] == "SKIPPED":
            if record["status"] != "SKIPPED" or record["limitations"] != expected["limitations"]:
                raise FrontendSnapshotAnalysisError("FILE_STATUS_MISMATCH")
        elif record["status"] == "SKIPPED":
            codes = {item["code"] for item in record["limitations"]}
            if codes != {"UNSUPPORTED_ENCODING"}:
                raise FrontendSnapshotAnalysisError("FILE_STATUS_MISMATCH")
        elif record["status"] == "PARTIAL":
            codes = [item["code"] for item in record["limitations"]]
            if (
                not codes
                or len(codes) != len(set(codes))
                or codes != sorted(codes)
                or any(item != _limitation(item["code"], partial=True) for item in record["limitations"])
                or any(code not in _PARTIAL_REASONS for code in codes)
            ):
                raise FrontendSnapshotAnalysisError("FILE_STATUS_MISMATCH")
        elif record["status"] != "ANALYZED":
            raise FrontendSnapshotAnalysisError("FILE_STATUS_MISMATCH")
        elif record["limitations"]:
            raise FrontendSnapshotAnalysisError("FILE_STATUS_MISMATCH")
        if record["status"] in {"ANALYZED", "PARTIAL"}:
            if type(record.get("line_count")) is not int or record["line_count"] < 1:
                raise FrontendSnapshotAnalysisError("FILE_LINE_COUNT_INVALID")

    symbols = analysis["symbols"]
    relations = analysis["relations"]
    roles = analysis["roles"]
    module_counts: dict[str, int] = {}
    for symbol in symbols:
        if symbol["kind"] == "module":
            path = symbol["path"]
            module_counts[path] = module_counts.get(path, 0) + 1
    for path, file_record in actual_records.items():
        has_syntax_error = any(
            item["code"] == "SYNTAX_ERROR" for item in file_record["limitations"]
        )
        expected_module_count = int(
            file_record["status"] in {"ANALYZED", "PARTIAL"} and not has_syntax_error
        )
        if module_counts.get(path, 0) != expected_module_count:
            raise FrontendSnapshotAnalysisError("MODULE_SYMBOL_COUNT_INVALID")

    fact_ids: set[str] = set()
    symbols_by_id: dict[str, Mapping[str, Any]] = {}
    evidence_expected: dict[str, Mapping[str, Any]] = {}
    facts = [*symbols, *relations, *roles]
    symbol_fact_ids = {fact["id"] for fact in symbols}
    for fact in facts:
        identifier = fact["id"]
        if identifier in fact_ids:
            raise FrontendSnapshotAnalysisError("DUPLICATE_FACT_ID")
        fact_ids.add(identifier)
        path = fact["path"]
        file_record = actual_records.get(path)
        if file_record is None or file_record["status"] not in {"ANALYZED", "PARTIAL"}:
            raise FrontendSnapshotAnalysisError("FACT_FILE_STATUS_MISMATCH")
        if (
            file_record["status"] == "PARTIAL"
            and any(item["code"] == "SYNTAX_ERROR" for item in file_record["limitations"])
        ):
            raise FrontendSnapshotAnalysisError("FACT_FILE_STATUS_MISMATCH")
        if fact["language"] != _language(path):
            raise FrontendSnapshotAnalysisError("FACT_LANGUAGE_MISMATCH")
        if not 1 <= fact["line_start"] <= fact["line_end"] <= file_record["line_count"]:
            raise FrontendSnapshotAnalysisError("FACT_LINE_INVALID")
        evidence_ids = fact["evidence_ids"]
        if len(evidence_ids) != 1:
            raise FrontendSnapshotAnalysisError("FACT_EVIDENCE_INVALID")
        evidence_id = evidence_ids[0]
        if evidence_id != _evidence_id(identifier):
            raise FrontendSnapshotAnalysisError("FACT_EVIDENCE_INVALID")
        digest = file_record["sha256"]
        evidence_expected[evidence_id] = _evidence_for_fact(
            {
                "id": identifier,
                "summary_kind": fact["kind"],
                "path": path,
                "line_start": fact["line_start"],
                "line_end": fact["line_end"],
            },
            revision=revision,
            digest=digest,
        )
        if identifier in symbol_fact_ids:
            symbols_by_id[identifier] = fact

    for relation in relations:
        source = symbols_by_id.get(relation["source_id"])
        target_id = relation["target_id"]
        target = symbols_by_id.get(target_id) if target_id is not None else None
        kind = relation["kind"]
        if source is None or source["path"] != relation["path"]:
            raise FrontendSnapshotAnalysisError("DANGLING_RELATION")
        if kind == "DEFINES":
            if (
                target is None
                or target["path"] != relation["path"]
                or source["id"] == target["id"]
                or relation["unresolved_target"] is not None
                or relation["certainty"] != "VERIFIED"
                or target["kind"] == "module"
                or relation["line_start"] != target["line_start"]
                or relation["line_end"] != target["line_end"]
                or source["line_start"] > target["line_start"]
                or source["line_end"] < target["line_end"]
            ):
                raise FrontendSnapshotAnalysisError("DANGLING_RELATION")
        elif kind in {"IMPORTS", "EXPORTS", "USES_API"}:
            expected_target = _UNRESOLVED_TARGETS[kind]
            expected_certainty = "CANDIDATE" if kind == "USES_API" else "UNRESOLVED"
            if (
                target_id is not None
                or relation["unresolved_target"] != expected_target
                or relation["certainty"] != expected_certainty
                or source["kind"] != "module" and kind in {"IMPORTS", "EXPORTS"}
                or (
                    kind == "USES_API"
                    and source["kind"] not in {"module", "function", "method", "constructor"}
                )
                or relation["line_start"] < source["line_start"]
                or relation["line_end"] > source["line_end"]
            ):
                raise FrontendSnapshotAnalysisError("RELATION_TARGET_INVALID")

    module_ids_by_path = {
        symbol["path"]: symbol["id"]
        for symbol in symbols if symbol["kind"] == "module"
    }
    role_keys = {(role["kind"], role["symbol_id"]) for role in roles}
    page_role_paths = {
        role["path"] for role in roles if role["kind"] == "page_candidate"
    }
    confirmed_page_role_paths = _confirmed_next_page_paths(
        page_role_paths,
        files_by_path,
        source_reader,
        regular_source_paths,
    )
    for role in roles:
        symbol = symbols_by_id.get(role["symbol_id"])
        role_kind = role["kind"]
        if (
            symbol is None
            or symbol["path"] != role["path"]
            or role["certainty"] != "CANDIDATE"
            or role["line_start"] != symbol["line_start"]
            or role["line_end"] != symbol["line_end"]
            or (role_kind == "hook_candidate" and symbol["kind"] != "function")
            or (
                role_kind == "component_candidate"
                and symbol["kind"] not in {"function", "class"}
            )
            or (
                role_kind == "page_candidate"
                and (
                    analysis_version != "1.5.0"
                    or role["path"] not in confirmed_page_role_paths
                    or symbol["kind"] not in {"function", "class"}
                    or ("component_candidate", symbol["id"]) not in role_keys
                    or not any(
                        relation["kind"] == "EXPORTS"
                        and relation["source_id"] == module_ids_by_path.get(role["path"])
                        and relation["path"] == role["path"]
                        and relation["line_start"] == symbol["line_start"]
                        and relation["line_end"] == symbol["line_end"]
                        for relation in relations
                    )
                )
            )
            or (
                role_kind == "store_candidate"
                and (
                    analysis_version != "1.5.0"
                    or symbol["kind"] != "variable"
                    or not any(
                        relation["kind"] == "DEFINES"
                        and relation["target_id"] == symbol["id"]
                        for relation in relations
                    )
                )
            )
            or (
                role_kind == "api_client_candidate"
                and (
                    analysis_version != "1.5.0"
                    or symbol["kind"] not in {"module", "function", "method", "constructor"}
                    or not any(
                        relation["kind"] == "USES_API"
                        and relation["source_id"] == symbol["id"]
                        and relation["path"] == symbol["path"]
                        for relation in relations
                    )
                )
            )
        ):
            raise FrontendSnapshotAnalysisError("DANGLING_ROLE")

    merged_by_id: dict[str, Mapping[str, Any]] = {}
    for item in merged_evidence["items"]:
        if item["id"] in merged_by_id:
            raise FrontendSnapshotAnalysisError("DUPLICATE_EVIDENCE_ID")
        merged_by_id[item["id"]] = item
    if any(merged_by_id.get(identifier) != item for identifier, item in original_by_id.items()):
        raise FrontendSnapshotAnalysisError("ORIGINAL_EVIDENCE_CHANGED")
    if set(merged_by_id) != set(original_by_id) | set(evidence_expected):
        raise FrontendSnapshotAnalysisError("EVIDENCE_SET_MISMATCH")
    if any(merged_by_id.get(identifier) != item for identifier, item in evidence_expected.items()):
        raise FrontendSnapshotAnalysisError("EVIDENCE_LOCATOR_MISMATCH")

    expected_status = _analysis_status(g01_status, actual_records)
    if analysis["analysis_status"] != expected_status:
        raise FrontendSnapshotAnalysisError("ANALYSIS_STATUS_MISMATCH")
    language_records = {row["language"]: row for row in analysis["languages"]}
    if len(language_records) != len(analysis["languages"]):
        raise FrontendSnapshotAnalysisError("DUPLICATE_LANGUAGE")
    expected_languages = {_language(path) for path in selected_paths}
    if set(language_records) != expected_languages:
        raise FrontendSnapshotAnalysisError("LANGUAGE_SET_MISMATCH")
    for language, row in language_records.items():
        expected_limitations = (
            _LANGUAGE_LIMITATIONS_V15
            if analysis_version == "1.5.0"
            else _LANGUAGE_LIMITATIONS
        )
        if row["limitations"] != expected_limitations:
            raise FrontendSnapshotAnalysisError("LANGUAGE_LIMITATIONS_MISMATCH")
        language_paths = {
            path: record for path, record in actual_records.items()
            if _language(path) == language
        }
        if row["analysis_status"] != _analysis_status(g01_status, language_paths):
            raise FrontendSnapshotAnalysisError("ANALYSIS_STATUS_MISMATCH")


__all__ = [
    "FrontendSnapshotAnalysisError",
    "analyze_frontend_artifacts",
    "validate_frontend_analysis_bundle",
]
