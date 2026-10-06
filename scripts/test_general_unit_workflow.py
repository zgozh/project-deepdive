#!/usr/bin/env python3
"""Authenticated prepare/build tests and one synthetic Phase 5 CLI pilot."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact  # noqa: E402
from test_phase6_chapter import _phase6_fixture  # noqa: E402
from test_phase6_general_learning import _candidate  # noqa: E402


class GeneralUnitWorkflowTests(unittest.TestCase):
    def test_cli_prepare_candidate_build_pilot_publishes_bound_three_file_artifact(self):
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        workflow = SKILL_ROOT / "scripts" / "general_unit_workflow.py"
        unit = next(
            unit for unit in context["curriculum"]["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER"
            and unit["scope"] == "GENERAL_LEARNING"
            and not any(unit["project_refs"].values())
        )
        prep = context["fixture"]["work"] / "general-unit-prepare"
        output = context["fixture"]["work"] / "general-unit-build"
        common = [
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
        ]
        prepare_command = [
            sys.executable, str(workflow), "prepare", *common,
            "--unit-id", unit["id"], "--out-dir", str(prep),
        ]
        prepared = subprocess.run(
            prepare_command, cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        facts_path = prep / "general-unit-facts.json"
        packet_path = prep / "general-unit-writer-packet.md"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        candidate = _candidate(facts)
        candidate_path = prep / "general-unit-candidate.json"
        candidate_raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8")
        candidate_path.write_bytes(candidate_raw)

        build_command = [
            sys.executable, str(workflow), "build", *common,
            "--unit-id", unit["id"],
            "--facts", str(facts_path), "--candidate", str(candidate_path),
            "--out-dir", str(output),
        ]
        built = subprocess.run(
            build_command, cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        self.assertTrue(packet_path.is_file())
        self.assertEqual(
            {"general-learning-unit.json", "general-learning-unit.md", "general-answer-book.md"},
            {path.name for path in output.iterdir()},
        )
        artifact_path = output / "general-learning-unit.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        lesson = (output / "general-learning-unit.md").read_bytes()
        answer_book = (output / "general-answer-book.md").read_bytes()
        self.assertEqual(hashlib.sha256(candidate_raw).hexdigest(), artifact["candidate_sha256"])
        self.assertEqual(hashlib.sha256(lesson).hexdigest(), artifact["lesson_markdown_sha256"])
        self.assertEqual(hashlib.sha256(answer_book).hexdigest(), artifact["answer_book_markdown_sha256"])
        self.assertEqual("PREREQUISITE_PRIMER", artifact["selected_unit"]["origin"])
        self.assertEqual("GENERAL_LEARNING", artifact["selected_unit"]["scope"])
        self.assertEqual("PARTIAL", artifact["overall_status"])
        self.assertEqual("UNVERIFIED_TEACHING", artifact["epistemic_status"])
        self.assertEqual("NOT_RUN", artifact["general_fact_review"])
        self.assertEqual("NOT_RUN", artifact["beginner_review"])
        self.assertEqual("NOT_RUN", artifact["answer_book_review"])
        self.assertEqual(dumps_artifact(artifact).encode("utf-8"), artifact_path.read_bytes())

    def test_tampered_facts_and_stale_phase5_input_fail_without_output(self):
        workflow = importlib.import_module("general_unit_workflow")
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        unit = next(
            unit for unit in context["curriculum"]["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "GENERAL_LEARNING"
        )
        prep = context["fixture"]["work"] / "general-unit-prepare"
        workflow.prepare_general_unit(inputs, unit_id=unit["id"], out_dir=prep)
        facts_path = prep / "general-unit-facts.json"
        original_facts = facts_path.read_bytes()
        candidate_path = prep / "general-unit-candidate.json"
        candidate_path.write_bytes(b"{}")
        facts_path.write_bytes(original_facts + b" ")
        output = context["fixture"]["work"] / "invalid-facts-build"
        with self.assertRaises(workflow.Phase6GeneralLearningError):
            workflow.build_general_unit(
                inputs, unit_id=unit["id"], facts_path=facts_path,
                candidate_path=candidate_path, out_dir=output,
            )
        self.assertFalse(output.exists())

        facts_path.write_bytes(original_facts)
        inputs.curriculum_path.write_bytes(b"not-json")
        stale_output = context["fixture"]["work"] / "stale-phase5-build"
        with self.assertRaises(workflow.Phase6GeneralLearningError):
            workflow.build_general_unit(
                inputs, unit_id=unit["id"], facts_path=facts_path,
                candidate_path=candidate_path, out_dir=stale_output,
            )
        self.assertFalse(stale_output.exists())
