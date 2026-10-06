#!/usr/bin/env python3
"""Authenticated D2A2 replay tests and one mechanism-only synthetic CLI pilot."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_phase6_chapter import _phase6_fixture  # noqa: E402
from test_phase6_general_learning import _candidate  # noqa: E402
from artifact_contract import dumps_artifact  # noqa: E402


def _common_inputs(inputs):
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


def _write_report_fixtures(review_dir: Path) -> None:
    """Create explicit synthetic reports for mechanism tests, not real review."""
    factual_path = review_dir / "general-factuality-review-template.json"
    factual = json.loads(factual_path.read_text(encoding="utf-8"))
    factual["generated_at"] = json.loads((review_dir / "general-review-session.json").read_text(encoding="utf-8"))["generated_at"]
    factual["reviewer_alias"] = "fixture-factuality"
    factual["reviewer_session_id"] = "fixture-factuality-session"
    factual["fresh_context_isolated"] = True
    factual["source_reference_inspection"] = True
    for index, row in enumerate(factual["results"]):
        row["findings"] = []
        row["authority_references"] = []
        if index == 0:
            row["outcome"] = "SUPPORTED_BY_AUTHORITY"
            row["authority_references"] = [{
                "reference_id": "fixture-reference-1", "title": "Fixture authority",
                "publisher": "Synthetic test fixture", "locator": "https://example.invalid/fixture",
                "edition": None, "authority_class": "OFFICIAL_DOCUMENTATION",
            }]
        elif index == 1:
            row["outcome"] = "NEEDS_REVISION"
            row["findings"] = [{
                "issue_code": "UNSUPPORTED_GENERAL_FACT", "location_id": row["occurrence_id"],
                "severity": "MINOR", "recommended_action": "CITE_AUTHORITY",
                "focus": "scope of the statement",
                "repair_guidance": "Narrow the general statement to supported conditions.",
            }]
        else:
            row["outcome"] = "NONFACTUAL_TEACHING"
    factual_raw = (json.dumps(factual, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (review_dir / "general-factuality-review.json").write_bytes(factual_raw)

    beginner_path = review_dir / "general-beginner-answer-review-template.json"
    beginner = json.loads(beginner_path.read_text(encoding="utf-8"))
    beginner["generated_at"] = factual["generated_at"]
    beginner["reviewer_alias"] = "fixture-beginner"
    beginner["reviewer_session_id"] = "fixture-beginner-session"
    beginner["fresh_context_isolated"] = True
    for row in beginner["results"]:
        row["outcome"] = "FOLLOWABLE_FOR_BEGINNER"
        row["findings"] = []
    beginner_raw = (json.dumps(beginner, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (review_dir / "general-beginner-review.json").write_bytes(beginner_raw)


def _build_test_unit(context, inputs, name):
    d2a = importlib.import_module("general_unit_workflow")
    unit = next(
        row for row in context["curriculum"]["units"]
        if row["origin"] == "PREREQUISITE_PRIMER" and not any(row["project_refs"].values())
    )
    prepare_dir = context["fixture"]["work"] / f"{name}-prepare"
    unit_dir = context["fixture"]["work"] / f"{name}-unit"
    d2a.prepare_general_unit(inputs, unit_id=unit["id"], out_dir=prepare_dir)
    facts_path = prepare_dir / "general-unit-facts.json"
    facts = json.loads(facts_path.read_bytes())
    candidate_raw = (json.dumps(_candidate(facts), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    candidate_path = prepare_dir / "general-unit-candidate.json"
    candidate_path.write_bytes(candidate_raw)
    d2a.build_general_unit(
        inputs, unit_id=unit["id"], facts_path=facts_path, candidate_path=candidate_path, out_dir=unit_dir,
    )
    return unit, prepare_dir, unit_dir, facts, candidate_raw


class GeneralLearningReviewWorkflowTests(unittest.TestCase):
    def test_authenticator_replays_immutable_v10_rendered_package(self):
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        unit, prepare_dir, unit_dir, facts, candidate_raw = _build_test_unit(context, inputs, "legacy-v10")
        api = importlib.import_module("phase6_general_learning")
        candidate = api.parse_general_unit_candidate(candidate_raw)
        lesson_raw = api.render_general_learning_unit(facts, candidate, schema_version="1.0.0").encode("utf-8")
        answer_raw = api.render_general_answer_book(facts, candidate, schema_version="1.0.0").encode("utf-8")
        artifact = api.build_general_learning_unit(
            facts, candidate, candidate_raw, lesson_raw, answer_raw, schema_version="1.0.0",
        )
        (unit_dir / "general-learning-unit.json").write_bytes(dumps_artifact(artifact).encode("utf-8"))
        (unit_dir / "general-learning-unit.md").write_bytes(lesson_raw)
        (unit_dir / "general-answer-book.md").write_bytes(answer_raw)

        workflow = importlib.import_module("general_learning_review_workflow")
        with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
            authenticated = workflow.authenticate_general_unit_for_review(
                inputs, prepare_dir=prepare_dir, unit_dir=unit_dir,
            )
        self.assertEqual(1, capture.call_count)
        self.assertEqual("1.0.0", authenticated.artifact["schema_version"])
        self.assertEqual(unit["id"], authenticated.artifact["selected_unit"]["id"])

    def test_authenticator_rejects_wrong_stored_kind_or_version_before_capture(self):
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        _unit, prepare_dir, unit_dir, _facts, _candidate_raw = _build_test_unit(context, inputs, "bad-version")
        workflow = importlib.import_module("general_learning_review_workflow")
        artifact_path = unit_dir / "general-learning-unit.json"
        original_raw = artifact_path.read_bytes()
        original = json.loads(original_raw)
        for key, value in (("artifact_kind", "general-unit-candidate"), ("schema_version", "9.9.9")):
            with self.subTest(key=key):
                altered = dict(original)
                altered[key] = value
                altered_raw = (json.dumps(altered, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
                artifact_path.write_bytes(altered_raw)
                with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
                    with self.assertRaises(workflow.GeneralLearningReviewWorkflowError) as raised:
                        workflow.authenticate_general_unit_for_review(
                            inputs, prepare_dir=prepare_dir, unit_dir=unit_dir,
                        )
                self.assertEqual("UNIT_BINDING_INVALID", raised.exception.code)
                self.assertEqual(0, capture.call_count)
        artifact_path.write_bytes(original_raw)

    def test_synthetic_git_cli_pilot_is_bound_and_non_promoting(self):
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        workflow = importlib.import_module("general_learning_review_workflow")
        d2a_cli = SCRIPTS / "general_unit_workflow.py"
        d2a2_cli = SCRIPTS / "general_learning_review_workflow.py"
        unit = next(
            row for row in context["curriculum"]["units"]
            if row["origin"] == "PREREQUISITE_PRIMER" and not any(row["project_refs"].values())
        )
        prepare_dir = context["fixture"]["work"] / "review-pilot-d2a-prepare"
        unit_dir = context["fixture"]["work"] / "review-pilot-d2a-unit"
        review_dir = context["fixture"]["work"] / "review-pilot-d2a2"
        common = _common_inputs(inputs)
        prepared = subprocess.run(
            [sys.executable, str(d2a_cli), "prepare", *common, "--unit-id", unit["id"], "--out-dir", str(prepare_dir)],
            cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        facts_path = prepare_dir / "general-unit-facts.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        candidate = _candidate(facts)
        candidate["writer_alias"] = "Writer A"
        candidate_raw = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        candidate_path = prepare_dir / "general-unit-candidate.json"
        candidate_path.write_bytes(candidate_raw)
        built = subprocess.run(
            [sys.executable, str(d2a_cli), "build", *common, "--unit-id", unit["id"],
             "--facts", str(facts_path), "--candidate", str(candidate_path), "--out-dir", str(unit_dir)],
            cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        original_prepare = {path.name: path.read_bytes() for path in prepare_dir.iterdir()}
        original_unit = {path.name: path.read_bytes() for path in unit_dir.iterdir()}

        prepared_review = subprocess.run(
            [sys.executable, str(d2a2_cli), "prepare", *common, "--prepare-dir", str(prepare_dir),
             "--unit-dir", str(unit_dir), "--review-dir", str(review_dir)],
            cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, prepared_review.returncode, prepared_review.stdout + prepared_review.stderr)
        _write_report_fixtures(review_dir)
        self.assertEqual({
            "general-review-session.json", "general-factuality-review-packet.md",
            "general-beginner-answer-review-packet.md", "general-factuality-review-template.json",
            "general-beginner-answer-review-template.json", "general-factuality-review.json", "general-beginner-review.json",
        }, {path.name for path in review_dir.iterdir()})
        status_path = review_dir / "general-review-status.json"
        finalized = subprocess.run(
            [sys.executable, str(d2a2_cli), "finalize", *common, "--prepare-dir", str(prepare_dir),
             "--unit-dir", str(unit_dir), "--review-dir", str(review_dir), "--out-status", str(status_path)],
            cwd=SKILL_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, finalized.returncode, finalized.stdout + finalized.stderr)
        status_raw = status_path.read_bytes()
        status = json.loads(status_raw)
        artifact = json.loads((unit_dir / "general-learning-unit.json").read_bytes())
        self.assertEqual("REPAIR_REQUIRED", status["review_state"])
        self.assertEqual("DRAFT", status["lesson_status"])
        self.assertEqual("DRAFT", status["answer_book_status"])
        self.assertEqual("PARTIAL", status["overall_status"])
        self.assertEqual("UNVERIFIED_TEACHING", status["epistemic_status"])
        self.assertEqual("NOT_RUN", status["general_fact_review"])
        self.assertEqual("NOT_RUN", status["beginner_review"])
        self.assertEqual("NOT_RUN", status["answer_book_review"])
        self.assertEqual(artifact["source_status"], status["source_status"])
        self.assertEqual(artifact["unknown_files"], status["unknown_files"])
        self.assertEqual(hashlib.sha256(candidate_raw).hexdigest(), status["candidate_sha256"])
        self.assertEqual("Writer A", status["writer_alias"])
        self.assertEqual(original_prepare, {path.name: path.read_bytes() for path in prepare_dir.iterdir()})
        self.assertEqual(original_unit, {path.name: path.read_bytes() for path in unit_dir.iterdir()})
        self.assertEqual("PREREQUISITE_PRIMER", status["selected_unit_origin"])
        self.assertEqual(unit["scope"], status["selected_unit_scope"])
        with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
            authenticated_status = workflow.authenticate_general_unit_review_status(
                inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir,
            )
        self.assertEqual(1, capture.call_count)
        self.assertEqual(status, authenticated_status)

    def test_missing_reports_yield_incomplete_and_tampered_d2a_stops_publication(self):
        workflow = importlib.import_module("general_learning_review_workflow")
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        unit = next(row for row in context["curriculum"]["units"] if row["origin"] == "PREREQUISITE_PRIMER" and not any(row["project_refs"].values()))
        d2a = importlib.import_module("general_unit_workflow")
        prepare_dir = context["fixture"]["work"] / "missing-review-d2a-prepare"
        unit_dir = context["fixture"]["work"] / "missing-review-d2a-unit"
        d2a.prepare_general_unit(inputs, unit_id=unit["id"], out_dir=prepare_dir)
        facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
        candidate_raw = (json.dumps(_candidate(facts), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        candidate_path = prepare_dir / "general-unit-candidate.json"
        candidate_path.write_bytes(candidate_raw)
        d2a.build_general_unit(inputs, unit_id=unit["id"], facts_path=prepare_dir / "general-unit-facts.json", candidate_path=candidate_path, out_dir=unit_dir)
        review_dir = context["fixture"]["work"] / "missing-review-d2a2"
        with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
            workflow.prepare_general_unit_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir)
            self.assertEqual(1, capture.call_count)
        with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
            status = workflow.finalize_general_unit_review(
                inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir,
                out_status=review_dir / "general-review-status.json",
            )
            self.assertEqual(1, capture.call_count)
        self.assertEqual("REVIEW_INCOMPLETE", status["review_state"])
        self.assertEqual(["GENERAL_FACTUALITY", "BEGINNER_ANSWER"], status["missing_roles"])
        with patch.object(workflow, "_capture_bundle", wraps=workflow._capture_bundle) as capture:
            authenticated_status = workflow.authenticate_general_unit_review_status(
                inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir,
            )
        self.assertEqual(1, capture.call_count)
        self.assertEqual(status, authenticated_status)

        # A new review attempt against changed D2A Markdown must fail before status publication.
        stale_review = context["fixture"]["work"] / "stale-review-d2a2"
        workflow.prepare_general_unit_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=stale_review)
        tamper_targets = [
            (prepare_dir / "general-unit-facts.json", b" "),
            (prepare_dir / "general-unit-writer-packet.md", b"changed"),
            (candidate_path, b" "),
            (unit_dir / "general-learning-unit.json", b" "),
            (unit_dir / "general-learning-unit.md", b"changed"),
            (unit_dir / "general-answer-book.md", b"changed"),
        ]
        for path, suffix in tamper_targets:
            original = path.read_bytes()
            with self.subTest(name=path.name):
                path.write_bytes(original + suffix)
                with self.assertRaises(workflow.GeneralLearningReviewWorkflowError):
                    workflow.finalize_general_unit_review(
                        inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=stale_review,
                        out_status=stale_review / "general-review-status.json",
                    )
                self.assertFalse((stale_review / "general-review-status.json").exists())
                path.write_bytes(original)

    def test_duplicate_key_report_is_rejected_without_status(self):
        workflow = importlib.import_module("general_learning_review_workflow")
        context, inputs, _chapter_unit_id, _output = _phase6_fixture(self)
        unit = next(row for row in context["curriculum"]["units"] if row["origin"] == "PREREQUISITE_PRIMER" and not any(row["project_refs"].values()))
        d2a = importlib.import_module("general_unit_workflow")
        prepare_dir = context["fixture"]["work"] / "duplicate-review-d2a-prepare"
        unit_dir = context["fixture"]["work"] / "duplicate-review-d2a-unit"
        d2a.prepare_general_unit(inputs, unit_id=unit["id"], out_dir=prepare_dir)
        facts = json.loads((prepare_dir / "general-unit-facts.json").read_bytes())
        candidate_path = prepare_dir / "general-unit-candidate.json"
        candidate_path.write_text(json.dumps(_candidate(facts), ensure_ascii=False), encoding="utf-8")
        d2a.build_general_unit(inputs, unit_id=unit["id"], facts_path=prepare_dir / "general-unit-facts.json", candidate_path=candidate_path, out_dir=unit_dir)
        review_dir = context["fixture"]["work"] / "duplicate-review-d2a2"
        workflow.prepare_general_unit_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir)
        (review_dir / "general-factuality-review.json").write_text(
            '{"artifact_kind":"general-factuality-review","artifact_kind":"general-factuality-review"}', encoding="utf-8",
        )
        with self.assertRaises(workflow.GeneralLearningReviewWorkflowError):
            workflow.finalize_general_unit_review(
                inputs, prepare_dir=prepare_dir, unit_dir=unit_dir, review_dir=review_dir,
                out_status=review_dir / "general-review-status.json",
            )
        self.assertFalse((review_dir / "general-review-status.json").exists())


if __name__ == "__main__":
    unittest.main()
