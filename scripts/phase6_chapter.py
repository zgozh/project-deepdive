#!/usr/bin/env python3
"""Authenticated Phase 6A fact packets and mechanical Markdown draft checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import unicodedata
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping
from urllib.parse import quote, unquote

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact, validate_artifact
from phase4_graph import canonical_tuple_sha256
from phase4_claim_evidence import (
    AuthenticatedClaimEvidenceContext,
    Phase4ClaimEvidenceError,
    authenticate_claim_evidence_bundle_4c,
)
from phase5_curriculum import Phase5CurriculumError, project_curriculum_outline
from phase5_prerequisites import Phase5PrerequisiteError, project_prerequisite_graph


_PHASE5_FILES = (
    ("prerequisite_candidates", "prerequisite-candidates", "1.0.0"),
    ("prerequisite_graph", "prerequisite-graph", "1.0.0"),
    ("curriculum_candidates", "curriculum-candidates", "1.0.0"),
    ("curriculum", "curriculum", "1.1.0"),
)
_PHASE4_ROLES = (
    ("phase4_base_graph", "knowledge-graph", "1.1.0", "knowledge-graph"),
    ("phase4_base_evidence", "evidence", "1.2.0", "evidence"),
    ("phase4_semantic_proposals", "semantic-proposals", "1.0.0", "semantic-proposals"),
    ("phase4_claim_candidates", "claim-candidates", "1.1.0", "claim_candidates"),
    ("phase4_claim_evidence", "claim-evidence", "1.1.0", "claim_evidence"),
    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.0.0", "claim_evidence_graph"),
)
_PHASE5_ROLES = (
    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.0.0", "prerequisite_candidates"),
    ("phase5_prerequisite_graph", "prerequisite-graph", "1.0.0", "prerequisite_graph"),
    ("phase5_curriculum_candidates", "curriculum-candidates", "1.0.0", "curriculum_candidates"),
    ("phase5_curriculum", "curriculum", "1.1.0", "curriculum"),
)
_DOCUMENTATION_PHASE4_ROLES = (
    ("phase4_base_graph", "knowledge-graph", "1.2.0", "knowledge-graph"),
    ("phase4_base_evidence", "evidence", "1.4.0", "evidence"),
    ("phase4_semantic_proposals", "semantic-proposals", "1.1.0", "semantic-proposals"),
    ("phase4_claim_candidates", "claim-candidates", "1.2.0", "claim_candidates"),
    ("phase4_claim_evidence", "claim-evidence", "1.2.0", "claim_evidence"),
    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.1.0", "claim_evidence_graph"),
)
_DOCUMENTATION_PHASE5_ROLES = (
    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.1.0", "prerequisite_candidates"),
    ("phase5_prerequisite_graph", "prerequisite-graph", "1.1.0", "prerequisite_graph"),
    ("phase5_curriculum_candidates", "curriculum-candidates", "1.1.0", "curriculum_candidates"),
    ("phase5_curriculum", "curriculum", "1.2.0", "curriculum"),
)
_CHAPTER_INPUT_PROFILES = {
    "1.0.0": {"phase4_roles": _PHASE4_ROLES, "phase5_roles": _PHASE5_ROLES},
    "1.1.0": {
        "phase4_roles": _DOCUMENTATION_PHASE4_ROLES,
        "phase5_roles": _DOCUMENTATION_PHASE5_ROLES,
    },
}
_HEADINGS = (
    "Intuition", "Prerequisite", "Project use", "Architecture", "Source",
    "Mechanism", "Failure and debugging", "Extension", "Learning checks",
)
_QUALIFYING_LEVELS = frozenset({"E0", "E1", "E2", "E3", "E4", "E5"})
MAX_EXCERPT_SOURCE_BYTES = 8 * 1024 * 1024
_CHAPTER_V2_MARKER = "<!-- project-deepdive-chapter-format: v2 -->"
_CHAPTER_FORMAT_MARKER_PREFIX = "<!-- project-deepdive-chapter-format:"
_V2_SENTENCE_ENDINGS = frozenset({".", "!", "?", "。", "！", "？"})
_V2_CLAIM_SUFFIX = re.compile(r" \[\^CLAIM-([0-9a-f]{64})\]$")
_V2_SOURCE_LOCATION = re.compile(r"^Source location: (.+):([1-9][0-9]*)-([1-9][0-9]*)$")
_RAW_HTML_START = re.compile(r"<(?:/?[A-Za-z]|[!?])")
MAX_V2_EXCERPT_LINES = 20
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CLAIM_LINE = re.compile(r"^Claim: (.+[.!?]) \[\^CLAIM-([0-9a-f]{64})\]$")
_GENERAL_LINE = re.compile(r"^General: .+[.!?]$")
_FOOTNOTE_LINE = re.compile(r"^\[\^CLAIM-([0-9a-f]{64})\]: (.+)$")
_EXERCISE_LINE = re.compile(
    r"^:::exercise id=(EX-CHAPTER-[0-9a-f]{64}-([a-z][a-z0-9-]{0,63})) "
    r"type=(recall|trace_predict) prerequisites=(none|[a-z][a-z0-9._-]*(?:,[a-z][a-z0-9._-]*)*) "
    r"claims=(none|CLAIM-[0-9a-f]{64}(?:,CLAIM-[0-9a-f]{64})*) "
    r"evidence=(none|EVID-[A-Za-z0-9._:-]+(?:,EVID-[A-Za-z0-9._:-]+)*)$"
)
_EXCERPT_LINE = re.compile(
    r"^<!-- SOURCE-EXCERPT evidence=(EVID-[A-Za-z0-9._:-]+) path=([^ ]+) "
    r"lines=([1-9][0-9]*)-([1-9][0-9]*) sha256=([0-9a-f]{64}) -->$"
)
_FIXED_ERRORS = (
    "MARKDOWN_FORMAT_INVALID",
    "HEADING_ORDER_INVALID",
    "CLAIM_MARKER_INVALID",
    "CLAIM_NOT_SUPPORTED",
    "FOOTNOTE_INVALID",
    "EXERCISE_INVALID",
    "EXERCISE_SYNC_INVALID",
    "EXCERPT_INVALID",
)


class Phase6ChapterError(ValueError):
    """A fixed, redacted Phase 6A input, provenance, or publication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChapterInputPaths:
    phase4c_package: Path
    claim_candidates_path: Path
    claim_evidence_path: Path
    claim_evidence_graph_path: Path
    prerequisite_candidates_path: Path
    prerequisite_graph_path: Path
    curriculum_candidates_path: Path
    curriculum_path: Path
    run_dir: Path
    root: Path


@dataclass(frozen=True)
class _ReadInput:
    path: Path
    raw: bytes
    artifact: Mapping[str, Any]
    sha256: str


@dataclass
class _CaptureReuseScope:
    inputs: ChapterInputPaths | None = None
    bundle: tuple[
        AuthenticatedClaimEvidenceContext,
        dict[str, _ReadInput],
        dict[str, Any],
        dict[str, Any],
    ] | None = None


_CAPTURE_REUSE_SCOPE: ContextVar[_CaptureReuseScope | None] = ContextVar(
    "phase6_chapter_capture_reuse_scope", default=None,
)


@contextmanager
def _authenticated_input_reuse_scope() -> Iterator[None]:
    """Allow one bounded caller batch to reuse a rechecked git-tree input bundle."""
    token = _CAPTURE_REUSE_SCOPE.set(_CaptureReuseScope())
    try:
        yield
    finally:
        _CAPTURE_REUSE_SCOPE.reset(token)


