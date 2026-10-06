#!/usr/bin/env python3
"""Workflow checks and one synthetic Git pilot for Phase 6D2B1."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import importlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import curriculum_run_workflow as workflow  # noqa: E402
from artifact_contract import dumps_artifact  # noqa: E402
from phase6_chapter import Phase6ChapterError  # noqa: E402
from test_phase6_chapter import _phase6_fixture  # noqa: E402


def _mixed_project_primer_fixture(testcase: unittest.TestCase):
    context, inputs, _unit_id, _output = _phase6_fixture(testcase)
    curriculum_tests = importlib.import_module("test_phase5_curriculum")
    candidate_units = curriculum_tests._starter_units(context)
    workflow_unit = next(unit for unit in candidate_units if unit["unit_key"] == "workflow-map")
    workflow_unit["introduces_concept_keys"] = []
    candidates = curriculum_tests._curriculum_candidates(context, candidate_units)
    curriculum_output = context["fixture"]["work"] / "curriculum-with-project-primer.json"
    context["curriculum_output_path"] = curriculum_output
    inputs = replace(inputs, curriculum_path=curriculum_output)
    context["curriculum"] = curriculum_tests._build(testcase, context, candidates)
    return context, inputs, context["curriculum"]


def _cli_arguments(inputs, out: Path) -> list[str]:
    return [
        "plan",
        "--phase4c-package", str(inputs.phase4c_package),
        "--claim-candidates", str(inputs.claim_candidates_path),
        "--claim-evidence", str(inputs.claim_evidence_path),
        "--claim-evidence-graph", str(inputs.claim_evidence_graph_path),
        "--prerequisite-candidates", str(inputs.prerequisite_candidates_path),
        "--prerequisite-graph", str(inputs.prerequisite_graph_path),
        "--curriculum-candidates", str(inputs.curriculum_candidates_path),
        "--curriculum", str(inputs.curriculum_path),
        "--run-dir", str(inputs.run_dir),
        "--root", str(inputs.root),
        "--out", str(out),
    ]


def _git_status(root: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class CurriculumRunWorkflowTests(unittest.TestCase):
    def test_cli_synthetic_git_pilot_routes_all_units_with_one_capture(self):
        context, inputs, curriculum = _mixed_project_primer_fixture(self)
        out = context["fixture"]["work"] / "curriculum-run-plan.json"
        before = _git_status(inputs.root)
        capture = workflow._capture_bundle
        stdout = io.StringIO()

        with patch.object(workflow, "_capture_bundle", wraps=capture) as capture_spy:
            with redirect_stdout(stdout):
                exit_code = workflow.main(_cli_arguments(inputs, out))

        self.assertEqual(0, exit_code, stdout.getvalue())
        self.assertEqual(1, capture_spy.call_count)
        self.assertEqual(before, _git_status(inputs.root))
        self.assertTrue(out.is_file())
        raw = out.read_bytes()
        plan = json.loads(raw)
        self.assertEqual(dumps_artifact(plan).encode("utf-8"), raw)
        self.assertEqual(curriculum["units"], [row["unit_identity"] for row in plan["units"]])
        self.assertEqual(list(range(1, len(curriculum["units"]) + 1)), [row["position"] for row in plan["units"]])
        self.assertEqual("PASS", plan["source_status"])
        self.assertEqual(curriculum["source_metadata"]["unknown_files"], plan["unknown_files"])
        self.assertEqual(10, len(plan["input_digests"]))
        self.assertEqual(
            sorted(row["role"] for row in plan["input_digests"]),
            [row["role"] for row in plan["input_digests"]],
        )

        rows_by_id = {row["unit_identity"]["id"]: row for row in plan["units"]}
        primer = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "PROJECT_SPECIFIC"
        )
        project_candidate = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "CANDIDATE" and unit["scope"] == "PROJECT_SPECIFIC"
        )
        self.assertEqual(0, sum(len(values) for values in primer["project_refs"].values()))
        self.assertEqual("D2A_GENERAL_LEARNING", rows_by_id[primer["id"]]["route"])
        self.assertEqual("PHASE6A_PROJECT_CLAIM", rows_by_id[project_candidate["id"]]["route"])
        digest = hashlib.sha256(raw).hexdigest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        print(f"synthetic_pilot_output_sha256={digest}")

    def test_output_inside_target_is_rejected_before_capture(self):
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        out = inputs.root / "must-not-be-written.json"
        stdout = io.StringIO()
        capture = workflow._capture_bundle

        with patch.object(workflow, "_capture_bundle", wraps=capture) as capture_spy:
            with redirect_stdout(stdout):
                exit_code = workflow.main(_cli_arguments(inputs, out))

        self.assertEqual(2, exit_code)
        self.assertIn("error=OUTPUT_INVALID", stdout.getvalue())
        self.assertEqual(0, capture_spy.call_count)
        self.assertFalse(out.exists())

    def test_changed_phase5_input_fails_without_publishing(self):
        _context, inputs, _unit_id, _output = _phase6_fixture(self)
        out = inputs.curriculum_path.parent / "stale-curriculum-run-plan.json"
        project = workflow._project_claim_facts
        changed = False

        def change_after_readiness(*args):
            nonlocal changed
            result = project(*args)
            if not changed:
                raw = inputs.curriculum_path.read_bytes()
                inputs.curriculum_path.write_bytes(raw + b" ")
                changed = True
            return result

        with patch.object(workflow, "_project_claim_facts", side_effect=change_after_readiness):
            with self.assertRaises(Phase6ChapterError) as caught:
                workflow.plan_curriculum_run(inputs, out=out)

        self.assertTrue(changed)
        self.assertEqual("INPUT_CHANGED", caught.exception.code)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
