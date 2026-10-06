#!/usr/bin/env python3
"""V3 mixed temporary-Git start, pause, resume, and drift pilot for D2B2a."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact  # noqa: E402
from test_phase6_chapter import _phase6_fixture, _valid_chapter  # noqa: E402
from test_phase6_review import _report_pair  # noqa: E402
from test_phase6_general_learning import _candidate  # noqa: E402
import curriculum_run_execution_workflow as run_execution  # noqa: E402
import chapter_review_workflow as b1_review  # noqa: E402


_COUNTED_DRIVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
import phase6_chapter as chapter
import curriculum_run_workflow as workflow
import curriculum_run_execution_workflow as execution
import general_unit_workflow as d2a
import general_learning_review_workflow as d2a2
count = [0]
original = chapter._capture_bundle
def counted(*args, **kwargs):
    count[0] += 1
    return original(*args, **kwargs)
auth_count = [0]
original_auth = chapter.authenticate_claim_evidence_bundle_4c
def counted_auth(*args, **kwargs):
    auth_count[0] += 1
    return original_auth(*args, **kwargs)
chapter.authenticate_claim_evidence_bundle_4c = counted_auth
chapter._capture_bundle = counted
workflow._capture_bundle = counted
d2a._capture_bundle = counted
d2a2._capture_bundle = counted
verb = sys.argv[2]
result = workflow.main(sys.argv[2:]) if verb == "plan" else execution.main(sys.argv[2:])
print(f"capture_count={count[0]}")
print(f"full_auth_count={auth_count[0]}")
raise SystemExit(result)
"""


def _inputs_args(inputs):
    return [
        "--phase4c-package", str(inputs.phase4c_package),
        "--claim-candidates", str(inputs.claim_candidates_path),
        "--claim-evidence", str(inputs.claim_evidence_path),
        "--claim-evidence-graph", str(inputs.claim_evidence_graph_path),
        "--prerequisite-candidates", str(inputs.prerequisite_candidates_path),
        "--prerequisite-graph", str(inputs.prerequisite_graph_path),
        "--curriculum-candidates", str(inputs.curriculum_candidates_path),
        "--curriculum", str(inputs.curriculum_path),
        "--run-dir", str(inputs.run_dir), "--root", str(inputs.root),
    ]


def _command(testcase, verb, inputs, *, plan, state_dir, capture_count=1, full_auth_count=None, extra_args=()):
    result = subprocess.run(
        [sys.executable, "-c", _COUNTED_DRIVER, str(SCRIPTS), verb, *_inputs_args(inputs),
         "--plan", str(plan), "--state-dir", str(state_dir), *extra_args],
        cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
    )
    testcase.assertEqual(0, result.returncode, result.stdout + result.stderr)
    match = re.search(r"capture_count=([0-9]+)", result.stdout)
    testcase.assertIsNotNone(match, result.stdout)
    testcase.assertEqual(capture_count, int(match.group(1)), f"{verb}: {result.stdout}")
    if full_auth_count is not None:
        auth_match = re.search(r"full_auth_count=([0-9]+)", result.stdout)
        testcase.assertIsNotNone(auth_match, result.stdout)
        testcase.assertEqual(full_auth_count, int(auth_match.group(1)), f"{verb}: {result.stdout}")
    return result.stdout


def _command_error(testcase, verb, inputs, *, plan, state_dir, code):
    result = subprocess.run(
        [sys.executable, "-c", _COUNTED_DRIVER, str(SCRIPTS), verb, *_inputs_args(inputs),
         "--plan", str(plan), "--state-dir", str(state_dir)],
        cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
    )
    testcase.assertEqual(2, result.returncode, result.stdout + result.stderr)
    testcase.assertIn(f"error={code}", result.stdout)
    testcase.assertIn("capture_count=1", result.stdout)


def _plan_command(testcase, inputs, output):
    result = subprocess.run(
        [sys.executable, "-c", _COUNTED_DRIVER, str(SCRIPTS), "plan", *_inputs_args(inputs), "--out", str(output)],
        cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
    )
    testcase.assertEqual(0, result.returncode, result.stdout + result.stderr)
    match = re.search(r"capture_count=([0-9]+)", result.stdout)
    testcase.assertIsNotNone(match, result.stdout)
    testcase.assertEqual(1, int(match.group(1)), result.stdout)