@dataclass(frozen=True)
class AuthenticatedChapterReviewContext:
    """Read-only authenticated view of one complete, immutable Phase 6A draft."""

    _inputs: ChapterInputPaths
    _output_dir: Path
    _authenticated: AuthenticatedClaimEvidenceContext
    _phase5_reads: Mapping[str, _ReadInput]
    _facts: Mapping[str, Any]
    _bank: Mapping[str, Any]
    _status: Mapping[str, Any]
    chapter_raw: bytes
    _facts_raw: bytes
    _packet_raw: bytes
    _bank_raw: bytes
    _status_raw: bytes

    @property
    def facts(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._facts))

    @property
    def bank(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._bank))

    @property
    def repository_revision(self) -> str:
        return self._facts["repository_revision"]

    @property
    def snapshot_kind(self) -> str:
        return self._facts["snapshot_kind"]

    @property
    def source_metadata(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._facts["source_metadata"])

    @property
    def source_status(self) -> str:
        return self._facts["source_status"]

    @property
    def source_run_manifest_sha256(self) -> str:
        return self._facts["source_run_manifest_sha256"]

    @property
    def unknown_files(self) -> int:
        return self._facts["unknown_files"]

    @property
    def input_digests(self) -> list[dict[str, str]]:
        return copy.deepcopy(self._facts["input_digests"])

    @property
    def phase3_member_digests(self) -> list[dict[str, str]]:
        members = self._authenticated.graph["source_run"]["members"]
        return sorted((
            {key: member[key] for key in ("path", "artifact_kind", "schema_version", "sha256")}
            for member in members
        ), key=lambda member: member["path"])

    @property
    def chapter_sha256(self) -> str:
        return _sha256(self.chapter_raw)

    @property
    def facts_sha256(self) -> str:
        return _sha256(self._facts_raw)

    @property
    def bank_sha256(self) -> str:
        return _sha256(self._bank_raw)

    @property
    def status_sha256(self) -> str:
        return _sha256(self._status_raw)

    @property
    def packet_sha256(self) -> str:
        return _sha256(self._packet_raw)

    def read_snapshot_source(self, path: str) -> bytes:
        """Read only a bounded, digest-checked source path from the authenticated snapshot."""
        return _read_snapshot_source(path, facts=self._facts, run_dir=self._inputs.run_dir, root=self._inputs.root)

    def recheck(self) -> None:
        """Recheck input/source freshness and exact Phase 6A bytes without another full replay."""
        try:
            self._authenticated.recheck()
        except Phase4ClaimEvidenceError as exc:
            _fail(exc.code)
        _recheck_inputs(self._phase5_reads)
        expected = {
            "chapter-facts.json": self._facts_raw,
            "writer-packet.md": self._packet_raw,
            "chapter.md": self.chapter_raw,
            "exercise-bank.json": self._bank_raw,
            "chapter-draft-status.json": self._status_raw,
        }
        _check_complete_chapter_directory(self._output_dir, self._inputs)
        for name, raw in expected.items():
            current = _read_review_file(self._output_dir / name)
            if current != raw:
                _fail("INPUT_CHANGED")


def _fail(code: str) -> None:
    raise Phase6ChapterError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _assert_no_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if _is_link(current):
            _fail("INPUT_INVALID")


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _inside(path: Path, base: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(base)))) == os.path.normcase(str(base))
    except ValueError:
        return False


