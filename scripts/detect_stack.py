#!/usr/bin/env python3
"""Build a declaration-only stack profile from audited Phase 2 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact
from coverage_audit import audit_coverage
from repository_scan import (
    RepositoryScanError,
    discover_git_context,
    enumerate_git_snapshot_entries,
    guard_worktree_path,
    hash_git_object,
)
from scan_repository import _assert_publishable, _publish, configure_stdio, utc_now
from stack_detection import MAX_MANIFEST_BYTES, StackDetectionError, build_stack_artifacts

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_OPERATIONAL = 2
_GENERATED_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_OUTPUT_NAMES = ("stack-profile.json", "evidence.json")
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect declared Python, Java and Node stack technologies from audited Phase 2 artifacts",
    )
    parser.add_argument("--root", required=True, type=Path, help="analysed Git repository or subtree")
    parser.add_argument("--index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--out", required=True, type=Path, help="directory for stack-profile.json and evidence.json")
    parser.add_argument("--generated-at", default=None, metavar="YYYY-MM-DDTHH:MM:SSZ",
                        help="fixed output generation timestamp; defaults to current UTC")
    return parser


def _print_audit(status: str, measurements: dict[str, int], violations) -> None:
    print(f"status={status} indexed_files={measurements['indexed_files']} "
          f"coverage_entries={measurements['coverage_entries']} "
          f"unknown_files={measurements['unknown_files']}")
    for violation in violations:
        print(f"violation {violation.code} {violation.path}: {violation.message}")


def _assert_outputs_separate(out: Path, inputs: tuple[Path, Path], tracked_paths: set[Path]) -> None:
    _assert_publishable(out, _OUTPUT_NAMES, tracked_paths, None)
    input_paths = {Path(path).resolve() for path in inputs}
    for name in _OUTPUT_NAMES:
        output = (out / name).resolve()
        if output in input_paths:
            raise RepositoryScanError(f"--out would overwrite a Phase 2 input artifact: {output}")


def _snapshot_reader(root: Path, context, snapshot_kind: str, project_index):
    revision = project_index["repository_revision"]
    try:
        git_entries = enumerate_git_snapshot_entries(
            context,
            snapshot_kind,
            revision if snapshot_kind == "git-tree" else None,
        )
    except RepositoryScanError:
        raise
    git_by_path = {entry.path: entry for entry in git_entries}
    regular_source_paths = frozenset(
        entry.path for entry in git_entries
        if entry.mode in {"100644", "100755"} and entry.object_type == "blob"
    )

    def read(path: str, indexed_entry) -> bytes:
        git_entry = git_by_path.get(path)
        if git_entry is None:
            raise RepositoryScanError(f"manifest is not present in the authoritative Git snapshot: {path}")
        if git_entry.mode in {_SYMLINK_MODE, _GITLINK_MODE} or git_entry.object_type == "commit":
            raise StackDetectionError(f"manifest is not a regular tracked file: {path}")
        if indexed_entry.get("content_kind") in {"binary", "symlink", "gitlink"}:
            raise StackDetectionError(f"manifest is not a regular text file: {path}")
        if indexed_entry["bytes"] > MAX_MANIFEST_BYTES:
            raise StackDetectionError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte size limit: {path}")

        if snapshot_kind == "git-tree":
            byte_count, digest, payload = hash_git_object(
                context.repository_root, git_entry.object_id, MAX_MANIFEST_BYTES + 1,
            )
            if byte_count > MAX_MANIFEST_BYTES:
                raise StackDetectionError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte size limit: {path}")
        else:
            try:
                target = guard_worktree_path(root, path, "file")
                with target.open("rb") as handle:
                    payload = handle.read(MAX_MANIFEST_BYTES + 1)
            except (OSError, RepositoryScanError) as exc:
                raise RepositoryScanError(f"cannot safely read tracked manifest {path}: {exc}") from exc
            if len(payload) > MAX_MANIFEST_BYTES:
                raise StackDetectionError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte size limit: {path}")
            byte_count = len(payload)
            digest = hashlib.sha256(payload).hexdigest()

        if byte_count != indexed_entry["bytes"] or digest != indexed_entry["sha256"]:
            raise StackDetectionError(f"manifest changed after the Phase 2 audit: {path}")
        return payload

    return read, regular_source_paths


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.generated_at is not None and not _GENERATED_AT_RE.fullmatch(args.generated_at):
        parser.error("--generated-at must match YYYY-MM-DDTHH:MM:SSZ")

    for label, path in (("project-index", args.index), ("coverage", args.coverage)):
        if not path.is_file():
            print(f"detect_stack.py: error: {label} input does not exist or is not a file: {path}", file=sys.stderr)
            return EXIT_OPERATIONAL
    try:
        project_index = load_artifact(args.index)
        coverage = load_artifact(args.coverage)
    except ArtifactValidationError as exc:
        if isinstance(exc.__cause__, OSError):
            print("detect_stack.py: error: cannot read a Phase 2 input artifact", file=sys.stderr)
            return EXIT_OPERATIONAL
        print("status=FAIL indexed_files=0 coverage_entries=0 unknown_files=0")
        print(f"violation SCHEMA_INVALID Phase 2 input: {exc}")
        return EXIT_FAILURE

    root = Path(args.root).resolve()
    try:
        audit = audit_coverage(project_index, coverage, root)
    except RepositoryScanError as exc:
        print(f"detect_stack.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    _print_audit(audit.status, audit.measurements, audit.violations)
    if audit.status == "FAIL":
        return EXIT_FAILURE

    try:
        context = discover_git_context(root)
        tracked_paths = {(root / item["path"]).resolve() for item in project_index["files"]}
        _assert_outputs_separate(args.out, (args.index, args.coverage), tracked_paths)
        read_manifest, regular_source_paths = _snapshot_reader(
            root, context, project_index["project"]["snapshot_kind"], project_index,
        )
        profile, evidence = build_stack_artifacts(
            project_index,
            coverage,
            read_manifest,
            generated_at=args.generated_at or utc_now(),
            regular_source_paths=regular_source_paths,
            g01_status=audit.status,
            unknown_files=audit.measurements["unknown_files"],
        )
    except StackDetectionError as exc:
        print(f"detect_stack.py: invalid declaration input: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except (RepositoryScanError, OSError) as exc:
        print(f"detect_stack.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    # Close the read/parse window against ordinary worktree changes before publishing.
    try:
        final_audit = audit_coverage(project_index, coverage, root)
    except RepositoryScanError as exc:
        print(f"detect_stack.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    if final_audit.status == "FAIL":
        _print_audit(final_audit.status, final_audit.measurements, final_audit.violations)
        print("detect_stack.py: error: Phase 2 snapshot changed before publication", file=sys.stderr)
        return EXIT_FAILURE

    try:
        _publish(args.out, {
            _OUTPUT_NAMES[0]: dumps_artifact(profile),
            _OUTPUT_NAMES[1]: dumps_artifact(evidence),
        })
    except OSError as exc:
        print(f"detect_stack.py: error: cannot publish stack artifacts: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    verified = sum(item["status"] == "VERIFIED" for item in profile["detections"])
    likely = sum(item["status"] == "LIKELY" for item in profile["detections"])
    print(f"published stack-profile.json and evidence.json; declarations={len(profile['detections'])} "
          f"verified={verified} likely={likely} evidence={len(evidence['items'])} "
          f"snapshot={project_index['project']['snapshot_kind']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
