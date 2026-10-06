#!/usr/bin/env python3
"""Temporary-Git contracts for Phase 6D1b independent answer review."""

from __future__ import annotations

import sys
import importlib
import copy
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import ArtifactValidationError, schema_path  # noqa: E402
from artifact_contract import dumps_artifact  # noqa: E402


def _answer_fixture(testcase, *, general_text=None):
    answer_book = importlib.import_module("phase6_answer_book")
    from test_phase6_answer_book import _completed_candidate, _reviewed_package

    inputs, chapter_dir, chapter_review_dir, package = _reviewed_package(testcase)
    candidate = _completed_candidate(answer_book, package)
    if general_text is not None:
        candidate["records"][0]["worked_steps"][0]["text"] = general_text
    candidate_raw = dumps_artifact(candidate).encode("utf-8")
    bank = answer_book.build_answer_bank(package, candidate, candidate_raw)
    bank_raw = dumps_artifact(bank).encode("utf-8")
    book_raw = answer_book.render_answer_book(bank).encode("utf-8")
    base = chapter_review_dir.parent
    candidate_path = base / "answer-candidates.json"
    candidate_path.write_bytes(candidate_raw)
    answer_dir = base / "answer-book"
    answer_dir.mkdir()
    (answer_dir / "exercise-bank.json").write_bytes(bank_raw)
    (answer_dir / "ANSWER-BOOK.md").write_bytes(book_raw)
    review_dir = base / "phase6d1b-review"
    return {
        "inputs": inputs,
        "chapter_dir": chapter_dir,
        "chapter_review_dir": chapter_review_dir,
        "package": package,
        "candidate": candidate,
        "candidate_path": candidate_path,
        "answer_dir": answer_dir,
        "review_dir": review_dir,
        "bank_raw": bank_raw,
        "book_raw": book_raw,
    }


def _prepare_answer_review(testcase, *, general_text=None):
    workflow = importlib.import_module("answer_review_workflow")
    testcase.assertTrue(callable(getattr(workflow, "prepare_answer_review", None)))
    fixture = _answer_fixture(testcase, general_text=general_text)
    result = workflow.prepare_answer_review(
        fixture["inputs"],
        chapter_dir=fixture["chapter_dir"],
        chapter_review_dir=fixture["chapter_review_dir"],
        answer_candidates=fixture["candidate_path"],
        answer_dir=fixture["answer_dir"],
        review_dir=fixture["review_dir"],
    )
    return workflow, fixture, result


def _cli_input_args(inputs):
    return [
        "--run-dir", str(inputs.run_dir),
        "--root", str(inputs.root),
        "--phase4c-package", str(inputs.phase4c_package),
        "--claim-candidates", str(inputs.claim_candidates_path),
        "--claim-evidence", str(inputs.claim_evidence_path),
        "--claim-evidence-graph", str(inputs.claim_evidence_graph_path),
        "--prerequisite-candidates", str(inputs.prerequisite_candidates_path),
        "--prerequisite-graph", str(inputs.prerequisite_graph_path),
        "--curriculum-candidates", str(inputs.curriculum_candidates_path),
        "--curriculum", str(inputs.curriculum_path),
    ]


