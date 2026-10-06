#!/usr/bin/env python3
"""Source-backed, deterministic referential auditing for Phase 4 claim inventories."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact, validate_artifact
from phase4_graph import (
    Phase4GraphError,
    canonical_tuple_sha256,
    reproject_phase4_graph_artifacts,
    validate_phase4_graph_artifacts,
)
from phase4_claim_graph import (
    Phase4ClaimGraphError,
    project_claim_evidence_graph_4c,
    validate_claim_evidence_graph_4c,
)
from phase4_proposals import (
    Phase4ProposalError,
    reproject_phase4c_artifacts,
    verify_phase4c_source_freshness,
)


CLAIM_SCHEMA_VERSION = "1.0.0"
CLAIM_SCHEMA_VERSION_4C = "1.1.0"
CLAIM_SCHEMA_VERSION_4C_DOCUMENTATION = "1.2.0"
_PHASE4_INPUTS = {
    "knowledge-graph": ("knowledge-graph", "1.1.0"),
    "evidence": ("evidence", "1.2.0"),
}
_PHASE4C_INPUTS_V11 = {
    "evidence": ("evidence.json", "evidence", "1.2.0"),
    "knowledge-graph": ("knowledge-graph.json", "knowledge-graph", "1.1.0"),
    "semantic-proposals": ("semantic-proposals.json", "semantic-proposals", "1.0.0"),
}
_PHASE4C_INPUTS_V12 = {
    "evidence": ("evidence.json", "evidence", "1.4.0"),
    "knowledge-graph": ("knowledge-graph.json", "knowledge-graph", "1.2.0"),
    "semantic-proposals": ("semantic-proposals.json", "semantic-proposals", "1.1.0"),
}
_PHASE4C_PROFILES = {
    CLAIM_SCHEMA_VERSION_4C: {
        "inputs": _PHASE4C_INPUTS_V11,
        "report_version": "1.1.0",
        "overlay_version": "1.0.0",
    },
    CLAIM_SCHEMA_VERSION_4C_DOCUMENTATION: {
        "inputs": _PHASE4C_INPUTS_V12,
        "report_version": "1.2.0",
        "overlay_version": "1.1.0",
    },
}
_PHASE4C_INPUTS = _PHASE4C_INPUTS_V11
_PHASE4C_PACKAGE_FILES = frozenset(record[0] for record in _PHASE4C_INPUTS_V11.values())
_DOCUMENTATION_SOURCE_LIMITS = [
    "SUPPORTED confirms referential coverage only; it does not establish semantic entailment.",
    "E2 repository-documentation evidence supports only what the cited document declares; it does not establish implementation or runtime behavior.",
]
_QUALIFYING_LEVELS = frozenset({"E0", "E1", "E2", "E3", "E4", "E5"})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class _CitationIndex:
    nodes: Mapping[str, Mapping[str, Any]]
    edges: Mapping[str, Mapping[str, Any]]
    items: Mapping[str, Mapping[str, Any]]
    file_paths: frozenset[str]
    symbols: frozenset[str]
    provisional_node_evidence: Mapping[str, frozenset[str]]
    provisional_edge_evidence: Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class _ClaimAuditContext:
    report: dict[str, Any]
    citation_index: _CitationIndex
    claims_path: Path
    claims_sha256: str
    package_dir: Path
    claims_schema_version: str
    input_paths: Mapping[str, Path]
    input_sha256: Mapping[str, str]
    run_dir: Path
    root: Path
    expected_graph: Mapping[str, Any]
    expected_evidence: Mapping[str, Any]


@dataclass(frozen=True)
class AuthenticatedClaimEvidenceContext:
    """Process-local Phase 4C/4E1/4E2 replay result with digest-bound freshness checks."""

    _audit: _ClaimAuditContext
    _report_path: Path
    _report_sha256: str
    _overlay_path: Path
    _overlay_sha256: str
    _graph_bytes: bytes
    _evidence_bytes: bytes
    _report_bytes: bytes
    _overlay_bytes: bytes

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        return json.loads(raw.decode("utf-8"))

    @property
    def graph(self) -> dict[str, Any]:
        return self._decode(self._graph_bytes)

    @property
    def evidence(self) -> dict[str, Any]:
        return self._decode(self._evidence_bytes)

    @property
    def report(self) -> dict[str, Any]:
        return self._decode(self._report_bytes)

    @property
    def overlay(self) -> dict[str, Any]:
        return self._decode(self._overlay_bytes)

    @property
    def source_anchor(self) -> dict[str, Any]:
        graph = self.graph
        return {
            key: graph[key]
            for key in (
                "repository_revision", "generated_at", "snapshot_kind",
                "source_metadata", "source_run", "status",
            )
        }

    @property
    def input_sha256(self) -> dict[str, str]:
        return {
            **self._audit.input_sha256,
            "claim_candidates": self._audit.claims_sha256,
            "claim_evidence": self._report_sha256,
            "claim_evidence_graph": self._overlay_sha256,
        }

    def recheck(self) -> None:
        """Recheck captured inputs and Phase3 snapshot freshness without another full replay."""
        _recheck_claim_audit_context(self._audit)
        _recheck_claim_report_path(self._report_path, self._report_sha256)
        _recheck_claim_report_path(self._overlay_path, self._overlay_sha256)
        try:
            verify_phase4c_source_freshness(
                self._audit.run_dir,
                root=self._audit.root,
                expected_graph=self._audit.expected_graph,
            )
        except Phase4ProposalError as exc:
            raise Phase4ClaimEvidenceError(exc.code) from None


class Phase4ClaimEvidenceError(ValueError):
    """A fixed, redacted failure for a Phase 4 claim audit."""

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
            raise Phase4ClaimEvidenceError("PATH_UNSAFE")


def _regular_file(value: str | Path) -> Path:
    try:
        path = Path(value)
        _assert_no_link_components(path)
        if _is_link_or_junction(path) or not path.is_file():
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        return path.resolve(strict=True)
    except Phase4ClaimEvidenceError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or posix.as_posix() != value
    ):
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_pair_inputs(
    claims_path: Path,
    claims: Mapping[str, Any],
    graph_path: str | Path,
    evidence_path: str | Path,
) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, tuple[Path, str]]]:
    records = claims.get("phase4_inputs")
    if not isinstance(records, list) or len(records) != 2:
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    by_kind: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        kind = record.get("artifact_kind")
        if kind not in _PHASE4_INPUTS or kind in by_kind:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        expected_kind, expected_version = _PHASE4_INPUTS[kind]
        if record.get("schema_version") != expected_version:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        by_kind[kind] = record
    if set(by_kind) != set(_PHASE4_INPUTS):
        raise Phase4ClaimEvidenceError("INPUT_INVALID")

    supplied = {
        "knowledge-graph": _regular_file(graph_path),
        "evidence": _regular_file(evidence_path),
    }
    try:
        claims_parent = claims_path.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
    loaded: dict[str, Mapping[str, Any]] = {}
    verified_inputs: dict[str, tuple[Path, str]] = {}
    for kind, record in by_kind.items():
        relative = _safe_relative_path(record.get("path"))
        recorded_path = _regular_file(claims_parent.joinpath(*PurePosixPath(relative).parts))
        if recorded_path != supplied[kind]:
            raise Phase4ClaimEvidenceError("INPUT_PATH_MISMATCH")
        before = _sha256_file(recorded_path)
        if before != record.get("sha256"):
            raise Phase4ClaimEvidenceError("INPUT_DIGEST_MISMATCH")
        try:
            artifact = load_artifact(recorded_path)
        except (ArtifactValidationError, OSError, UnicodeError, ValueError):
            raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
        after = _sha256_file(recorded_path)
        if before != after:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        expected_kind, expected_version = _PHASE4_INPUTS[kind]
        if artifact.get("artifact_kind") != expected_kind or artifact.get("schema_version") != expected_version:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        loaded[kind] = artifact
        verified_inputs[kind] = (recorded_path, after)
    return loaded["knowledge-graph"], loaded["evidence"], verified_inputs


def _source_identity_matches(
    claims: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    fields = (
        "repository_revision",
        "generated_at",
        "snapshot_kind",
        "source_metadata",
        "source_run",
    )
    if any(claims.get(field) != graph.get(field) for field in fields):
        raise Phase4ClaimEvidenceError("SOURCE_IDENTITY_MISMATCH")
    if any(graph.get(field) != evidence.get(field) for field in fields):
        raise Phase4ClaimEvidenceError("SOURCE_IDENTITY_MISMATCH")


def _verify_source_replay(
    claims: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
    run_dir: str | Path,
    root: str | Path,
) -> None:
    try:
        expected_graph, expected_evidence = reproject_phase4_graph_artifacts(run_dir, root=root)
    except Phase4GraphError as exc:
        raise Phase4ClaimEvidenceError("SOURCE_RUN_INVALID") from exc
    if (
        any(claims.get(field) != expected_graph.get(field) for field in (
            "repository_revision", "generated_at", "snapshot_kind", "source_metadata", "source_run",
        ))
        or graph != expected_graph
        or evidence != expected_evidence
    ):
        raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH")


def _claim_id(snapshot_key: str, category: str, text: str) -> str:
    normalized_text = unicodedata.normalize("NFC", text.strip())
    if not normalized_text:
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    return "CLAIM-" + canonical_tuple_sha256(("claim", snapshot_key, category, normalized_text))


def _unique_claims(claims: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    snapshot_key = canonical_tuple_sha256((
        claims["repository_revision"],
        claims["snapshot_kind"],
        claims["source_metadata"],
    ))
    by_id: dict[str, Mapping[str, Any]] = {}
    for claim in claims["claims"]:
        for citation in claim["citations"]:
            expected_fields = {"kind", "path"} if citation["kind"] == "file" else {"kind", "id"}
            if set(citation) != expected_fields:
                raise Phase4ClaimEvidenceError("INPUT_INVALID")
        expected_id = _claim_id(snapshot_key, claim["category"], claim["text"])
        if claim["id"] != expected_id:
            raise Phase4ClaimEvidenceError("CLAIM_ID_INVALID")
        previous = by_id.get(expected_id)
        if previous is not None and previous != claim:
            raise Phase4ClaimEvidenceError("CLAIM_ID_CONFLICT")
        by_id[expected_id] = claim
    return [by_id[identifier] for identifier in sorted(by_id)]


def _resolve_citations(
    claim: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = {row["id"]: row for row in graph["nodes"]}
    edges = {row["id"]: row for row in graph["edges"]}
    items = {row["id"]: row for row in evidence["items"]}
    file_paths = {
        row.get("properties", {}).get("path")
        for row in graph["nodes"]
        if row.get("type") == "File"
    }
    symbols = {
        row["id"]
        for row in graph["nodes"]
        if row.get("type") == "Symbol"
    }
    results: list[dict[str, Any]] = []
    levels: set[str] = set()

    def add(kind: str, value: str, resolved: bool, reason_code: str) -> None:
        results.append({"kind": kind, "value": value, "resolved": resolved, "reason_code": reason_code})

    for identifier in claim["evidence_ids"]:
        item = items.get(identifier)
        if item is None:
            add("evidence", identifier, False, "EVIDENCE_NOT_FOUND")
        else:
            levels.add(item["level"])
            add("evidence", identifier, True, "OK")
    for identifier in claim["graph_node_ids"]:
        add("node", identifier, identifier in nodes, "OK" if identifier in nodes else "GRAPH_NODE_NOT_FOUND")
    for identifier in claim["graph_edge_ids"]:
        add("edge", identifier, identifier in edges, "OK" if identifier in edges else "GRAPH_EDGE_NOT_FOUND")
    for citation in claim["citations"]:
        if citation["kind"] == "file":
            value = citation["path"]
            resolved = False
            try:
                _safe_relative_path(value)
                resolved = value in file_paths
            except Phase4ClaimEvidenceError:
                pass
            add("file", value, resolved, "OK" if resolved else "FILE_NOT_FOUND")
        else:
            value = citation["id"]
            resolved = value in symbols
            add("symbol", value, resolved, "OK" if resolved else "SYMBOL_NOT_FOUND")
    results.sort(key=lambda row: (row["kind"], row["value"], row["reason_code"]))
    return results, sorted(levels)


def _matrix_claim(claim: Mapping[str, Any], graph: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    citation_results, evidence_levels = _resolve_citations(claim, graph, evidence)
    reason_codes = {row["reason_code"] for row in citation_results if not row["resolved"]}
    has_qualifying_level = bool(_QUALIFYING_LEVELS.intersection(evidence_levels))
    all_resolved = all(row["resolved"] for row in citation_results)
    if has_qualifying_level and all_resolved:
        disposition = "SUPPORTED"
    elif evidence_levels and set(evidence_levels) == {"E6"} and all_resolved:
        disposition = "INFERENCE"
        reason_codes.add("E6_INFERENCE")
    else:
        disposition = "UNVERIFIED"
        if not claim["evidence_ids"]:
            reason_codes.add("NO_QUALIFYING_EVIDENCE")
    return {
        **claim,
        "resolved_evidence_levels": evidence_levels,
        "citation_results": citation_results,
        "disposition": disposition,
        "reason_codes": sorted(reason_codes),
    }


def _phase4c_claim_input_records(claims: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    profile = _PHASE4C_PROFILES.get(claims.get("schema_version"))
    if profile is None:
        raise Phase4ClaimEvidenceError("UNSUPPORTED_VERSION")
    expected_inputs = profile["inputs"]
    records = claims.get("phase4_inputs")
    if not isinstance(records, list) or len(records) != len(expected_inputs):
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    by_kind: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {"path", "artifact_kind", "schema_version", "sha256"}:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        kind = record.get("artifact_kind")
        if kind not in expected_inputs or kind in by_kind:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        expected_path, _expected_kind, expected_version = expected_inputs[kind]
        if record.get("path") != expected_path:
            raise Phase4ClaimEvidenceError("INPUT_PATH_MISMATCH")
        if record.get("schema_version") != expected_version:
            raise Phase4ClaimEvidenceError("UNSUPPORTED_VERSION")
        by_kind[kind] = record
    if set(by_kind) != set(expected_inputs):
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    ordered = sorted(records, key=lambda row: (row["artifact_kind"], row["path"]))
    if records != ordered:
        raise Phase4ClaimEvidenceError("INPUT_INVALID")
    return by_kind


def _regular_package_directory(value: str | Path) -> Path:
    try:
        path = Path(value)
        _assert_no_link_components(path)
        if _is_link_or_junction(path) or not path.is_dir():
            raise Phase4ClaimEvidenceError("PACKAGE_INVALID")
        package = path.resolve(strict=True)
        entries = list(package.iterdir())
        if len(entries) != len(_PHASE4C_PACKAGE_FILES) or {entry.name for entry in entries} != _PHASE4C_PACKAGE_FILES:
            raise Phase4ClaimEvidenceError("PACKAGE_INVALID")
        for name in _PHASE4C_PACKAGE_FILES:
            entry = package / name
            if _is_link_or_junction(entry) or not entry.is_file():
                raise Phase4ClaimEvidenceError("PACKAGE_INVALID")
        return package
    except Phase4ClaimEvidenceError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("PACKAGE_INVALID") from None


def _read_phase4c_package(
    package_dir: str | Path,
    claims: Mapping[str, Any],
) -> tuple[Path, dict[str, Mapping[str, Any]], dict[str, Path], dict[str, str], dict[str, bytes]]:
    package = _regular_package_directory(package_dir)
    records = _phase4c_claim_input_records(claims)
    profile = _PHASE4C_PROFILES[claims["schema_version"]]
    expected_inputs = profile["inputs"]
    artifacts: dict[str, Mapping[str, Any]] = {}
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    raw_inputs: dict[str, bytes] = {}
    for kind, (name, expected_kind, expected_version) in expected_inputs.items():
        path = _regular_file(package / name)
        if path != package / name:
            raise Phase4ClaimEvidenceError("PACKAGE_INVALID")
        raw = path.read_bytes()
        before = hashlib.sha256(raw).hexdigest()
        if before != records[kind].get("sha256"):
            raise Phase4ClaimEvidenceError("INPUT_DIGEST_MISMATCH")
        try:
            artifact = load_artifact(path)
        except (ArtifactValidationError, OSError, UnicodeError, ValueError):
            raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
        if _sha256_file(path) != before:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        if artifact.get("artifact_kind") != expected_kind or artifact.get("schema_version") != expected_version:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        artifacts[kind] = artifact
        paths[kind] = path
        digests[kind] = before
        raw_inputs[kind] = raw
    return package, artifacts, paths, digests, raw_inputs


def _build_citation_index(graph: Mapping[str, Any], evidence: Mapping[str, Any]) -> _CitationIndex:
    nodes = {row["id"]: row for row in graph["nodes"]}
    edges = {row["id"]: row for row in graph["edges"]}
    items = {row["id"]: row for row in evidence["items"]}
    file_paths = frozenset(
        row.get("properties", {}).get("path")
        for row in graph["nodes"]
        if row.get("type") == "File"
    )
    symbols = frozenset(
        row["id"] for row in graph["nodes"] if row.get("type") == "Symbol"
    )
    provisional_node_evidence = {
        identifier: frozenset(
            evidence_id for evidence_id in row.get("evidence_ids", ())
            if items.get(evidence_id, {}).get("level") == "E6"
        )
        for identifier, row in nodes.items()
        if row.get("properties", {}).get("certainty") == "PROVISIONAL"
    }
    provisional_edge_evidence = {
        identifier: frozenset(
            evidence_id for evidence_id in row.get("evidence_ids", ())
            if items.get(evidence_id, {}).get("level") == "E6"
        )
        for identifier, row in edges.items()
        if row.get("certainty") == "PROVISIONAL"
    }
    return _CitationIndex(
        nodes=nodes,
        edges=edges,
        items=items,
        file_paths=file_paths,
        symbols=symbols,
        provisional_node_evidence=provisional_node_evidence,
        provisional_edge_evidence=provisional_edge_evidence,
    )


def _resolve_citations_4c(
    claim: Mapping[str, Any],
    index: _CitationIndex,
) -> tuple[list[dict[str, Any]], list[str], bool, bool]:
    results: list[dict[str, Any]] = []
    levels: set[str] = set()
    cited_evidence_ids = set(claim["evidence_ids"])
    provisional_citation = False
    missing_provisional_evidence = False

    def add(kind: str, value: str, resolved: bool, reason_code: str) -> None:
        results.append({"kind": kind, "value": value, "resolved": resolved, "reason_code": reason_code})

    for identifier in claim["evidence_ids"]:
        item = index.items.get(identifier)
        if item is None:
            add("evidence", identifier, False, "EVIDENCE_NOT_FOUND")
        else:
            levels.add(item["level"])
            add("evidence", identifier, True, "OK")
    for identifier in claim["graph_node_ids"]:
        node = index.nodes.get(identifier)
        resolved = node is not None
        add("node", identifier, resolved, "OK" if resolved else "GRAPH_NODE_NOT_FOUND")
        if identifier in index.provisional_node_evidence:
            provisional_citation = True
            required_evidence = index.provisional_node_evidence[identifier]
            if not required_evidence or not required_evidence.issubset(cited_evidence_ids):
                missing_provisional_evidence = True
    for identifier in claim["graph_edge_ids"]:
        edge = index.edges.get(identifier)
        resolved = edge is not None
        add("edge", identifier, resolved, "OK" if resolved else "GRAPH_EDGE_NOT_FOUND")
        if identifier in index.provisional_edge_evidence:
            provisional_citation = True
            required_evidence = index.provisional_edge_evidence[identifier]
            if not required_evidence or not required_evidence.issubset(cited_evidence_ids):
                missing_provisional_evidence = True
    for citation in claim["citations"]:
        if citation["kind"] == "file":
            value = citation["path"]
            resolved = False
            try:
                _safe_relative_path(value)
                resolved = value in index.file_paths
            except Phase4ClaimEvidenceError:
                pass
            add("file", value, resolved, "OK" if resolved else "FILE_NOT_FOUND")
        else:
            value = citation["id"]
            resolved = value in index.symbols
            add("symbol", value, resolved, "OK" if resolved else "SYMBOL_NOT_FOUND")
    results.sort(key=lambda row: (row["kind"], row["value"], row["reason_code"]))
    return results, sorted(levels), provisional_citation, missing_provisional_evidence


def _matrix_claim_4c(claim: Mapping[str, Any], index: _CitationIndex) -> dict[str, Any]:
    citation_results, evidence_levels, provisional_citation, missing_provisional_evidence = _resolve_citations_4c(claim, index)
    reason_codes = {row["reason_code"] for row in citation_results if not row["resolved"]}
    all_resolved = all(row["resolved"] for row in citation_results)
    if missing_provisional_evidence:
        reason_codes.add("PROVISIONAL_EVIDENCE_NOT_CITED")
    if not all_resolved or missing_provisional_evidence:
        disposition = "UNVERIFIED"
    elif "E6" in evidence_levels or provisional_citation:
        disposition = "INFERENCE"
        reason_codes.add("E6_INFERENCE")
    elif _QUALIFYING_LEVELS.intersection(evidence_levels):
        disposition = "SUPPORTED"
    else:
        disposition = "UNVERIFIED"
        if not claim["evidence_ids"]:
            reason_codes.add("NO_QUALIFYING_EVIDENCE")
    return {
        **claim,
        "resolved_evidence_levels": evidence_levels,
        "citation_results": citation_results,
        "disposition": disposition,
        "reason_codes": sorted(reason_codes),
    }


def _audit_claim_candidates_4c_context(
    claims_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
) -> _ClaimAuditContext:
    try:
        resolved_claims_path = _regular_file(claims_path)
        claims_sha256 = _sha256_file(resolved_claims_path)
        try:
            claims = load_artifact(resolved_claims_path)
        except (ArtifactValidationError, OSError, UnicodeError, ValueError):
            raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
        if _sha256_file(resolved_claims_path) != claims_sha256:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        if (
            claims.get("artifact_kind") != "claim-candidates"
            or claims.get("schema_version") not in _PHASE4C_PROFILES
        ):
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        unique_claims = _unique_claims(claims)
        package, artifacts, input_paths, input_sha256, raw_inputs = _read_phase4c_package(package_dir, claims)
        try:
            expected_graph, expected_evidence = reproject_phase4c_artifacts(
                package / "semantic-proposals.json",
                run_dir=run_dir,
                root=root,
            )
        except Phase4ProposalError as exc:
            raise Phase4ClaimEvidenceError(exc.code) from None

        if (
            raw_inputs["knowledge-graph"] != dumps_artifact(expected_graph).encode("utf-8")
            or raw_inputs["evidence"] != dumps_artifact(expected_evidence).encode("utf-8")
            or artifacts["knowledge-graph"] != expected_graph
            or artifacts["evidence"] != expected_evidence
        ):
            raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH")
        _source_identity_matches(claims, expected_graph, expected_evidence)
        index = _build_citation_index(expected_graph, expected_evidence)
        matrix = [_matrix_claim_4c(claim, index) for claim in unique_claims]
        unresolved_reference = any(
            not result["resolved"]
            for row in matrix
            for result in row["citation_results"]
        )
        missing_provisional_evidence = any(
            "PROVISIONAL_EVIDENCE_NOT_CITED" in row["reason_codes"]
            for row in matrix
        )
        critical_unsupported = any(
            row["critical"] and row["disposition"] != "SUPPORTED"
            for row in matrix
        )
        noncritical_unsupported = any(
            not row["critical"] and row["disposition"] != "SUPPORTED"
            for row in matrix
        )
        if unresolved_reference or missing_provisional_evidence or critical_unsupported:
            status = "FAIL"
        elif noncritical_unsupported:
            status = "PARTIAL"
        else:
            status = "PASS"
        report = {
            "artifact_kind": "claim-evidence",
            "schema_version": _PHASE4C_PROFILES[claims["schema_version"]]["report_version"],
            "repository_revision": claims["repository_revision"],
            "generated_at": claims["generated_at"],
            "snapshot_kind": claims["snapshot_kind"],
            "source_metadata": claims["source_metadata"],
            "source_run": claims["source_run"],
            "phase4_inputs": sorted(
                (dict(record) for record in claims["phase4_inputs"]),
                key=lambda record: (record["artifact_kind"], record["path"]),
            ),
            "audit_status": status,
            "claims": matrix,
        }
        if claims["schema_version"] == CLAIM_SCHEMA_VERSION_4C_DOCUMENTATION:
            report["source_limits"] = list(_DOCUMENTATION_SOURCE_LIMITS)
        validate_artifact(report)
        context = _ClaimAuditContext(
            report=report,
            citation_index=index,
            claims_path=resolved_claims_path,
            claims_sha256=claims_sha256,
            package_dir=package,
            claims_schema_version=claims["schema_version"],
            input_paths=input_paths,
            input_sha256=input_sha256,
            run_dir=Path(run_dir),
            root=Path(root),
            expected_graph=expected_graph,
            expected_evidence=expected_evidence,
        )
        _recheck_claim_audit_context(context)
        return context
    except Phase4ClaimEvidenceError:
        raise
    except (ArtifactValidationError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None


def _recheck_claim_audit_context(context: _ClaimAuditContext) -> None:
    try:
        if _sha256_file(_regular_file(context.claims_path)) != context.claims_sha256:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        package = _regular_package_directory(context.package_dir)
        if package != context.package_dir:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        for kind, path in context.input_paths.items():
            if _regular_file(path) != path or _sha256_file(path) != context.input_sha256[kind]:
                raise Phase4ClaimEvidenceError("INPUT_CHANGED")
    except Phase4ClaimEvidenceError as exc:
        if exc.code == "INPUT_CHANGED":
            raise
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None
    except (OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None


def audit_claim_candidates_4c(
    claims_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
) -> dict[str, Any]:
    """Return the complete source-authenticated matrix without publishing it."""
    return _audit_claim_candidates_4c_context(
        claims_path,
        package_dir=package_dir,
        run_dir=run_dir,
        root=root,
    ).report


def _output_path_4c(root: str | Path, package_dir: str | Path, out: str | Path) -> Path:
    output = _output_path(root, out)
    package = _regular_package_directory(package_dir)
    if output == package or package in output.parents:
        raise Phase4ClaimEvidenceError("OUTPUT_INVALID")
    return output


def _publish_claim_evidence_4c(context: _ClaimAuditContext, out: str | Path) -> Path:
    _output_path_4c(context.root, context.package_dir, out)
    _recheck_claim_audit_context(context)
    try:
        verify_phase4c_source_freshness(
            context.run_dir,
            root=context.root,
            expected_graph=context.expected_graph,
        )
    except Phase4ProposalError as exc:
        raise Phase4ClaimEvidenceError(exc.code) from None
    _recheck_claim_audit_context(context)
    return publish_claim_evidence(context.report, root=context.root, out=out)


def audit_and_publish_claim_evidence_4c(
    claims_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Audit and publish in one context so freshness checks bind the exact inputs read."""
    context = _audit_claim_candidates_4c_context(
        claims_path,
        package_dir=package_dir,
        run_dir=run_dir,
        root=root,
    )
    _publish_claim_evidence_4c(context, out)
    return context.report


