#!/usr/bin/env python3
"""Assemble and revalidate portable Phase 3 artifact runs."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact
from audit_phase3 import build_parser as _build_audit_parser
from coverage_audit import audit_coverage
from phase3_bundle_audit import audit_phase3_bundles


MANIFEST_NAME = "phase3-run.json"
SCHEMA_VERSION = "1.0.0"
DOCUMENTATION_SCHEMA_VERSION = "1.1.0"
_GROUPS = ("phase2", "phase3a", "python", "java-v12", "java-v13", "frontend")
_DOCUMENTATION_GROUPS = (*_GROUPS, "documentation")
_REQUIRED_MEMBERS = frozenset({
    "phase2/project-index.json",
    "phase2/coverage.json",
    "phase3a/stack-profile.json",
    "phase3a/evidence.json",
})

# Keys match audit_phase3.py's argparse destinations and audit API parameters.
MEMBER_PATHS = {
    "project_index": "phase2/project-index.json",
    "coverage": "phase2/coverage.json",
    "stack_profile": "phase3a/stack-profile.json",
    "phase3a_evidence": "phase3a/evidence.json",
    "python_analysis": "python/static-analysis.json",
    "python_evidence": "python/evidence.json",
    "java_analysis_v12": "java-v12/static-analysis.json",
    "java_evidence_v12": "java-v12/evidence.json",
    "java_analysis_v13": "java-v13/static-analysis.json",
    "java_evidence_v13": "java-v13/evidence.json",
    "frontend_analysis": "frontend/static-analysis.json",
    "frontend_evidence": "frontend/evidence.json",
}
DOCUMENTATION_MEMBER_PATH = "documentation/evidence.json"
_ALL_MEMBER_PATHS = {**MEMBER_PATHS, "documentation_evidence": DOCUMENTATION_MEMBER_PATH}
_MEMBER_PATH_SET = frozenset(_ALL_MEMBER_PATHS.values())
_REQUIRED_KEYS = frozenset({"project_index", "coverage", "stack_profile", "phase3a_evidence"})
_E1_MEMBER_KEYS = frozenset(MEMBER_PATHS)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class Phase3RunError(ValueError):
    """A run package could not be safely assembled or validated."""


def _is_link_or_junction(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _assert_no_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_link_or_junction(current):
            raise Phase3RunError("filesystem link or junction is not allowed")


def _output_path(root: str | Path, out: str | Path) -> Path:
    try:
        project_root = Path(root).resolve(strict=True)
        output = Path(os.path.abspath(os.fspath(out)))
        parent = output.parent
        if not project_root.is_dir() or not parent.is_dir():
            raise Phase3RunError("project root and output parent must be existing directories")
        _assert_no_link_components(parent)
        if os.path.lexists(output):
            raise Phase3RunError("output path already exists")
        resolved_parent = parent.resolve(strict=True)
        resolved_output = resolved_parent / output.name
        if resolved_output == project_root or project_root in resolved_output.parents:
            raise Phase3RunError("output path must be outside the audited project")
        return output
    except Phase3RunError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise Phase3RunError("project root or output path is invalid") from exc


def _validate_input_paths(input_paths: Mapping[str, str | Path | None]) -> dict[str, Path | None]:
    unknown = set(input_paths) - set(_ALL_MEMBER_PATHS)
    if unknown:
        raise Phase3RunError("an input slot is unsupported")
    normalized: dict[str, Path | None] = {key: None for key in _ALL_MEMBER_PATHS}
    for key, value in input_paths.items():
        if value is None:
            continue
        path = Path(value)
        try:
            info = path.stat()
        except OSError as exc:
            raise Phase3RunError("an input artifact is unavailable") from exc
        if not stat.S_ISREG(info.st_mode):
            raise Phase3RunError("an input artifact must be a regular file")
        normalized[key] = path
    if any(normalized[key] is None for key in _REQUIRED_KEYS):
        raise Phase3RunError("the Phase 2 and Phase 3A inputs are required")
    return normalized


def _copy_members(stage: Path, input_paths: Mapping[str, Path | None]) -> None:
    groups = _DOCUMENTATION_GROUPS if input_paths.get("documentation_evidence") is not None else _GROUPS
    for group in groups:
        (stage / group).mkdir()
    for key, relative in _ALL_MEMBER_PATHS.items():
        source = input_paths[key]
        if source is None:
            continue
        destination = stage.joinpath(*PurePosixPath(relative).parts)
        shutil.copyfile(source, destination)


def _load_bundle(directory: Path) -> dict[str, Mapping[str, Any] | None]:
    artifacts: dict[str, Mapping[str, Any] | None] = {}
    for key, relative in _ALL_MEMBER_PATHS.items():
        path = directory.joinpath(*PurePosixPath(relative).parts)
        if not os.path.lexists(path):
            artifacts[key] = None
            continue
        if _is_link_or_junction(path) or not path.is_file():
            raise Phase3RunError("package member is not a regular file")
        try:
            artifacts[key] = load_artifact(path)
        except (ArtifactValidationError, OSError, ValueError) as exc:
            raise Phase3RunError("package member is invalid") from exc
    return artifacts


def _run_e1_audit(root: str | Path, directory: Path) -> tuple[dict[str, Any], dict[str, Mapping[str, Any] | None]]:
    artifacts = _load_bundle(directory)
    try:
        report = audit_phase3_bundles(
            root=root,
            **{key: artifacts[key] for key in _E1_MEMBER_KEYS},
        )
    except Exception as exc:
        raise Phase3RunError("Phase 3E1 audit could not be completed") from exc
    return report, artifacts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _member_records(directory: Path) -> list[dict[str, str]]:
    members = []
    for key, relative in _ALL_MEMBER_PATHS.items():
        path = directory.joinpath(*PurePosixPath(relative).parts)
        if not os.path.lexists(path):
            continue
        artifact = load_artifact(path)
        members.append({
            "path": relative,
            "artifact_kind": artifact["artifact_kind"],
            "schema_version": artifact["schema_version"],
            "sha256": _sha256_file(path),
        })
    return members


def _new_manifest(directory: Path, report: dict[str, Any], artifacts: Mapping[str, Any]) -> dict[str, Any]:
    index = artifacts["project_index"]
    profile = artifacts["stack_profile"]
    if index is None or profile is None:
        raise Phase3RunError("required Phase 2 or Phase 3A artifact is missing")
    try:
        manifest = {
            "artifact_kind": "phase3-run",
            "schema_version": (
                DOCUMENTATION_SCHEMA_VERSION
                if artifacts.get("documentation_evidence") is not None else SCHEMA_VERSION
            ),
            "repository_revision": index["repository_revision"],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status": report["status"],
            "snapshot_kind": index["project"]["snapshot_kind"],
            "source_metadata": profile["source_metadata"],
            "members": _member_records(directory),
            "e1_audit": report,
        }
    except (KeyError, TypeError) as exc:
        raise Phase3RunError("required package metadata is unavailable") from exc
    return manifest


def _package_groups(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    version = manifest.get("schema_version")
    if version == SCHEMA_VERSION:
        groups = _GROUPS
        documentation_expected = False
    elif version == DOCUMENTATION_SCHEMA_VERSION:
        groups = _DOCUMENTATION_GROUPS
        documentation_expected = True
    else:
        raise Phase3RunError("run manifest schema version is unsupported")
    documentation_present = any(
        isinstance(item, Mapping) and item.get("path") == DOCUMENTATION_MEMBER_PATH
        for item in manifest.get("members", [])
    )
    if documentation_present != documentation_expected:
        raise Phase3RunError("documentation member does not match the run manifest version")
    return groups


def _check_package_files(directory: Path, manifest: Mapping[str, Any]) -> set[str]:
    members = manifest["members"]
    groups = _package_groups(manifest)
    _assert_no_link_components(directory)
    if _is_link_or_junction(directory) or not directory.is_dir():
        raise Phase3RunError("package directory is not a regular directory")
    expected_top = set(groups) | {MANIFEST_NAME}
    try:
        entries = {entry.name: entry for entry in directory.iterdir()}
    except OSError as exc:
        raise Phase3RunError("package directory cannot be read") from exc
    if set(entries) != expected_top:
        raise Phase3RunError("package directory layout is invalid")
    for name in groups:
        folder = entries[name]
        if _is_link_or_junction(folder) or not folder.is_dir():
            raise Phase3RunError("package directory layout contains an unsafe entry")
    manifest_path = entries[MANIFEST_NAME]
    if _is_link_or_junction(manifest_path) or not manifest_path.is_file():
        raise Phase3RunError("run manifest is not a regular file")

    member_paths: list[str] = []
    for record in members:
        relative = record.get("path")
        if not isinstance(relative, str) or relative not in _MEMBER_PATH_SET:
            raise Phase3RunError("manifest contains an unsafe or unknown member path")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative or pure.as_posix() != relative:
            raise Phase3RunError("manifest contains an unsafe member path")
        member_paths.append(relative)
    if len(member_paths) != len(set(member_paths)) or not _REQUIRED_MEMBERS.issubset(member_paths):
        raise Phase3RunError("manifest member inventory is incomplete or duplicated")

    actual_paths: set[str] = set()
    for group in groups:
        folder = directory / group
        for entry in folder.iterdir():
            if _is_link_or_junction(entry) or not entry.is_file():
                raise Phase3RunError("package member layout contains an unsafe entry")
            relative = f"{group}/{entry.name}"
            if relative not in _MEMBER_PATH_SET:
                raise Phase3RunError("package contains an unlisted file")
            actual_paths.add(relative)
    if actual_paths != set(member_paths):
        raise Phase3RunError("manifest and package member inventory differ")
    return actual_paths


def _validate_manifest_members(directory: Path, manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    _check_package_files(directory, manifest)
    loaded: dict[str, Mapping[str, Any]] = {}
    for record in manifest["members"]:
        relative = record["path"]
        path = directory.joinpath(*PurePosixPath(relative).parts)
        try:
            artifact = load_artifact(path)
        except (ArtifactValidationError, OSError, ValueError) as exc:
            raise Phase3RunError("a package member failed artifact validation") from exc
        if (
            artifact["artifact_kind"] != record["artifact_kind"]
            or artifact["schema_version"] != record["schema_version"]
            or _sha256_file(path) != record["sha256"]
        ):
            raise Phase3RunError("a package member identity or digest does not match the manifest")
        loaded[relative] = artifact

    if manifest["schema_version"] == DOCUMENTATION_SCHEMA_VERSION:
        documentation = loaded.get(DOCUMENTATION_MEMBER_PATH)
        if (
            documentation is None
            or documentation.get("artifact_kind") != "evidence"
            or documentation.get("schema_version") != "1.3.0"
        ):
            raise Phase3RunError("documentation member has an unsupported artifact version")

    index = loaded[MEMBER_PATHS["project_index"]]
    profile = loaded[MEMBER_PATHS["stack_profile"]]
    phase3a_evidence = loaded[MEMBER_PATHS["phase3a_evidence"]]
    if (
        manifest["repository_revision"] != index["repository_revision"]
        or manifest["snapshot_kind"] != index["project"]["snapshot_kind"]
        or manifest["source_metadata"] != profile.get("source_metadata")
        or manifest["source_metadata"] != phase3a_evidence.get("source_metadata")
        or manifest["source_metadata"].get("snapshot_kind") != manifest["snapshot_kind"]
    ):
        raise Phase3RunError("manifest snapshot metadata does not match its members")
    return loaded


def validate_phase3_run(run_dir: str | Path, *, root: str | Path) -> dict[str, Any]:
    """Read a run package, verify its members, and recompute its E1 audit."""
    directory = Path(run_dir)
    try:
        _assert_no_link_components(directory)
        if _is_link_or_junction(directory) or not directory.is_dir():
            raise Phase3RunError("package directory is not a regular directory")
        manifest_path = directory / MANIFEST_NAME
        if _is_link_or_junction(manifest_path) or not manifest_path.is_file():
            raise Phase3RunError("run manifest is missing or unsafe")
        try:
            manifest = load_artifact(manifest_path)
        except (ArtifactValidationError, OSError, ValueError) as exc:
            raise Phase3RunError("run manifest is invalid") from exc
        loaded = _validate_manifest_members(directory, manifest)
        artifacts_by_key = {
            key: loaded.get(relative)
            for key, relative in _ALL_MEMBER_PATHS.items()
        }
        try:
            report = audit_phase3_bundles(
                root=root,
                **{key: artifacts_by_key[key] for key in _E1_MEMBER_KEYS},
            )
        except Exception as exc:
            raise Phase3RunError("Phase 3E1 revalidation could not be completed") from exc
        documentation = loaded.get(DOCUMENTATION_MEMBER_PATH)
        if documentation is not None:
            try:
                from repository_documentation_evidence import validate_repository_documentation_evidence

                validate_repository_documentation_evidence(
                    documentation,
                    root=root,
                    project_index=loaded[MEMBER_PATHS["project_index"]],
                    coverage=loaded[MEMBER_PATHS["coverage"]],
                    g01_status=report["g01"]["after"],
                    unknown_files=report["counts"]["unknown_tracked_files"],
                )
            except Exception as exc:
                raise Phase3RunError("repository documentation evidence does not match the pinned snapshot") from exc
        if report["status"] not in {"PASS", "PARTIAL"} or report != manifest["e1_audit"]:
            raise Phase3RunError("recomputed Phase 3E1 audit does not match the manifest")
        if manifest["status"] != report["status"]:
            raise Phase3RunError("run status does not match the recomputed audit")
        return manifest
    except Phase3RunError:
        raise
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        raise Phase3RunError("run package could not be validated") from exc


def _g01_signature(result) -> tuple[Any, ...]:
    return (
        result.status,
        tuple(sorted(result.measurements.items())),
        tuple((item.code, item.path, item.message) for item in result.violations),
    )


def _pinned_g01_signature(manifest: Mapping[str, Any], coverage: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        audit = manifest["e1_audit"]
        statuses = audit["g01"]
        counts = audit["counts"]
        status = statuses["before"]
        if status not in {"PASS", "PARTIAL"} or statuses["after"] != status:
            raise Phase3RunError("pinned Phase 3 G01 audit is not stable")
        measurements = {
            "indexed_files": counts["tracked_files"],
            "coverage_entries": len(coverage["entries"]),
            "unknown_files": counts["unknown_tracked_files"],
            "violations": 0,
        }
        if (
            measurements["indexed_files"] != coverage["tracked_file_count"]
            or measurements["unknown_files"] != coverage["unknown_count"]
        ):
            raise Phase3RunError("pinned Phase 3 G01 measurements are inconsistent")
        return status, tuple(sorted(measurements.items())), ()
    except Phase3RunError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase3RunError("pinned Phase 3 G01 audit is invalid") from exc


def _check_package_raw_identity(
    directory: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> None:
    """Check package bytes/layout after snapshot auditing without re-running E1."""
    try:
        _check_package_files(directory, manifest)
        if _sha256_file(manifest_path) != manifest_sha256:
            raise Phase3RunError("run manifest changed during freshness validation")
        for record in manifest["members"]:
            relative = record["path"]
            path = directory.joinpath(*PurePosixPath(relative).parts)
            _assert_no_link_components(path)
            if _is_link_or_junction(path) or not path.is_file():
                raise Phase3RunError("package member changed during freshness validation")
            if _sha256_file(path) != record["sha256"]:
                raise Phase3RunError("package member digest changed during freshness validation")
    except Phase3RunError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Phase3RunError("run package changed during freshness validation") from exc


def verify_phase3_run_freshness(
    run_dir: str | Path,
    *,
    root: str | Path,
    expected_manifest: Mapping[str, Any],
    expected_manifest_sha256: str,
) -> None:
    """Recheck package bytes and G01 after a prior full semantic validation.

    This freshness proof does not replace ``validate_phase3_run``: callers must
    first complete that full Phase3 semantic audit. Here the package schema and
    member identities are checked, then G01 is sampled twice against the pinned
    snapshot, and package bytes/layout are checked again to catch mid-check edits.
    No target code is executed and no lock or crash-atomicity guarantee is made.
    """
    directory = Path(run_dir)
    manifest_path = directory / MANIFEST_NAME
    try:
        _assert_no_link_components(directory)
        if _is_link_or_junction(directory) or not directory.is_dir():
            raise Phase3RunError("package directory is not a regular directory")
        if _is_link_or_junction(manifest_path) or not manifest_path.is_file():
            raise Phase3RunError("run manifest is missing or unsafe")
        if _sha256_file(manifest_path) != expected_manifest_sha256:
            raise Phase3RunError("run manifest changed after semantic validation")
        try:
            manifest = load_artifact(manifest_path)
        except (ArtifactValidationError, OSError, ValueError) as exc:
            raise Phase3RunError("run manifest is invalid") from exc
        if (
            _sha256_file(manifest_path) != expected_manifest_sha256
            or manifest != expected_manifest
        ):
            raise Phase3RunError("run manifest identity changed after semantic validation")

        loaded = _validate_manifest_members(directory, manifest)
        index = loaded[MEMBER_PATHS["project_index"]]
        coverage = loaded[MEMBER_PATHS["coverage"]]
        expected_signature = _pinned_g01_signature(manifest, coverage)
        try:
            before = audit_coverage(index, coverage, Path(root))
            after = audit_coverage(index, coverage, Path(root))
        except Exception as exc:
            raise Phase3RunError("G01 freshness audit could not be completed") from exc
        before_signature = _g01_signature(before)
        after_signature = _g01_signature(after)
        if (
            before.status not in {"PASS", "PARTIAL"}
            or after.status not in {"PASS", "PARTIAL"}
            or before_signature != expected_signature
            or after_signature != expected_signature
            or before_signature != after_signature
        ):
            raise Phase3RunError("G01 snapshot changed after semantic validation")

        _check_package_raw_identity(directory, manifest_path, manifest, expected_manifest_sha256)
    except Phase3RunError:
        raise
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        raise Phase3RunError("Phase 3 run freshness could not be verified") from exc


def _publish_directory(stage: Path, output: Path) -> None:
    if os.path.lexists(output):
        raise Phase3RunError("output path already exists")
    os.rename(stage, output)


def assemble_phase3_run(
    *,
    root: str | Path,
    input_paths: Mapping[str, str | Path | None],
    out: str | Path,
) -> dict[str, Any]:
    """Copy existing bundles to staging, audit their bytes, then publish."""
    output = _output_path(root, out)
    paths = _validate_input_paths(input_paths)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}.phase3-staging-",
            dir=output.parent,
        ) as temporary:
            stage = Path(temporary)
            _copy_members(stage, paths)
            report, artifacts = _run_e1_audit(root, stage)
            if report["status"] not in {"PASS", "PARTIAL"}:
                raise Phase3RunError("Phase 3E1 audit failed; run was not published")
            manifest = _new_manifest(stage, report, artifacts)
            manifest_text = dumps_artifact(manifest)
            with (stage / MANIFEST_NAME).open("w", encoding="utf-8", newline="\n") as destination:
                destination.write(manifest_text)
            validate_phase3_run(stage, root=root)
            _output_path(root, output)
            _publish_directory(stage, output)
            return manifest
    except Phase3RunError:
        raise
    except Exception as exc:
        raise Phase3RunError("Phase 3 run assembly failed") from exc


def _summary(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    adapters = report["adapters"]
    first = (
        f"status={report['status']} g01={report['g01']['before']}/{report['g01']['after']} "
        f"tracked={counts['tracked_files']} covered={counts['covered_tracked_files']} "
        f"unknown={counts['unknown_tracked_files']} analyzed={counts['analyzed_files']} "
        f"skipped={counts['skipped_files']} partial={counts['partial_files']} "
        f"unreported={counts['unreported_source_files']} outside={counts['outside_analyzer_files']}"
    )
    second = "adapters=" + ",".join(
        f"{name}:{adapters[name]['status']}" for name in ("python", "java", "frontend")
    )
    return f"{first}\n{second}\nlimitations=" + ",".join(report["limitations"])


def _assemble_parser() -> argparse.ArgumentParser:
    parser = _build_audit_parser()
    parser.description = "Copy and audit existing Phase 2 and Phase 3 bundles into a portable run directory"
    parser.add_argument("--documentation-evidence", type=Path,
                        help="optional repository documentation evidence v1.3.0")
    parser.add_argument("--out", required=True, type=Path, help="new output directory outside the audited project")
    return parser


def assemble_main(argv: list[str] | None = None) -> int:
    args = _assemble_parser().parse_args(argv)
    input_paths = {key: getattr(args, key) for key in _ALL_MEMBER_PATHS}
    try:
        manifest = assemble_phase3_run(root=args.root, input_paths=input_paths, out=args.out)
    except Exception:
        print("status=FAIL\nlimitations=RUN_ASSEMBLY_FAILED")
        return 1
    print(_summary(manifest["e1_audit"]))
    return 0


def validate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revalidate a portable Phase 3 run package from disk")
    parser.add_argument("run_dir", type=Path, help="phase3-run directory")
    parser.add_argument("--root", required=True, type=Path, help="audited project Git root for G01 and source checks")
    args = parser.parse_args(argv)
    try:
        manifest = validate_phase3_run(args.run_dir, root=args.root)
    except Exception:
        print("status=FAIL\nlimitations=RUN_VALIDATION_FAILED")
        return 1
    print(_summary(manifest["e1_audit"]))
    return 0


__all__ = [
    "MEMBER_PATHS",
    "Phase3RunError",
    "assemble_phase3_run",
    "validate_phase3_run",
    "verify_phase3_run_freshness",
]
