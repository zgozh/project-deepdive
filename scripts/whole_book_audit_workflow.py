"""Read-only D3b integrity audit over an existing D3a assembly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import phase6_chapter as chapter
import whole_book_workflow as d3a
from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
from phase6_curriculum_run_state import sha256
from phase6_whole_book_audit import audit_whole_book, render_audit_report


class WholeBookAuditError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise WholeBookAuditError(code)


def _read(path: Path) -> bytes:
    try:
        return chapter._read_review_file(path)
    except chapter.Phase6ChapterError as exc:
        raise WholeBookAuditError("INPUT_INVALID") from exc


def _disjoint(output: Path, *inputs: Path) -> None:
    for source in inputs:
        try:
            common = os.path.commonpath((os.path.normcase(str(output)), os.path.normcase(str(source))))
        except ValueError:
            _fail("OUTPUT_INVALID")
        if common in {os.path.normcase(str(output)), os.path.normcase(str(source))}:
            _fail("OUTPUT_INVALID")


def _selected_attempt(state: Mapping[str, Any], state_row: Mapping[str, Any], attempt_id: str) -> list[dict[str, Any]]:
    if state["schema_version"] == "1.0.0":
        if state_row.get("attempt_id") != attempt_id or state_row.get("execution_state") != "REVIEWED_DRAFT":
            _fail("STATE_BINDING_INVALID")
        return state_row["artifact_digests"]
    attempts = [row for row in state_row["attempts"] if row["attempt_id"] == attempt_id]
    if len(attempts) != 1 or attempts[0]["execution_state"] != "REVIEWED_DRAFT":
        _fail("STATE_BINDING_INVALID")
    return attempts[0]["artifact_digests"]


def audit_assembly(*, assembly_dir: Path, state_dir: Path, target_root: Path, out_dir: Path) -> dict[str, Any]:
    assembly_root = assembly_dir.absolute()
    state_root = state_dir.absolute()
    manifest_path = assembly_root / "whole-book-assembly.json"
    manifest_raw = _read(manifest_path)
    try:
        assembly = d3a._canonical_artifact(manifest_raw, "whole-book-assembly")
    except d3a.WholeBookWorkflowError as exc:
        raise WholeBookAuditError("ASSEMBLY_INVALID") from exc

    expected_outputs = {
        "whole_book": "WHOLE-BOOK.md",
        "answer_book": "WHOLE-ANSWER-BOOK.md",
        "quality_and_gaps": "QUALITY-AND-GAPS.md",
    }
    if {(row["role"], row["path"]) for row in assembly["outputs"]} != set(expected_outputs.items()):
        _fail("ASSEMBLY_OUTPUT_BINDING_INVALID")
    output_raws: dict[str, bytes] = {}
    checked_paths: dict[str, Path] = {}
    for output in assembly["outputs"]:
        path = assembly_root / output["path"]
        raw = _read(path)
        if sha256(raw) != output["sha256"]:
            _fail("ASSEMBLY_OUTPUT_DRIFT")
        output_raws[output["path"]] = raw
        checked_paths[output["path"]] = path

    revision = assembly["run_state_revision"]
    state_path = state_root / f"run-state-{revision:06d}.json"
    state_raw = _read(state_path)
    try:
        state = d3a._canonical_artifact(state_raw, "curriculum-run-state")
    except d3a.WholeBookWorkflowError as exc:
        raise WholeBookAuditError("STATE_INVALID") from exc
    state_bindings = {
        "run_state_id": assembly["run_state_id"],
        "state_revision": assembly["run_state_revision"],
        "run_plan_id": assembly["run_plan_id"],
        "run_plan_sha256": assembly["run_plan_sha256"],
        "schema_version": assembly["run_state_schema_version"],
        "repository_revision": assembly["repository_revision"],
        "snapshot_kind": assembly["snapshot_kind"],
        "source_metadata": assembly["source_metadata"],
        "source_status": assembly["source_status"],
        "source_run_manifest_sha256": assembly["source_run_manifest_sha256"],
        "unknown_files": assembly["unknown_files"],
        "curriculum_sha256": assembly["curriculum_sha256"],
        "input_digests": assembly["input_digests"],
    }
    if (
        sha256(state_raw) != assembly["run_state_sha256"]
        or any(state.get(key) != value for key, value in state_bindings.items())
        or len(state["units"]) != len(assembly["units"])
    ):
        _fail("STATE_BINDING_INVALID")
    for position, (row, state_row) in enumerate(zip(assembly["units"], state["units"], strict=True), start=1):
        if any((
            row["position"] != position,
            state_row["position"] != position,
            row["unit_id"] != state_row["unit_id"],
            row["unit_identity_sha256"] != state_row["unit_identity_sha256"],
            row["route"] != state_row["route"],
            row["plan_state"] != state_row["plan_state"],
            row["reason_codes"] != state_row["reason_codes"],
            row["blocked_by"] != state_row["blocked_by"],
            row["prerequisite_ids"] != state_row["prerequisite_ids"],
            row["execution_state"] != state_row["execution_state"],
        )):
            _fail("STATE_BINDING_INVALID")

    answers = {
        row["unit_id"]: row["project_answer"]
        for row in assembly["units"] if row.get("project_answer") is not None
    }
    assembly_seed = json.dumps({
        "plan": assembly["run_plan_sha256"], "state": assembly["run_state_sha256"],
        "selection": [(row["unit_id"], row["attempt_id"]) for row in assembly["units"] if row["selection_state"] == "SELECTED"],
        "project_answers": answers,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if assembly["assembly_id"] != "WHOLE-BOOK-ASSEMBLY-" + sha256(assembly_seed):
        _fail("ASSEMBLY_ID_INVALID")

    state_bytes: dict[Path, bytes] = {manifest_path: manifest_raw, state_path: state_raw}
    state_bytes.update({path: output_raws[name] for name, path in checked_paths.items()})
    facts_by_unit: dict[str, dict[str, Any]] = {}
    selected_artifact_rows: list[dict[str, Any]] = []
    for row in assembly["units"]:
        if row["selection_state"] != "SELECTED":
            continue
        position = row["position"]
        if position > len(state["units"]):
            _fail("STATE_BINDING_INVALID")
        state_row = state["units"][position - 1]
        if (
            state_row["position"] != position
            or state_row["unit_id"] != row["unit_id"]
            or state_row["unit_identity_sha256"] != row["unit_identity_sha256"]
            or state_row["route"] != row["route"]
            or state_row["prerequisite_ids"] != row["prerequisite_ids"]
        ):
            _fail("STATE_BINDING_INVALID")
        records = _selected_attempt(state, state_row, row["attempt_id"])
        if records != row["artifact_digests"]:
            _fail("ATTEMPT_BINDING_INVALID")
        status_role = "chapter_review_status" if row["route"] == "PHASE6A_PROJECT_CLAIM" else "general_review_status"
        status_records = [record for record in records if record["role"] == status_role]
        if len(status_records) != 1 or status_records[0]["sha256"] != row["review_status_sha256"]:
            _fail("REVIEW_STATUS_BINDING_INVALID")
        try:
            raw_by_role, path_by_role = d3a._recorded_files(state_root, state_row, row["attempt_id"], records)
        except d3a.WholeBookWorkflowError as exc:
            raise WholeBookAuditError(exc.code) from exc
        for path, raw in ((path_by_role[role], raw) for role, raw in raw_by_role.items()):
            state_bytes[path] = raw
        status_kind = status_role.replace("_", "-")
        try:
            status = d3a._canonical_artifact(raw_by_role[status_role], status_kind)
        except d3a.WholeBookWorkflowError as exc:
            raise WholeBookAuditError("REVIEW_STATUS_BINDING_INVALID") from exc
        if status.get("review_state") != "REVIEWED_DRAFT":
            _fail("REVIEW_STATUS_BINDING_INVALID")
        selected_artifact_rows.append({
            "position": position, "unit_id": row["unit_id"], "route": row["route"],
            "attempt_id": row["attempt_id"], "prerequisite_ids": list(row["prerequisite_ids"]),
            "review_status_sha256": row["review_status_sha256"],
            "chapter_facts_sha256": None, "answer_status": row["answer_status"],
            "d1b_review_state": (row.get("project_answer") or {}).get("d1b_review_state"),
        })
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            facts_raw = raw_by_role.get("chapter_facts")
            fact_records = [record for record in records if record["role"] == "chapter_facts"]
            if facts_raw is None or len(fact_records) != 1:
                _fail("FACTS_BINDING_INVALID")
            try:
                facts = d3a._canonical_artifact(facts_raw, "chapter-facts")
            except d3a.WholeBookWorkflowError as exc:
                raise WholeBookAuditError("FACTS_BINDING_INVALID") from exc
            if (
                fact_records[0]["sha256"] != sha256(facts_raw)
                or facts.get("selected_unit", {}).get("curriculum_unit_id") != row["unit_id"]
                or status.get("chapter_facts_sha256") != sha256(facts_raw)
                or any(facts.get(key) != state.get(key) for key in (
                    "repository_revision", "snapshot_kind", "source_status", "source_run_manifest_sha256",
                    "source_metadata", "unknown_files", "input_digests",
                ))
            ):
                _fail("FACTS_BINDING_INVALID")
            facts_by_unit[row["unit_id"]] = facts
            selected_artifact_rows[-1]["chapter_facts_sha256"] = sha256(facts_raw)

    manifest_sha = sha256(manifest_raw)
    try:
        book_text = output_raws["WHOLE-BOOK.md"].decode("utf-8")
    except UnicodeError as exc:
        raise WholeBookAuditError("ASSEMBLY_OUTPUT_INVALID") from exc
    findings, counts = audit_whole_book(assembly, book_text, facts_by_unit)
    artifact: dict[str, Any] = {
        "artifact_kind": "whole-book-consistency-audit", "schema_version": "1.0.0",
        "repository_revision": assembly["repository_revision"], "generated_at": assembly["generated_at"],
        "audit_id": "WHOLE-BOOK-AUDIT-" + hashlib.sha256(
            json.dumps({"assembly": manifest_sha, "findings": findings, "counts": counts}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "assembly_id": assembly["assembly_id"], "assembly_manifest_sha256": manifest_sha,
        "run_plan_id": assembly["run_plan_id"], "run_plan_sha256": assembly["run_plan_sha256"],
        "run_state_id": assembly["run_state_id"], "run_state_revision": revision,
        "run_state_sha256": assembly["run_state_sha256"], "run_state_schema_version": assembly["run_state_schema_version"],
        "snapshot_kind": assembly["snapshot_kind"], "source_status": assembly["source_status"],
        "source_run_manifest_sha256": assembly["source_run_manifest_sha256"], "unknown_files": assembly["unknown_files"],
        "curriculum_sha256": assembly["curriculum_sha256"], "input_digests": assembly["input_digests"],
        "selected_units": selected_artifact_rows, "d3a_outputs": assembly["outputs"],
        "check_counts": counts, "findings": findings, "audit_status": "REPORT_ONLY",
        "semantic_consistency": "NOT_RUN", "glossary_review": "NOT_RUN",
        "lesson_status": "DRAFT", "answer_book_status": "DRAFT", "overall_status": "PARTIAL",
    }
    report = render_audit_report(artifact).encode("utf-8")
    artifact["report_sha256"] = sha256(report)
    try:
        validate_artifact(artifact)
    except ArtifactValidationError as exc:
        raise WholeBookAuditError("AUDIT_ARTIFACT_INVALID") from exc
    artifact_raw = dumps_artifact(artifact).encode("utf-8")

    for path, expected in state_bytes.items():
        if _read(path) != expected:
            _fail("INPUT_CHANGED")
    output = Path(os.path.abspath(os.fspath(out_dir)))
    target = Path(os.path.abspath(os.fspath(target_root)))
    chapter._assert_no_link_components(target)
    if not target.is_dir():
        _fail("TARGET_ROOT_INVALID")
    chapter._assert_no_link_components(output.parent)
    if os.path.lexists(output):
        _fail("OUTPUT_EXISTS")
    _disjoint(output, assembly_root, state_root, target)
    if not output.parent.is_dir():
        _fail("OUTPUT_INVALID")
    try:
        output.mkdir()
        chapter._publish_new_file(output / "WHOLE-BOOK-AUDIT.md", report)
        chapter._publish_new_file(output / "whole-book-consistency-audit.json", artifact_raw)
    except (OSError, chapter.Phase6ChapterError) as exc:
        raise WholeBookAuditError(getattr(exc, "code", "OUTPUT_FAILED")) from exc
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    audit = parser.add_subparsers(dest="command", required=True).add_parser("audit")
    audit.add_argument("--assembly-dir", type=Path, required=True)
    audit.add_argument("--state-dir", type=Path, required=True)
    audit.add_argument("--target-root", type=Path, required=True, help="target root used only to prevent output overlap")
    audit.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        artifact = audit_assembly(assembly_dir=args.assembly_dir, state_dir=args.state_dir,
                                  target_root=args.target_root, out_dir=args.out_dir)
        print(json.dumps({"status": artifact["audit_status"], "audit_id": artifact["audit_id"],
                          "finding_count": artifact["check_counts"]["finding_count"],
                          "semantic_consistency": "NOT_RUN", "glossary_review": "NOT_RUN"}, separators=(",", ":")))
        return 0
    except (WholeBookAuditError, chapter.Phase6ChapterError) as exc:
        print(json.dumps({"error": exc.code}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