def _read_artifact(path_value: str | Path) -> _ReadInput:
    path = _absolute(path_value)
    _assert_no_link_components(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            _fail("INPUT_INVALID")
        raw = path.read_bytes()
        digest = _sha256(raw)
        artifact = load_artifact(path)
        if _sha256(path.read_bytes()) != digest:
            _fail("INPUT_CHANGED")
    except Phase6ChapterError:
        raise
    except ArtifactValidationError:
        _fail("INPUT_INVALID")
    except (OSError, RuntimeError, UnicodeError, ValueError):
        _fail("INPUT_INVALID")
    return _ReadInput(path, raw, artifact, digest)


def _read_phase5_inputs(inputs: ChapterInputPaths) -> dict[str, _ReadInput]:
    paths = {
        "prerequisite_candidates": inputs.prerequisite_candidates_path,
        "prerequisite_graph": inputs.prerequisite_graph_path,
        "curriculum_candidates": inputs.curriculum_candidates_path,
        "curriculum": inputs.curriculum_path,
    }
    return {role: _read_artifact(paths[role]) for role, _kind, _version in _PHASE5_FILES}


def _input_paths(inputs: ChapterInputPaths) -> tuple[Path, ...]:
    return tuple(_absolute(value) for value in (
        inputs.claim_candidates_path,
        inputs.claim_evidence_path,
        inputs.claim_evidence_graph_path,
        inputs.prerequisite_candidates_path,
        inputs.prerequisite_graph_path,
        inputs.curriculum_candidates_path,
        inputs.curriculum_path,
    ))


def _check_external_output(
    out_dir: str | Path,
    inputs: ChapterInputPaths,
    *,
    must_exist: bool,
) -> Path:
    output = _absolute(out_dir)
    _assert_no_link_components(output.parent)
    if _is_link(output):
        _fail("OUTPUT_INVALID")
    if must_exist:
        if not output.is_dir():
            _fail("OUTPUT_INVALID")
    elif os.path.lexists(output):
        _fail("OUTPUT_EXISTS")
    if not output.parent.is_dir():
        _fail("OUTPUT_INVALID")
    root = _absolute(inputs.root)
    run = _absolute(inputs.run_dir)
    package = _absolute(inputs.phase4c_package)
    _assert_no_link_components(root)
    _assert_no_link_components(run)
    _assert_no_link_components(package)
    try:
        root = root.resolve(strict=True)
        run = run.resolve(strict=True)
        package = package.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("INPUT_INVALID")
    if any(_inside(output, item) for item in (root, run, package)):
        _fail("OUTPUT_INVALID")
    for path in _input_paths(inputs):
        if _inside(path, output) or path == output:
            _fail("OUTPUT_INVALID")
    return output


def _verify_output_directory(
    out_dir: str | Path,
    inputs: ChapterInputPaths,
    facts_path: str | Path,
    markdown_path: str | Path,
) -> tuple[Path, Path, Path]:
    output = _check_external_output(out_dir, inputs, must_exist=True)
    expected = {"chapter-facts.json", "writer-packet.md", "chapter.md"}
    try:
        entries = list(output.iterdir())
    except OSError:
        _fail("OUTPUT_INVALID")
    if {entry.name for entry in entries} != expected or len(entries) != len(expected):
        _fail("OUTPUT_INVALID")
    facts = _absolute(facts_path)
    markdown = _absolute(markdown_path)
    if facts != output / "chapter-facts.json" or markdown != output / "chapter.md":
        _fail("OUTPUT_INVALID")
    for path in (facts, output / "writer-packet.md", markdown):
        _assert_no_link_components(path)
        if _is_link(path) or not path.is_file():
            _fail("OUTPUT_INVALID")
    return output, facts, markdown


def _read_review_file(path: Path) -> bytes:
    _assert_no_link_components(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_link(path):
            _fail("INPUT_INVALID")
        return path.read_bytes()
    except Phase6ChapterError:
        raise
    except OSError:
        _fail("INPUT_INVALID")


def _check_complete_chapter_directory(output: Path, inputs: ChapterInputPaths) -> None:
    _check_external_output(output, inputs, must_exist=True)
    expected = {
        "chapter-facts.json", "writer-packet.md", "chapter.md", "exercise-bank.json",
        "chapter-draft-status.json",
    }
    try:
        entries = list(output.iterdir())
    except OSError:
        _fail("OUTPUT_INVALID")
    if len(entries) != len(expected) or {entry.name for entry in entries} != expected:
        _fail("INPUT_INVALID")
    for entry in entries:
        _assert_no_link_components(entry)
        if _is_link(entry) or not entry.is_file():
            _fail("INPUT_INVALID")


def _recheck_inputs(reads: Mapping[str, _ReadInput]) -> None:
    for value in reads.values():
        current = _read_artifact(value.path)
        if current.raw != value.raw or current.sha256 != value.sha256:
            _fail("INPUT_CHANGED")


def _project_phase5(
    reads: Mapping[str, _ReadInput],
    authenticated: AuthenticatedClaimEvidenceContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prerequisite_candidates = reads["prerequisite_candidates"]
    prerequisite_graph = reads["prerequisite_graph"]
    curriculum_candidates = reads["curriculum_candidates"]
    curriculum = reads["curriculum"]
    graph = authenticated.graph
    evidence = authenticated.evidence
    try:
        expected_prerequisite_graph = project_prerequisite_graph(
            prerequisite_candidates.artifact,
            graph=graph,
            evidence=evidence,
            candidate_sha256=prerequisite_candidates.sha256,
        )
        if (
            prerequisite_graph.raw != dumps_artifact(expected_prerequisite_graph).encode("utf-8")
            or prerequisite_graph.artifact != expected_prerequisite_graph
        ):
            _fail("PROVENANCE_MISMATCH")
        expected_curriculum = project_curriculum_outline(
            curriculum_candidates.artifact,
            graph=graph,
            evidence=evidence,
            prerequisite_graph=expected_prerequisite_graph,
            candidate_sha256=curriculum_candidates.sha256,
        )
        if (
            curriculum.raw != dumps_artifact(expected_curriculum).encode("utf-8")
            or curriculum.artifact != expected_curriculum
        ):
            _fail("PROVENANCE_MISMATCH")
    except Phase6ChapterError:
        raise
    except Phase5PrerequisiteError as exc:
        _fail(exc.code)
    except Phase5CurriculumError as exc:
        _fail(exc.code)
    except (ArtifactValidationError, KeyError, OSError, TypeError, ValueError):
        _fail("INPUT_INVALID")
    return expected_prerequisite_graph, expected_curriculum


def _source_refs(
    *,
    graph_node_ids=(),
    graph_edge_ids=(),
    evidence_ids=(),
    file_paths=(),
) -> dict[str, list[str]]:
    return {
        "graph_node_ids": sorted(set(graph_node_ids)),
        "graph_edge_ids": sorted(set(graph_edge_ids)),
        "evidence_ids": sorted(set(evidence_ids)),
        "file_paths": sorted(set(file_paths)),
    }


def _safe_relative_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _project_refs_for_claim(claim: Mapping[str, Any]) -> dict[str, list[str]]:
    nodes = set(claim.get("graph_node_ids", ()))
    edges = set(claim.get("graph_edge_ids", ()))
    evidence_ids = set(claim.get("evidence_ids", ()))
    paths: set[str] = set()
    for citation in claim.get("citations", ()):
        if citation.get("kind") == "symbol":
            nodes.add(citation["id"])
        elif citation.get("kind") == "file":
            paths.add(citation["path"])
    return _source_refs(
        graph_node_ids=nodes,
        graph_edge_ids=edges,
        evidence_ids=evidence_ids,
        file_paths=paths,
    )


def _phase4_artifacts_by_role(
    authenticated: AuthenticatedClaimEvidenceContext,
) -> dict[str, Mapping[str, Any]]:
    graph = authenticated.graph
    evidence = authenticated.evidence
    report = authenticated.report
    overlay = authenticated.overlay
    proposal_inputs = [
        row for row in graph.get("derived_inputs", [])
        if isinstance(row, Mapping) and row.get("artifact_kind") == "semantic-proposals"
    ]
    overlay_inputs: dict[str, Mapping[str, Any]] = {}
    for row in overlay.get("input_digests", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("role"), str):
            _fail("PROVENANCE_MISMATCH")
        role = row["role"]
        if role in overlay_inputs:
            _fail("PROVENANCE_MISMATCH")
        overlay_inputs[role] = row
    if len(proposal_inputs) != 1:
        _fail("PROVENANCE_MISMATCH")
    return {
        "phase4_base_graph": graph,
        "phase4_base_evidence": evidence,
        "phase4_semantic_proposals": proposal_inputs[0],
        "phase4_claim_candidates": overlay_inputs.get("claim_candidates", {}),
        "phase4_claim_evidence": report,
        "phase4_claim_evidence_graph": overlay,
    }


def _authenticated_input_profile(
    reads: Mapping[str, _ReadInput],
    authenticated: AuthenticatedClaimEvidenceContext,
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]:
    candidate = reads.get("curriculum_candidates")
    if candidate is None or candidate.artifact.get("artifact_kind") != "curriculum-candidates":
        _fail("PROVENANCE_MISMATCH")
    profile = _CHAPTER_INPUT_PROFILES.get(candidate.artifact.get("schema_version"))
    if profile is None:
        _fail("PROVENANCE_MISMATCH")

    phase4_artifacts = _phase4_artifacts_by_role(authenticated)
    phase4_sha = authenticated.input_sha256
    for role, kind, version, lookup_key in profile["phase4_roles"]:
        artifact = phase4_artifacts[role]
        digest = phase4_sha.get(lookup_key)
        if (
            (artifact.get("artifact_kind"), artifact.get("schema_version")) != (kind, version)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            _fail("PROVENANCE_MISMATCH")
    for _role, kind, version, lookup_key in profile["phase5_roles"]:
        phase5_read = reads.get(lookup_key)
        digest = phase5_read.sha256 if phase5_read is not None else None
        if (
            phase5_read is None
            or (phase5_read.artifact.get("artifact_kind"), phase5_read.artifact.get("schema_version")) != (kind, version)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            _fail("PROVENANCE_MISMATCH")
    return profile, phase4_artifacts


def _authenticated_input_digest_records(
    reads: Mapping[str, _ReadInput],
    authenticated: AuthenticatedClaimEvidenceContext,
) -> list[dict[str, str]]:
    """Return actual role-sorted versions and digests for one exact Phase 4/5 profile."""
    profile, phase4_artifacts = _authenticated_input_profile(reads, authenticated)
    phase4_sha = authenticated.input_sha256
    records = [
        {
            "role": role,
            "artifact_kind": phase4_artifacts[role]["artifact_kind"],
            "schema_version": phase4_artifacts[role]["schema_version"],
            "sha256": phase4_sha[lookup_key],
        }
        for role, _kind, _version, lookup_key in profile["phase4_roles"]
    ]
    records.extend(
        {
            "role": role,
            "artifact_kind": reads[lookup_key].artifact["artifact_kind"],
            "schema_version": reads[lookup_key].artifact["schema_version"],
            "sha256": reads[lookup_key].sha256,
        }
        for role, _kind, _version, lookup_key in profile["phase5_roles"]
    )
    return sorted(records, key=lambda record: record["role"])


def _project_claim_facts(
    inputs: ChapterInputPaths,
    unit_id: str,
    reads: Mapping[str, _ReadInput],
    prerequisite_graph: Mapping[str, Any],
    curriculum: Mapping[str, Any],
    authenticated: AuthenticatedClaimEvidenceContext,
) -> dict[str, Any]:
    units = {unit["id"]: unit for unit in curriculum["units"]}
    unit = units.get(unit_id)
    if unit is None:
        _fail("UNIT_NOT_FOUND")
    if unit["scope"] != "PROJECT_SPECIFIC":
        _fail("UNIT_SCOPE_INVALID")

    refs = unit["project_refs"]
    declared = {key: set(refs[key]) for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")}
    matrix_by_id = {row["id"]: row for row in authenticated.report["claims"]}
    selected: list[tuple[Mapping[str, Any], dict[str, list[str]]]] = []
    for claim in authenticated.report["claims"]:
        claim_refs = _project_refs_for_claim(claim)
        overlaps = any(
            set(claim_refs[key]) & declared[key]
            for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids")
        )
        if not overlaps:
            continue
        if any(set(claim_refs[key]) - declared[key] for key in declared):
            _fail("CLAIM_SCOPE_AMBIGUOUS")
        selected.append((claim, claim_refs))
    selected.sort(key=lambda pair: pair[0]["id"])
    if not any(
        claim["disposition"] == "SUPPORTED"
        and set(claim["resolved_evidence_levels"]) & _QUALIFYING_LEVELS
        and "E6" not in claim["resolved_evidence_levels"]
        for claim, _ in selected
    ):
        _fail("CLAIM_SET_UNSUPPORTED")

    snapshot_key = canonical_tuple_sha256((
        curriculum["repository_revision"], curriculum["snapshot_kind"], curriculum["source_metadata"],
    ))
    selected_claim_ids = sorted(claim["id"] for claim, _ in selected)
    chapter_id = "CHAPTER-" + canonical_tuple_sha256((
        "phase6-chapter", snapshot_key, unit_id, selected_claim_ids,
    ))
    claims_out: list[dict[str, Any]] = []
    used_evidence: set[str] = set()
    for claim, claim_refs in selected:
        source_claim = matrix_by_id[claim["id"]]
        source_text = source_claim["text"]
        normalized_text = unicodedata.normalize("NFC", source_text.strip())
        claims_out.append({
            "id": source_claim["id"],
            "statement_sha256": _sha256(normalized_text.encode("utf-8")),
            "category": source_claim["category"],
            "critical": source_claim["critical"],
            "disposition": source_claim["disposition"],
            "reason_codes": sorted(source_claim["reason_codes"]),
            "resolved_evidence_levels": sorted(set(source_claim["resolved_evidence_levels"])),
            "evidence_ids": list(claim_refs["evidence_ids"]),
            "graph_node_ids": list(claim_refs["graph_node_ids"]),
            "graph_edge_ids": list(claim_refs["graph_edge_ids"]),
            "file_paths": list(claim_refs["file_paths"]),
        })
        used_evidence.update(claim_refs["evidence_ids"])

    evidence_by_id = {item["id"]: item for item in authenticated.evidence["items"]}
    evidence_out: list[dict[str, Any]] = []
    for evidence_id in sorted(used_evidence):
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        locator = item.get("locator", {})
        safe_locator = {
            key: locator[key]
            for key in ("path", "symbol", "line_start", "line_end")
            if key in locator
        }
        if "path" in safe_locator and not _safe_relative_path(safe_locator["path"]):
            _fail("SOURCE_LOCATOR_INVALID")
        if "line_start" in safe_locator and "line_end" in safe_locator and safe_locator["line_end"] < safe_locator["line_start"]:
            _fail("SOURCE_LOCATOR_INVALID")
        evidence_out.append({
            "id": item["id"],
            "level": item["level"],
            "provisional": item["level"] == "E6",
            "locator": safe_locator,
            "source_members": sorted(set(item["source_members"])),
        })

    digest_records = _authenticated_input_digest_records(reads, authenticated)
    source_metadata = curriculum["source_metadata"]
    facts = {
        "artifact_kind": "chapter-facts",
        "schema_version": "1.0.0",
        "repository_revision": curriculum["repository_revision"],
        "generated_at": curriculum["generated_at"],
        "snapshot_kind": curriculum["snapshot_kind"],
        "source_metadata": source_metadata,
        "source_status": curriculum["source_status"],
        "source_run_manifest_sha256": curriculum["source_run_manifest_sha256"],
        "unknown_files": source_metadata["unknown_files"],
        "input_digests": digest_records,
        "selected_unit": {
            "curriculum_unit_id": unit["id"],
            "chapter_id": chapter_id,
            "stage": unit["kind"],
            "title": unit["title"],
            "scope": unit["scope"],
            "prerequisite_concept_keys": sorted(set(unit["requires_concept_keys"])),
            "project_refs": _source_refs(**refs),
        },
        "claims": claims_out,
        "evidence": evidence_out,
    }
    try:
        validate_artifact(facts)
    except ArtifactValidationError:
        _fail("INPUT_INVALID")
    return facts


def _writer_packet(facts: Mapping[str, Any]) -> str:
    unit = facts["selected_unit"]
    lines = [
        "# Phase 6A Writer Packet",
        "",
        "Status: DRAFT only. Referential coverage is not semantic entailment.",
        f"Chapter: {unit['chapter_id']}",
        f"Curriculum unit: {unit['curriculum_unit_id']} — {unit['title']}",
        "Snapshot key: " + canonical_tuple_sha256((
            facts["repository_revision"], facts["snapshot_kind"], facts["source_metadata"],
        )),
        f"Repository revision: {facts['repository_revision']}",
        f"Snapshot kind: {facts['snapshot_kind']}",
        f"Phase3 manifest SHA-256: {facts['source_run_manifest_sha256']}",
        f"Source status: {facts['source_status']}; unknown tracked files: {facts['unknown_files']}",
        "",
        "## Allowed source selection",
    ]
    if facts["snapshot_kind"] == "git-tree":
        lines.append(
            "Inspect only the listed referenced bytes through the Git object reader at the exact repository revision above; never read the current worktree."
        )
    else:
        lines.append("Inspect only the listed relative locators from the authenticated worktree snapshot; do not follow links or run target code.")
    for claim in facts["claims"]:
        lines.append(
            f"- {claim['id']}: {claim['disposition']}; critical={str(claim['critical']).lower()}; "
            f"levels={','.join(claim['resolved_evidence_levels']) or 'none'}; "
            f"evidence={','.join(claim['evidence_ids']) or 'none'}"
        )
    for item in facts["evidence"]:
        locator = item["locator"]
        location = locator.get("path", "no relative path")
        if "symbol" in locator:
            location += f"#{locator['symbol']}"
        if "line_start" in locator and "line_end" in locator:
            location += f":{locator['line_start']}-{locator['line_end']}"
        lines.append(f"- {item['id']} ({item['level']}, provisional={str(item['provisional']).lower()}): {location}")
    eligible_claims = [
        claim for claim in facts["claims"]
        if claim["disposition"] == "SUPPORTED"
        and set(claim["resolved_evidence_levels"]) & _QUALIFYING_LEVELS
        and "E6" not in claim["resolved_evidence_levels"]
    ]
    if eligible_claims:
        lines.extend(["", "## Canonical footnote definitions (eligible claims only)"])
        lines.extend(_canonical_footnote(facts, claim["id"]) for claim in eligible_claims)
    lines.extend([
        "",
        "Use the checked-in prompt at `skills/project-deepdive/prompts/phase6a-markdown-chapter-writer.md`.",
        "The exact authenticated claim-candidates input may be read for its user-supplied statement text; never copy that text into sidecars.",
        "Copy only an eligible claim's exact canonical footnote definition from the packet's canonical footnote section; do not derive or edit its JSON.",
        "Use the required headings and line grammar from the prompt. A `Claim:` line may use only a SUPPORTED claim with E0–E5 and no E6.",
        "E6 is inference, and unresolved or unsupported claims are not project facts. Prefer the fixed evidence-gap callout.",
        "Learning checks use only question blocks; provide no answers and do not ask the learner to respond.",
        "",
    ])
    return "\n".join(lines)


def _load_facts_for_write(path: Path, expected: Mapping[str, Any]) -> bytes:
    read = _read_artifact(path)
    canonical = dumps_artifact(expected).encode("utf-8")
    if read.raw != canonical or read.artifact != expected:
        _fail("PROVENANCE_MISMATCH")
    return read.raw


def _check_prepare_output(out_dir: Path, inputs: ChapterInputPaths) -> None:
    output = _check_external_output(out_dir, inputs, must_exist=False)
    if output != _absolute(out_dir):
        _fail("OUTPUT_INVALID")


def _publish_prepare_files(output: Path, files: Mapping[str, bytes]) -> None:
    created: list[tuple[Path, int, int]] = []
    made_directory = False
    try:
        with tempfile.TemporaryDirectory(prefix=".phase6a-staging-", dir=output.parent) as temporary:
            stage_dir = Path(temporary)
            staged: dict[str, Path] = {}
            for name, payload in files.items():
                path = stage_dir / name
                path.write_bytes(payload)
                staged[name] = path
            output.mkdir()
            made_directory = True
            for name, source in staged.items():
                destination = output / name
                os.link(source, destination)
                info = destination.stat()
                created.append((destination, info.st_dev, info.st_ino))
    except Phase6ChapterError:
        raise
    except FileExistsError:
        _fail("OUTPUT_EXISTS")
    except OSError:
        for path, device, inode in created:
            try:
                info = path.stat()
                if info.st_dev == device and info.st_ino == inode:
                    path.unlink()
            except OSError:
                pass
        if made_directory:
            try:
                output.rmdir()
            except OSError:
                pass
        _fail("OUTPUT_FAILED")


def _unlink_if_owned(path: Path, owner: tuple[int, int]) -> None:
    try:
        info = path.lstat()
        if (info.st_dev, info.st_ino) == owner:
            path.unlink()
    except OSError:
        pass


def _publish_new_file(path: Path, payload: bytes) -> tuple[int, int]:
    owner: tuple[int, int] | None = None
    linked = False
    try:
        with tempfile.TemporaryDirectory(prefix=f".{path.name}.phase6a-staging-", dir=path.parent) as temporary:
            staged = Path(temporary) / path.name
            staged.write_bytes(payload)
            staged_info = staged.stat()
            try:
                os.link(staged, path)
                linked = True
                owner = (staged_info.st_dev, staged_info.st_ino)
            except FileExistsError:
                _fail("OUTPUT_EXISTS")
            destination_info = path.lstat()
            if (destination_info.st_dev, destination_info.st_ino) != owner:
                _fail("OUTPUT_FAILED")
        assert owner is not None
        return owner
    except Phase6ChapterError:
        if linked and owner is not None:
            _unlink_if_owned(path, owner)
        raise
    except OSError:
        if linked and owner is not None:
            _unlink_if_owned(path, owner)
        _fail("OUTPUT_FAILED")


def _all_path_values(inputs: ChapterInputPaths) -> tuple[Path, ...]:
    return (_absolute(inputs.root), _absolute(inputs.run_dir), _absolute(inputs.phase4c_package), *_input_paths(inputs))


def _capture_bundle(
    inputs: ChapterInputPaths,
) -> tuple[AuthenticatedClaimEvidenceContext, dict[str, _ReadInput], dict[str, Any], dict[str, Any]]:
    scope = _CAPTURE_REUSE_SCOPE.get()
    if scope is not None and scope.inputs == inputs and scope.bundle is not None:
        authenticated, phase5_reads, prerequisite_graph, curriculum = scope.bundle
        try:
            authenticated.recheck()
        except Phase4ClaimEvidenceError as exc:
            _fail(exc.code)
        _recheck_inputs(phase5_reads)
        return (
            authenticated,
            copy.deepcopy(phase5_reads),
            copy.deepcopy(prerequisite_graph),
            copy.deepcopy(curriculum),
        )
    if scope is not None:
        scope.inputs = inputs
        scope.bundle = None

    phase5_reads = _read_phase5_inputs(inputs)
    try:
        authenticated = authenticate_claim_evidence_bundle_4c(
            inputs.claim_candidates_path,
            inputs.claim_evidence_path,
            inputs.claim_evidence_graph_path,
            package_dir=inputs.phase4c_package,
            run_dir=inputs.run_dir,
            root=inputs.root,
        )
    except Phase4ClaimEvidenceError as exc:
        _fail(exc.code)
    prerequisite_graph, curriculum = _project_phase5(phase5_reads, authenticated)
    bundle = authenticated, phase5_reads, prerequisite_graph, curriculum
    if scope is not None and authenticated.source_anchor["snapshot_kind"] == "git-tree":
        scope.inputs = inputs
        scope.bundle = bundle
        return (
            authenticated,
            copy.deepcopy(phase5_reads),
            copy.deepcopy(prerequisite_graph),
            copy.deepcopy(curriculum),
        )
    return bundle


def prepare_chapter_facts(
    inputs: ChapterInputPaths,
    *,
    unit_id: str,
    out_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Authenticate the source and publish only facts plus the explicit Writer packet."""
    _check_prepare_output(out_dir, inputs)
    authenticated, phase5_reads, prerequisite_graph, curriculum = _capture_bundle(inputs)
    facts = _project_claim_facts(inputs, unit_id, phase5_reads, prerequisite_graph, curriculum, authenticated)
    packet = _writer_packet(facts)
    fact_bytes = dumps_artifact(facts).encode("utf-8")
    packet_bytes = packet.encode("utf-8")
    authenticated.recheck()
    _recheck_inputs(phase5_reads)
    _check_prepare_output(out_dir, inputs)
    output = _absolute(out_dir)
    _publish_prepare_files(output, {"chapter-facts.json": fact_bytes, "writer-packet.md": packet_bytes})
    return facts, packet


def _canonical_footnote(facts: Mapping[str, Any], claim_id: str) -> str:
    claim = next((row for row in facts["claims"] if row["id"] == claim_id), None)
    if claim is None:
        _fail("CLAIM_MARKER_INVALID")
    evidence_by_id = {item["id"]: item for item in facts["evidence"]}
    cited = []
    for evidence_id in claim["evidence_ids"]:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        locator = {
            key: item["locator"][key]
            for key in ("path", "symbol", "line_start", "line_end")
            if key in item["locator"]
        }
        cited.append({"id": evidence_id, "level": item["level"], "locator": locator})
    payload = json.dumps({"evidence": cited}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[^CLAIM-{claim_id.removeprefix('CLAIM-')}]: {payload}"


def _safe_path_from_marker(encoded_path: str) -> str:
    path = unquote(encoded_path)
    if quote(path, safe="/-._~") != encoded_path or not _safe_relative_path(path):
        _fail("EXCERPT_INVALID")
    return path


def _parse_v2_prose_line(line: str) -> tuple[str, str | None] | None:
    """Classify one v2 paragraph or top-level bullet without normalizing its bytes."""
    if not line or line != line.strip():
        return None
    if (
        line.startswith(("* ", "+ "))
        or re.match(r"^[0-9]+[.)] ", line) is not None
        or (line.startswith("-") and not line.startswith("- "))
    ):
        return None
    content = line[2:] if line.startswith("- ") else line
    if not content or content != content.strip():
        return None
    if content.startswith(("#", ":::", "<!--", ">", "|", "\x60\x60\x60", "~~~")):
        return None
    if _RAW_HTML_START.search(content) is not None:
        return None

    match = _V2_CLAIM_SUFFIX.search(content)
    claim_id = "CLAIM-" + match.group(1) if match is not None else None
    prose = content[:match.start()] if match is not None else content
    if "[^" in prose or (match is None and "[^" in content):
        return None
    if not prose or prose[-1] not in _V2_SENTENCE_ENDINGS:
        return None
    return ("CLAIM", claim_id) if claim_id is not None else ("GENERAL", None)


def _read_git_object_bounded(root: Path, revision: str, path: str) -> bytes:
    try:
        process = subprocess.Popen(
            ["git", "-C", str(root), "show", f"{revision}:{path}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError:
        _fail("EXCERPT_INVALID")
    assert process.stdout is not None
    output = bytearray()
    exceeded_limit = threading.Event()
    read_failed = threading.Event()

    def drain_bounded_stdout() -> None:
        try:
            while True:
                remaining = MAX_EXCERPT_SOURCE_BYTES + 1 - len(output)
                if remaining <= 0:
                    exceeded_limit.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                chunk = process.stdout.read(min(64 * 1024, remaining))
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > MAX_EXCERPT_SOURCE_BYTES:
                    exceeded_limit.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except OSError:
            read_failed.set()

    reader = threading.Thread(target=drain_bounded_stdout, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=2)
        _fail("EXCERPT_INVALID")
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=2)
        process.stdout.close()
    if reader.is_alive() or exceeded_limit.is_set() or read_failed.is_set() or returncode != 0:
        _fail("EXCERPT_INVALID")
    return bytes(output)


def _read_snapshot_source(
    path: str,
    *,
    facts: Mapping[str, Any],
    run_dir: Path,
    root: Path,
) -> bytes:
    if not _safe_relative_path(path):
        _fail("EXCERPT_INVALID")
    index_path = _absolute(run_dir) / "phase2" / "project-index.json"
    index_read = _read_artifact(index_path)
    record = next((row for row in index_read.artifact["files"] if row["path"] == path), None)
    if record is None:
        _fail("EXCERPT_INVALID")
    expected_bytes = record.get("bytes")
    if (
        record.get("content_kind") != "text"
        or type(expected_bytes) is not int
        or expected_bytes < 0
        or expected_bytes > MAX_EXCERPT_SOURCE_BYTES
    ):
        _fail("EXCERPT_INVALID")
    if facts["snapshot_kind"] == "git-tree":
        raw = _read_git_object_bounded(_absolute(root), facts["repository_revision"], path)
    else:
        source_path = _absolute(root).joinpath(*PurePosixPath(path).parts)
        _assert_no_link_components(source_path)
        if _is_link(source_path) or not source_path.is_file():
            _fail("EXCERPT_INVALID")
        try:
            with source_path.open("rb") as source_file:
                raw = source_file.read(MAX_EXCERPT_SOURCE_BYTES + 1)
        except OSError:
            _fail("EXCERPT_INVALID")
    if len(raw) != expected_bytes or _sha256(raw) != record.get("sha256"):
        _fail("EXCERPT_INVALID")
    return raw


def _verify_source_excerpt(
    lines: list[str],
    start_index: int,
    facts: Mapping[str, Any],
    inputs: ChapterInputPaths,
) -> tuple[int, bool]:
    match = _EXCERPT_LINE.fullmatch(lines[start_index])
    if match is None or start_index + 3 >= len(lines) or lines[start_index + 1] != "```text":
        return start_index + 1, False
    evidence_id, encoded_path, first_text, last_text, declared_sha = match.groups()
    try:
        path = _safe_path_from_marker(encoded_path)
    except Phase6ChapterError:
        return start_index + 1, False
    first, last = int(first_text), int(last_text)
    end_fence = next((index for index in range(start_index + 2, len(lines)) if lines[index] == "```"), None)
    if end_fence is None or end_fence + 1 >= len(lines) or lines[end_fence + 1] != "<!-- /SOURCE-EXCERPT -->":
        return start_index + 1, False
    evidence = next((item for item in facts["evidence"] if item["id"] == evidence_id), None)
    if evidence is None:
        return end_fence + 2, False
    locator = evidence["locator"]
    if locator.get("path") != path or locator.get("line_start") != first or locator.get("line_end") != last:
        return end_fence + 2, False
    try:
        source = _read_snapshot_source(path, facts=facts, run_dir=inputs.run_dir, root=inputs.root)
        source_lines = source.splitlines(keepends=True)
        selected = b"".join(source_lines[first - 1:last])
        shown = ("\n".join(lines[start_index + 2:end_fence]) + "\n").encode("utf-8")
    except (Phase6ChapterError, UnicodeError):
        return end_fence + 2, False
    if first > last or last > len(source_lines) or not selected.endswith(b"\n"):
        return end_fence + 2, False
    if shown != selected or _sha256(selected) != declared_sha:
        return end_fence + 2, False
    return end_fence + 2, True


def _verify_source_excerpt_v2(
    lines: list[str],
    caption_index: int,
    facts: Mapping[str, Any],
    inputs: ChapterInputPaths,
) -> tuple[int, bool]:
    caption_match = _V2_SOURCE_LOCATION.fullmatch(lines[caption_index])
    marker_index = caption_index + 1
    if caption_match is None or marker_index >= len(lines):
        return caption_index + 1, False
    marker_match = _EXCERPT_LINE.fullmatch(lines[marker_index])
    if (
        marker_match is None
        or marker_index + 2 >= len(lines)
        or lines[marker_index + 1] != "\x60\x60\x60text"
    ):
        return caption_index + 1, False
    caption_path, caption_first_text, caption_last_text = caption_match.groups()
    evidence_id, encoded_path, first_text, last_text, declared_sha = marker_match.groups()
    try:
        path = _safe_path_from_marker(encoded_path)
    except Phase6ChapterError:
        return caption_index + 1, False
    if not _safe_relative_path(caption_path):
        return caption_index + 1, False
    first, last = int(first_text), int(last_text)
    if (
        caption_path != path
        or caption_first_text != first_text
        or caption_last_text != last_text
        or first > last
        or last - first + 1 > MAX_V2_EXCERPT_LINES
    ):
        return caption_index + 1, False

    end_fence = next(
        (index for index in range(marker_index + 2, len(lines)) if lines[index] == "\x60\x60\x60"),
        None,
    )
    if end_fence is None or end_fence + 1 >= len(lines) or lines[end_fence + 1] != "<!-- /SOURCE-EXCERPT -->":
        return caption_index + 1, False
    evidence = next((item for item in facts["evidence"] if item["id"] == evidence_id), None)
    if evidence is None or evidence.get("level") != "E1":
        return end_fence + 2, False
    locator = evidence["locator"]
    locator_first = locator.get("line_start")
    locator_last = locator.get("line_end")
    if (
        locator.get("path") != path
        or type(locator_first) is not int
        or type(locator_last) is not int
        or not locator_first <= first <= last <= locator_last
    ):
        return end_fence + 2, False
    try:
        source = _read_snapshot_source(path, facts=facts, run_dir=inputs.run_dir, root=inputs.root)
        source_lines = source.splitlines(keepends=True)
        selected = b"".join(source_lines[first - 1:last])
        shown = ("\n".join(lines[marker_index + 2:end_fence]) + "\n").encode("utf-8")
    except (Phase6ChapterError, UnicodeError):
        return end_fence + 2, False
    if first < 1 or last > len(source_lines):
        return end_fence + 2, False
    # The LF before the closing fence is framing when the selected bytes end at EOF without LF.
    body_matches = shown == selected or (
        shown.endswith(b"\n") and not selected.endswith(b"\n") and shown[:-1] == selected
    )
    if not body_matches or _sha256(selected) != declared_sha:
        return end_fence + 2, False
    return end_fence + 2, True


def _v2_structural_lines(lines: list[str]) -> list[str]:
    """Hide only the body of a structurally bounded v2 excerpt from chapter scans."""
    structural = list(lines)
    index = 1
    while index + 2 < len(lines):
        if (
            _V2_SOURCE_LOCATION.fullmatch(lines[index - 1]) is None
            or _EXCERPT_LINE.fullmatch(lines[index]) is None
            or lines[index + 1] != "\x60\x60\x60text"
        ):
            index += 1
            continue
        end_fence = next(
            (candidate for candidate in range(index + 2, len(lines)) if lines[candidate] == "\x60\x60\x60"),
            None,
        )
        if (
            end_fence is None
            or end_fence + 1 >= len(lines)
            or lines[end_fence + 1] != "<!-- /SOURCE-EXCERPT -->"
        ):
            index += 1
            continue
        for body_index in range(index + 2, end_fence):
            structural[body_index] = ""
        index = end_fence + 2
    return structural


def _parse_sorted_attribute(value: str, *, pattern: re.Pattern[str]) -> list[str] | None:
    if value == "none":
        return []
    values = value.split(",")
    if any(pattern.fullmatch(item) is None for item in values) or values != sorted(set(values)):
        return None
    return values


def _exercise_source_refs(
    claim_ids: list[str],
    evidence_ids: list[str],
    facts: Mapping[str, Any],
) -> dict[str, list[str]]:
    claims = {claim["id"]: claim for claim in facts["claims"]}
    evidence = {item["id"]: item for item in facts["evidence"]}
    nodes: set[str] = set()
    edges: set[str] = set()
    files: set[str] = set()
    all_evidence = set(evidence_ids)
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            _fail("EXERCISE_INVALID")
        nodes.update(claim["graph_node_ids"])
        edges.update(claim["graph_edge_ids"])
        files.update(claim["file_paths"])
    for evidence_id in evidence_ids:
        item = evidence.get(evidence_id)
        if item is None:
            _fail("EXERCISE_INVALID")
        path = item["locator"].get("path")
        if path:
            files.add(path)
    return _source_refs(
        graph_node_ids=nodes,
        graph_edge_ids=edges,
        evidence_ids=all_evidence,
        file_paths=files,
    )


def _scan_markdown_format(
    raw: bytes,
) -> tuple[str | None, list[str], set[str], int, dict[int, re.Match]]:
    """Inspect only Markdown bytes and the lexical shape of the footnote tail."""
    errors: set[str] = set()
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return None, [], {"MARKDOWN_FORMAT_INVALID"}, 0, {}

    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n") or "\t" in text:
        errors.add("MARKDOWN_FORMAT_INVALID")
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    footnote_start = next(
        (index for index, line in enumerate(lines) if _FOOTNOTE_LINE.fullmatch(line)),
        len(lines),
    )
    footnote_matches: dict[int, re.Match] = {}
    for index in range(footnote_start, len(lines)):
        line = lines[index]
        if not line:
            continue
        match = _FOOTNOTE_LINE.fullmatch(line)
        if match is None:
            errors.add("FOOTNOTE_INVALID")
        else:
            footnote_matches[index] = match
    return text, lines, errors, footnote_start, footnote_matches


def markdown_format_preflight(raw: bytes) -> tuple[str, ...]:
    """Return fixed lexical diagnostic codes without reading project evidence."""
    _text, _lines, errors, _footnote_start, _footnote_matches = _scan_markdown_format(raw)
    return tuple(sorted(errors))


def _verify_markdown(
    raw: bytes,
    facts: Mapping[str, Any],
    inputs: ChapterInputPaths,
) -> tuple[dict[str, Any] | None, dict[str, str], list[str]]:
    checks = {
        "reference_integrity": "PASS",
        "heading_order": "PASS",
        "claim_footnotes": "PASS",
        "exercise_sync": "PASS",
        "excerpt_identity": "NOT_RUN",
    }
    text, lines, errors, footnote_start, footnote_matches = _scan_markdown_format(raw)
    if text is None:
        return None, {
            **checks,
            "reference_integrity": "FAIL",
            "heading_order": "FAIL",
            "claim_footnotes": "FAIL",
            "exercise_sync": "FAIL",
        }, sorted(errors)
    v2_candidate = (
        len(lines) > 1
        and lines[0] == "# Chapter"
        and lines[1] == _CHAPTER_V2_MARKER
    )
    structural_lines = _v2_structural_lines(lines) if v2_candidate else lines
    format_markers = [
        (index, line) for index, line in enumerate(structural_lines)
        if line.startswith(_CHAPTER_FORMAT_MARKER_PREFIX)
    ]
    chapter_v2 = v2_candidate and format_markers == [(1, _CHAPTER_V2_MARKER)]
    if format_markers and not chapter_v2:
        errors.add("MARKDOWN_FORMAT_INVALID")
    headings = [line[3:] for line in structural_lines if line.startswith("## ")]
    if not lines or lines[0] != "# Chapter" or headings != list(_HEADINGS):
        checks["heading_order"] = "FAIL"
        errors.add("HEADING_ORDER_INVALID")
    if any(line.startswith("#") and not line.startswith("## ") for line in structural_lines[1:]):
        checks["heading_order"] = "FAIL"
        errors.add("HEADING_ORDER_INVALID")

    section_starts = [index for index, line in enumerate(structural_lines) if line.startswith("## ")]
    if chapter_v2 and section_starts:
        for index, line in enumerate(structural_lines[:section_starts[0]]):
            if (index, line) not in {(0, "# Chapter"), (1, _CHAPTER_V2_MARKER)} and line:
                errors.add("MARKDOWN_FORMAT_INVALID")
    section_bodies: dict[str, list[str]] = {}
    for position, start in enumerate(section_starts):
        end = section_starts[position + 1] if position + 1 < len(section_starts) else len(structural_lines)
        section_bodies[structural_lines[start][3:]] = lines[start + 1:end]

    used_claims: list[str] = []
    excerpt_seen = False
    v2_excerpts_valid = True
    for heading in _HEADINGS[:-1]:
        body = section_bodies.get(heading, [])
        index = 0
        while index < len(body):
            line = body[index]
            if not line:
                index += 1
                continue
            if line == "> Evidence gap — NOT ESTABLISHED IN THIS SLICE":
                index += 1
                continue
            if chapter_v2:
                if line.startswith("Source location:"):
                    next_index, valid = _verify_source_excerpt_v2(body, index, facts, inputs)
                    excerpt_seen = True
                    if heading != "Source" or not valid:
                        checks["excerpt_identity"] = "FAIL"
                        v2_excerpts_valid = False
                        errors.add("EXCERPT_INVALID")
                    index = next_index
                    continue
                if line.startswith("<!-- SOURCE-EXCERPT ") or line == "<!-- /SOURCE-EXCERPT -->":
                    excerpt_seen = True
                    checks["excerpt_identity"] = "FAIL"
                    v2_excerpts_valid = False
                    errors.add("EXCERPT_INVALID")
                    index += 1
                    continue
                if line.startswith("<!--"):
                    errors.add("MARKDOWN_FORMAT_INVALID")
                    index += 1
                    continue
                parsed = _parse_v2_prose_line(line)
                if parsed is not None:
                    kind, claim_id = parsed
                    if kind == "CLAIM":
                        claim = next((row for row in facts["claims"] if row["id"] == claim_id), None)
                        if claim is None:
                            checks["reference_integrity"] = "FAIL"
                            checks["claim_footnotes"] = "FAIL"
                            errors.add("CLAIM_MARKER_INVALID")
                        elif (
                            claim["disposition"] != "SUPPORTED"
                            or not (set(claim["resolved_evidence_levels"]) & _QUALIFYING_LEVELS)
                            or "E6" in claim["resolved_evidence_levels"]
                        ):
                            checks["claim_footnotes"] = "FAIL"
                            errors.add("CLAIM_NOT_SUPPORTED")
                        used_claims.append(claim_id)
                    index += 1
                    continue
                if "[^" in line:
                    checks["reference_integrity"] = "FAIL"
                    checks["claim_footnotes"] = "FAIL"
                    errors.add("CLAIM_MARKER_INVALID")
                else:
                    errors.add("MARKDOWN_FORMAT_INVALID")
                index += 1
                continue
            claim_match = _CLAIM_LINE.fullmatch(line)
            if claim_match is not None and line.count("[^CLAIM-") == 1:
                claim_id = "CLAIM-" + claim_match.group(2)
                claim = next((row for row in facts["claims"] if row["id"] == claim_id), None)
                if claim is None:
                    checks["reference_integrity"] = "FAIL"
                    checks["claim_footnotes"] = "FAIL"
                    errors.add("CLAIM_MARKER_INVALID")
                elif (
                    claim["disposition"] != "SUPPORTED"
                    or not (set(claim["resolved_evidence_levels"]) & _QUALIFYING_LEVELS)
                    or "E6" in claim["resolved_evidence_levels"]
                ):
                    checks["claim_footnotes"] = "FAIL"
                    errors.add("CLAIM_NOT_SUPPORTED")
                used_claims.append(claim_id)
                index += 1
                continue
            if _GENERAL_LINE.fullmatch(line) is not None and "[^CLAIM-" not in line:
                index += 1
                continue
            if line.startswith("<!-- SOURCE-EXCERPT "):
                excerpt_match = _EXCERPT_LINE.fullmatch(line)
                if excerpt_match is None or excerpt_match.group(1) not in {item["id"] for item in facts["evidence"]}:
                    checks["reference_integrity"] = "FAIL"
                next_index, valid = _verify_source_excerpt(body, index, facts, inputs)
                excerpt_seen = True
                if heading != "Source" or not valid:
                    checks["excerpt_identity"] = "FAIL"
                    errors.add("EXCERPT_INVALID")
                else:
                    checks["excerpt_identity"] = "PASS"
                index = next_index
                continue
            if "[^CLAIM-" in line:
                checks["reference_integrity"] = "FAIL"
                checks["claim_footnotes"] = "FAIL"
                errors.add("CLAIM_MARKER_INVALID")
            else:
                errors.add("MARKDOWN_FORMAT_INVALID")
            index += 1

    learning = section_bodies.get("Learning checks", [])
    exercises: list[dict[str, Any]] = []
    objective_keys: set[str] = set()
    line_index = 0
    concept_pattern = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
    claim_pattern = re.compile(r"^CLAIM-[0-9a-f]{64}$")
    evidence_pattern = re.compile(r"^EVID-[A-Za-z0-9._:-]+$")
    while line_index < len(learning):
        line = learning[line_index]
        if not line:
            line_index += 1
            continue
        if _FOOTNOTE_LINE.fullmatch(line) is not None:
            break
        match = _EXERCISE_LINE.fullmatch(line)
        if match is None or line_index + 2 >= len(learning):
            if line.startswith(":::exercise "):
                checks["reference_integrity"] = "FAIL"
            checks["exercise_sync"] = "FAIL"
            errors.add("EXERCISE_INVALID")
            line_index += 1
            continue
        exercise_id, objective_key, exercise_type, prerequisites_raw, claims_raw, evidence_raw = match.groups()
        if learning[line_index + 2] != ":::" or not learning[line_index + 1].startswith("Question: "):
            checks["exercise_sync"] = "FAIL"
            errors.add("EXERCISE_INVALID")
            line_index += 1
            continue
        prompt = learning[line_index + 1][len("Question: "):]
        prerequisites = _parse_sorted_attribute(prerequisites_raw, pattern=concept_pattern)
        claim_ids = _parse_sorted_attribute(claims_raw, pattern=claim_pattern)
        evidence_ids = _parse_sorted_attribute(evidence_raw, pattern=evidence_pattern)
        known_claim_ids = {claim["id"] for claim in facts["claims"]}
        known_evidence_ids = {item["id"] for item in facts["evidence"]}
        claim_refs_valid = claim_ids is not None and set(claim_ids) <= known_claim_ids
        evidence_refs_valid = evidence_ids is not None and set(evidence_ids) <= known_evidence_ids
        if not claim_refs_valid or not evidence_refs_valid:
            checks["reference_integrity"] = "FAIL"
        if (
            not prompt.strip()
            or any(token in prompt for token in ("::: ", "<!--", "[^CLAIM-"))
            or (chapter_v2 and _RAW_HTML_START.search(prompt) is not None)
            or prerequisites is None
            or claim_ids is None
            or evidence_ids is None
            or objective_key in objective_keys
            or exercise_id != f"EX-{facts['selected_unit']['chapter_id']}-{objective_key}"
            or not set(prerequisites or ()) <= set(facts["selected_unit"]["prerequisite_concept_keys"])
            or not claim_refs_valid
            or not evidence_refs_valid
        ):
            checks["exercise_sync"] = "FAIL"
            errors.add("EXERCISE_INVALID")
            line_index += 3
            continue
        objective_keys.add(objective_key)
        source_refs = _exercise_source_refs(claim_ids or [], evidence_ids or [], facts)
        normalized_prompt = unicodedata.normalize("NFC", prompt.strip())
        exercises.append({
            "id": exercise_id,
            "objective_key": objective_key,
            "type": exercise_type,
            "prompt": prompt,
            "prompt_sha256": _sha256(normalized_prompt.encode("utf-8")),
            "prerequisite_concept_keys": prerequisites,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "source_refs": source_refs,
            "answer_status": "NOT_AUTHORED",
        })
        line_index += 3
    if not {"recall", "trace_predict"} <= {item["type"] for item in exercises}:
        checks["exercise_sync"] = "FAIL"
        errors.add("EXERCISE_INVALID")

    definition_ids: list[str] = []
    for line_index in range(footnote_start, len(lines)):
        line = lines[line_index]
        if not line:
            continue
        match = footnote_matches.get(line_index)
        if match is None:
            if line.startswith("[^CLAIM-"):
                checks["reference_integrity"] = "FAIL"
            checks["claim_footnotes"] = "FAIL"
            continue
        definition_ids.append("CLAIM-" + match.group(1))
        claim_id = "CLAIM-" + match.group(1)
        if not any(row["id"] == claim_id for row in facts["claims"]):
            checks["reference_integrity"] = "FAIL"
            checks["claim_footnotes"] = "FAIL"
            errors.add("FOOTNOTE_INVALID")
        elif line != _canonical_footnote(facts, claim_id):
            checks["reference_integrity"] = "FAIL"
            checks["claim_footnotes"] = "FAIL"
            errors.add("FOOTNOTE_INVALID")
    if definition_ids != sorted(set(definition_ids)) or set(definition_ids) != set(used_claims):
        checks["reference_integrity"] = "FAIL"
        checks["claim_footnotes"] = "FAIL"
        errors.add("FOOTNOTE_INVALID")
    if len(exercises) < 2:
        checks["exercise_sync"] = "FAIL"
        errors.add("EXERCISE_INVALID")
    if chapter_v2:
        if not excerpt_seen:
            errors.add("EXCERPT_INVALID")
            v2_excerpts_valid = False
        checks["excerpt_identity"] = "PASS" if excerpt_seen and v2_excerpts_valid else "FAIL"
    elif not excerpt_seen:
        checks["excerpt_identity"] = "NOT_RUN"

    if errors:
        bank = None
    else:
        bank = {
            "artifact_kind": "exercise-bank",
            "schema_version": "1.0.0",
            "repository_revision": facts["repository_revision"],
            "generated_at": facts["generated_at"],
            "snapshot_kind": facts["snapshot_kind"],
            "chapter_id": facts["selected_unit"]["chapter_id"],
            "curriculum_unit_id": facts["selected_unit"]["curriculum_unit_id"],
            "records": exercises,
        }
        try:
            validate_artifact(bank)
        except ArtifactValidationError:
            errors.add("EXERCISE_INVALID")
            checks["exercise_sync"] = "FAIL"
            bank = None
    return bank, checks, [error for error in _FIXED_ERRORS if error in errors]


def _draft_status(
    facts: Mapping[str, Any],
    *,
    chapter_sha256: str,
    facts_sha256: str,
    exercise_sha256: str | None,
    checks: Mapping[str, str],
    errors: list[str],
) -> dict[str, Any]:
    status = {
        "artifact_kind": "chapter-draft-status",
        "schema_version": "1.0.0",
        "repository_revision": facts["repository_revision"],
        "generated_at": facts["generated_at"],
        "snapshot_kind": facts["snapshot_kind"],
        "source_metadata": facts["source_metadata"],
        "source_status": facts["source_status"],
        "source_run_manifest_sha256": facts["source_run_manifest_sha256"],
        "unknown_files": facts["unknown_files"],
        "chapter_id": facts["selected_unit"]["chapter_id"],
        "chapter_sha256": chapter_sha256,
        "chapter_facts_sha256": facts_sha256,
        "chapter_status": "DRAFT",
        "structural_status": "FAIL" if errors else "PASS",
        "overall_status": "PARTIAL",
        "check_results": dict(checks),
        "errors": errors,
        "semantic_entailment": "NOT_RUN",
        "evidence_verifier": "NOT_RUN",
        "beginner_critic": "NOT_RUN",
        "privacy_review": "NOT_RUN",
        "g08": "PARTIAL",
        "g09": "PARTIAL",
        "g11": "NOT_RUN",
    }
    if exercise_sha256 is not None:
        status["exercise_bank_sha256"] = exercise_sha256
    try:
        validate_artifact(status)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return status


def _check_verify_freshness(
    authenticated: AuthenticatedClaimEvidenceContext,
    phase5_reads: Mapping[str, _ReadInput],
    facts_path: Path,
    facts_sha256: str,
    packet_path: Path,
    packet_sha256: str,
    markdown_path: Path,
    markdown_sha256: str,
) -> None:
    authenticated.recheck()
    _recheck_inputs(phase5_reads)
    for path, expected in (
        (facts_path, facts_sha256),
        (packet_path, packet_sha256),
        (markdown_path, markdown_sha256),
    ):
        _assert_no_link_components(path)
        if _is_link(path) or not path.is_file():
            _fail("INPUT_CHANGED")
        try:
            if _sha256(path.read_bytes()) != expected:
                _fail("INPUT_CHANGED")
        except OSError:
            _fail("INPUT_CHANGED")


def authenticate_chapter_for_review(
    inputs: ChapterInputPaths,
    *,
    facts_path: Path,
    markdown_path: Path,
    exercise_bank_path: Path,
    draft_status_path: Path,
) -> AuthenticatedChapterReviewContext:
    """Replay the source once and authenticate an exact, complete Phase 6A draft."""
    output = _check_external_output(Path(facts_path).parent, inputs, must_exist=True)
    expected_paths = {
        "facts": output / "chapter-facts.json",
        "packet": output / "writer-packet.md",
        "chapter": output / "chapter.md",
        "bank": output / "exercise-bank.json",
        "status": output / "chapter-draft-status.json",
    }
    if (
        _absolute(facts_path) != expected_paths["facts"]
        or _absolute(markdown_path) != expected_paths["chapter"]
        or _absolute(exercise_bank_path) != expected_paths["bank"]
        or _absolute(draft_status_path) != expected_paths["status"]
    ):
        _fail("INPUT_INVALID")
    _check_complete_chapter_directory(output, inputs)

    authenticated, phase5_reads, prerequisite_graph, curriculum = _capture_bundle(inputs)
    facts_read = _read_artifact(expected_paths["facts"])
    try:
        unit_id = facts_read.artifact["selected_unit"]["curriculum_unit_id"]
        facts = _project_claim_facts(
            inputs, unit_id, phase5_reads, prerequisite_graph, curriculum, authenticated,
        )
    except (KeyError, TypeError):
        _fail("PROVENANCE_MISMATCH")
    expected_facts = dumps_artifact(facts).encode("utf-8")
    if facts_read.raw != expected_facts or facts_read.artifact != facts:
        _fail("PROVENANCE_MISMATCH")
    packet_raw = _read_review_file(expected_paths["packet"])
    if packet_raw != _writer_packet(facts).encode("utf-8"):
        _fail("PROVENANCE_MISMATCH")
    chapter_raw = _read_review_file(expected_paths["chapter"])
    expected_bank, checks, errors = _verify_markdown(chapter_raw, facts, inputs)
    if expected_bank is None or errors:
        _fail("PROVENANCE_MISMATCH")
    bank_read = _read_artifact(expected_paths["bank"])
    bank_raw = dumps_artifact(expected_bank).encode("utf-8")
    if bank_read.raw != bank_raw or bank_read.artifact != expected_bank:
        _fail("PROVENANCE_MISMATCH")
    status_read = _read_artifact(expected_paths["status"])
    expected_status = _draft_status(
        facts,
        chapter_sha256=_sha256(chapter_raw),
        facts_sha256=facts_read.sha256,
        exercise_sha256=_sha256(bank_raw),
        checks=checks,
        errors=[],
    )
    if status_read.raw != dumps_artifact(expected_status).encode("utf-8") or status_read.artifact != expected_status:
        _fail("PROVENANCE_MISMATCH")
    context = AuthenticatedChapterReviewContext(
        _inputs=inputs,
        _output_dir=output,
        _authenticated=authenticated,
        _phase5_reads=phase5_reads,
        _facts=facts,
        _bank=expected_bank,
        _status=expected_status,
        chapter_raw=chapter_raw,
        _facts_raw=expected_facts,
        _packet_raw=packet_raw,
        _bank_raw=bank_raw,
        _status_raw=status_read.raw,
    )
    for name, raw in (
        ("chapter-facts.json", expected_facts),
        ("writer-packet.md", packet_raw),
        ("chapter.md", chapter_raw),
        ("exercise-bank.json", bank_raw),
        ("chapter-draft-status.json", status_read.raw),
    ):
        if _read_review_file(output / name) != raw:
            _fail("INPUT_CHANGED")
    return context


def verify_chapter_draft(
    inputs: ChapterInputPaths,
    *,
    unit_id: str,
    facts_path: Path,
    markdown_path: Path,
    out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reauthenticate the snapshot, check the draft, then publish bank/status without overwrite."""
    output, resolved_facts_path, resolved_markdown_path = _verify_output_directory(
        out_dir, inputs, facts_path, markdown_path,
    )
    authenticated, phase5_reads, prerequisite_graph, curriculum = _capture_bundle(inputs)
    facts = _project_claim_facts(inputs, unit_id, phase5_reads, prerequisite_graph, curriculum, authenticated)
    expected_facts = dumps_artifact(facts).encode("utf-8")
    expected_packet = _writer_packet(facts).encode("utf-8")
    facts_read = _read_artifact(resolved_facts_path)
    packet_path = output / "writer-packet.md"
    _assert_no_link_components(packet_path)
    try:
        packet_raw = packet_path.read_bytes()
        packet_sha = _sha256(packet_raw)
    except OSError:
        _fail("INPUT_INVALID")
    if facts_read.raw != expected_facts or facts_read.artifact != facts or packet_raw != expected_packet:
        _fail("PROVENANCE_MISMATCH")
    try:
        markdown_raw = resolved_markdown_path.read_bytes()
    except OSError:
        _fail("INPUT_INVALID")
    markdown_sha = _sha256(markdown_raw)
    bank, checks, errors = _verify_markdown(markdown_raw, facts, inputs)
    bank_bytes = dumps_artifact(bank).encode("utf-8") if bank is not None else None
    status = _draft_status(
        facts,
        chapter_sha256=markdown_sha,
        facts_sha256=facts_read.sha256,
        exercise_sha256=_sha256(bank_bytes) if bank_bytes is not None else None,
        checks=checks,
        errors=errors,
    )
    status_bytes = dumps_artifact(status).encode("utf-8")

    _check_verify_freshness(
        authenticated,
        phase5_reads,
        resolved_facts_path,
        facts_read.sha256,
        packet_path,
        packet_sha,
        resolved_markdown_path,
        markdown_sha,
    )
    try:
        names = {path.name for path in output.iterdir()}
    except OSError:
        _fail("OUTPUT_INVALID")
    if names != {"chapter-facts.json", "writer-packet.md", "chapter.md"}:
        _fail("OUTPUT_EXISTS")
    bank_path = output / "exercise-bank.json"
    status_path = output / "chapter-draft-status.json"
    if os.path.lexists(bank_path) or os.path.lexists(status_path):
        _fail("OUTPUT_EXISTS")
    bank_owner: tuple[int, int] | None = None
    if bank_bytes is not None:
        bank_owner = _publish_new_file(bank_path, bank_bytes)
    try:
        _publish_new_file(status_path, status_bytes)
    except BaseException:
        if bank_owner is not None:
            _unlink_if_owned(bank_path, bank_owner)
        raise
    return bank if bank is not None else {}, status


__all__ = [
    "ChapterInputPaths",
    "Phase6ChapterError",
    "AuthenticatedChapterReviewContext",
    "authenticate_chapter_for_review",
    "prepare_chapter_facts",
    "verify_chapter_draft",
]