def _completed_reports(review_dir, *, evidence_alias="evidence-local", beginner_alias="beginner-local", same_sessions=False):
    session = json.loads((review_dir / "answer-review-session.json").read_text(encoding="utf-8"))
    evidence = json.loads((review_dir / "answer-evidence-review-template.json").read_text(encoding="utf-8"))
    beginner = json.loads((review_dir / "answer-beginner-review-template.json").read_text(encoding="utf-8"))
    evidence.update({
        "generated_at": session["generated_at"],
        "reviewer_alias": evidence_alias,
        "reviewer_session_id": "evidence-session-01",
        "fresh_context_isolated": True,
        "direct_source_inspection": True,
    })
    beginner.update({
        "generated_at": session["generated_at"],
        "reviewer_alias": beginner_alias,
        "reviewer_session_id": "evidence-session-01" if same_sessions else "beginner-session-01",
        "fresh_context_isolated": True,
    })
    for row in evidence["results"]:
        row["outcome"] = "CONSISTENT_WITH_CITED_EVIDENCE" if row["kind"] == "CLAIM" else "GENERAL_TEACHING"
        row["findings"] = []
    for row in beginner["results"]:
        row["outcome"] = "FOLLOWABLE_FOR_BEGINNER"
        row["findings"] = []
    (review_dir / "answer-evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
    (review_dir / "answer-beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
    return evidence, beginner


class AnswerReviewSchemaTests(unittest.TestCase):
    def test_answer_review_sidecars_register_only_v1_contracts(self):
        for kind in (
            "answer-review-session",
            "answer-evidence-review",
            "answer-beginner-review",
            "answer-review-status",
        ):
            with self.subTest(kind=kind):
                self.assertEqual(f"{kind}.schema.json", schema_path(kind, "1.0.0").name)
                for version in ("1.1.0", "2.0.0"):
                    with self.assertRaises(ArtifactValidationError):
                        schema_path(kind, version)


class AnswerReviewInventoryTests(unittest.TestCase):
    def test_inventory_covers_every_project_and_general_fragment(self):
        module = importlib.import_module("answer_review")
        self.assertTrue(callable(getattr(module, "expected_answer_occurrences", None)))
        claim_id = "CLAIM-" + "c" * 64
        question_id = "EX-CHAPTER-" + "a" * 64 + "-trace-request"
        bank = {
            "claim_evidence_catalog": [{
                "claim_id": claim_id,
                "evidence": [{"id": "EVID-source", "level": "E1", "locator": {"path": "src/order.py", "line_start": 8, "line_end": 9}}],
            }],
            "records": [{
                "id": question_id,
                "prompt": "Trace what happens to an order.",
                "worked_steps": [
                    {"kind": "GENERAL", "text": "Start with the request."},
                    {"kind": "CLAIM", "text": "The order service validates the request.", "claim_id": claim_id},
                ],
                "progressive_hints": [
                    {"level": 1, "fragments": [{"kind": "GENERAL", "text": "Find the first operation."}]},
                    {"level": 2, "fragments": [{"kind": "GENERAL", "text": "Follow the service call."}]},
                ],
                "common_mistakes": [{"kind": "GENERAL", "text": "Do not skip the validation step."}],
                "rubric": [{"kind": "GENERAL", "text": "Name the input, operation, and result."}],
                "acceptable_tradeoffs": [],
            }],
        }

        occurrences = module.expected_answer_occurrences(bank)
        scopes = module.expected_beginner_scopes(bank, occurrences)

        self.assertEqual(6, len(occurrences))
        self.assertEqual(["GENERAL", "CLAIM", "GENERAL", "GENERAL", "GENERAL", "GENERAL"], [row["kind"] for row in occurrences])
        claim = occurrences[1]
        self.assertEqual(claim_id, claim["claim_id"])
        self.assertEqual(["EVID-source"], claim["evidence_ids"])
        self.assertEqual(5, len(scopes))
        self.assertEqual(["question_prompt", "worked_steps", "progressive_hints", "common_mistakes", "rubric"], [row["section"] for row in scopes])
        self.assertEqual([row["occurrence_id"] for row in occurrences], [row["occurrence_id"] for row in module.expected_answer_occurrences(bank)])

    def test_inventory_identity_changes_when_answer_text_changes(self):
        module = importlib.import_module("answer_review")
        self.assertTrue(callable(getattr(module, "expected_answer_occurrences", None)))
        question_id = "EX-CHAPTER-" + "a" * 64 + "-trace-request"
        bank = {
            "claim_evidence_catalog": [],
            "records": [{
                "id": question_id,
                "prompt": "Trace what happens to an order.",
                "worked_steps": [{"kind": "GENERAL", "text": "Start with the request."}],
                "progressive_hints": [{"level": 1, "fragments": [{"kind": "GENERAL", "text": "Find the first operation."}]}],
                "common_mistakes": [{"kind": "GENERAL", "text": "Do not skip the validation step."}],
                "rubric": [{"kind": "GENERAL", "text": "Name the input, operation, and result."}],
                "acceptable_tradeoffs": [],
            }],
        }
        first = module.expected_answer_occurrences(bank)
        bank["records"][0]["worked_steps"][0]["text"] = "Start with the response."
        second = module.expected_answer_occurrences(bank)
        self.assertNotEqual(first[0]["occurrence_id"], second[0]["occurrence_id"])

    def test_beginner_findings_cannot_reference_another_section_or_question(self):
        module = importlib.import_module("answer_review")
        from test_artifact_contract import minimal_artifacts

        bank = {
            "claim_evidence_catalog": [],
            "records": [
                {
                    "id": "EX-CHAPTER-" + "1" * 64 + "-first-question",
                    "prompt": "Explain the first operation.",
                    "worked_steps": [{"kind": "GENERAL", "text": "First answer step."}],
                    "progressive_hints": [],
                    "common_mistakes": [],
                    "rubric": [],
                    "acceptable_tradeoffs": [],
                },
                {
                    "id": "EX-CHAPTER-" + "2" * 64 + "-second-question",
                    "prompt": "Explain the second operation.",
                    "worked_steps": [{"kind": "GENERAL", "text": "Second answer step."}],
                    "progressive_hints": [],
                    "common_mistakes": [],
                    "rubric": [],
                    "acceptable_tradeoffs": [],
                },
            ],
        }
        occurrences = module.expected_answer_occurrences(bank)
        scopes = module.expected_beginner_scopes(bank, occurrences)
        artifacts = minimal_artifacts()
        session = artifacts["answer-review-session"]
        template = artifacts["answer-beginner-review"]
        template["results"] = [
            {**scope, "outcome": "FOLLOWABLE_FOR_BEGINNER", "findings": []}
            for scope in scopes
        ]

        for invalid_location in (scopes[1]["scope_id"], scopes[2]["scope_id"]):
            with self.subTest(invalid_location=invalid_location):
                report = copy.deepcopy(template)
                report["results"][0]["outcome"] = "NEEDS_REVISION"
                report["results"][0]["findings"] = [{
                    "issue_code": "UNDEFINED_JARGON",
                    "severity": "MAJOR",
                    "recommended_action": "DEFINE_TERM_BEFORE_USE",
                    "location_id": invalid_location,
                }]
                with self.assertRaises(module.Phase6AnswerReviewError) as raised:
                    module.validate_beginner_report(
                        report,
                        session,
                        report["review_session_sha256"],
                        report["packet_sha256"],
                        scopes,
                    )
                self.assertEqual("REPORT_REFERENCE_INVALID", raised.exception.code)


class AnswerReviewWorkflowTests(unittest.TestCase):
    def test_prepare_and_finalize_bind_the_exact_answer_book_without_promotion(self):
        workflow = importlib.import_module("answer_review_workflow")
        fixture = _answer_fixture(self)
        base_args = _cli_input_args(fixture["inputs"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, workflow.main([
                "prepare", *base_args,
                "--chapter-dir", str(fixture["chapter_dir"]),
                "--chapter-review-dir", str(fixture["chapter_review_dir"]),
                "--answer-candidates", str(fixture["candidate_path"]),
                "--answer-dir", str(fixture["answer_dir"]),
                "--review-dir", str(fixture["review_dir"]),
            ]))
        self.assertEqual("prepare", json.loads(output.getvalue())["command"])
        review_dir = fixture["review_dir"]
        self.assertEqual({
            "answer-review-session.json",
            "answer-evidence-verifier-packet.md",
            "answer-beginner-reviewer-packet.md",
            "answer-evidence-review-template.json",
            "answer-beginner-review-template.json",
        }, {path.name for path in review_dir.iterdir()})
        session = json.loads((review_dir / "answer-review-session.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["package"].context.source_status, session["source_status"])
        self.assertEqual(fixture["package"].context.unknown_files, session["unknown_files"])
        self.assertEqual("NOT_RUN", session["answer_evidence_review"])
        self.assertEqual("NOT_RUN", session["answer_beginner_review"])
        evidence_packet = (review_dir / "answer-evidence-verifier-packet.md").read_text(encoding="utf-8")
        beginner_packet = (review_dir / "answer-beginner-reviewer-packet.md").read_text(encoding="utf-8")
        self.assertIn(fixture["candidate"]["records"][0]["worked_steps"][0]["text"], evidence_packet)
        self.assertIn(fixture["candidate"]["records"][0]["worked_steps"][0]["text"], beginner_packet)
        for item in fixture["package"].context.facts["evidence"]:
            relative = item["locator"].get("path")
            if relative:
                source_body = fixture["package"].context.read_snapshot_source(relative).decode("utf-8")
                self.assertNotIn(source_body, evidence_packet)
                self.assertNotIn(source_body, beginner_packet)

        _completed_reports(review_dir)
        self.assertTrue(callable(getattr(workflow, "finalize_answer_review", None)))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, workflow.main([
                "finalize", *base_args,
                "--chapter-dir", str(fixture["chapter_dir"]),
                "--chapter-review-dir", str(fixture["chapter_review_dir"]),
                "--answer-candidates", str(fixture["candidate_path"]),
                "--answer-dir", str(fixture["answer_dir"]),
                "--review-dir", str(review_dir),
                "--evidence-report", str(review_dir / "answer-evidence-review.json"),
                "--beginner-report", str(review_dir / "answer-beginner-review.json"),
                "--out-status", str(review_dir / "answer-review-status.json"),
            ]))
        status = json.loads(output.getvalue())
        self.assertEqual("REVIEWED_DRAFT", status["review_state"])
        self.assertEqual("PARTIAL", status["overall_status"])
        self.assertEqual("DRAFT", status["answer_book_status"])
        self.assertEqual(fixture["package"].context.source_status, status["source_status"])
        self.assertEqual(fixture["package"].context.unknown_files, status["unknown_files"])
        self.assertEqual(fixture["bank_raw"], (fixture["answer_dir"] / "exercise-bank.json").read_bytes())
        self.assertEqual(fixture["book_raw"], (fixture["answer_dir"] / "ANSWER-BOOK.md").read_bytes())
        self.assertEqual("NOT_RUN", json.loads((fixture["answer_dir"] / "exercise-bank.json").read_text(encoding="utf-8"))["answer_evidence_review"])

    def test_finalize_rejects_missing_occurrence_and_forged_report_binding_without_status(self):
        workflow, fixture, _ = _prepare_answer_review(self)
        review_dir = fixture["review_dir"]
        evidence, _ = _completed_reports(review_dir)
        missing = evidence["results"].pop()
        evidence_path = review_dir / "answer-evidence-review.json"
        evidence_path.write_bytes(dumps_artifact(evidence).encode("utf-8"))
        status_path = review_dir / "answer-review-status.json"
        finalize_args = {
            "chapter_dir": fixture["chapter_dir"],
            "chapter_review_dir": fixture["chapter_review_dir"],
            "answer_candidates": fixture["candidate_path"],
            "answer_dir": fixture["answer_dir"],
            "review_dir": review_dir,
            "evidence_report_path": evidence_path,
            "beginner_report_path": review_dir / "answer-beginner-review.json",
            "out_status": status_path,
        }
        with self.assertRaises(importlib.import_module("answer_review").Phase6AnswerReviewError) as raised:
            workflow.finalize_answer_review(fixture["inputs"], **finalize_args)
        self.assertEqual("REPORT_COVERAGE_INVALID", raised.exception.code)
        self.assertFalse(status_path.exists())

        evidence["results"].append(missing)
        evidence["answer_book_sha256"] = "f" * 64
        evidence_path.write_bytes(dumps_artifact(evidence).encode("utf-8"))
        with self.assertRaises(importlib.import_module("answer_review").Phase6AnswerReviewError) as raised:
            workflow.finalize_answer_review(fixture["inputs"], **finalize_args)
        self.assertEqual("REPORT_BINDING_INVALID", raised.exception.code)
        self.assertFalse(status_path.exists())

    def test_reconstruction_rejects_each_altered_d1_input(self):
        workflow = importlib.import_module("answer_review_workflow")
        fixture = _answer_fixture(self)
        targets = (
            (fixture["candidate_path"], fixture["candidate_path"].read_bytes()),
            (fixture["answer_dir"] / "exercise-bank.json", (fixture["answer_dir"] / "exercise-bank.json").read_bytes()),
            (fixture["answer_dir"] / "ANSWER-BOOK.md", (fixture["answer_dir"] / "ANSWER-BOOK.md").read_bytes()),
        )
        with patch.object(workflow, "authenticate_chapter_review_package", return_value=fixture["package"]):
            for path, original in targets:
                with self.subTest(path=path.name):
                    path.write_bytes(original + b"\n")
                    try:
                        with self.assertRaises(workflow.AnswerReviewWorkflowError):
                            workflow._authenticate_answer_book(
                                fixture["inputs"],
                                chapter_dir=fixture["chapter_dir"],
                                chapter_review_dir=fixture["chapter_review_dir"],
                                answer_candidates=fixture["candidate_path"],
                                answer_dir=fixture["answer_dir"],
                            )
                    finally:
                        path.write_bytes(original)

    def test_stale_worktree_snapshot_publishes_no_status(self):
        workflow, fixture, _ = _prepare_answer_review(self)
        context = fixture["package"].context
        if context.snapshot_kind != "worktree":
            self.skipTest("fixture does not exercise a worktree snapshot")
        relative = next(item["locator"]["path"] for item in context.facts["evidence"] if item["locator"].get("path"))
        source_path = context._inputs.root.joinpath(*relative.split("/"))
        original = source_path.read_bytes()
        source_path.write_bytes(original + b"\n# snapshot drift\n")
        review_dir = fixture["review_dir"]
        _completed_reports(review_dir)
        status_path = review_dir / "answer-review-status.json"
        with self.assertRaises(workflow.AnswerReviewWorkflowError):
            workflow.finalize_answer_review(
                fixture["inputs"], chapter_dir=fixture["chapter_dir"],
                chapter_review_dir=fixture["chapter_review_dir"], answer_candidates=fixture["candidate_path"],
                answer_dir=fixture["answer_dir"], review_dir=review_dir,
                evidence_report_path=review_dir / "answer-evidence-review.json",
                beginner_report_path=review_dir / "answer-beginner-review.json",
                out_status=status_path,
            )
        self.assertFalse(status_path.exists())

    def test_prepare_rolls_back_a_partial_review_package_publication(self):
        workflow = importlib.import_module("answer_review_workflow")
        fixture = _answer_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        real_link = chapter.os.link
        calls = 0

        def fail_second_link(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publication failure")
            return real_link(source, destination)

        with patch.object(chapter.os, "link", side_effect=fail_second_link):
            with self.assertRaises(workflow.AnswerReviewWorkflowError) as raised:
                workflow.prepare_answer_review(
                    fixture["inputs"], chapter_dir=fixture["chapter_dir"],
                    chapter_review_dir=fixture["chapter_review_dir"], answer_candidates=fixture["candidate_path"],
                    answer_dir=fixture["answer_dir"], review_dir=fixture["review_dir"],
                )
        self.assertEqual("OUTPUT_FAILED", raised.exception.code)
        self.assertEqual(2, calls)
        self.assertFalse(fixture["review_dir"].exists())

    def test_reported_project_fact_in_general_yields_repair_required(self):
        hidden_fact = "The order service always retries failed database writes three times."
        workflow, fixture, _ = _prepare_answer_review(self, general_text=hidden_fact)
        review_dir = fixture["review_dir"]
        evidence, _ = _completed_reports(review_dir)
        matching = next(row for row in evidence["results"] if row["kind"] == "GENERAL" and row["text_sha256"] == hashlib.sha256(hidden_fact.encode()).hexdigest())
        matching["outcome"] = "PROJECT_FACT_UNMARKED"
        matching["findings"] = [{
            "issue_code": "PROJECT_FACT_UNMARKED",
            "severity": "MAJOR",
            "recommended_action": "MARK_AS_CLAIM_OR_REWRITE_GENERAL",
            "evidence_id": None,
        }]
        (review_dir / "answer-evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        status = workflow.finalize_answer_review(
            fixture["inputs"], chapter_dir=fixture["chapter_dir"],
            chapter_review_dir=fixture["chapter_review_dir"], answer_candidates=fixture["candidate_path"],
            answer_dir=fixture["answer_dir"], review_dir=review_dir,
            evidence_report_path=review_dir / "answer-evidence-review.json",
            beginner_report_path=review_dir / "answer-beginner-review.json",
            out_status=review_dir / "answer-review-status.json",
        )
        self.assertEqual("REPAIR_REQUIRED", status["review_state"])
        self.assertEqual(1, status["finding_counts"]["MAJOR"])

    def test_shared_reviewer_alias_and_session_yield_review_incomplete(self):
        workflow, fixture, _ = _prepare_answer_review(self)
        _completed_reports(fixture["review_dir"], beginner_alias="evidence-local", same_sessions=True)
        status_path = fixture["review_dir"] / "answer-review-status.json"
        status = workflow.finalize_answer_review(
            fixture["inputs"], chapter_dir=fixture["chapter_dir"],
            chapter_review_dir=fixture["chapter_review_dir"], answer_candidates=fixture["candidate_path"],
            answer_dir=fixture["answer_dir"], review_dir=fixture["review_dir"],
            evidence_report_path=fixture["review_dir"] / "answer-evidence-review.json",
            beginner_report_path=fixture["review_dir"] / "answer-beginner-review.json",
            out_status=status_path,
        )
        self.assertEqual("REVIEW_INCOMPLETE", status["review_state"])
        self.assertFalse(status["reviewer_contexts_distinct"])


if __name__ == "__main__":
    unittest.main()
