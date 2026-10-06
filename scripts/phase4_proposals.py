#!/usr/bin/env python3
"""Source-backed deterministic import of provisional Phase 4C proposals."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
from phase4_graph import (
    Phase4GraphError,
    canonical_tuple_sha256,
    reproject_phase4_graph_artifacts,
    validate_phase4_graph_artifacts,
)
from phase3_run import MANIFEST_NAME, Phase3RunError, verify_phase3_run_freshness


PROPOSAL_SCHEMA_VERSION = "1.0.0"
_PROPOSAL_SCHEMA_VERSIONS = frozenset({"1.0.0", "1.1.0"})
_PROPOSAL_PATH = "semantic-proposals.json"
_PHASE4_INPUTS = {
    "evidence": ("phase4a/evidence.json", "evidence"),
    "knowledge-graph": ("phase4a/knowledge-graph.json", "knowledge-graph"),
}
_PROPOSAL_PHASE4_PAIRS = {
    "1.0.0": {"knowledge-graph": "1.1.0", "evidence": "1.2.0"},
    "1.1.0": {"knowledge-graph": "1.2.0", "evidence": "1.4.0"},
}
_NODE_CATEGORIES = {
    "BusinessCapability": "business",
    "BusinessWorkflow": "business",
    "RuntimeWorkflow": "business",
    "Mechanism": "mechanism",
    "Concept": "concept",
}
_EDGE_TYPES = frozenset({
    "CALLS", "CALLED_BY", "DEPENDS_ON", "IMPLEMENTS", "EXTENDS", "USES",
    "ROUTES_TO", "READS", "WRITES", "PERSISTS_TO", "PUBLISHES", "CONSUMES",
    "RENDERS", "TRIGGERS", "PART_OF", "REQUIRES_CONCEPT", "EVIDENCED_BY",
    "TESTED_BY", "EXTENSION_OF", "FAILS_WHEN",
})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class Phase4ProposalError(ValueError):
    """A fixed, redacted failure for Phase 4C proposal import."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
            raise Phase4ProposalError("PATH_UNSAFE")


def _regular_file(value: str | Path) -> Path:
    try:
        path = Path(value)
        _assert_no_link_components(path)
        if _is_link_or_junction(path) or not path.is_file():
            raise Phase4ProposalError("INPUT_INVALID")
        return path.resolve(strict=True)
    except Phase4ProposalError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise Phase4ProposalError("INPUT_INVALID") from None


