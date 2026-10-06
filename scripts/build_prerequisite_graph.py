#!/usr/bin/env python3
"""Authenticate a Phase 4C package and publish a compact Phase 5A graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
import phase4_proposals
from phase4_proposals import Phase4ProposalError
from phase5_prerequisites import (
    Phase5PrerequisiteError,
    _phase4_profile,
    project_prerequisite_graph,
)


_PACKAGE_FILES = ("evidence.json", "knowledge-graph.json", "semantic-proposals.json")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PrerequisiteBuildError(Phase5PrerequisiteError):
    """A fixed, redacted Phase 5A build failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise PrerequisiteBuildError(code)


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


def _read_regular_file(value: str | Path, *, changed_code: str = "INPUT_INVALID") -> tuple[Path, bytes]:
    path = Path(os.path.abspath(os.fspath(value)))
    _assert_no_link_components(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            _fail(changed_code)
        raw = path.read_bytes()
    except PrerequisiteBuildError:
        raise
    except OSError:
        _fail(changed_code)
    return path, raw


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _parse_artifact(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
        return validate_artifact(value)
    except (ArtifactValidationError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("INPUT_INVALID")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inside(path: Path, base: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(base)))) == os.path.normcase(str(base))
    except ValueError:
        return False


def _output_path(value: str | Path, *, root: Path, candidate: Path, package: Path) -> Path:
    output = Path(os.path.abspath(os.fspath(value)))
    _assert_no_link_components(output.parent)
    if output.exists() or os.path.lexists(output):
        _fail("OUTPUT_EXISTS")
    if not output.parent.is_dir():
        _fail("OUTPUT_INVALID")
    if _inside(output, root) or _inside(output, package) or _inside(candidate, output):
        _fail("OUTPUT_INVALID")
    return output


def _resolve_input_root(value: str | Path) -> Path:
    original = Path(os.path.abspath(os.fspath(value)))
    _assert_no_link_components(original)
    try:
        resolved = original.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("INPUT_INVALID")
    if not resolved.is_dir():
        _fail("INPUT_INVALID")
    return resolved


def _package_snapshot(package_dir: str | Path) -> tuple[Path, dict[str, Path], dict[str, bytes], dict[str, str]]:
    package = Path(os.path.abspath(os.fspath(package_dir)))
    _assert_no_link_components(package)
    if _is_link(package) or not package.is_dir():
        _fail("INPUT_INVALID")
    try:
        entries = list(package.iterdir())
    except OSError:
        _fail("INPUT_INVALID")
    if sorted(path.name for path in entries) != sorted(_PACKAGE_FILES):
        _fail("INPUT_INVALID")

    paths: dict[str, Path] = {}
    raw_by_name: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for name in _PACKAGE_FILES:
        path = package / name
        if _is_link(path):
            _fail("INPUT_INVALID")
        resolved, raw = _read_regular_file(path)
        paths[name] = resolved
        raw_by_name[name] = raw
        digests[name] = _sha256(raw)
    return package, paths, raw_by_name, digests


def _check_candidate_inputs(candidate: Mapping[str, Any], digests: Mapping[str, str]) -> None:
    expected = _phase4_profile(candidate)["inputs"]
    rows = candidate.get("phase4_inputs")
    if not isinstance(rows, list) or len(rows) != len(expected):
        _fail("PROVENANCE_MISMATCH")
    for row, (name, kind, version) in zip(rows, expected):
        if (
            row.get("path") != name
            or row.get("artifact_kind") != kind
            or row.get("schema_version") != version
            or row.get("sha256") != digests[name]
        ):
            _fail("PROVENANCE_MISMATCH")


def _check_pair_bytes(
    raw_by_name: Mapping[str, bytes], graph: Mapping[str, Any], evidence: Mapping[str, Any],
) -> None:
    expected = {
        "knowledge-graph.json": graph,
        "evidence.json": evidence,
    }
    for name, artifact in expected.items():
        if raw_by_name[name] != dumps_artifact(artifact).encode("utf-8"):
            _fail("PROVENANCE_MISMATCH")


def _publish_new_file(path: Path, payload: bytes) -> None:
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{path.name}.phase5-prerequisite-staging-",
            dir=path.parent,
        ) as temporary:
            staged = Path(temporary) / path.name
            staged.write_bytes(payload)
            try:
                os.link(staged, path)
            except FileExistsError:
                _fail("OUTPUT_EXISTS")
            except OSError:
                _fail("OUTPUT_FAILED")
    except PrerequisiteBuildError:
        raise
    except OSError:
        _fail("OUTPUT_FAILED")


def build_prerequisite_graph(
    candidates_path: str | Path,
    *,
    package_dir: str | Path,
    run_dir: str | Path,
    root: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Replay, project, freshness-check, then publish one new prerequisite artifact."""
    try:
        root_path = _resolve_input_root(root)
        candidate_path, candidate_raw = _read_regular_file(candidates_path)
        candidate = _parse_artifact(candidate_raw)
        package, package_paths, package_raw, package_digests = _package_snapshot(package_dir)
        output_path = _output_path(out, root=root_path, candidate=candidate_path, package=package)
        if _inside(candidate_path, package):
            _fail("INPUT_INVALID")
        _check_candidate_inputs(candidate, package_digests)
        candidate_sha256 = _sha256(candidate_raw)

        # Reprojection performs the one full Phase 4A source replay and 4C transform.
        graph, evidence = phase4_proposals.reproject_phase4c_artifacts(
            package_paths["semantic-proposals.json"], run_dir=run_dir, root=root_path,
        )
        _check_pair_bytes(package_raw, graph, evidence)
        _check_candidate_inputs(candidate, package_digests)
        projected = project_prerequisite_graph(
            candidate,
            graph=graph,
            evidence=evidence,
            candidate_sha256=candidate_sha256,
        )
        output_bytes = dumps_artifact(projected).encode("utf-8")

        # Bind final freshness to the replayed graph, then catch package/candidate races.
        phase4_proposals.verify_phase4c_source_freshness(
            run_dir, root=root_path, expected_graph=graph,
        )
        current_candidate_path, current_candidate_raw = _read_regular_file(
            candidate_path, changed_code="INPUT_CHANGED",
        )
        current_package, _current_paths, current_raw, current_digests = _package_snapshot(
            package,
        )
        if (
            current_candidate_path != candidate_path
            or current_package != package
            or _sha256(current_candidate_raw) != candidate_sha256
            or current_digests != package_digests
            or current_raw != package_raw
        ):
            _fail("INPUT_CHANGED")
        _output_path(output_path, root=root_path, candidate=candidate_path, package=package)
        _publish_new_file(output_path, output_bytes)
        return projected
    except PrerequisiteBuildError:
        raise
    except Phase5PrerequisiteError as exc:
        _fail(exc.code)
    except Phase4ProposalError:
        _fail("PROVENANCE_MISMATCH")
    except (ArtifactValidationError, OSError, RuntimeError, KeyError, TypeError, ValueError):
        _fail("INPUT_INVALID")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = build_prerequisite_graph(
            args.candidates,
            package_dir=args.package,
            run_dir=args.run_dir,
            root=args.root,
            out=args.out,
        )
    except PrerequisiteBuildError as exc:
        print(f"ERROR {exc.code}", file=sys.stderr)
        return 2
    print(
        f"status={result['source_status']} nodes={len(result['nodes'])} edges={len(result['edges'])} output=published",
    )
    return 0


__all__ = ["PrerequisiteBuildError", "build_prerequisite_graph", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
