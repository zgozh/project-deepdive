"""V3 mixed temporary-Git D3a CLI pilot over D2B2b's mixed state chain."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, validate_artifact  # noqa: E402
from test_curriculum_run_execution_workflow import CurriculumRunExecutionTests, _inputs_args  # noqa: E402
from test_phase6_answer_book import _completed_candidate  # noqa: E402
from test_answer_review_workflow import _completed_reports  # noqa: E402
import whole_book_workflow as whole_book_workflow  # noqa: E402


_COUNTED_D3_DRIVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
import phase6_chapter as chapter
import curriculum_run_workflow as planning
import whole_book_workflow as workflow
count = [0]
original = chapter._capture_bundle
def counted(*args, **kwargs):
    count[0] += 1
    return original(*args, **kwargs)
chapter._capture_bundle = counted
planning._capture_bundle = counted
result = workflow.main(sys.argv[2:])
print(f"capture_count={count[0]}")
raise SystemExit(result)
"""


class WholeBookWorkflowTests(unittest.TestCase):
    def test_recorded_attempt_rejects_windows_paths_before_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_dir.mkdir()
            for relative_path in (
                r"units/0001/attempt-0001/phase6a/..\..\..\outside.json",
                "units/0001/attempt-0001/phase6a/D:/outside.json",
            ):
                with self.subTest(relative_path=relative_path):
                    with self.assertRaisesRegex(
                        whole_book_workflow.WholeBookWorkflowError,
                        "ATTEMPT_BINDING_INVALID",
                    ):
                        whole_book_workflow._recorded_files(
                            state_dir,
                            {"position": 1},
                            "attempt-0001",
                            [{"role": "chapter_markdown", "relative_path": relative_path, "sha256": "0" * 64}],
                        )

    def test_cli_assembles_explicit_mixed_attempts_from_authenticated_git_run(self):
        source_test = CurriculumRunExecutionTests(
            "test_repair_cli_runs_general_and_project_attempt_two_with_pause_resume",
        )
        try:
            source_test.test_repair_cli_runs_general_and_project_attempt_two_with_pause_resume()
            context, inputs, plan_path, state_dir, final_state = source_test.d3a_pilot_data
            plan = json.loads(plan_path.read_bytes())
            selected = {}
            for plan_row, state_row in zip(plan["units"], final_state["units"], strict=True):
                if plan_row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
                    continue
                self.assertEqual("REVIEWED_DRAFT", state_row["execution_state"])
                selected[plan_row["unit_identity"]["id"]] = state_row["attempt_id"]
            self.assertGreaterEqual(len(selected), 2)
            output_dir = context["fixture"]["work"] / "whole-book-d3a-output"
            project_row = next(row for row in plan["units"] if row["route"] == "PHASE6A_PROJECT_CLAIM")
            project_id = project_row["unit_identity"]["id"]
            project_attempt = state_dir / "units" / f"{project_row['position']:04d}" / selected[project_id]
            chapter_dir = project_attempt / "phase6a"
            chapter_review_dir = project_attempt / "phase6a-review"
            import answer_review_workflow
            import chapter_review_workflow
            import phase6_answer_book

            package = chapter_review_workflow.authenticate_chapter_review_package(
                inputs, chapter_dir=chapter_dir, review_dir=chapter_review_dir,
            )
            answer_candidate = _completed_candidate(phase6_answer_book, package)
            answer_candidate_raw = dumps_artifact(answer_candidate).encode("utf-8")
            answer_bank = phase6_answer_book.build_answer_bank(package, answer_candidate, answer_candidate_raw)
            answer_bank_raw = dumps_artifact(answer_bank).encode("utf-8")
            answer_book_raw = phase6_answer_book.render_answer_book(answer_bank).encode("utf-8")
            candidate_path = context["fixture"]["work"] / "d3a-project-answer-candidate.json"
            candidate_path.write_bytes(answer_candidate_raw)
            answer_dir = context["fixture"]["work"] / "d3a-project-answer"
            answer_dir.mkdir()
            (answer_dir / "exercise-bank.json").write_bytes(answer_bank_raw)
            (answer_dir / "ANSWER-BOOK.md").write_bytes(answer_book_raw)
            answer_review_dir = context["fixture"]["work"] / "d3a-project-answer-review"
            answer_review_workflow.prepare_answer_review(
                inputs, chapter_dir=chapter_dir, chapter_review_dir=chapter_review_dir,
                answer_candidates=candidate_path, answer_dir=answer_dir, review_dir=answer_review_dir,
            )
            _completed_reports(answer_review_dir)
            answer_review_workflow.finalize_answer_review(
                inputs, chapter_dir=chapter_dir, chapter_review_dir=chapter_review_dir,
                answer_candidates=candidate_path, answer_dir=answer_dir, review_dir=answer_review_dir,
                evidence_report_path=answer_review_dir / "answer-evidence-review.json",
                beginner_report_path=answer_review_dir / "answer-beginner-review.json",
                out_status=answer_review_dir / "answer-review-status.json",
            )
            command = [
                sys.executable, "-c", _COUNTED_D3_DRIVER, str(SCRIPTS), "assemble",
                *_inputs_args(inputs), "--plan", str(plan_path), "--state-dir", str(state_dir),
                "--out-dir", str(output_dir),
            ]
            for unit_id, attempt_id in selected.items():
                command.extend(["--select", f"{unit_id}={attempt_id}"])
            command.extend([
                "--project-answer-candidate", f"{project_id}={candidate_path}",
                "--project-answer-dir", f"{project_id}={answer_dir}",
                "--project-answer-review-dir", f"{project_id}={answer_review_dir}",
            ])
            result = subprocess.run(command, cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('"status":"PARTIAL"', result.stdout)
            self.assertIn('"cross_chapter_audit":"NOT_RUN"', result.stdout)
            self.assertIn("capture_count=2", result.stdout)

            manifest_raw = (output_dir / "whole-book-assembly.json").read_bytes()
            manifest = json.loads(manifest_raw)
            validate_artifact(manifest)
            self.assertEqual("1.1.0", manifest["run_state_schema_version"])
            self.assertEqual("PARTIAL", manifest["source_status"])
            self.assertGreater(manifest["unknown_files"], 0)
            self.assertEqual(len(plan["units"]), manifest["unit_count"])
            self.assertEqual([row["unit_identity"]["id"] for row in plan["units"]], [row["unit_id"] for row in manifest["units"]])
            self.assertEqual(selected, {
                row["unit_id"]: row["attempt_id"] for row in manifest["units"]
                if row["selection_state"] == "SELECTED"
            })
            project_manifest_row = next(row for row in manifest["units"] if row["unit_id"] == project_id)
            self.assertEqual("INCLUDED", project_manifest_row["answer_status"])
            self.assertEqual("REVIEWED_DRAFT", project_manifest_row["project_answer"]["d1b_review_state"])
            self.assertIsNotNone(project_manifest_row["project_answer"]["d1b_evidence_report_sha256"])
            state_chain = sorted(state_dir.glob("run-state-*.json"))
            self.assertEqual("1.0.0", json.loads(state_chain[0].read_bytes())["schema_version"])
            self.assertEqual("1.1.0", json.loads(state_chain[-1].read_bytes())["schema_version"])
            for name, field in (
                ("WHOLE-BOOK.md", "whole_book"),
                ("WHOLE-ANSWER-BOOK.md", "answer_book"),
                ("QUALITY-AND-GAPS.md", "quality_and_gaps"),
            ):
                raw = (output_dir / name).read_bytes()
                entry = next(item for item in manifest["outputs"] if item["role"] == field)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
            whole = (output_dir / "WHOLE-BOOK.md").read_text(encoding="utf-8")
            answer = (output_dir / "WHOLE-ANSWER-BOOK.md").read_text(encoding="utf-8")
            gaps = (output_dir / "QUALITY-AND-GAPS.md").read_text(encoding="utf-8")
            self.assertIn("Project-specific", whole)
            self.assertIn("Teaching-only", whole)
            self.assertIn("origin=PREREQUISITE_PRIMER · scope=PROJECT_SPECIFIC", whole)
            self.assertIn("Name the question's inputs", answer)
            self.assertIn("Whole Answer Book", answer)
            self.assertTrue(any(
                row["selection_state"] == "SELECTED" and row["answer_status"] == "INCLUDED"
                for row in manifest["units"]
            ))
            self.assertIn("NOT_RUN", gaps)
            audit_dir = context["fixture"]["work"] / "whole-book-d3b-audit"
            audit_command = [
                sys.executable, str(SCRIPTS / "whole_book_audit_workflow.py"), "audit",
                "--assembly-dir", str(output_dir), "--state-dir", str(state_dir),
                "--target-root", str(context["fixture"]["root"]), "--out-dir", str(audit_dir),
            ]
            audit_result = subprocess.run(audit_command, cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(0, audit_result.returncode, audit_result.stdout + audit_result.stderr)
            self.assertIn('"status":"REPORT_ONLY"', audit_result.stdout)
            audit_raw = (audit_dir / "whole-book-consistency-audit.json").read_bytes()
            audit = json.loads(audit_raw)
            validate_artifact(audit)
            self.assertEqual("NOT_RUN", audit["semantic_consistency"])
            self.assertEqual("NOT_RUN", audit["glossary_review"])
            self.assertEqual("PARTIAL", audit["source_status"])
            self.assertGreater(audit["unknown_files"], 0)
            self.assertEqual(manifest["run_state_sha256"], audit["run_state_sha256"])
            self.assertEqual(
                {row["unit_id"] for row in manifest["units"] if row["selection_state"] == "SELECTED"},
                {row["unit_id"] for row in audit["selected_units"]},
            )
            self.assertEqual(1, sum(row["chapter_facts_sha256"] is not None for row in audit["selected_units"]))
            print(f"d3b_audit_id={audit['audit_id']}")
            print(f"d3b_audit_sha256={hashlib.sha256(audit_raw).hexdigest()}")
            repeat_dir = context["fixture"]["work"] / "whole-book-d3b-audit-repeat"
            repeat = subprocess.run([
                sys.executable, str(SCRIPTS / "whole_book_audit_workflow.py"), "audit",
                "--assembly-dir", str(output_dir), "--state-dir", str(state_dir),
                "--target-root", str(context["fixture"]["root"]), "--out-dir", str(repeat_dir),
            ], cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(0, repeat.returncode, repeat.stdout + repeat.stderr)
            self.assertEqual(audit_raw, (repeat_dir / "whole-book-consistency-audit.json").read_bytes())
            self.assertEqual(
                (audit_dir / "WHOLE-BOOK-AUDIT.md").read_bytes(),
                (repeat_dir / "WHOLE-BOOK-AUDIT.md").read_bytes(),
            )
            target_collision = context["fixture"]["root"] / "d3b-output-must-not-exist"
            collision = subprocess.run([
                sys.executable, str(SCRIPTS / "whole_book_audit_workflow.py"), "audit",
                "--assembly-dir", str(output_dir), "--state-dir", str(state_dir),
                "--target-root", str(context["fixture"]["root"]), "--out-dir", str(target_collision),
            ], cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(2, collision.returncode, collision.stdout + collision.stderr)
            self.assertIn('"error":"OUTPUT_INVALID"', collision.stdout)
            self.assertFalse(target_collision.exists())
            (output_dir / "WHOLE-BOOK.md").write_text(whole + "\nchanged after assembly\n", encoding="utf-8")
            drift = subprocess.run([
                sys.executable, str(SCRIPTS / "whole_book_audit_workflow.py"), "audit",
                "--assembly-dir", str(output_dir), "--state-dir", str(state_dir),
                "--target-root", str(context["fixture"]["root"]),
                "--out-dir", str(context["fixture"]["work"] / "whole-book-d3b-audit-drift"),
            ], cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(2, drift.returncode, drift.stdout + drift.stderr)
            self.assertIn('"error":"ASSEMBLY_OUTPUT_DRIFT"', drift.stdout)
            print(f"d3a_pilot_plan_sha256={manifest['run_plan_sha256']}")
            print(f"d3a_pilot_state_sha256={manifest['run_state_sha256']}")
            print(f"d3a_pilot_assembly_id={manifest['assembly_id']}")
        finally:
            source_test.doCleanups()


if __name__ == "__main__":
    unittest.main()
