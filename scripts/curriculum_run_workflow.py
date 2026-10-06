#!/usr/bin/env python3
"""Authenticated, read-only CLI for deterministic curriculum run planning."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifact_contract import ArtifactValidationError, _strict_json_loads, dumps_artifact, validate_artifact
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _assert_no_link_components,
    _capture_bundle,
    _check_external_output,
    _project_claim_facts,
    _publish_new_file,
    _recheck_inputs,
    _read_review_file,
    _inside,
)
from phase6_curriculum_run import (
    Phase6CurriculumRunError,
    build_curriculum_run_plan,
)


def _has_project_refs(unit: dict[str, Any]) -> bool:
    refs = unit["project_refs"]
    return any(refs[key] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths"))


@dataclass(frozen=True)
class AuthenticatedCurriculumRunPlan:
    inputs: ChapterInputPaths
    path: Path
    raw: bytes
    plan: dict[str, Any]
    authenticated: Any
    reads: dict[str, Any]
    prerequisite_graph: dict[str, Any]
    curriculum: dict[str, Any]

    def recheck(self) -> None:
        self.authenticated.recheck()
        _recheck_inputs(self.reads)
        if _read_review_file(self.path) != self.raw:
            raise Phase6ChapterError("INPUT_CHANGED")


def read_canonical_curriculum_run_plan(
    inputs: ChapterInputPaths, plan_path: Path,
) -> tuple[dict[str, Any], bytes, Path]:
    """Read a canonical external plan without replaying Phase 4/5."""
    path = _absolute(plan_path)
    _assert_no_link_components(path)
    if path.is_symlink() or not path.is_file():
        raise Phase6CurriculumRunError("PLAN_INVALID")
    for boundary in (_absolute(inputs.root), _absolute(inputs.run_dir), _absolute(inputs.phase4c_package)):
        try:
            boundary = boundary.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise Phase6ChapterError("INPUT_INVALID") from exc
        if _inside(path, boundary):
            raise Phase6CurriculumRunError("PLAN_INVALID")
    if path in tuple(_absolute(item) for item in (
        inputs.phase4c_package, inputs.claim_candidates_path, inputs.claim_evidence_path,
        inputs.claim_evidence_graph_path, inputs.prerequisite_candidates_path, inputs.prerequisite_graph_path,
        inputs.curriculum_candidates_path, inputs.curriculum_path, inputs.run_dir, inputs.root,
    )):
        raise Phase6CurriculumRunError("PLAN_INVALID")
    try:
        raw = _read_review_file(path)
        plan = _strict_json_loads(raw.decode("utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("plan is not an object")
        validate_artifact(plan)
        if plan.get("artifact_kind") != "curriculum-run-plan" or dumps_artifact(plan).encode("utf-8") != raw:
            raise ValueError("plan is not canonical")
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError, Phase6ChapterError) as exc:
        raise Phase6CurriculumRunError("PLAN_INVALID") from exc
    return plan, raw, path


def _derive_plan(inputs: ChapterInputPaths):
    authenticated, reads, prerequisite_graph, curriculum = _capture_bundle(inputs)

    readiness: dict[str, str] = {}
    for unit in curriculum["units"]:
        if (
            unit["origin"] != "CANDIDATE"
            or unit["scope"] != "PROJECT_SPECIFIC"
            or not _has_project_refs(unit)
        ):
            continue
        try:
            _project_claim_facts(
                inputs,
                unit["id"],
                reads,
                prerequisite_graph,
                curriculum,
                authenticated,
            )
        except Phase6ChapterError as exc:
            if exc.code == "CLAIM_SET_UNSUPPORTED":
                readiness[unit["id"]] = "NO_QUALIFYING_SUPPORTED_CLAIM"
            elif exc.code == "CLAIM_SCOPE_AMBIGUOUS":
                readiness[unit["id"]] = "CLAIM_SCOPE_AMBIGUOUS"
            else:
                raise
        else:
            readiness[unit["id"]] = "READY"

    plan = build_curriculum_run_plan(
        authenticated,
        reads,
        prerequisite_graph,
        curriculum,
        readiness,
    )
    return plan, authenticated, reads, prerequisite_graph, curriculum


def authenticate_curriculum_run_plan(
    inputs: ChapterInputPaths, *, plan_path: Path,
) -> AuthenticatedCurriculumRunPlan:
    """Authenticate a canonical D2B1 plan against one current Phase 4/5 capture."""
    plan, raw, path = read_canonical_curriculum_run_plan(inputs, plan_path)
    rebuilt, authenticated, reads, prerequisite_graph, curriculum = _derive_plan(inputs)
    if dumps_artifact(rebuilt).encode("utf-8") != raw:
        raise Phase6CurriculumRunError("PLAN_STALE")
    authenticated.recheck()
    _recheck_inputs(reads)
    if _read_review_file(path) != raw:
        raise Phase6CurriculumRunError("PLAN_CHANGED")
    return AuthenticatedCurriculumRunPlan(
        inputs, path, raw, plan, authenticated, reads, prerequisite_graph, curriculum,
    )


def plan_curriculum_run(inputs: ChapterInputPaths, *, out: Path) -> dict[str, Any]:
    """Capture once, route every unit, recheck freshness, and publish one new plan."""
    output = _check_external_output(out, inputs, must_exist=False)
    plan, authenticated, reads, _prerequisite_graph, _curriculum = _derive_plan(inputs)
    payload = dumps_artifact(plan).encode("utf-8")

    authenticated.recheck()
    _recheck_inputs(reads)
    output = _check_external_output(out, inputs, must_exist=False)
    _publish_new_file(output, payload)
    return plan


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase4c-package", required=True, type=Path)
    parser.add_argument("--claim-candidates", required=True, type=Path)
    parser.add_argument("--claim-evidence", required=True, type=Path)
    parser.add_argument("--claim-evidence-graph", required=True, type=Path)
    parser.add_argument("--prerequisite-candidates", required=True, type=Path)
    parser.add_argument("--prerequisite-graph", required=True, type=Path)
    parser.add_argument("--curriculum-candidates", required=True, type=Path)
    parser.add_argument("--curriculum", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan deterministic Phase 6 routes for an authenticated curriculum.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="authenticate and publish one external curriculum run plan")
    _add_inputs(plan)
    plan.add_argument("--out", required=True, type=Path)
    for command in ("start", "advance", "resume"):
        sub = subparsers.add_parser(command, help=f"{command} one authenticated curriculum run state")
        _add_inputs(sub)
        sub.add_argument("--plan", required=True, type=Path)
        sub.add_argument("--state-dir", required=True, type=Path)
    return parser


def _inputs(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=args.phase4c_package,
        claim_candidates_path=args.claim_candidates,
        claim_evidence_path=args.claim_evidence,
        claim_evidence_graph_path=args.claim_evidence_graph,
        prerequisite_candidates_path=args.prerequisite_candidates,
        prerequisite_graph_path=args.prerequisite_graph,
        curriculum_candidates_path=args.curriculum_candidates,
        curriculum_path=args.curriculum,
        run_dir=args.run_dir,
        root=args.root,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = plan_curriculum_run(_inputs(args), out=args.out)
            print(
                f"status=PLANNED run_plan_id={plan['run_plan_id']} "
                f"units={plan['unit_count']} source_status={plan['source_status']}"
            )
        else:
            from curriculum_run_execution_workflow import main as execution_main

            return execution_main(argv)
        return 0
    except Phase6ChapterError as exc:
        print(f"error={exc.code}")
        return 2
    except Phase6CurriculumRunError as exc:
        print(f"error={exc.code}")
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(f"error={code}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
