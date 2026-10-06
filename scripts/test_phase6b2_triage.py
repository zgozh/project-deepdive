#!/usr/bin/env python3
"""Focused Phase 6B2a authenticated triage and publication tests."""

from __future__ import annotations

import importlib
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from test_phase6_review import _prepare_review, _report_pair  # noqa: E402


class Phase6B2TriageTests(unittest.TestCase):
    def test_classifier_is_deterministic_and_unknown_codes_stop_upstream(self):
        review_api = importlib.import_module("phase6_review")
        evidence = {"results": [{
            "occurrence_id": "OCC-" + "4" * 64, "line_number": 7, "kind": "CLAIM",
            "findings": [{"issue_code": "CITATION_MISMATCH", "severity": "MAJOR",
                          "recommended_action": "ADD_OR_FIX_REFERENCE", "evidence_id": "EVID-X"}],
        }]}
        beginner = {"results": [{
            "scope_id": "SECTION-" + "5" * 64, "line_number": 3, "scope_kind": "SECTION",
            "findings": [{"issue_code": "UNDEFINED_JARGON", "severity": "MINOR",
                          "recommended_action": "DEFINE_TERM_BEFORE_USE", "location_kind": "section",
                          "location_id": "SECTION-" + "5" * 64}],
        }, {
            "scope_id": "EXERCISES-" + "6" * 64, "line_number": 12, "scope_kind": "EXERCISE_SET",
            "findings": [{"issue_code": "EXERCISE_UNCLEAR", "severity": "MINOR",
                          "recommended_action": "REWRITE_EXERCISE", "location_kind": "exercise",
                          "location_id": "EX-1"}],
        }, {
            "scope_id": "SECTION-" + "7" * 64, "line_number": 15, "scope_kind": "SECTION",
            "findings": [{"issue_code": "UNDEFINED_JARGON", "severity": "MINOR",
                          "recommended_action": "ADD_PROJECT_CONTEXT", "location_kind": "section",
                          "location_id": "SECTION-" + "7" * 64}],
        }, {
            "scope_id": "SECTION-" + "8" * 64, "line_number": 17, "scope_kind": "SECTION",
            "findings": [{"issue_code": "FUTURE_UNRECOGNIZED", "severity": "MINOR",
                          "recommended_action": "NO_CHANGE", "location_kind": "section",
                          "location_id": "SECTION-" + "8" * 64}],
        }]}

        first = review_api.classify_b2a_findings(
            snapshot_key="b" * 64, chapter_id="CHAPTER-" + "e" * 64,
            review_session_id="REVIEW-" + "3" * 64,
            evidence_report=evidence, beginner_report=beginner,
        )
        second = review_api.classify_b2a_findings(
            snapshot_key="b" * 64, chapter_id="CHAPTER-" + "e" * 64,
            review_session_id="REVIEW-" + "3" * 64,
            evidence_report=evidence, beginner_report=beginner,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [
                "UPSTREAM_CLAIM_REQUIRED", "TEACHING_EDIT_POSSIBLE", "TEACHING_EDIT_POSSIBLE",
                "TEACHING_EDIT_POSSIBLE", "UPSTREAM_CLAIM_REQUIRED",
            ],
            [row["classification"] for row in first],
        )

    def test_only_exact_unmarked_general_finding_allows_same_facts_rewrite(self):
        review_api = importlib.import_module("phase6_review")
        base_finding = {
            "issue_code": "PROJECT_FACT_UNMARKED",
            "severity": "MAJOR",
            "recommended_action": "MARK_AS_CLAIM_OR_REWRITE_AS_GENERAL",
            "evidence_id": None,
        }

        def classify(finding, *, kind="GENERAL", role="EVIDENCE"):
            evidence = {"results": []}
            beginner = {"results": []}
            if role == "EVIDENCE":
                evidence["results"].append({
                    "occurrence_id": "OCC-" + "a" * 64,
                    "kind": kind,
                    "findings": [finding],
                })
            else:
                beginner["results"].append({
                    "scope_id": "SECTION-" + "b" * 64,
                    "findings": [finding],
                })
            return review_api.classify_b2a_findings(
                snapshot_key="c" * 64,
                chapter_id="CHAPTER-" + "d" * 64,
                review_session_id="REVIEW-" + "e" * 64,
                evidence_report=evidence,
                beginner_report=beginner,
            )[0]["classification"]

        self.assertEqual("TEACHING_EDIT_POSSIBLE", classify(base_finding))
        self.assertEqual("UPSTREAM_CLAIM_REQUIRED", classify(base_finding, kind="CLAIM"))
        self.assertEqual(
            "UPSTREAM_CLAIM_REQUIRED",
            classify({**base_finding, "recommended_action": "ADD_OR_FIX_REFERENCE"}),
        )
        self.assertEqual(
            "UPSTREAM_CLAIM_REQUIRED",
            classify({**base_finding, "evidence_id": "EVID-existing"}),
        )
        self.assertEqual(
            "UPSTREAM_CLAIM_REQUIRED",
            classify({**base_finding, "issue_code": "CITATION_MISMATCH"}),
        )
        self.assertEqual("UPSTREAM_CLAIM_REQUIRED", classify(base_finding, role="BEGINNER"))

    def test_cli_publishes_only_authenticated_teaching_handoff_and_triage(self):
        workflow = importlib.import_module("chapter_review_workflow")
        cli = importlib.import_module("chapter_repair_triage")
        _fixture, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        evidence_result = next(row for row in evidence["results"] if row["kind"] == "CLAIM")
        evidence_result["outcome"] = "NEEDS_REVISION"
        evidence_result["findings"] = [{
            "issue_code": "CITATION_MISMATCH", "severity": "MAJOR",
            "recommended_action": "ADD_OR_FIX_REFERENCE", "evidence_id": evidence_result["evidence_ids"][0],
        }]
        general_result = next(row for row in evidence["results"] if row["kind"] == "GENERAL")
        general_result["outcome"] = "PROJECT_FACT_UNMARKED"
        general_result["findings"] = [{
            "issue_code": "PROJECT_FACT_UNMARKED", "severity": "MAJOR",
            "recommended_action": "MARK_AS_CLAIM_OR_REWRITE_AS_GENERAL", "evidence_id": None,
        }]
        beginner_result = beginner["results"][0]
        beginner_result["outcome"] = "NEEDS_REVISION"
        beginner_result["findings"] = [{
            "location_kind": "section", "location_id": beginner_result["scope_id"],
            "issue_code": "UNDEFINED_JARGON", "severity": "MAJOR",
            "recommended_action": "ADD_PROJECT_CONTEXT",
        }]
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        workflow.finalize_chapter_review(
            inputs, chapter_dir=chapter_dir, review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=review_dir / "chapter-review-status.json",
        )
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "triage-output"
            code = cli.main([
                "--run-dir", str(inputs.run_dir), "--root", str(inputs.root),
                "--phase4c-package", str(inputs.phase4c_package),
                "--claim-candidates", str(inputs.claim_candidates_path),
                "--claim-evidence", str(inputs.claim_evidence_path),
                "--claim-evidence-graph", str(inputs.claim_evidence_graph_path),
                "--prerequisite-candidates", str(inputs.prerequisite_candidates_path),
                "--prerequisite-graph", str(inputs.prerequisite_graph_path),
                "--curriculum-candidates", str(inputs.curriculum_candidates_path),
                "--curriculum", str(inputs.curriculum_path),
                "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
                "--out-dir", str(output),
            ])

            self.assertEqual(0, code)
            self.assertEqual({"chapter-repair-triage.json", "writer-handoff.md"}, {p.name for p in output.iterdir()})
            artifact_path = output / "chapter-repair-triage.json"
            artifact = load_artifact(artifact_path)
            self.assertEqual(dumps_artifact(artifact).encode("utf-8"), artifact_path.read_bytes())
            handoff = (output / "writer-handoff.md").read_text(encoding="utf-8")

        self.assertEqual("UPSTREAM_CLAIM_REQUIRED", artifact["overall_disposition"])
        facts = load_artifact(chapter_dir / "chapter-facts.json")
        self.assertEqual(facts["source_status"], artifact["source_status"])
        self.assertEqual(facts["unknown_files"], artifact["unknown_files"])
        for artifact_key, report_name in (
            ("review_session_sha256", "chapter-review-session.json"),
            ("review_status_sha256", "chapter-review-status.json"),
            ("evidence_report_sha256", "evidence-review.json"),
            ("beginner_report_sha256", "beginner-review.json"),
        ):
            expected_sha = hashlib.sha256((review_dir / report_name).read_bytes()).hexdigest()
            self.assertEqual(expected_sha, artifact[artifact_key])
        self.assertEqual(1, sum(row["classification"] == "UPSTREAM_CLAIM_REQUIRED" for row in artifact["findings"]))
        self.assertEqual(2, sum(row["classification"] == "TEACHING_EDIT_POSSIBLE" for row in artifact["findings"]))
        self.assertIn("UPSTREAM_CLAIM_REQUIRED", handoff)
        self.assertNotIn("CITATION_MISMATCH", handoff)
        self.assertIn("REWRITE_AS_GENERAL_WITHOUT_PROJECT_ASSERTION", handoff)
        self.assertIn("Do not convert this sentence to a Claim or attach evidence references.", handoff)
        self.assertIn("DEFINE_TERM_BEFORE_USE", handoff)
        self.assertNotIn("ADD_PROJECT_CONTEXT", handoff)
        self.assertNotIn(_fixture["claim"]["text"], handoff)

    def test_cli_rejects_existing_output_without_overwrite(self):
        cli = importlib.import_module("chapter_repair_triage")
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "already-exists"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("owner", encoding="utf-8")
            with self.assertRaises(importlib.import_module("phase6_chapter").Phase6ChapterError) as raised:
                cli.run_triage(
                    inputs=type("Inputs", (), {})(),
                    chapter_dir=Path(parent) / "chapter",
                    review_dir=Path(parent) / "review",
                    out_dir=output,
                )
            self.assertEqual("OUTPUT_EXISTS", raised.exception.code)
            self.assertEqual("owner", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