def _read_bytes(path: Path) -> bytes:
    try:
        if _is_link_or_junction(path) or not path.is_file():
            raise Phase4ProposalError("INPUT_INVALID")
        return path.read_bytes()
    except Phase4ProposalError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Phase4ProposalError("INPUT_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_constant: str) -> None:
    raise Phase4ProposalError("INPUT_INVALID")


def _decode_json(payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except Phase4ProposalError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None


def _parse_artifact(payload: bytes) -> Mapping[str, Any]:
    try:
        return validate_artifact(_decode_json(payload))
    except Phase4ProposalError:
        raise
    except ArtifactValidationError as exc:
        # Keep unsupported semantic-proposals versions distinguishable without
        # exposing validator details to callers.
        if "semantic-proposals" in str(exc) and "schema version" in str(exc):
            raise Phase4ProposalError("UNSUPPORTED_VERSION") from None
        raise Phase4ProposalError("INPUT_INVALID") from None


def _load_proposals(path: Path) -> tuple[Mapping[str, Any], bytes, str]:
    raw = _read_bytes(path)
    value = _decode_json(raw)
    if not isinstance(value, Mapping) or value.get("artifact_kind") != "semantic-proposals":
        raise Phase4ProposalError("INPUT_INVALID")
    if value.get("schema_version") not in _PROPOSAL_SCHEMA_VERSIONS:
        raise Phase4ProposalError("UNSUPPORTED_VERSION")
    try:
        artifact = validate_artifact(value)
    except ArtifactValidationError:
        raise Phase4ProposalError("INPUT_INVALID") from None
    return artifact, raw, _sha256(raw)


def _output_path(root: str | Path, out: str | Path) -> Path:
    try:
        raw_root = Path(root)
        _assert_no_link_components(raw_root)
        project_root = raw_root.resolve(strict=True)
        output = Path(os.path.abspath(os.fspath(out)))
        parent = output.parent
        _assert_no_link_components(parent)
        if not project_root.is_dir() or not parent.is_dir():
            raise Phase4ProposalError("OUTPUT_INVALID")
        if os.path.lexists(output):
            raise Phase4ProposalError("OUTPUT_EXISTS")
        resolved_output = parent.resolve(strict=True) / output.name
        if resolved_output == project_root or project_root in resolved_output.parents:
            raise Phase4ProposalError("OUTPUT_INVALID")
        return resolved_output
    except Phase4ProposalError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise Phase4ProposalError("OUTPUT_INVALID") from None


def _input_records(artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records = artifact.get("phase4_inputs")
    if not isinstance(records, list) or len(records) != 2:
        raise Phase4ProposalError("INPUT_INVALID")
    if any(not isinstance(record, Mapping) for record in records):
        raise Phase4ProposalError("INPUT_INVALID")
    expected_versions = _PROPOSAL_PHASE4_PAIRS.get(artifact.get("schema_version"))
    if expected_versions is None:
        raise Phase4ProposalError("UNSUPPORTED_VERSION")
    seen: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if set(record) != {"path", "artifact_kind", "schema_version", "sha256"}:
            raise Phase4ProposalError("INPUT_INVALID")
        kind = record.get("artifact_kind")
        if kind not in _PHASE4_INPUTS or kind in seen:
            raise Phase4ProposalError("INPUT_INVALID")
        expected_path, _expected_kind = _PHASE4_INPUTS[kind]
        if record.get("path") != expected_path:
            raise Phase4ProposalError("INPUT_PATH_MISMATCH")
        if record.get("schema_version") != expected_versions[kind]:
            raise Phase4ProposalError("UNSUPPORTED_VERSION")
        seen[kind] = record
    if set(seen) != set(_PHASE4_INPUTS) or [record["path"] for record in records] != sorted(
        record["path"] for record in records
    ):
        raise Phase4ProposalError("INPUT_INVALID")
    return seen


def _read_phase4_inputs(
    artifact: Mapping[str, Any],
    graph_path: str | Path,
    evidence_path: str | Path,
) -> tuple[dict[str, bytes], dict[str, str], dict[str, Path]]:
    records = _input_records(artifact)
    supplied = {
        "knowledge-graph": _regular_file(graph_path),
        "evidence": _regular_file(evidence_path),
    }
    raw_by_kind: dict[str, bytes] = {}
    digest_by_kind: dict[str, str] = {}
    path_by_kind: dict[str, Path] = {}
    for kind in ("evidence", "knowledge-graph"):
        raw = _read_bytes(supplied[kind])
        digest = _sha256(raw)
        if digest != records[kind].get("sha256"):
            raise Phase4ProposalError("INPUT_DIGEST_MISMATCH")
        raw_by_kind[kind] = raw
        digest_by_kind[kind] = digest
        path_by_kind[kind] = supplied[kind]
    return raw_by_kind, digest_by_kind, path_by_kind


def _source_replay(run_dir: str | Path, root: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return reproject_phase4_graph_artifacts(run_dir, root=root)
    except Phase4GraphError:
        raise Phase4ProposalError("SOURCE_RUN_INVALID") from None


def _source_freshness_anchor(
    run_dir: str | Path,
    expected_graph: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    """Load the manifest whose raw digest was embedded by the initial replay."""
    source_run = expected_graph.get("source_run")
    manifest_sha256 = source_run.get("manifest_sha256") if isinstance(source_run, Mapping) else None
    if (
        not isinstance(manifest_sha256, str)
        or len(manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in manifest_sha256)
    ):
        raise Phase4ProposalError("SOURCE_RUN_INVALID")
    try:
        manifest_path = _regular_file(Path(run_dir) / MANIFEST_NAME)
        manifest_bytes = _read_bytes(manifest_path)
        manifest = validate_artifact(_decode_json(manifest_bytes))
    except (ArtifactValidationError, Phase4ProposalError, OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ProposalError("SOURCE_RUN_INVALID") from None
    if manifest.get("artifact_kind") != "phase3-run" or _sha256(manifest_bytes) != manifest_sha256:
        raise Phase4ProposalError("SOURCE_RUN_INVALID")
    return manifest, manifest_sha256


def _verify_source_freshness(
    run_dir: str | Path,
    *,
    root: str | Path,
    expected_graph: Mapping[str, Any],
) -> None:
    manifest, manifest_sha256 = _source_freshness_anchor(run_dir, expected_graph)
    try:
        verify_phase3_run_freshness(
            run_dir,
            root=root,
            expected_manifest=manifest,
            expected_manifest_sha256=manifest_sha256,
        )
    except (Phase3RunError, OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ProposalError("SOURCE_RUN_INVALID") from None


def verify_phase4c_source_freshness(
    run_dir: str | Path,
    *,
    root: str | Path,
    expected_graph: Mapping[str, Any],
) -> None:
    """Recheck the original run/root freshness against an already authenticated source anchor."""
    _verify_source_freshness(run_dir, root=root, expected_graph=expected_graph)


def _check_source_identity(
    artifact: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    fields = (
        "repository_revision", "generated_at", "snapshot_kind", "source_metadata", "source_run",
    )
    if any(artifact.get(field) != graph.get(field) for field in fields):
        raise Phase4ProposalError("SOURCE_IDENTITY_MISMATCH")
    if any(graph.get(field) != evidence.get(field) for field in fields):
        raise Phase4ProposalError("SOURCE_IDENTITY_MISMATCH")


def _verify_phase4_pair(
    raw_by_kind: Mapping[str, bytes],
    expected_graph: Mapping[str, Any],
    expected_evidence: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    expected = {
        "knowledge-graph": expected_graph,
        "evidence": expected_evidence,
    }
    parsed: dict[str, Mapping[str, Any]] = {}
    for kind, value in expected.items():
        canonical_bytes = dumps_artifact(value).encode("utf-8")
        raw = raw_by_kind[kind]
        if raw != canonical_bytes:
            raise Phase4ProposalError("PROVENANCE_MISMATCH")
        artifact = _parse_artifact(raw)
        if artifact != value:
            raise Phase4ProposalError("PROVENANCE_MISMATCH")
        parsed[kind] = artifact
    try:
        validate_phase4_graph_artifacts(parsed["knowledge-graph"], parsed["evidence"])
    except (ArtifactValidationError, Phase4GraphError, TypeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None
    return parsed["knowledge-graph"], parsed["evidence"]


def _proposal_rows(
    artifact: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any], str]]:
    snapshot_key = canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))
    by_id: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for proposal in artifact["proposals"]:
        payload = {key: value for key, value in proposal.items() if key != "id"}
        identifier = proposal["id"]
        previous = by_id.get(identifier)
        if previous is not None and previous[1] != payload:
            raise Phase4ProposalError("PROPOSAL_ID_CONFLICT")
        by_id[identifier] = (proposal, payload)

    result: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    for identifier in sorted(by_id):
        proposal, payload = by_id[identifier]
        label = payload.get("label")
        if (
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            or unicodedata.normalize("NFC", label) != label
        ):
            raise Phase4ProposalError("INPUT_INVALID")
        kind = payload.get("kind")
        category = payload.get("category")
        if kind == "node":
            if set(proposal) != {"id", "category", "kind", "label", "node_type"}:
                raise Phase4ProposalError("INPUT_INVALID")
            if _NODE_CATEGORIES.get(payload.get("node_type")) != category:
                raise Phase4ProposalError("INPUT_INVALID")
        elif kind == "edge":
            if set(proposal) != {"id", "category", "kind", "label", "edge_type", "from", "to"}:
                raise Phase4ProposalError("INPUT_INVALID")
            if payload.get("edge_type") not in _EDGE_TYPES:
                raise Phase4ProposalError("INPUT_INVALID")
        else:
            raise Phase4ProposalError("INPUT_INVALID")

        expected_id = "PROP-" + canonical_tuple_sha256(("proposal", snapshot_key, payload))
        if identifier != expected_id:
            raise Phase4ProposalError("PROPOSAL_ID_INVALID")
        evidence_id = "EVID-INFERENCE-" + canonical_tuple_sha256(("inference", snapshot_key, payload))
        result.append((proposal, payload, evidence_id))
    return result


def _collect_existing_ids(graph: Mapping[str, Any], evidence: Mapping[str, Any]) -> set[str]:
    identifiers = {row["id"] for row in graph["nodes"]}
    identifiers.update(row["id"] for row in graph["edges"])
    identifiers.update(row["id"] for row in evidence["items"])
    for node in graph["nodes"]:
        identifiers.update(role["id"] for role in node["properties"].get("roles", []))
    return identifiers


def _add_proposals(
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
    rows: list[tuple[Mapping[str, Any], Mapping[str, Any], str]],
    proposal_sha256: str,
    proposal_schema_version: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    identifiers = _collect_existing_ids(graph, evidence)
    proposal_ids = [proposal["id"] for proposal, _payload, _evidence_id in rows]
    evidence_ids = [evidence_id for _proposal, _payload, evidence_id in rows]
    if (
        len(set(proposal_ids)) != len(proposal_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
        or set(proposal_ids) & set(evidence_ids)
        or set(proposal_ids) & identifiers
        or set(evidence_ids) & identifiers
    ):
        raise Phase4ProposalError("PROPOSAL_ID_CONFLICT")

    node_proposal_ids = {
        proposal["id"] for proposal, payload, _evidence_id in rows if payload["kind"] == "node"
    }
    valid_node_ids = {node["id"] for node in graph["nodes"]} | node_proposal_ids
    for _proposal, payload, _evidence_id in rows:
        if payload["kind"] == "edge" and (
            payload["from"] not in valid_node_ids or payload["to"] not in valid_node_ids
        ):
            raise Phase4ProposalError("REFERENCE_MISSING")

    output_graph = copy.deepcopy(graph)
    output_evidence = copy.deepcopy(evidence)
    output_graph["derived_inputs"] = [{
        "path": _PROPOSAL_PATH,
        "artifact_kind": "semantic-proposals",
        "schema_version": proposal_schema_version,
        "sha256": proposal_sha256,
    }]
    output_evidence["derived_inputs"] = copy.deepcopy(output_graph["derived_inputs"])
    for proposal, payload, evidence_id in rows:
        identifier = proposal["id"]
        output_evidence["items"].append({
            "id": evidence_id,
            "level": "E6",
            "kind": "inference",
            "summary": payload["label"],
            "confidence": 0.0,
            "locator": {"path": _PROPOSAL_PATH, "observation": identifier},
            "source_members": [_PROPOSAL_PATH],
        })
        if payload["kind"] == "node":
            output_graph["nodes"].append({
                "id": identifier,
                "type": payload["node_type"],
                "label": payload["label"],
                "evidence_ids": [evidence_id],
                "properties": {
                    "certainty": "PROVISIONAL",
                    "proposal_id": identifier,
                    "proposal_category": payload["category"],
                    "source_members": [_PROPOSAL_PATH],
                },
            })
        else:
            output_graph["edges"].append({
                "id": identifier,
                "type": payload["edge_type"],
                "from": payload["from"],
                "to": payload["to"],
                "certainty": "PROVISIONAL",
                "unresolved_target": None,
                "evidence_ids": [evidence_id],
                "source_members": [_PROPOSAL_PATH],
                "source_record": None,
            })
    output_graph["nodes"].sort(key=lambda row: (row["type"], row["id"]))
    output_graph["edges"].sort(key=lambda row: (row["type"], row["id"]))
    output_evidence["items"].sort(key=lambda row: row["id"])
    try:
        validate_artifact(output_graph)
        validate_artifact(output_evidence)
        validate_phase4_graph_artifacts(output_graph, output_evidence)
    except (ArtifactValidationError, Phase4GraphError, TypeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None
    return output_graph, output_evidence


def reproject_phase4c_artifacts(
    proposals_path: str | Path,
    *,
    run_dir: str | Path,
    root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate proposal bytes against a full Phase 4A replay and project the 4C pair in memory."""
    try:
        proposals_file = _regular_file(proposals_path)
        artifact, _proposal_bytes, proposal_sha256 = _load_proposals(proposals_file)
        graph, evidence = _source_replay(run_dir, root)
        _check_source_identity(artifact, graph, evidence)

        records = _input_records(artifact)
        base_artifacts = {
            "knowledge-graph": (graph, dumps_artifact(graph).encode("utf-8")),
            "evidence": (evidence, dumps_artifact(evidence).encode("utf-8")),
        }
        for kind, (base_artifact, canonical_bytes) in base_artifacts.items():
            _expected_path, expected_kind = _PHASE4_INPUTS[kind]
            if (
                base_artifact.get("artifact_kind") != expected_kind
                or base_artifact.get("schema_version") != records[kind].get("schema_version")
                or _sha256(canonical_bytes) != records[kind].get("sha256")
            ):
                raise Phase4ProposalError("INPUT_DIGEST_MISMATCH")

        rows = _proposal_rows(artifact, graph)
        return _add_proposals(
            graph,
            evidence,
            rows,
            proposal_sha256,
            artifact["schema_version"],
        )
    except Phase4ProposalError:
        raise
    except (ArtifactValidationError, Phase4GraphError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None


def _recheck_inputs(
    proposals_path: Path,
    proposal_sha256: str,
    input_paths: Mapping[str, Path],
    input_digests: Mapping[str, str],
) -> None:
    if _sha256(_read_bytes(proposals_path)) != proposal_sha256:
        raise Phase4ProposalError("INPUT_CHANGED")
    for kind, path in input_paths.items():
        if _sha256(_read_bytes(path)) != input_digests[kind]:
            raise Phase4ProposalError("INPUT_CHANGED")


def import_semantic_proposals(
    proposals_path: str | Path,
    *,
    graph_path: str | Path,
    evidence_path: str | Path,
    run_dir: str | Path,
    root: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Validate source replay and atomically publish the exact three-file extension package."""
    try:
        proposals_file = _regular_file(proposals_path)
        artifact, proposals_bytes, proposals_digest = _load_proposals(proposals_file)
        raw_inputs, input_digests, input_paths = _read_phase4_inputs(artifact, graph_path, evidence_path)
        expected_graph, expected_evidence = _source_replay(run_dir, root)
        _check_source_identity(artifact, expected_graph, expected_evidence)
        graph, evidence = _verify_phase4_pair(raw_inputs, expected_graph, expected_evidence)
        proposal_rows = _proposal_rows(artifact, graph)
        output_graph, output_evidence = _add_proposals(
            graph, evidence, proposal_rows, proposals_digest, artifact["schema_version"],
        )
        output = _output_path(root, out)

        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}.phase4-proposal-staging-",
            dir=output.parent,
        ) as temporary:
            stage = Path(temporary)
            (stage / "knowledge-graph.json").write_bytes(dumps_artifact(output_graph).encode("utf-8"))
            (stage / "evidence.json").write_bytes(dumps_artifact(output_evidence).encode("utf-8"))
            (stage / _PROPOSAL_PATH).write_bytes(proposals_bytes)
            _recheck_inputs(proposals_file, proposals_digest, input_paths, input_digests)
            _verify_source_freshness(run_dir, root=root, expected_graph=expected_graph)
            # Catch edits that race with the final package/snapshot freshness proof.
            _recheck_inputs(proposals_file, proposals_digest, input_paths, input_digests)
            _output_path(root, output)
            try:
                os.rename(stage, output)
            except OSError:
                if os.path.lexists(output):
                    raise Phase4ProposalError("OUTPUT_EXISTS") from None
                raise Phase4ProposalError("OUTPUT_FAILED") from None
        return {
            "status": output_graph["status"],
            "proposals": len(proposal_rows),
            "nodes": len(output_graph["nodes"]),
            "edges": len(output_graph["edges"]),
            "evidence": len(output_evidence["items"]),
        }
    except Phase4ProposalError:
        raise
    except (ArtifactValidationError, Phase4GraphError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4ProposalError("INPUT_INVALID") from None


__all__ = [
    "PROPOSAL_SCHEMA_VERSION",
    "Phase4ProposalError",
    "import_semantic_proposals",
    "reproject_phase4c_artifacts",
    "verify_phase4c_source_freshness",
]
