#!/usr/bin/env python3
"""Build a deterministic Phase 4 graph and evidence fold from a Phase 3 run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact, validate_artifact
from phase3_run import (
    MANIFEST_NAME,
    MEMBER_PATHS,
    Phase3RunError,
    validate_phase3_run,
    verify_phase3_run_freshness,
)


SCHEMA_VERSION = "1.1.0"
EVIDENCE_SCHEMA_VERSION = "1.2.0"
DOCUMENTATION_SCHEMA_VERSION = "1.2.0"
DOCUMENTATION_EVIDENCE_SCHEMA_VERSION = "1.4.0"
_DOCUMENTATION_MEMBER = "documentation/evidence.json"
_SOURCE_RUN_VERSIONS = {
    (SCHEMA_VERSION, EVIDENCE_SCHEMA_VERSION): "1.0.0",
    (DOCUMENTATION_SCHEMA_VERSION, DOCUMENTATION_EVIDENCE_SCHEMA_VERSION): "1.1.0",
}
_PHASE2_INDEX = MEMBER_PATHS["project_index"]
_PHASE2_COVERAGE = MEMBER_PATHS["coverage"]
_ANALYSIS_VERSIONS = {
    MEMBER_PATHS["python_analysis"]: {"1.0.0", "1.1.0"},
    MEMBER_PATHS["java_analysis_v12"]: {"1.2.0"},
    MEMBER_PATHS["java_analysis_v13"]: {"1.3.0"},
    MEMBER_PATHS["frontend_analysis"]: {"1.4.0", "1.5.0"},
}
_EVIDENCE_MEMBERS = frozenset(
    path for name, path in MEMBER_PATHS.items() if "evidence" in name
) | frozenset({_DOCUMENTATION_MEMBER})
_RELATION_KINDS = frozenset({
    "DEFINES", "IMPORTS", "CALL_CANDIDATE", "EXTENDS", "ROUTE_TO",
    "IMPLEMENTS", "EXPORTS", "USES_API",
})
_LEGACY_PROPOSAL_EDGE_KINDS = frozenset({
    "CALLS", "CALLED_BY", "DEPENDS_ON", "IMPLEMENTS", "EXTENDS", "USES",
    "ROUTES_TO", "READS", "WRITES", "PERSISTS_TO", "PUBLISHES", "CONSUMES",
    "RENDERS", "TRIGGERS", "PART_OF", "REQUIRES_CONCEPT", "EVIDENCED_BY",
    "TESTED_BY", "EXTENSION_OF", "FAILS_WHEN",
})
_PROVISIONAL_NODE_CATEGORIES = {
    "BusinessCapability": "business",
    "BusinessWorkflow": "business",
    "RuntimeWorkflow": "business",
    "Mechanism": "mechanism",
    "Concept": "concept",
}
_PROPOSAL_MEMBER = "semantic-proposals.json"
_ROLE_KINDS = frozenset({
    "data_model_candidate", "test_candidate", "component_candidate",
    "hook_candidate", "page_candidate", "store_candidate", "api_client_candidate",
})
_PHASE2_MEMBERS = frozenset({_PHASE2_INDEX, _PHASE2_COVERAGE})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class Phase4GraphError(ValueError):
    """A fixed, redacted failure for Phase 4A graph generation or validation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json_value(value: Any, active: set[int]) -> None:
    value_type = type(value)
    if value is None or value_type in {str, bool, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if value_type in {list, dict}:
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic value is not JSON-compatible")
        active.add(identity)
        try:
            if value_type is list:
                for child in value:
                    _json_value(child, active)
            else:
                for key, child in value.items():
                    if type(key) is not str:
                        raise TypeError("JSON object keys must be strings")
                    _json_value(child, active)
        finally:
            active.remove(identity)
        return
    raise TypeError(f"value is not JSON-compatible: {value_type.__name__}")


def canonical_tuple_sha256(parts: tuple[Any, ...]) -> str:
    """Hash a tuple using compact, sorted-key, UTF-8 JSON-array bytes."""
    if not isinstance(parts, tuple):
        raise TypeError("canonical tuple hash requires a tuple")
    _json_value(list(parts), set())
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_link_or_junction(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _assert_no_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_link_or_junction(current):
            raise Phase4GraphError("PATH_UNSAFE")


def _output_path(root: str | Path, out: str | Path) -> Path:
    try:
        raw_root = Path(root)
        _assert_no_link_components(raw_root)
        project_root = raw_root.resolve(strict=True)
        output = Path(os.path.abspath(os.fspath(out)))
        parent = output.parent
        if not project_root.is_dir() or not parent.is_dir():
            raise Phase4GraphError("OUTPUT_INVALID")
        _assert_no_link_components(parent)
        if os.path.lexists(output):
            raise Phase4GraphError("OUTPUT_EXISTS")
        resolved_output = parent.resolve(strict=True) / output.name
        if resolved_output == project_root or project_root in resolved_output.parents:
            raise Phase4GraphError("OUTPUT_INVALID")
        return resolved_output
    except Phase4GraphError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise Phase4GraphError("OUTPUT_INVALID") from None


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise Phase4GraphError("PATH_UNSAFE")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or posix.as_posix() != value
    ):
        raise Phase4GraphError("PATH_UNSAFE")
    return value


def _insert_unique(records: dict[str, Any], identifier: Any, payload: Any) -> None:
    if not isinstance(identifier, str) or not identifier:
        raise Phase4GraphError("INPUT_INVALID")
    previous = records.get(identifier)
    if previous is not None and previous != payload:
        raise Phase4GraphError("DUPLICATE_ID_CONFLICT")
    records[identifier] = payload


def _fold_evidence_members(members: list[tuple[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    folded: dict[str, dict[str, Any]] = {}
    source_members: dict[str, set[str]] = {}
    for member_path, artifact in members:
        _safe_relative_path(member_path)
        items = artifact.get("items")
        if not isinstance(items, list):
            raise Phase4GraphError("INPUT_INVALID")
        for item in items:
            if not isinstance(item, Mapping):
                raise Phase4GraphError("INPUT_INVALID")
            if item.get("level") not in {"E1", "E2"}:
                raise Phase4GraphError("EVIDENCE_LEVEL_INVALID")
            if member_path == _DOCUMENTATION_MEMBER and (
                item.get("level") != "E2"
                or item.get("kind") != "repository_documentation"
                or not isinstance(item.get("source_bytes"), Mapping)
            ):
                raise Phase4GraphError("INPUT_INVALID")
            identifier = item.get("id")
            payload = dict(item)
            payload.pop("source_members", None)
            _insert_unique(folded, identifier, payload)
            source_members.setdefault(identifier, set()).add(member_path)
    result = []
    for identifier in sorted(folded):
        item = dict(folded[identifier])
        item["source_members"] = sorted(source_members[identifier])
        result.append(item)
    return result


def _collect_static_facts(
    members: list[tuple[str, Mapping[str, Any]]],
    file_paths: set[str],
    evidence_ids: set[str],
) -> dict[str, Any]:
    symbols: dict[str, dict[str, Any]] = {}
    symbol_members: dict[str, set[str]] = {}
    relations: dict[str, dict[str, Any]] = {}
    relation_members: dict[str, set[str]] = {}
    role_records: dict[str, dict[str, Any]] = {}
    for member_path, artifact in members:
        supported_versions = _ANALYSIS_VERSIONS.get(member_path)
        if supported_versions is None or artifact.get("schema_version") not in supported_versions:
            raise Phase4GraphError("UNSUPPORTED_VERSION")
        symbols_list = artifact.get("symbols")
        relations_list = artifact.get("relations")
        roles_list = artifact.get("roles", [])
        if not isinstance(symbols_list, list) or not isinstance(relations_list, list) or not isinstance(roles_list, list):
            raise Phase4GraphError("INPUT_INVALID")
        for symbol in symbols_list:
            if not isinstance(symbol, Mapping):
                raise Phase4GraphError("INPUT_INVALID")
            path = _safe_relative_path(symbol.get("path"))
            if path not in file_paths:
                raise Phase4GraphError("SOURCE_PATH_INVALID")
            payload = dict(symbol)
            _insert_unique(symbols, symbol.get("id"), payload)
            symbol_members.setdefault(symbol["id"], set()).add(member_path)
            if not set(payload.get("evidence_ids", [])) <= evidence_ids:
                raise Phase4GraphError("EVIDENCE_MISSING")
        for relation in relations_list:
            if not isinstance(relation, Mapping):
                raise Phase4GraphError("INPUT_INVALID")
            if relation.get("kind") not in _RELATION_KINDS:
                raise Phase4GraphError("RELATION_KIND_INVALID")
            path = _safe_relative_path(relation.get("path"))
            if path not in file_paths:
                raise Phase4GraphError("SOURCE_PATH_INVALID")
            payload = dict(relation)
            _insert_unique(relations, relation.get("id"), payload)
            relation_members.setdefault(relation["id"], set()).add(member_path)
            if not set(payload.get("evidence_ids", [])) <= evidence_ids:
                raise Phase4GraphError("EVIDENCE_MISSING")
        for role in roles_list:
            if not isinstance(role, Mapping):
                raise Phase4GraphError("INPUT_INVALID")
            if role.get("kind") not in _ROLE_KINDS:
                raise Phase4GraphError("INPUT_INVALID")
            path = _safe_relative_path(role.get("path"))
            if path not in file_paths:
                raise Phase4GraphError("SOURCE_PATH_INVALID")
            payload = dict(role)
            _insert_unique(role_records, role.get("id"), payload)
            if not set(payload.get("evidence_ids", [])) <= evidence_ids:
                raise Phase4GraphError("EVIDENCE_MISSING")

    roles_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for role in role_records.values():
        symbol_id = role.get("symbol_id")
        if symbol_id not in symbols:
            raise Phase4GraphError("SYMBOL_MISSING")
        roles_by_symbol.setdefault(symbol_id, []).append(role)
    for relation in relations.values():
        if relation.get("source_id") not in symbols:
            raise Phase4GraphError("SYMBOL_MISSING")
        target_id = relation.get("target_id")
        if target_id is not None and target_id not in symbols:
            raise Phase4GraphError("SYMBOL_MISSING")
    return {
        "symbols": symbols,
        "symbol_members": symbol_members,
        "relations": relations,
        "relation_members": relation_members,
        "roles": roles_by_symbol,
    }


def _containment_edge(from_id: str, to_id: str, source_member_path: str) -> dict[str, Any]:
    return {
        "id": "EDGE-" + canonical_tuple_sha256(("contains", from_id, to_id)),
        "type": "CONTAINS",
        "from": from_id,
        "to": to_id,
        "certainty": "VERIFIED",
        "unresolved_target": None,
        "evidence_ids": [],
        "source_members": [source_member_path],
        "source_record": None,
    }


def _source_run(manifest: Mapping[str, Any], manifest_sha256: str) -> dict[str, Any]:
    return {
        "artifact_kind": "phase3-run",
        "schema_version": manifest["schema_version"],
        "manifest_sha256": manifest_sha256,
        "status": manifest["status"],
        "e1_audit": manifest["e1_audit"],
        "members": sorted((dict(row) for row in manifest["members"]), key=lambda row: row["path"]),
    }


def _load_run_members(run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    loaded: dict[str, Mapping[str, Any]] = {}
    known_members = set(MEMBER_PATHS.values()) | {_DOCUMENTATION_MEMBER}
    run_version = manifest.get("schema_version")
    documentation_records = [
        record for record in manifest["members"]
        if record.get("path") == _DOCUMENTATION_MEMBER
    ]
    if (
        run_version not in {"1.0.0", "1.1.0"}
        or (run_version == "1.0.0" and documentation_records)
        or (run_version == "1.1.0" and len(documentation_records) != 1)
    ):
        raise Phase4GraphError("INPUT_INVALID")
    for record in manifest["members"]:
        relative = record["path"]
        if relative not in known_members:
            raise Phase4GraphError("PATH_UNSAFE")
        if relative == _DOCUMENTATION_MEMBER and (
            run_version != "1.1.0"
            or record.get("artifact_kind") != "evidence"
            or record.get("schema_version") != "1.3.0"
        ):
            raise Phase4GraphError("INPUT_INVALID")
        path = run_dir.joinpath(*PurePosixPath(relative).parts)
        _assert_no_link_components(path)
        if _is_link_or_junction(path) or not path.is_file():
            raise Phase4GraphError("INPUT_INVALID")
        try:
            before = _sha256_file(path)
            artifact = load_artifact(path)
            after = _sha256_file(path)
        except (ArtifactValidationError, OSError, UnicodeError, ValueError):
            raise Phase4GraphError("INPUT_INVALID") from None
        if before != record["sha256"] or after != record["sha256"]:
            raise Phase4GraphError("INPUT_CHANGED")
        if artifact.get("artifact_kind") != record["artifact_kind"] or artifact.get("schema_version") != record["schema_version"]:
            raise Phase4GraphError("INPUT_INVALID")
        loaded[relative] = artifact
    return loaded


def _phase4_artifacts(
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    members: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_version = manifest.get("schema_version")
    documentation = members.get(_DOCUMENTATION_MEMBER)
    if run_version == "1.0.0":
        if documentation is not None or any(
            row.get("path") == _DOCUMENTATION_MEMBER for row in manifest.get("members", [])
        ):
            raise Phase4GraphError("INPUT_INVALID")
        graph_schema_version = SCHEMA_VERSION
        evidence_schema_version = EVIDENCE_SCHEMA_VERSION
    elif run_version == "1.1.0":
        if (
            documentation is None
            or documentation.get("artifact_kind") != "evidence"
            or documentation.get("schema_version") != "1.3.0"
            or not any(
                row.get("path") == _DOCUMENTATION_MEMBER
                and row.get("artifact_kind") == "evidence"
                and row.get("schema_version") == "1.3.0"
                for row in manifest.get("members", [])
            )
        ):
            raise Phase4GraphError("INPUT_INVALID")
        graph_schema_version = DOCUMENTATION_SCHEMA_VERSION
        evidence_schema_version = DOCUMENTATION_EVIDENCE_SCHEMA_VERSION
    else:
        raise Phase4GraphError("UNSUPPORTED_VERSION")
    try:
        index = members[_PHASE2_INDEX]
        coverage = members[_PHASE2_COVERAGE]
        profile = members[MEMBER_PATHS["stack_profile"]]
        revision = manifest["repository_revision"]
        snapshot_kind = manifest["snapshot_kind"]
        source_metadata = dict(manifest["source_metadata"])
    except (KeyError, TypeError):
        raise Phase4GraphError("INPUT_INVALID") from None

    for relative, artifact in members.items():
        if artifact.get("repository_revision") != revision:
            raise Phase4GraphError("METADATA_MISMATCH")
        artifact_metadata = artifact.get("source_metadata")
        if artifact_metadata is not None and artifact_metadata != source_metadata:
            raise Phase4GraphError("METADATA_MISMATCH")
    if (
        index.get("project", {}).get("snapshot_kind") != snapshot_kind
        or source_metadata.get("snapshot_kind") != snapshot_kind
        or profile.get("source_metadata") != source_metadata
    ):
        raise Phase4GraphError("METADATA_MISMATCH")

    file_rows = index.get("files")
    coverage_rows = coverage.get("entries")
    if not isinstance(file_rows, list) or not isinstance(coverage_rows, list):
        raise Phase4GraphError("INPUT_INVALID")
    file_by_path: dict[str, Mapping[str, Any]] = {}
    for row in file_rows:
        if not isinstance(row, Mapping):
            raise Phase4GraphError("INPUT_INVALID")
        path = _safe_relative_path(row.get("path"))
        if path in file_by_path:
            raise Phase4GraphError("INPUT_INVALID")
        file_by_path[path] = row
    coverage_by_path: dict[str, Mapping[str, Any]] = {}
    for row in coverage_rows:
        if not isinstance(row, Mapping):
            raise Phase4GraphError("INPUT_INVALID")
        path = _safe_relative_path(row.get("path"))
        if path not in file_by_path or path in coverage_by_path:
            raise Phase4GraphError("INPUT_INVALID")
        coverage_by_path[path] = row

    evidence_members = [
        (relative, artifact)
        for relative, artifact in members.items()
        if relative in _EVIDENCE_MEMBERS
    ]
    evidence_items = _fold_evidence_members(evidence_members)
    evidence_ids = {item["id"] for item in evidence_items}
    documentation_evidence_by_path: dict[str, list[str]] = {}
    if documentation is not None:
        for item in evidence_items:
            if _DOCUMENTATION_MEMBER not in item["source_members"]:
                continue
            locator = item.get("locator")
            if not isinstance(locator, Mapping):
                raise Phase4GraphError("INPUT_INVALID")
            path = _safe_relative_path(locator.get("path"))
            if path not in file_by_path:
                raise Phase4GraphError("SOURCE_PATH_INVALID")
            documentation_evidence_by_path.setdefault(path, []).append(item["id"])
    analysis_members = [
        (relative, artifact)
        for relative, artifact in members.items()
        if relative in _ANALYSIS_VERSIONS
    ]
    facts = _collect_static_facts(analysis_members, set(file_by_path), evidence_ids)

    snapshot_key = canonical_tuple_sha256((revision, snapshot_kind, source_metadata))
    project_id = "PROJ-" + canonical_tuple_sha256(("project", snapshot_key))
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}

    def add_node(node: dict[str, Any]) -> None:
        _insert_unique(nodes_by_id, node["id"], node)

    def add_edge(edge: dict[str, Any]) -> None:
        _insert_unique(edges_by_id, edge["id"], edge)

    project = index.get("project", {})
    add_node({
        "id": project_id,
        "type": "Project",
        "label": project.get("name", "Project"),
        "evidence_ids": [],
        "properties": {
            "snapshot_key": snapshot_key,
            "repository_revision": revision,
            "snapshot_kind": snapshot_kind,
        },
    })

    surfaces: set[str] = set()
    file_surfaces: dict[str, list[str]] = {}
    for path, row in coverage_by_path.items():
        primary = row.get("surface")
        secondary = row.get("secondary_surfaces", [])
        if not isinstance(primary, str) or not isinstance(secondary, list):
            raise Phase4GraphError("INPUT_INVALID")
        surface_names = sorted({primary, *secondary})
        surfaces.update(surface_names)
        file_surfaces[path] = surface_names
    surface_ids: dict[str, str] = {}
    for surface in sorted(surfaces):
        surface_id = "SURFACE-" + canonical_tuple_sha256(("surface", project_id, surface))
        surface_ids[surface] = surface_id
        add_node({
            "id": surface_id,
            "type": "RepositorySurface",
            "label": surface,
            "evidence_ids": [],
            "properties": {"surface": surface},
        })
        add_edge(_containment_edge(project_id, surface_id, _PHASE2_COVERAGE))

    file_ids: dict[str, str] = {}
    for path in sorted(file_by_path):
        row = file_by_path[path]
        file_id = "FILE-" + canonical_tuple_sha256(("file", project_id, path, row["sha256"]))
        file_ids[path] = file_id
        properties: dict[str, Any] = {
            "path": path,
            "tracked": row["tracked"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
        }
        if "content_kind" in row:
            properties["content_kind"] = row["content_kind"]
        if "vcs_object_id" in row:
            properties["vcs_object_id"] = row["vcs_object_id"]
        coverage_row = coverage_by_path.get(path)
        if coverage_row is not None:
            for field in ("surface", "secondary_surfaces", "classification", "teaching_status", "rule_id"):
                if field in coverage_row:
                    properties[field] = coverage_row[field]
        add_node({
            "id": file_id,
            "type": "File",
            "label": path,
            "evidence_ids": sorted(documentation_evidence_by_path.get(path, [])),
            "properties": properties,
        })
        for surface in file_surfaces.get(path, []):
            add_edge(_containment_edge(surface_ids[surface], file_id, _PHASE2_COVERAGE))

    for symbol_id in sorted(facts["symbols"]):
        source_record = facts["symbols"][symbol_id]
        source_record = dict(source_record)
        source_record["evidence_ids"] = sorted(source_record["evidence_ids"])
        roles = sorted(facts["roles"].get(symbol_id, []), key=lambda row: row["id"])
        roles = [
            {**role, "evidence_ids": sorted(role["evidence_ids"])}
            for role in roles
        ]
        source_members = sorted(facts["symbol_members"][symbol_id])
        evidence_for_node = sorted({
            *source_record.get("evidence_ids", []),
            *(identifier for role in roles for identifier in role.get("evidence_ids", [])),
        })
        add_node({
            "id": symbol_id,
            "type": "Symbol",
            "label": source_record["qualified_name"],
            "evidence_ids": evidence_for_node,
            "properties": {
                "source_record": source_record,
                "source_members": source_members,
                "roles": roles,
            },
        })
        path = source_record["path"]
        add_edge(_containment_edge(file_ids[path], symbol_id, _PHASE2_INDEX))

    for relation_id in sorted(facts["relations"]):
        relation = dict(facts["relations"][relation_id])
        relation["evidence_ids"] = sorted(relation["evidence_ids"])
        add_edge({
            "id": relation_id,
            "type": relation["kind"],
            "from": relation["source_id"],
            "to": relation["target_id"],
            "certainty": relation["certainty"],
            "unresolved_target": relation["unresolved_target"],
            "evidence_ids": sorted(relation["evidence_ids"]),
            "source_members": sorted(facts["relation_members"][relation_id]),
            "source_record": relation,
        })

    source_run = _source_run(manifest, manifest_sha256)
    graph = {
        "artifact_kind": "knowledge-graph",
        "schema_version": graph_schema_version,
        "repository_revision": revision,
        "generated_at": manifest["generated_at"],
        "status": manifest["status"],
        "snapshot_kind": snapshot_kind,
        "source_metadata": source_metadata,
        "source_run": source_run,
        "derived_inputs": [],
        "nodes": sorted(nodes_by_id.values(), key=lambda row: (row["type"], row["id"])),
        "edges": sorted(edges_by_id.values(), key=lambda row: (row["type"], row["id"])),
    }
    evidence = {
        "artifact_kind": "evidence",
        "schema_version": evidence_schema_version,
        "repository_revision": revision,
        "generated_at": manifest["generated_at"],
        "status": manifest["status"],
        "snapshot_kind": snapshot_kind,
        "source_metadata": source_metadata,
        "source_run": source_run,
        "derived_inputs": [],
        "items": evidence_items,
    }
    return graph, evidence


def validate_phase4_graph_artifacts(graph: Any, evidence: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Validate Phase 4 graph/evidence schemas, run identity, and references."""
    try:
        graph = validate_artifact(graph)
        evidence = validate_artifact(evidence)
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        raise Phase4GraphError("SCHEMA_INVALID") from None
    common_fields = ("repository_revision", "generated_at", "status", "snapshot_kind", "source_metadata", "source_run", "derived_inputs")
    expected_run_version = _SOURCE_RUN_VERSIONS.get((graph["schema_version"], evidence["schema_version"]))
    if expected_run_version is None:
        raise Phase4GraphError("UNSUPPORTED_VERSION")
    if any(graph[field] != evidence[field] for field in common_fields):
        raise Phase4GraphError("METADATA_MISMATCH")
    source_run = graph["source_run"]
    if source_run["schema_version"] != expected_run_version:
        raise Phase4GraphError("UNSUPPORTED_VERSION")
    if (
        graph["repository_revision"] == ""
        or source_run["status"] != graph["status"]
        or graph["status"] != source_run["e1_audit"]["status"]
    ):
        raise Phase4GraphError("METADATA_MISMATCH")

    manifest_members = {row["path"]: row for row in source_run["members"]}
    if len(manifest_members) != len(source_run["members"]):
        raise Phase4GraphError("INPUT_INVALID")
    documentation_record = manifest_members.get(_DOCUMENTATION_MEMBER)
    if expected_run_version == "1.1.0":
        if (
            documentation_record is None
            or documentation_record["artifact_kind"] != "evidence"
            or documentation_record["schema_version"] != "1.3.0"
        ):
            raise Phase4GraphError("INPUT_INVALID")
    elif documentation_record is not None:
        raise Phase4GraphError("INPUT_INVALID")
    evidence_members = {
        path for path, record in manifest_members.items()
        if record["artifact_kind"] == "evidence"
    }
    analysis_members = {
        path for path, record in manifest_members.items()
        if path in _ANALYSIS_VERSIONS
        and record["artifact_kind"] == "static-analysis"
        and record["schema_version"] in _ANALYSIS_VERSIONS[path]
    }
    proposal_input = None
    if graph["derived_inputs"]:
        if len(graph["derived_inputs"]) != 1:
            raise Phase4GraphError("INPUT_INVALID")
        proposal_input = graph["derived_inputs"][0]
        expected_proposal_version = "1.0.0" if expected_run_version == "1.0.0" else "1.1.0"
        if (
            proposal_input.get("path") != _PROPOSAL_MEMBER
            or proposal_input.get("artifact_kind") != "semantic-proposals"
            or proposal_input.get("schema_version") != expected_proposal_version
        ):
            raise Phase4GraphError("INPUT_INVALID")

    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    e6_by_proposal: dict[str, str] = {}
    documentation_evidence_by_path: dict[str, list[str]] = {}
    for item in evidence["items"]:
        identifier = item["id"]
        if identifier in evidence_by_id:
            raise Phase4GraphError("DUPLICATE_ID_CONFLICT")
        source_members = item["source_members"]
        if source_members != sorted(set(source_members)):
            raise Phase4GraphError("INPUT_INVALID")
        is_documentation_item = _DOCUMENTATION_MEMBER in source_members
        if expected_run_version == "1.1.0":
            if is_documentation_item:
                locator = item["locator"]
                if (
                    source_members != [_DOCUMENTATION_MEMBER]
                    or item["level"] != "E2"
                    or item["kind"] != "repository_documentation"
                    or item["summary"] != "The selected repository documentation span records a repository declaration."
                    or item["confidence"] != 1.0
                    or set(locator) != {"path", "line_start", "line_end"}
                    or locator["line_end"] < locator["line_start"]
                    or not isinstance(item.get("source_bytes"), Mapping)
                ):
                    raise Phase4GraphError("INPUT_INVALID")
                path = _safe_relative_path(locator["path"])
                documentation_evidence_by_path.setdefault(path, []).append(identifier)
            elif item["kind"] == "repository_documentation" or "source_bytes" in item:
                raise Phase4GraphError("INPUT_INVALID")
        elif item["kind"] == "repository_documentation" or "source_bytes" in item:
            raise Phase4GraphError("INPUT_INVALID")
        if proposal_input is None:
            # Preserve the historical pair-coherence contract.  Exact Phase
            # 4A replay still admits only the deterministic E1/E2 fold.
            if not set(source_members) <= evidence_members:
                raise Phase4GraphError("INPUT_INVALID")
        elif item["level"] in {"E1", "E2"}:
            if not set(source_members) <= evidence_members:
                raise Phase4GraphError("INPUT_INVALID")
        elif item["level"] == "E6" and proposal_input is not None:
            locator = item["locator"]
            match = re.fullmatch(r"PROP-[0-9a-f]{64}", locator.get("observation", ""))
            if (
                item["kind"] != "inference"
                or item["confidence"] != 0.0
                or not re.fullmatch(r"EVID-INFERENCE-[0-9a-f]{64}", identifier)
                or locator != {"path": _PROPOSAL_MEMBER, "observation": locator.get("observation")}
                or match is None
                or source_members != [_PROPOSAL_MEMBER]
                or locator["observation"] in e6_by_proposal
            ):
                raise Phase4GraphError("INPUT_INVALID")
            e6_by_proposal[locator["observation"]] = identifier
        else:
            raise Phase4GraphError("INPUT_INVALID")
        evidence_by_id[identifier] = item
    if expected_run_version == "1.1.0" and not documentation_evidence_by_path:
        raise Phase4GraphError("INPUT_INVALID")

    # Graph records share this large ID set; materialize it once, not per record.
    evidence_id_set = set(evidence_by_id)

    nodes: dict[str, Mapping[str, Any]] = {}
    files_by_path: dict[str, str] = {}
    symbol_nodes: dict[str, Mapping[str, Any]] = {}
    proposal_reference_counts: dict[str, int] = {}
    for node in graph["nodes"]:
        identifier = node["id"]
        if identifier in nodes:
            raise Phase4GraphError("DUPLICATE_ID_CONFLICT")
        nodes[identifier] = node
        if not set(node["evidence_ids"]) <= evidence_id_set:
            raise Phase4GraphError("EVIDENCE_MISSING")
        properties = node["properties"]
        if proposal_input is not None:
            e6_ids = [evidence_id for evidence_id in node["evidence_ids"] if evidence_by_id[evidence_id]["level"] == "E6"]
            if e6_ids or node["type"] in _PROVISIONAL_NODE_CATEGORIES:
                if (
                    len(e6_ids) != 1
                    or node["evidence_ids"] != e6_ids
                    or node["type"] not in _PROVISIONAL_NODE_CATEGORIES
                    or set(properties) != {"certainty", "proposal_id", "proposal_category", "source_members"}
                    or node["id"] != properties.get("proposal_id")
                    or not re.fullmatch(r"PROP-[0-9a-f]{64}", node["id"])
                    or properties.get("certainty") != "PROVISIONAL"
                    or properties.get("proposal_category") != _PROVISIONAL_NODE_CATEGORIES[node["type"]]
                    or properties.get("source_members") != [_PROPOSAL_MEMBER]
                ):
                    raise Phase4GraphError("INPUT_INVALID")
                item = evidence_by_id[e6_ids[0]]
                if item["locator"]["observation"] != node["id"] or item["summary"] != node["label"]:
                    raise Phase4GraphError("INPUT_INVALID")
                proposal_reference_counts[node["id"]] = proposal_reference_counts.get(node["id"], 0) + 1
            elif properties.get("certainty") == "PROVISIONAL":
                raise Phase4GraphError("INPUT_INVALID")
        if node["type"] == "File":
            path = _safe_relative_path(node["properties"].get("path"))
            if path in files_by_path:
                raise Phase4GraphError("INPUT_INVALID")
            files_by_path[path] = identifier
        elif node["type"] == "Symbol":
            symbol_nodes[identifier] = node

    documentation_evidence_ids = {
        identifier
        for identifiers in documentation_evidence_by_path.values()
        for identifier in identifiers
    }
    for path, identifiers in documentation_evidence_by_path.items():
        file_id = files_by_path.get(path)
        if file_id is None:
            raise Phase4GraphError("SOURCE_PATH_INVALID")
        file_node = nodes[file_id]
        linked_documentation_ids = set(file_node["evidence_ids"]) & documentation_evidence_ids
        if linked_documentation_ids != set(identifiers):
            raise Phase4GraphError("INPUT_INVALID")
    for node in graph["nodes"]:
        if node["type"] != "File" and set(node["evidence_ids"]) & documentation_evidence_ids:
            raise Phase4GraphError("INPUT_INVALID")
        if node["type"] == "File":
            path = node["properties"].get("path")
            linked_documentation_ids = set(node["evidence_ids"]) & documentation_evidence_ids
            if linked_documentation_ids != set(documentation_evidence_by_path.get(path, [])):
                raise Phase4GraphError("INPUT_INVALID")

    role_ids: set[str] = set()
    for symbol_id, node in symbol_nodes.items():
        properties = node["properties"]
        source = properties.get("source_record")
        source_members = properties.get("source_members")
        roles = properties.get("roles")
        if not isinstance(source, Mapping) or source.get("id") != symbol_id:
            raise Phase4GraphError("INPUT_INVALID")
        if not set(source.get("evidence_ids", [])) <= evidence_id_set:
            raise Phase4GraphError("EVIDENCE_MISSING")
        if proposal_input is not None and any(
            evidence_by_id[evidence_id]["level"] == "E6" for evidence_id in source.get("evidence_ids", [])
        ):
            raise Phase4GraphError("INPUT_INVALID")
        if source.get("path") not in files_by_path:
            raise Phase4GraphError("SOURCE_PATH_INVALID")
        if source_members != sorted(set(source_members or [])) or not set(source_members or []) <= analysis_members:
            raise Phase4GraphError("INPUT_INVALID")
        if not isinstance(roles, list):
            raise Phase4GraphError("INPUT_INVALID")
        for role in roles:
            role_id = role.get("id")
            if role_id in role_ids or role.get("symbol_id") != symbol_id or role.get("kind") not in _ROLE_KINDS:
                raise Phase4GraphError("DUPLICATE_ID_CONFLICT")
            role_ids.add(role_id)
            if role.get("path") not in files_by_path or not set(role.get("evidence_ids", [])) <= evidence_id_set:
                raise Phase4GraphError("EVIDENCE_MISSING")
            if proposal_input is not None and any(
                evidence_by_id[evidence_id]["level"] == "E6" for evidence_id in role.get("evidence_ids", [])
            ):
                raise Phase4GraphError("INPUT_INVALID")

    edges: dict[str, Mapping[str, Any]] = {}
    for edge in graph["edges"]:
        identifier = edge["id"]
        if identifier in edges:
            raise Phase4GraphError("DUPLICATE_ID_CONFLICT")
        edges[identifier] = edge
        if edge["from"] not in nodes or edge["to"] is not None and edge["to"] not in nodes:
            raise Phase4GraphError("SYMBOL_MISSING")
        if not set(edge["evidence_ids"]) <= evidence_id_set:
            raise Phase4GraphError("EVIDENCE_MISSING")
        source_members = edge["source_members"]
        if not source_members or source_members != sorted(set(source_members)):
            raise Phase4GraphError("INPUT_INVALID")
        if edge["type"] == "CONTAINS":
            if (
                edge["source_record"] is not None
                or proposal_input is not None and any(
                    evidence_by_id[evidence_id]["level"] == "E6" for evidence_id in edge["evidence_ids"]
                )
                or any(
                    path not in _PHASE2_MEMBERS
                    or path not in manifest_members
                    or manifest_members[path]["artifact_kind"] != (
                        "project-index" if path == _PHASE2_INDEX else "coverage"
                    )
                    for path in source_members
                )
            ):
                raise Phase4GraphError("INPUT_INVALID")
            continue
        source = edge["source_record"]
        if source is None and proposal_input is not None:
            if (
                edge["type"] not in _LEGACY_PROPOSAL_EDGE_KINDS
                or not re.fullmatch(r"PROP-[0-9a-f]{64}", identifier)
                or edge["certainty"] != "PROVISIONAL"
                or edge["to"] is None
                or edge["unresolved_target"] is not None
                or source_members != [_PROPOSAL_MEMBER]
                or len(edge["evidence_ids"]) != 1
                or evidence_by_id[edge["evidence_ids"][0]]["level"] != "E6"
                or evidence_by_id[edge["evidence_ids"][0]]["locator"]["observation"] != identifier
            ):
                raise Phase4GraphError("INPUT_INVALID")
            proposal_reference_counts[identifier] = proposal_reference_counts.get(identifier, 0) + 1
            continue
        if not isinstance(source, Mapping) or not set(source_members) <= analysis_members:
            raise Phase4GraphError("INPUT_INVALID")
        if proposal_input is not None and any(
            evidence_by_id[evidence_id]["level"] == "E6" for evidence_id in edge["evidence_ids"]
        ):
            raise Phase4GraphError("INPUT_INVALID")
        if (
            source.get("id") != edge["id"]
            or source.get("kind") != edge["type"]
            or source.get("source_id") != edge["from"]
            or source.get("target_id") != edge["to"]
            or source.get("certainty") != edge["certainty"]
            or source.get("unresolved_target") != edge["unresolved_target"]
            or sorted(source.get("evidence_ids", [])) != edge["evidence_ids"]
        ):
            raise Phase4GraphError("INPUT_INVALID")
        if edge["from"] not in symbol_nodes or edge["to"] is not None and edge["to"] not in symbol_nodes:
            raise Phase4GraphError("SYMBOL_MISSING")
        if source.get("path") not in files_by_path:
            raise Phase4GraphError("SOURCE_PATH_INVALID")
    if proposal_input is not None and (
        set(proposal_reference_counts) != set(e6_by_proposal)
        or any(count != 1 for count in proposal_reference_counts.values())
    ):
        raise Phase4GraphError("INPUT_INVALID")
    return graph, evidence


def _publish_directory(stage: Path, output: Path) -> None:
    if os.path.lexists(output):
        raise Phase4GraphError("OUTPUT_EXISTS")
    try:
        os.rename(stage, output)
    except OSError:
        raise Phase4GraphError("OUTPUT_PUBLISH_FAILED") from None


def _summary(graph: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    symbols = [node for node in graph["nodes"] if node["type"] == "Symbol"]
    relations = [edge for edge in graph["edges"] if edge["type"] != "CONTAINS"]
    return {
        "status": graph["status"],
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "files": sum(node["type"] == "File" for node in graph["nodes"]),
        "symbols": len(symbols),
        "relations": len(relations),
        "roles": sum(len(node["properties"].get("roles", [])) for node in symbols),
        "evidence": len(evidence["items"]),
    }


def _project_validated_phase3_run(
    run_dir: str | Path,
    *,
    root: str | Path,
) -> tuple[Path, Path, dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    run_path = Path(run_dir)
    manifest_path = run_path / MANIFEST_NAME
    try:
        _assert_no_link_components(run_path)
        if _is_link_or_junction(run_path) or not run_path.is_dir():
            raise Phase4GraphError("RUN_INVALID")
        if _is_link_or_junction(manifest_path) or not manifest_path.is_file():
            raise Phase4GraphError("RUN_INVALID")
        initial_manifest_sha = _sha256_file(manifest_path)
        manifest = validate_phase3_run(run_path, root=root)
        if _sha256_file(manifest_path) != initial_manifest_sha:
            raise Phase4GraphError("INPUT_CHANGED")
        if not isinstance(manifest.get("members"), list):
            raise Phase4GraphError("INPUT_INVALID")
        loaded = _load_run_members(run_path, manifest)
        graph, evidence = _phase4_artifacts(manifest, initial_manifest_sha, loaded)
        validate_phase4_graph_artifacts(graph, evidence)
        return run_path, manifest_path, manifest, initial_manifest_sha, graph, evidence
    except Phase4GraphError:
        raise
    except Phase3RunError:
        raise Phase4GraphError("RUN_INVALID") from None
    except (ArtifactValidationError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4GraphError("BUILD_FAILED") from None


def _revalidate_phase3_run(
    run_path: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    initial_manifest_sha: str,
    *,
    root: str | Path,
) -> None:
    """Perform the final freshness proof after the initial full Phase3 audit.

    The initial projection path runs ``validate_phase3_run`` for the complete
    semantic audit. This later check revalidates package identities and G01
    snapshot freshness without repeating source-adapter semantic analysis.
    """
    try:
        if _sha256_file(manifest_path) != initial_manifest_sha:
            raise Phase4GraphError("INPUT_CHANGED")
        verify_phase3_run_freshness(
            run_path,
            root=root,
            expected_manifest=manifest,
            expected_manifest_sha256=initial_manifest_sha,
        )
    except Phase4GraphError:
        raise
    except Phase3RunError:
        raise Phase4GraphError("RUN_INVALID") from None
    except (OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4GraphError("BUILD_FAILED") from None


def reproject_phase4_graph_artifacts(
    run_dir: str | Path,
    *,
    root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Revalidate and deterministically re-project Phase 4A without publishing files."""
    run_path, manifest_path, manifest, manifest_sha, graph, evidence = _project_validated_phase3_run(
        run_dir,
        root=root,
    )
    _revalidate_phase3_run(run_path, manifest_path, manifest, manifest_sha, root=root)
    return graph, evidence


def build_phase4_graph(
    run_dir: str | Path,
    *,
    root: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Revalidate, project, and publish the two Phase 4A artifacts."""
    output = _output_path(root, out)
    try:
        run_path, manifest_path, manifest, initial_manifest_sha, graph, evidence = _project_validated_phase3_run(
            run_dir,
            root=root,
        )
        graph_text = dumps_artifact(graph)
        evidence_text = dumps_artifact(evidence)
        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}.phase4-staging-",
            dir=output.parent,
        ) as temporary:
            stage = Path(temporary)
            (stage / "knowledge-graph.json").write_text(graph_text, encoding="utf-8", newline="\n")
            (stage / "evidence.json").write_text(evidence_text, encoding="utf-8", newline="\n")
            _revalidate_phase3_run(run_path, manifest_path, manifest, initial_manifest_sha, root=root)
            output = _output_path(root, output)
            _publish_directory(stage, output)
        return _summary(graph, evidence)
    except Phase4GraphError:
        raise
    except Phase3RunError:
        raise Phase4GraphError("RUN_INVALID") from None
    except (ArtifactValidationError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4GraphError("BUILD_FAILED") from None


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "Phase4GraphError",
    "build_phase4_graph",
    "canonical_tuple_sha256",
    "reproject_phase4_graph_artifacts",
    "validate_phase4_graph_artifacts",
]
