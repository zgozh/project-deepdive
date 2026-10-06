#!/usr/bin/env python3
"""Generate applicable static bundles from Phase 2 inputs and package them."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import _strict_json_loads, validate_artifact
from coverage_audit import audit_coverage
from phase3_bundle_audit import _family_paths
import phase3_run


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
EXIT_OK = 0
EXIT_FAILURE = 1


class RunPhase3Error(ValueError):
    """An integrated run failed before a verified package was published."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Phase 3A and applicable static-analysis bundles from an "
            "existing Phase 2 pair, then assemble an E1-audited phase3-run"
        ),
    )
    parser.add_argument("--root", required=True, type=Path, help="target Git repository")
    parser.add_argument("--project-index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--out", required=True, type=Path, help="new output directory outside the target")
    return parser


def _run_cli(script: str, arguments: list[str], adapter: str) -> None:
    command = [sys.executable, str(SCRIPTS / script), *arguments]
    try:
        result = subprocess.run(
            command,
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise RunPhase3Error(f"ADAPTER_FAILED_{adapter.upper()}") from None
    if result.returncode != 0:
        # Tool diagnostics may contain repository paths or source-derived values.
        raise RunPhase3Error(f"ADAPTER_FAILED_{adapter.upper()}")


def _read_phase2(path: Path) -> tuple[bytes, Mapping[str, Any]]:
    raw = path.read_bytes()
    artifact = validate_artifact(_strict_json_loads(raw.decode("utf-8")))
    return raw, artifact


def _stage_phase2(staging: Path, index_bytes: bytes, coverage_bytes: bytes) -> tuple[Path, Path]:
    phase2 = staging / "phase2"
    phase2.mkdir()
    index_path = phase2 / "project-index.json"
    coverage_path = phase2 / "coverage.json"
    index_path.write_bytes(index_bytes)
    coverage_path.write_bytes(coverage_bytes)
    return index_path, coverage_path


def _common_args(root: Path, index: Path, coverage: Path) -> list[str]:
    return [
        "--root", str(root),
        "--index", str(index),
        "--coverage", str(coverage),
    ]


def _assert_java_v12_bytes(java_c1: Path, java_c2: Path) -> None:
    for name in ("static-analysis-v1.2.json", "evidence-v1.2.json"):
        try:
            retained = (java_c2 / name).read_bytes()
            original = (java_c1 / ("static-analysis.json" if name.startswith("static-analysis") else "evidence.json")).read_bytes()
        except OSError:
            raise RunPhase3Error("JAVA_C1_C2_PAIR_MISSING") from None
        if retained != original:
            raise RunPhase3Error("JAVA_C1_C2_PAIR_MISMATCH")


def _build_adapters(
    *,
    root: Path,
    index_path: Path,
    coverage_path: Path,
    project_index: Mapping[str, Any],
    applicable: Mapping[str, set[str]],
    staging: Path,
) -> dict[str, Path | None]:
    profile_dir = staging / "phase3a"
    profile_path = profile_dir / "stack-profile.json"
    phase3a_evidence_path = profile_dir / "evidence.json"
    _run_cli(
        "detect_stack.py",
        [
            *_common_args(root, index_path, coverage_path),
            "--out", str(profile_dir),
            "--generated-at", str(project_index["generated_at"]),
        ],
        "phase3a",
    )

    members: dict[str, Path | None] = {
        "project_index": index_path,
        "coverage": coverage_path,
        "stack_profile": profile_path,
        "phase3a_evidence": phase3a_evidence_path,
        "python_analysis": None,
        "python_evidence": None,
        "java_analysis_v12": None,
        "java_evidence_v12": None,
        "java_analysis_v13": None,
        "java_evidence_v13": None,
        "frontend_analysis": None,
        "frontend_evidence": None,
    }
    common = _common_args(root, index_path, coverage_path)

    if applicable["python"]:
        python_dir = staging / "python"
        _run_cli(
            "analyze_python.py",
            [
                *common,
                "--stack-profile", str(profile_path),
                "--evidence", str(phase3a_evidence_path),
                "--out", str(python_dir),
                "--analysis-version", "1.1.0",
            ],
            "python",
        )
        members["python_analysis"] = python_dir / "static-analysis.json"
        members["python_evidence"] = python_dir / "evidence.json"

    if applicable["java"]:
        java_c1 = staging / "java-v12"
        java_c2 = staging / "java-c2"
        _run_cli(
            "analyze_java.py",
            [
                *common,
                "--stack-profile", str(profile_path),
                "--evidence", str(phase3a_evidence_path),
                "--out", str(java_c1),
            ],
            "java_v12",
        )
        _run_cli(
            "analyze_java_frameworks.py",
            [
                *common,
                "--stack-profile", str(profile_path),
                "--phase3a-evidence", str(phase3a_evidence_path),
                "--java-analysis-v1.2", str(java_c1 / "static-analysis.json"),
                "--java-evidence-v1.2", str(java_c1 / "evidence.json"),
                "--out", str(java_c2),
            ],
            "java_v13",
        )
        _assert_java_v12_bytes(java_c1, java_c2)
        members["java_analysis_v12"] = java_c1 / "static-analysis.json"
        members["java_evidence_v12"] = java_c1 / "evidence.json"
        members["java_analysis_v13"] = java_c2 / "static-analysis.json"
        members["java_evidence_v13"] = java_c2 / "evidence.json"

    if applicable["frontend"]:
        frontend_dir = staging / "frontend"
        _run_cli(
            "analyze_frontend_snapshot.py",
            [
                *common,
                "--stack-profile", str(profile_path),
                "--phase3a-evidence", str(phase3a_evidence_path),
                "--out", str(frontend_dir),
                "--analysis-version", "1.5.0",
            ],
            "frontend",
        )
        members["frontend_analysis"] = frontend_dir / "static-analysis.json"
        members["frontend_evidence"] = frontend_dir / "evidence.json"

    return members


def _summary(report: Mapping[str, Any]) -> str:
    audit = report["e1_audit"]
    counts = audit["counts"]
    adapters = audit["adapters"]
    return (
        f"status={report['status']} g01={audit['g01']['before']}/{audit['g01']['after']} "
        f"tracked={counts['tracked_files']} covered={counts['covered_tracked_files']} "
        f"unknown={counts['unknown_tracked_files']} analyzed={counts['analyzed_files']} "
        f"skipped={counts['skipped_files']} partial={counts['partial_files']} "
        f"unreported={counts['unreported_source_files']} outside={counts['outside_analyzer_files']}\n"
        "adapters=" + ",".join(
            f"{name}:{adapters[name]['status']}" for name in ("python", "java", "frontend")
        )
        + "\nlimitations=" + ",".join(audit["limitations"])
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project_index_bytes, project_index = _read_phase2(args.project_index)
        coverage_bytes, coverage = _read_phase2(args.coverage)
        initial_g01 = audit_coverage(project_index, coverage, args.root)
    except Exception:
        print("status=FAIL\nlimitations=PHASE2_INPUT_INVALID")
        return EXIT_FAILURE

    if initial_g01.status == "FAIL":
        print("status=FAIL\nlimitations=G01_FAILED")
        return EXIT_FAILURE

    try:
        try:
            output = phase3_run._output_path(args.root, args.out)
        except phase3_run.Phase3RunError:
            raise RunPhase3Error("OUTPUT_INVALID") from None
        applicable, _tracked, _covered, _outside = _family_paths(project_index, coverage)
        if not isinstance(project_index.get("generated_at"), str):
            raise RunPhase3Error("PHASE2_TIMESTAMP_INVALID")
        with tempfile.TemporaryDirectory(
            prefix=".phase3-integrated-staging-",
            dir=output.parent,
        ) as temporary:
            staging = Path(temporary)
            staged_index_path, staged_coverage_path = _stage_phase2(
                staging, project_index_bytes, coverage_bytes,
            )
            input_paths = _build_adapters(
                root=args.root,
                index_path=staged_index_path,
                coverage_path=staged_coverage_path,
                project_index=project_index,
                applicable=applicable,
                staging=staging,
            )
            manifest = phase3_run.assemble_phase3_run(
                root=args.root,
                input_paths=input_paths,
                out=output,
            )
    except RunPhase3Error as exc:
        print(f"status=FAIL\nlimitations={exc.code}")
        return EXIT_FAILURE
    except Exception:
        print("status=FAIL\nlimitations=RUN_ASSEMBLY_FAILED")
        return EXIT_FAILURE

    print(_summary(manifest))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