def _minimal_mixed_fixture(testcase, *, git_tree=False):
    sources = {
        "src/module.py": b"def request():\n    return 'ok'\n",
        "notes.unclassified": b"Tracked unknown source fixture.\n",
    }
    context, inputs, _unit_id, _output = _phase6_fixture(testcase, source_files=sources, git_tree=git_tree)
    prerequisite_tests = __import__("test_phase5_prerequisites")
    curriculum_tests = __import__("test_phase5_curriculum")
    refs = prerequisite_tests._e1_reference(context)
    node = prerequisite_tests._node(
        context, "http-client", scope="PROJECT_SPECIFIC", kind="FrameworkMechanism", refs=refs,
    )
    prerequisite_candidates = prerequisite_tests._candidate(context, [node], [])
    prerequisite_graph = curriculum_tests._project_prerequisites(testcase, context, prerequisite_candidates)
    context["prerequisite_candidates"] = prerequisite_candidates
    context["prerequisite_graph"] = prerequisite_graph
    context["prerequisite_candidates_path"] = context["fixture"]["work"] / "d2b2-prerequisite-candidates.json"
    context["prerequisite_graph_path"] = context["fixture"]["work"] / "d2b2-prerequisite-graph.json"
    context["curriculum_candidates_path"] = context["fixture"]["work"] / "d2b2-curriculum-candidates.json"
    context["curriculum_output_path"] = context["fixture"]["work"] / "d2b2-curriculum.json"
    general_purpose = curriculum_tests._unit("project-purpose", "project-purpose", "What this project is for")
    general_workflow = curriculum_tests._unit("workflow-map", "workflow", "Follow one real workflow")
    request_unit = next(row for row in curriculum_tests._starter_units(context) if row["unit_key"] == "request-path")
    candidates = curriculum_tests._curriculum_candidates(context, [general_purpose, general_workflow, request_unit])
    curriculum = curriculum_tests._build(testcase, context, candidates)
    inputs = replace(
        inputs,
        prerequisite_candidates_path=context["prerequisite_candidates_path"],
        prerequisite_graph_path=context["prerequisite_graph_path"],
        curriculum_candidates_path=context["curriculum_candidates_path"],
        curriculum_path=context["curriculum_output_path"],
    )
    context["curriculum"] = curriculum
    return context, inputs, curriculum


def _state(state_dir):
    paths = sorted(state_dir.glob("run-state-*.json"))
    return json.loads(paths[-1].read_bytes())