def _load_authenticated_claim_report_4c(
    report_path: str | Path,
    context: _ClaimAuditContext,
) -> tuple[Path, str]:
    resolved_path = _regular_file(report_path)
    try:
        before = _sha256_file(resolved_path)
        supplied_report = load_artifact(resolved_path)
        raw = resolved_path.read_bytes()
        after = _sha256_file(resolved_path)
    except ArtifactValidationError:
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
    except (OSError, RuntimeError):
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None
    except (UnicodeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
    if before != after or hashlib.sha256(raw).hexdigest() != after:
        raise Phase4ClaimEvidenceError("INPUT_CHANGED")
    canonical = dumps_artifact(context.report).encode("utf-8")
    if supplied_report != context.report or raw != canonical:
        raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH")
    return resolved_path, after


def _claim_graph_input_digests(
    context: _ClaimAuditContext,
    audit_report_sha256: str,
) -> list[dict[str, str]]:
    profile = _PHASE4C_PROFILES[context.claims_schema_version]
    package_inputs = profile["inputs"]
    inputs = (
        ("base_graph", "knowledge-graph", package_inputs["knowledge-graph"][2], context.input_sha256["knowledge-graph"]),
        ("base_evidence", "evidence", package_inputs["evidence"][2], context.input_sha256["evidence"]),
        ("semantic_proposals", "semantic-proposals", package_inputs["semantic-proposals"][2], context.input_sha256["semantic-proposals"]),
        ("claim_candidates", "claim-candidates", context.claims_schema_version, context.claims_sha256),
        ("claim_evidence", "claim-evidence", profile["report_version"], audit_report_sha256),
    )
    return sorted(
        ({"role": role, "artifact_kind": kind, "schema_version": version, "sha256": digest}
         for role, kind, version, digest in inputs),
        key=lambda record: record["role"],
    )


def _recheck_claim_report_path(path: Path, expected_sha256: str) -> None:
    try:
        if _regular_file(path) != path or _sha256_file(path) != expected_sha256:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
    except Phase4ClaimEvidenceError as exc:
        if exc.code == "INPUT_CHANGED":
            raise
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None
    except (OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None


def _publish_claim_evidence_graph_4c(
    context: _ClaimAuditContext,
    report_path: Path,
    report_sha256: str,
    artifact: Mapping[str, Any],
    out: str | Path,
) -> Path:
    output = _output_path_4c(context.root, context.package_dir, out)
    source_paths = {*context.input_paths.values(), context.claims_path, report_path}
    if output in source_paths:
        raise Phase4ClaimEvidenceError("OUTPUT_INVALID")
    _recheck_claim_audit_context(context)
    _recheck_claim_report_path(report_path, report_sha256)
    try:
        verify_phase4c_source_freshness(
            context.run_dir,
            root=context.root,
            expected_graph=context.expected_graph,
        )
    except Phase4ProposalError as exc:
        raise Phase4ClaimEvidenceError(exc.code) from None
    _recheck_claim_audit_context(context)
    _recheck_claim_report_path(report_path, report_sha256)
    return publish_claim_evidence(artifact, root=context.root, out=out)


def build_claim_evidence_graph_4c(
    claims_path: str | Path,
    audit_report_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Authenticate one Phase 4E1 report and publish its compact 4E2 overlay."""
    context = _audit_claim_candidates_4c_context(
        claims_path,
        package_dir=package_dir,
        run_dir=run_dir,
        root=root,
    )
    resolved_report_path, report_sha256 = _load_authenticated_claim_report_4c(audit_report_path, context)
    input_digests = _claim_graph_input_digests(context, report_sha256)
    try:
        artifact = project_claim_evidence_graph_4c(
            context.expected_graph,
            context.citation_index.items,
            context.report,
            input_digests,
        )
        validate_claim_evidence_graph_4c(
            artifact,
            context.expected_graph,
            context.citation_index.items,
            context.report,
            input_digests,
        )
    except Phase4ClaimGraphError:
        raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH") from None
    _publish_claim_evidence_graph_4c(
        context,
        resolved_report_path,
        report_sha256,
        artifact,
        out,
    )
    return artifact


def authenticate_claim_evidence_bundle_4c(
    claims_path: str | Path,
    audit_report_path: str | Path,
    overlay_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
) -> AuthenticatedClaimEvidenceContext:
    """Replay and authenticate the canonical 4C package, 4E1 report, and 4E2 overlay once."""
    context = _audit_claim_candidates_4c_context(
        claims_path,
        package_dir=package_dir,
        run_dir=run_dir,
        root=root,
    )
    resolved_report_path, report_sha256 = _load_authenticated_claim_report_4c(
        audit_report_path, context,
    )
    input_digests = _claim_graph_input_digests(context, report_sha256)
    try:
        expected_overlay = project_claim_evidence_graph_4c(
            context.expected_graph,
            context.citation_index.items,
            context.report,
            input_digests,
        )
        validate_claim_evidence_graph_4c(
            expected_overlay,
            context.expected_graph,
            context.citation_index.items,
            context.report,
            input_digests,
        )
    except Phase4ClaimGraphError:
        raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH") from None

    overlay_file = _regular_file(overlay_path)
    try:
        overlay_before = _sha256_file(overlay_file)
        supplied_overlay = load_artifact(overlay_file)
        overlay_raw = overlay_file.read_bytes()
        overlay_after = _sha256_file(overlay_file)
    except ArtifactValidationError:
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
    except (OSError, RuntimeError):
        raise Phase4ClaimEvidenceError("INPUT_CHANGED") from None
    except (UnicodeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
    if overlay_before != overlay_after or hashlib.sha256(overlay_raw).hexdigest() != overlay_after:
        raise Phase4ClaimEvidenceError("INPUT_CHANGED")
    if (
        supplied_overlay != expected_overlay
        or overlay_raw != dumps_artifact(expected_overlay).encode("utf-8")
    ):
        raise Phase4ClaimEvidenceError("PROVENANCE_MISMATCH")

    authenticated = AuthenticatedClaimEvidenceContext(
        _audit=context,
        _report_path=resolved_report_path,
        _report_sha256=report_sha256,
        _overlay_path=overlay_file,
        _overlay_sha256=overlay_after,
        _graph_bytes=dumps_artifact(context.expected_graph).encode("utf-8"),
        _evidence_bytes=dumps_artifact(context.expected_evidence).encode("utf-8"),
        _report_bytes=dumps_artifact(context.report).encode("utf-8"),
        _overlay_bytes=dumps_artifact(expected_overlay).encode("utf-8"),
    )
    authenticated.recheck()
    return authenticated


def audit_claim_candidates(
    claims_path: str | Path,
    *,
    graph_path: str | Path,
    evidence_path: str | Path,
    run_dir: str | Path,
    root: str | Path,
) -> dict[str, Any]:
    """Validate source provenance and return the complete deterministic claim matrix."""
    try:
        resolved_claims_path = _regular_file(claims_path)
        claims_digest_before = _sha256_file(resolved_claims_path)
        claims = load_artifact(resolved_claims_path)
        claims_digest_after = _sha256_file(resolved_claims_path)
        if claims_digest_before != claims_digest_after:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        if claims.get("artifact_kind") != "claim-candidates" or claims.get("schema_version") != CLAIM_SCHEMA_VERSION:
            raise Phase4ClaimEvidenceError("INPUT_INVALID")
        graph, evidence, verified_inputs = _load_pair_inputs(
            resolved_claims_path,
            claims,
            graph_path,
            evidence_path,
        )
        try:
            validate_phase4_graph_artifacts(graph, evidence)
        except (ArtifactValidationError, Phase4GraphError, TypeError, ValueError):
            raise Phase4ClaimEvidenceError("INPUT_INVALID") from None
        _source_identity_matches(claims, graph, evidence)
        _verify_source_replay(claims, graph, evidence, run_dir, root)

        matrix = [
            _matrix_claim(claim, graph, evidence)
            for claim in _unique_claims(claims)
        ]
        if _sha256_file(resolved_claims_path) != claims_digest_after:
            raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        for path, expected_digest in verified_inputs.values():
            if _sha256_file(_regular_file(path)) != expected_digest:
                raise Phase4ClaimEvidenceError("INPUT_CHANGED")
        invalid_reference = any(
            not result["resolved"]
            for row in matrix
            for result in row["citation_results"]
        )
        critical_unsupported = any(
            row["critical"] and row["disposition"] != "SUPPORTED"
            for row in matrix
        )
        noncritical_unsupported = any(
            not row["critical"] and row["disposition"] != "SUPPORTED"
            for row in matrix
        )
        if invalid_reference or critical_unsupported:
            status = "FAIL"
        elif noncritical_unsupported:
            status = "PARTIAL"
        else:
            status = "PASS"
        report = {
            "artifact_kind": "claim-evidence",
            "schema_version": CLAIM_SCHEMA_VERSION,
            "repository_revision": claims["repository_revision"],
            "generated_at": claims["generated_at"],
            "snapshot_kind": claims["snapshot_kind"],
            "source_metadata": claims["source_metadata"],
            "source_run": claims["source_run"],
            "phase4_inputs": sorted(
                (dict(record) for record in claims["phase4_inputs"]),
                key=lambda record: (record["artifact_kind"], record["path"]),
            ),
            "audit_status": status,
            "claims": matrix,
        }
        validate_artifact(report)
        return report
    except Phase4ClaimEvidenceError:
        raise
    except (ArtifactValidationError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("INPUT_INVALID") from None


def _output_path(root: str | Path, out: str | Path) -> Path:
    try:
        target_root = Path(root)
        _assert_no_link_components(target_root)
        resolved_root = target_root.resolve(strict=True)
        output = Path(os.path.abspath(os.fspath(out)))
        _assert_no_link_components(output.parent)
        parent = output.parent.resolve(strict=True)
        if not resolved_root.is_dir() or not parent.is_dir():
            raise Phase4ClaimEvidenceError("OUTPUT_INVALID")
        resolved_output = parent / output.name
        if os.path.lexists(resolved_output):
            raise Phase4ClaimEvidenceError("OUTPUT_EXISTS")
        if resolved_output == resolved_root or resolved_root in resolved_output.parents:
            raise Phase4ClaimEvidenceError("OUTPUT_INVALID")
        return resolved_output
    except Phase4ClaimEvidenceError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise Phase4ClaimEvidenceError("OUTPUT_INVALID") from None


def publish_claim_evidence(
    report: Mapping[str, Any],
    *,
    root: str | Path,
    out: str | Path,
) -> Path:
    """Validate and atomically publish a complete report outside the target root."""
    output = _output_path(root, out)
    try:
        validate_artifact(report)
        payload = dumps_artifact(report).encode("utf-8")
        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}.phase4-claim-staging-",
            dir=output.parent,
        ) as temporary:
            staged = Path(temporary) / output.name
            staged.write_bytes(payload)
            try:
                os.link(staged, output)
            except FileExistsError:
                raise Phase4ClaimEvidenceError("OUTPUT_EXISTS") from None
        return output
    except Phase4ClaimEvidenceError:
        raise
    except (ArtifactValidationError, OSError, RuntimeError, TypeError, ValueError):
        raise Phase4ClaimEvidenceError("OUTPUT_FAILED") from None


__all__ = [
    "CLAIM_SCHEMA_VERSION",
    "CLAIM_SCHEMA_VERSION_4C",
    "CLAIM_SCHEMA_VERSION_4C_DOCUMENTATION",
    "Phase4ClaimEvidenceError",
    "AuthenticatedClaimEvidenceContext",
    "audit_claim_candidates",
    "audit_claim_candidates_4c",
    "audit_and_publish_claim_evidence_4c",
    "build_claim_evidence_graph_4c",
    "authenticate_claim_evidence_bundle_4c",
    "publish_claim_evidence",
]
