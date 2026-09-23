#!/usr/bin/env python3
"""Scan a Git repository into canonical Project DeepDive coverage artifacts.

The command builds and audits both documents in memory, stages both payloads,
and rolls back handled publication failures when possible. It contains no
classification rules: those live in ``file_classification``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from artifact_contract import dumps_artifact
from coverage_audit import audit_coverage
from repository_scan import RepositoryScanError, ScanOptions, scan_repository

PROJECT_INDEX_NAME = "project-index.json"
COVERAGE_NAME = "coverage.json"
GENERATED_AT_FORMAT = "YYYY-MM-DDTHH:MM:SSZ"
_GENERATED_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


class PublicationRollbackError(OSError):
    """A handled publication failure could not restore every previous output."""


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan a Git repository into project-index.json and coverage.json",
    )
    parser.add_argument("--root", required=True, type=Path,
                        help="Git repository or analysed subtree to scan")
    parser.add_argument("--out", required=True, type=Path,
                        help="directory that receives project-index.json and coverage.json")
    parser.add_argument("--snapshot", choices=("worktree", "git-tree"), default="worktree",
                        help="hash current worktree bytes (default) or the committed revision")
    parser.add_argument("--overrides", type=Path, default=None,
                        help="exact-path coverage override JSON")
    parser.add_argument("--generated-at", dest="generated_at", default=None, metavar=GENERATED_AT_FORMAT,
                        help=f"fixed generation timestamp ({GENERATED_AT_FORMAT}); defaults to now")
    parser.add_argument("--require-complete", action="store_true",
                        help="fail when any tracked file is still UNKNOWN")
    return parser


def _publish(out_dir: Path, documents: dict[str, str]) -> None:
    """Stage both outputs and roll back only final replacements completed here."""
    out_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    owned_backups: set[Path] = set()
    backups: dict[Path, Path | None] = {}
    replaced: list[Path] = []

    def cleanup(paths, keep=frozenset()) -> None:
        for path in paths:
            if path in keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def preserve_existing(final: Path) -> Path | None:
        try:
            info = final.lstat()
        except FileNotFoundError:
            return None
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise OSError(f"cannot replace artifact target that is not a file: {final}")

        handle = tempfile.NamedTemporaryFile(
            "wb",
            dir=out_dir,
            prefix=f".{final.name}.",
            suffix=".bak",
            delete=False,
        )
        backup = Path(handle.name)
        handle.close()
        owned_backups.add(backup)
        backup.unlink()
        shutil.copy2(final, backup, follow_symlinks=False)
        return backup

    try:
        for name, text in documents.items():
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=out_dir,
                prefix=f".{name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, out_dir / name))
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        for _temporary, final in staged:
            backups[final] = preserve_existing(final)
        for temporary, final in staged:
            os.replace(temporary, final)
            # A failed replacement may leave a concurrent writer's target in place.
            replaced.append(final)
    except BaseException as publish_error:
        rollback_errors: list[tuple[Path, Path | None, OSError]] = []
        for final in reversed(replaced):
            backup = backups[final]
            try:
                if backup is None:
                    final.unlink(missing_ok=True)
                else:
                    os.replace(backup, final)
            except OSError as rollback_error:
                rollback_errors.append((final, backup, rollback_error))

        retained_backups = {backup for _final, backup, _error in rollback_errors if backup is not None}
        cleanup((temporary for temporary, _final in staged))
        cleanup(owned_backups, keep=retained_backups)
        if rollback_errors:
            details = []
            for final, backup, error in rollback_errors:
                detail = f"{final.name}: {error}"
                if backup is not None:
                    detail += f"; original backup retained at {backup}"
                details.append(detail)
            raise PublicationRollbackError(
                f"publication failed: {publish_error}; rollback failed for "
                + "; ".join(details)
            ) from publish_error
        raise
    cleanup((temporary for temporary, _final in staged))
    cleanup(owned_backups)


def _assert_publishable(
    out_dir: Path,
    names: tuple[str, ...],
    tracked_paths: set[Path],
    overrides_path: Path | None,
) -> None:
    for name in names:
        final = (out_dir / name).resolve()
        if overrides_path is not None and final == Path(overrides_path).resolve():
            raise RepositoryScanError(f"--out would overwrite the override input: {final}")
        if final in tracked_paths:
            raise RepositoryScanError(f"--out would overwrite a tracked repository file: {final}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    generated_at = args.generated_at or utc_now()
    if args.generated_at is not None and not _GENERATED_AT_RE.fullmatch(args.generated_at):
        parser.error(f"--generated-at must match {GENERATED_AT_FORMAT}")

    analysis_root = Path(args.root).resolve()
    names = (PROJECT_INDEX_NAME, COVERAGE_NAME)
    try:
        scan = scan_repository(ScanOptions(
            root=args.root,
            snapshot_kind=args.snapshot,
            generated_at=generated_at,
            overrides_path=args.overrides,
        ))
    except RepositoryScanError as exc:
        print(f"scan_repository.py: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    result = audit_coverage(scan.project_index, scan.coverage, analysis_root,
                            require_complete=args.require_complete)
    measurements = result.measurements
    print(f"status={result.status} indexed_files={measurements['indexed_files']} "
          f"coverage_entries={measurements['coverage_entries']} "
          f"unknown_files={measurements['unknown_files']}")
    for violation in result.violations:
        print(f"violation {violation.code} {violation.path}: {violation.message}")
    if result.status == "FAIL":
        return EXIT_FAILURE

    tracked_paths = {
        (analysis_root / entry["path"]).resolve() for entry in scan.project_index["files"]
    }
    try:
        _assert_publishable(args.out, names, tracked_paths, args.overrides)
    except RepositoryScanError as exc:
        print(f"scan_repository.py: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        _publish(args.out, {
            PROJECT_INDEX_NAME: dumps_artifact(scan.project_index),
            COVERAGE_NAME: dumps_artifact(scan.coverage),
        })
    except OSError as exc:
        print(f"scan_repository.py: error: cannot publish artifacts: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