def _general_reports(review_dir, *, repair_finding=False):
    session = json.loads((review_dir / "general-review-session.json").read_bytes())
    factual = json.loads((review_dir / "general-factuality-review-template.json").read_bytes())
    factual.update({
        "generated_at": session["generated_at"], "reviewer_alias": "fixture-factuality",
        "reviewer_session_id": "fixture-factuality-session", "fresh_context_isolated": True,
        "source_reference_inspection": True,
    })
    for index, row in enumerate(factual["results"]):
        row["findings"] = []
        if index == 0:
            row["outcome"] = "SUPPORTED_BY_AUTHORITY"
            row["authority_references"] = [{
                "reference_id": "fixture-authority-1", "title": "Fixture authority",
                "publisher": "Synthetic test fixture", "locator": "https://example.invalid/fixture",
                "edition": None, "authority_class": "OFFICIAL_DOCUMENTATION",
            }]
        else:
            row["outcome"] = "NONFACTUAL_TEACHING"
            row["authority_references"] = []
    if repair_finding:
        row = factual["results"][1]
        row["outcome"] = "NEEDS_REVISION"
        row["authority_references"] = []
        row["findings"] = [{
            "issue_code": "MISSING_SCOPE_OR_CONDITION", "location_id": row["occurrence_id"],
            "severity": "MINOR", "recommended_action": "NARROW_OR_CORRECT_CLAIM",
            "focus": "scope or condition", "repair_guidance": "Narrow the existing statement to its supported scope.",
        }]
    (review_dir / "general-factuality-review.json").write_bytes(dumps_artifact(factual).encode("utf-8"))

    beginner = json.loads((review_dir / "general-beginner-answer-review-template.json").read_bytes())
    beginner.update({
        "generated_at": session["generated_at"], "reviewer_alias": "fixture-beginner",
        "reviewer_session_id": "fixture-beginner-session", "fresh_context_isolated": True,
    })
    for row in beginner["results"]:
        row["outcome"] = "FOLLOWABLE_FOR_BEGINNER"
        row["findings"] = []
    if repair_finding:
        row = beginner["results"][0]
        row["outcome"] = "NEEDS_REVISION"
        row["findings"] = [{
            "issue_code": "UNCLEAR_EXPLANATION", "location_id": row["scope_id"],
            "severity": "MAJOR", "recommended_action": "CLARIFY_EXPLANATION",
            "focus": "The explanation skips one beginner step.",
            "repair_guidance": "Explain the existing concept in one concrete step.",
        }]
    (review_dir / "general-beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))


class CurriculumRunExecutionTests(unittest.TestCase):
    def test_cli_bounded_advance_processes_ready_units_then_stops_at_review_wait(self):
        context, inputs, _curriculum = _minimal_mixed_fixture(self, git_tree=True)
        plan_path = context["fixture"]["work"] / "d2b2-batch-plan.json"
        state_dir = context["fixture"]["work"] / "d2b2-batch-state"
        _plan_command(self, inputs, plan_path)
        _command(self, "start", inputs, plan=plan_path, state_dir=state_dir)

        for _ in range(3):
            before_prepare = _state(state_dir)["state_revision"]
            default_output = _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
            self.assertEqual(1, sum(line.startswith("status=") for line in default_output.splitlines()))
            self.assertIn("action=PREPARED:", default_output)
            self.assertEqual(before_prepare + 1, _state(state_dir)["state_revision"])

        prepared_rows = _state(state_dir)["units"][:3]
        self.assertTrue(all(row["execution_state"] == "PREPARED" for row in prepared_rows))
        for row in prepared_rows[:2]:
            prepare_dir = state_dir / "units" / f"{row['position']:04d}" / "attempt-0001" / "general-prepare"
            facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
            (prepare_dir / "general-unit-candidate.json").write_bytes(
                dumps_artifact(_candidate(facts)).encode("utf-8")
            )
        before_batch = _state(state_dir)["state_revision"]
        batch_output = _command(
            self,
            "advance",
            inputs,
            plan=plan_path,
            state_dir=state_dir,
            capture_count=5,
            full_auth_count=1,
            extra_args=("--max-actions", "8"),
        )

        status_lines = [line for line in batch_output.splitlines() if line.startswith("status=")]
        self.assertEqual(5, len(status_lines), batch_output)
        self.assertIn("action=BUILT_DRAFT:", status_lines[0])
        self.assertIn("action=AWAITING_REVIEW:", status_lines[1])
        self.assertIn("action=BUILT_DRAFT:", status_lines[2])
        self.assertIn("action=AWAITING_REVIEW:", status_lines[3])
        self.assertIn("action=WAITING_FOR_REVIEW", status_lines[4])
        self.assertEqual(before_batch + 4, _state(state_dir)["state_revision"])
        self.assertEqual("AWAITING_REVIEW", _state(state_dir)["units"][0]["execution_state"])
        self.assertEqual("AWAITING_REVIEW", _state(state_dir)["units"][1]["execution_state"])

    def test_cli_mixed_git_run_waits_restarts_reuses_outputs_and_dispatches_dependent_project(self):
        context, inputs, curriculum = _minimal_mixed_fixture(self)
        self.assertEqual("PARTIAL", curriculum["source_status"])
        self.assertGreater(curriculum["source_metadata"]["unknown_files"], 0)
        primer = next(row for row in curriculum["units"] if row["origin"] == "PREREQUISITE_PRIMER")
        project = next(row for row in curriculum["units"] if row["origin"] == "CANDIDATE" and row["scope"] == "PROJECT_SPECIFIC")
        self.assertEqual("PROJECT_SPECIFIC", primer["scope"])
        self.assertEqual(0, sum(len(values) for values in primer["project_refs"].values()))
        self.assertIn(primer["id"], project["prerequisite_ids"])
        self.assertEqual(4, len(curriculum["units"]))

        plan_path = context["fixture"]["work"] / "d2b2-plan.json"
        state_dir = context["fixture"]["work"] / "d2b2-state"
        _plan_command(self, inputs, plan_path)
        _command(self, "start", inputs, plan=plan_path, state_dir=state_dir)
        plan = json.loads(plan_path.read_bytes())
        self.assertEqual([unit["id"] for unit in curriculum["units"]], [row["unit_identity"]["id"] for row in plan["units"]])
        self.assertEqual("PARTIAL", _state(state_dir)["source_status"])
        self.assertEqual(curriculum["source_metadata"]["unknown_files"], _state(state_dir)["unknown_files"])

        _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
        _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
        _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
        state = _state(state_dir)
        general_rows = [row for row in plan["units"] if row["route"] == "D2A_GENERAL_LEARNING"]
        self.assertEqual(3, len(general_rows))
        self.assertTrue(all(state["units"][row["position"] - 1]["execution_state"] == "PREPARED" for row in general_rows))
        before_wait = state["state_revision"]
        self.assertIn("WAITING_FOR_WRITER", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        self.assertEqual(before_wait, _state(state_dir)["state_revision"])

        first_general = general_rows[0]
        first_prepare = state_dir / "units" / f"{first_general['position']:04d}" / "attempt-0001" / "general-prepare"
        (first_prepare / "general-unit-candidate.json").write_bytes(b"{}\n")
        _command_error(self, "advance", inputs, plan=plan_path, state_dir=state_dir, code="CANDIDATE_INVALID")
        self.assertEqual(before_wait, _state(state_dir)["state_revision"])

        primer_outputs = {}
        for general_row in general_rows:
            position = general_row["position"]
            prepare_dir = state_dir / "units" / f"{position:04d}" / "attempt-0001" / "general-prepare"
            facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
            candidate = _candidate(facts)
            candidate["writer_alias"] = "Writer A"
            candidate_raw = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            (prepare_dir / "general-unit-candidate.json").write_bytes(candidate_raw)
            self.assertIn("BUILT_DRAFT:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
            unit_dir = state_dir / "units" / f"{position:04d}" / "attempt-0001" / "general-unit"
            unit_before = {path.name: path.read_bytes() for path in unit_dir.iterdir()}
            self.assertEqual(hashlib.sha256(candidate_raw).hexdigest(), json.loads(unit_before["general-learning-unit.json"])["candidate_sha256"])
            self.assertIn("AWAITING_REVIEW:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
            waiting_revision = _state(state_dir)["state_revision"]
            if general_row["unit_identity"]["origin"] == "PREREQUISITE_PRIMER":
                self.assertIn("WAITING_FOR_REVIEW", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
                self.assertEqual(waiting_revision, _state(state_dir)["state_revision"])
            review_dir = state_dir / "units" / f"{position:04d}" / "attempt-0001" / "general-review"
            _general_reports(review_dir)
            self.assertIn("REVIEWED_DRAFT:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
            if general_row["unit_identity"]["origin"] == "PREREQUISITE_PRIMER":
                primer_outputs = {"prepare": {path.name: path.read_bytes() for path in prepare_dir.iterdir()},
                                  "unit": unit_before, "review": {path.name: path.read_bytes() for path in review_dir.iterdir()}}

        completed_state = _state(state_dir)
        self.assertIn("RESUMED", _command(self, "resume", inputs, plan=plan_path, state_dir=state_dir))
        self.assertEqual(completed_state["state_revision"], _state(state_dir)["state_revision"])
        primer_position = next(row["position"] for row in general_rows if row["unit_identity"]["origin"] == "PREREQUISITE_PRIMER")
        primer_base = state_dir / "units" / f"{primer_position:04d}" / "attempt-0001"
        self.assertEqual(primer_outputs["unit"], {path.name: path.read_bytes() for path in (primer_base / "general-unit").iterdir()})
        self.assertEqual("PLANNED", _state(state_dir)["units"][-1]["execution_state"])

        self.assertIn("PREPARED:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        project_dir = state_dir / "units" / f"{len(plan['units']):04d}" / "attempt-0001" / "phase6a"
        facts = json.loads((project_dir / "chapter-facts.json").read_bytes())
        (project_dir / "chapter.md").write_text(_valid_chapter(facts, context["claim"]["text"]), encoding="utf-8", newline="\n")
        self.assertIn("BUILT_DRAFT:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        project_review = state_dir / "units" / f"{len(plan['units']):04d}" / "attempt-0001" / "phase6a-review"
        self.assertIn("AWAITING_REVIEW:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        evidence_report, beginner_report = _report_pair(self, inputs, project_dir, project_review)
        (project_review / "evidence-review.json").write_bytes(dumps_artifact(evidence_report).encode("utf-8"))
        (project_review / "beginner-review.json").write_bytes(dumps_artifact(beginner_report).encode("utf-8"))

        # Simulate a crash after the existing B1 finalizer published its status
        # but before the coordinator appended the matching state revision.
        before_recovery = _state(state_dir)
        project_position = len(plan["units"])
        self.assertEqual("AWAITING_REVIEW", before_recovery["units"][project_position - 1]["execution_state"])
        b1_status = b1_review.finalize_chapter_review(
            inputs, chapter_dir=project_dir, review_dir=project_review,
            evidence_report_path=project_review / "evidence-review.json",
            beginner_report_path=project_review / "beginner-review.json",
            out_status=project_review / "chapter-review-status.json",
        )
        self.assertEqual("REVIEWED_DRAFT", b1_status["review_state"])
        self.assertEqual(before_recovery["state_revision"], _state(state_dir)["state_revision"])
        project_files = {
            "chapter_facts": project_dir / "chapter-facts.json",
            "writer_packet": project_dir / "writer-packet.md",
            "chapter_markdown": project_dir / "chapter.md",
            "exercise_bank": project_dir / "exercise-bank.json",
            "chapter_draft_status": project_dir / "chapter-draft-status.json",
            "chapter_review_session": project_review / "chapter-review-session.json",
            "evidence_report": project_review / "evidence-review.json",
            "beginner_report": project_review / "beginner-review.json",
            "chapter_review_status": project_review / "chapter-review-status.json",
        }
        project_bytes_before_resume = {role: path.read_bytes() for role, path in project_files.items()}
        with patch.object(run_execution.b1, "finalize_chapter_review", side_effect=AssertionError("resume reran B1 finalizer")):
            with patch.object(run_execution.chapter, "_capture_bundle", wraps=run_execution.chapter._capture_bundle) as capture:
                reconciled = run_execution.resume_curriculum_run(inputs, plan_path=plan_path, state_dir=state_dir)
        self.assertEqual(1, capture.call_count)
        self.assertEqual(f"RECONCILED_REVIEWED_DRAFT:{plan['units'][project_position - 1]['unit_identity']['id']}", reconciled["action"])
        final = _state(state_dir)
        self.assertEqual(before_recovery["state_revision"] + 1, final["state_revision"])
        self.assertEqual(project_bytes_before_resume, {role: path.read_bytes() for role, path in project_files.items()})
        digest_by_role = {item["role"]: item["sha256"] for item in final["units"][project_position - 1]["artifact_digests"]}
        self.assertEqual(
            {role: hashlib.sha256(raw).hexdigest() for role, raw in project_bytes_before_resume.items()},
            digest_by_role,
        )
        self.assertEqual("DRAFTS_REVIEWED", final["run_state"])
        self.assertTrue(all(row["execution_state"] == "REVIEWED_DRAFT" for row in final["units"]))
        self.assertTrue(all(row["artifact_digests"] for row in final["units"]))
        self.assertEqual("PARTIAL", final["source_status"])
        self.assertGreater(final["unknown_files"], 0)
        print(f"pilot_plan_sha256={hashlib.sha256(plan_path.read_bytes()).hexdigest()}")
        final_state_path = state_dir / ("run-state-%06d.json" % final["state_revision"])
        print(f"pilot_final_state_sha256={hashlib.sha256(final_state_path.read_bytes()).hexdigest()}")

        before_drift_revision = final["state_revision"]
        primer_unit_dir = primer_base / "general-unit"
        (primer_unit_dir / "general-answer-book.md").write_bytes(primer_outputs["unit"]["general-answer-book.md"] + b"drift\n")
        drift = subprocess.run(
            [sys.executable, "-c", _COUNTED_DRIVER, str(SCRIPTS), "resume", *_inputs_args(inputs),
             "--plan", str(plan_path), "--state-dir", str(state_dir)],
            cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(2, drift.returncode, drift.stdout + drift.stderr)
        self.assertIn("OUTPUT_DRIFT", drift.stdout)
        self.assertEqual(before_drift_revision, _state(state_dir)["state_revision"])

    def test_repair_cli_runs_general_and_project_attempt_two_with_pause_resume(self):
        context, inputs, curriculum = _minimal_mixed_fixture(self)
        plan_path = context["fixture"]["work"] / "d2b2b-plan.json"
        state_dir = context["fixture"]["work"] / "d2b2b-state"
        _plan_command(self, inputs, plan_path)
        _command(self, "start", inputs, plan=plan_path, state_dir=state_dir)
        plan = json.loads(plan_path.read_bytes())
        general_rows = [row for row in plan["units"] if row["route"] == "D2A_GENERAL_LEARNING"]
        primer = next(row for row in general_rows if row["unit_identity"]["origin"] == "PREREQUISITE_PRIMER")
        project = next(row for row in plan["units"] if row["route"] == "PHASE6A_PROJECT_CLAIM")

        def current(unit_row):
            return _state(state_dir)["units"][unit_row["position"] - 1]

        def drive_general(unit_row, *, repair_finding):
            for _ in range(16):
                row = current(unit_row)
                if row["execution_state"] in {"REVIEWED_DRAFT", "BLOCKED_REVIEW"}:
                    return row
                self.assertNotEqual("BLOCKED_PREREQUISITE", row["execution_state"])
                if row["execution_state"] == "PREPARED":
                    attempt = state_dir / "units" / f"{unit_row['position']:04d}" / "attempt-0001"
                    prepare_dir = attempt / "general-prepare"
                    facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
                    (prepare_dir / "general-unit-candidate.json").write_bytes(dumps_artifact(_candidate(facts)).encode("utf-8"))
                if row["execution_state"] == "AWAITING_REVIEW":
                    review_dir = state_dir / "units" / f"{unit_row['position']:04d}" / "attempt-0001" / "general-review"
                    _general_reports(review_dir, repair_finding=repair_finding)
                _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
            self.fail(f"general unit did not reach review: {unit_row['unit_identity']['id']}")

        def finish_general_repair(unit_row):
            original_legacy_revisions = {
                path.name: path.read_bytes() for path in state_dir.glob("run-state-*.json")
            }
            source_path = context["fixture"]["root"] / "src/module.py"
            original_source = source_path.read_bytes()
            try:
                source_path.write_bytes(original_source + b"# stale snapshot\n")
                _command_error(self, "repair", inputs, plan=plan_path, state_dir=state_dir, code="SOURCE_RUN_INVALID")
                self.assertEqual(original_legacy_revisions, {
                    path.name: path.read_bytes() for path in state_dir.glob("run-state-*.json")
                })
            finally:
                source_path.write_bytes(original_source)
            self.assertIn("STATE_V1_1_MIGRATED", _command(
                self, "repair", inputs, plan=plan_path, state_dir=state_dir, capture_count=1,
            ))
            original_revisions = {
                path.name: path.read_bytes() for path in state_dir.glob("run-state-*.json")
            }
            self.assertIn("REPAIR_OPENED:attempt-0002", _command(
                self, "repair", inputs, plan=plan_path, state_dir=state_dir, capture_count=1,
            ))
            self.assertEqual(original_revisions, {
                name: (state_dir / name).read_bytes() for name in original_revisions
            })
            attempt = state_dir / "units" / f"{unit_row['position']:04d}" / "attempt-0002"
            self.assertIn("PREPARED:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
            prepared_bytes = {path.name: path.read_bytes() for path in (attempt / "general-prepare").iterdir()}
            prepared_revision = _state(state_dir)["state_revision"]
            self.assertIn("RESUMED", _command(self, "resume", inputs, plan=plan_path, state_dir=state_dir))
            self.assertEqual(prepared_revision, _state(state_dir)["state_revision"])
            self.assertEqual(prepared_bytes, {path.name: path.read_bytes() for path in (attempt / "general-prepare").iterdir()})
            prepare_dir = attempt / "general-prepare"
            facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
            candidate_path = prepare_dir / "general-unit-candidate.json"
            candidate_path.write_bytes(b"{}\n")
            before_invalid = _state(state_dir)["state_revision"]
            _command_error(self, "repair", inputs, plan=plan_path, state_dir=state_dir, code="CANDIDATE_INVALID")
            self.assertEqual(before_invalid, _state(state_dir)["state_revision"])
            candidate_path.write_bytes(dumps_artifact(_candidate(facts)).encode("utf-8"))
            self.assertIn("BUILT_DRAFT:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
            self.assertIn("AWAITING_REVIEW:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
            waiting_revision = _state(state_dir)["state_revision"]
            self.assertIn("WAITING_FOR_REVIEW", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
            self.assertEqual(waiting_revision, _state(state_dir)["state_revision"])
            _general_reports(attempt / "general-review", repair_finding=False)
            self.assertIn("REVIEWED_DRAFT:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))

        # Complete the primer's same-snapshot teaching repair before its dependent project unit.
        row = drive_general(primer, repair_finding=True)
        self.assertEqual("BLOCKED_REVIEW", row["execution_state"])
        attempt1 = state_dir / "units" / f"{primer['position']:04d}" / "attempt-0001"
        original_general = {
            path.relative_to(attempt1).as_posix(): path.read_bytes()
            for path in attempt1.rglob("*") if path.is_file()
        }
        original_v1 = {path.name: path.read_bytes() for path in state_dir.glob("run-state-*.json")}
        finish_general_repair(primer)
        self.assertEqual(original_general, {
            path.relative_to(attempt1).as_posix(): path.read_bytes()
            for path in attempt1.rglob("*") if path.is_file()
        })

        for row in general_rows:
            if row["unit_identity"]["id"] != primer["unit_identity"]["id"]:
                drive_general(row, repair_finding=False)

        # The project unit is dispatched only after the repaired primer and remaining prerequisites pass review.
        for _ in range(8):
            if current(project)["execution_state"] == "PREPARED":
                break
            _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir)
        self.assertEqual("PREPARED", current(project)["execution_state"])
        project1 = state_dir / "units" / f"{project['position']:04d}" / "attempt-0001"
        facts = json.loads((project1 / "phase6a" / "chapter-facts.json").read_bytes())
        (project1 / "phase6a" / "chapter.md").write_text(
            _valid_chapter(facts, context["claim"]["text"]), encoding="utf-8", newline="\n",
        )
        self.assertIn("BUILT_DRAFT:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        self.assertIn("AWAITING_REVIEW:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        project_review = project1 / "phase6a-review"
        evidence, beginner = _report_pair(self, inputs, project1 / "phase6a", project_review)
        target = beginner["results"][0]
        target["outcome"] = "NEEDS_REVISION"
        target["findings"] = [{
            "location_kind": "section", "location_id": target["scope_id"],
            "issue_code": "UNDEFINED_JARGON", "severity": "MAJOR",
            "recommended_action": "DEFINE_TERM_BEFORE_USE",
        }]
        (project_review / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (project_review / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        self.assertIn("BLOCKED_REVIEW:", _command(self, "advance", inputs, plan=plan_path, state_dir=state_dir))
        project1_before = {
            path.relative_to(project1).as_posix(): path.read_bytes()
            for path in project1.rglob("*") if path.is_file()
        }
        self.assertIn("REPAIR_OPENED:attempt-0002", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
        project2 = state_dir / "units" / f"{project['position']:04d}" / "attempt-0002"
        self.assertIn("PREPARED:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
        project2_prepare = project2 / "phase6a"
        project2_prepared = {path.name: path.read_bytes() for path in project2_prepare.iterdir()}
        project2_revision = _state(state_dir)["state_revision"]
        self.assertIn("RESUMED", _command(self, "resume", inputs, plan=plan_path, state_dir=state_dir))
        self.assertEqual(project2_revision, _state(state_dir)["state_revision"])
        self.assertEqual(project2_prepared, {path.name: path.read_bytes() for path in project2_prepare.iterdir()})
        (project2_prepare / "chapter.md").write_text(
            _valid_chapter(json.loads((project2_prepare / "chapter-facts.json").read_bytes()), context["claim"]["text"]),
            encoding="utf-8", newline="\n",
        )
        self.assertIn("BUILT_DRAFT:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
        self.assertIn("AWAITING_REVIEW:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
        project2_review = project2 / "phase6a-review"
        self.assertIn("WAITING_FOR_REVIEW", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))
        project2_evidence, project2_beginner = _report_pair(self, inputs, project2_prepare, project2_review)
        (project2_review / "evidence-review.json").write_bytes(dumps_artifact(project2_evidence).encode("utf-8"))
        (project2_review / "beginner-review.json").write_bytes(dumps_artifact(project2_beginner).encode("utf-8"))
        self.assertIn("REVIEWED_DRAFT:", _command(self, "repair", inputs, plan=plan_path, state_dir=state_dir))

        final = _state(state_dir)
        self.assertEqual("DRAFTS_REVIEWED", final["run_state"])
        self.assertEqual("PARTIAL", final["source_status"])
        self.assertGreater(final["unknown_files"], 0)
        self.assertEqual(10, len(final["input_digests"]))
        for plan_row in (primer, project):
            row = final["units"][plan_row["position"] - 1]
            self.assertEqual(["attempt-0001", "attempt-0002"], [item["attempt_id"] for item in row["attempts"]])
            predecessor = row["attempts"][0]
            repair_attempt = row["attempts"][1]
            status_role = "general_review_status" if row["route"] == "D2A_GENERAL_LEARNING" else "chapter_review_status"
            status_digest = next(item["sha256"] for item in predecessor["artifact_digests"] if item["role"] == status_role)
            self.assertEqual(status_digest, repair_attempt["predecessor_review_status_sha256"])
            report_roles = ("factuality_report", "beginner_report") if row["route"] == "D2A_GENERAL_LEARNING" else ("evidence_report", "beginner_report")
            self.assertEqual([
                {"role": role, "sha256": next(item["sha256"] for item in predecessor["artifact_digests"] if item["role"] == role)}
                for role in report_roles
            ], repair_attempt["predecessor_report_digests"])
            self.assertEqual(row["artifact_digests"], [entry for attempt in row["attempts"] for entry in attempt["artifact_digests"]])
            for entry in row["artifact_digests"]:
                path = state_dir.joinpath(*entry["relative_path"].split("/"))
                self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        for filename, raw in original_v1.items():
            self.assertEqual(raw, (state_dir / filename).read_bytes())
        self.assertEqual(project1_before, {
            path.relative_to(project1).as_posix(): path.read_bytes()
            for path in project1.rglob("*") if path.is_file()
        })
        self.assertEqual(original_general, {
            path.relative_to(attempt1).as_posix(): path.read_bytes()
            for path in attempt1.rglob("*") if path.is_file()
        })
        print(f"repair_pilot_plan_sha256={hashlib.sha256(plan_path.read_bytes()).hexdigest()}")
        final_state_path = state_dir / f"run-state-{final['state_revision']:06d}.json"
        print(f"repair_pilot_final_state_sha256={hashlib.sha256(final_state_path.read_bytes()).hexdigest()}")
        self.d3a_pilot_data = (context, inputs, plan_path, state_dir, final)


if __name__ == "__main__":
    unittest.main()
