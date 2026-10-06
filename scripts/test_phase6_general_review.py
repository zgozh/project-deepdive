#!/usr/bin/env python3
"""Focused deterministic contracts for the Phase 6D2A2 review sidecar."""

from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from phase6_general_review import (  # noqa: E402
    Phase6GeneralReviewError,
    aggregate_general_review_status,
    expected_general_beginner_scopes,
    expected_general_text_occurrences,
    validate_general_beginner_report,
    validate_general_factuality_report,
)
from artifact_contract import dumps_artifact  # noqa: E402
from test_artifact_contract import minimal_artifacts  # noqa: E402


def _facts():
    return minimal_artifacts()["general-unit-facts"]


def _artifact():
    artifact = copy.deepcopy(minimal_artifacts()["general-learning-unit"])
    artifact["candidate"]["lesson"]["intuition"] = "The same phrase appears twice in this beginner lesson."
    artifact["candidate"]["lesson"]["worked_example"] = "The same phrase appears twice in this beginner lesson."
    return artifact


def _session():
    sha = "a" * 64
    artifact = _artifact()
    return {
        "artifact_kind": "general-review-session",
        "schema_version": "1.0.0",
        "review_session_id": "GENERAL-REVIEW-" + sha,
        "review_round": 0,
        "generated_at": artifact["generated_at"],
        "repository_revision": artifact["repository_revision"],
        "snapshot_kind": artifact["snapshot_kind"],
        "source_metadata": artifact["source_metadata"],
        "source_status": artifact["source_status"],
        "source_run_manifest_sha256": artifact["source_run_manifest_sha256"],
        "unknown_files": artifact["unknown_files"],
        "input_digests": artifact["input_digests"],
        "prompt_versions": {"general_factuality": "1.0.0", "beginner_answer": "1.0.0"},
        "selected_unit_id": artifact["selected_unit"]["id"],
        "selected_unit_origin": artifact["selected_unit"]["origin"],
        "selected_unit_scope": artifact["selected_unit"]["scope"],
        "facts_sha256": sha,
        "candidate_sha256": artifact["candidate_sha256"],
        "unit_artifact_sha256": sha,
        "lesson_markdown_sha256": artifact["lesson_markdown_sha256"],
        "answer_book_markdown_sha256": artifact["answer_book_markdown_sha256"],
        "writer_packet_sha256": sha,
        "writer_alias": artifact["candidate"]["writer_alias"],
        "occurrence_inventory_sha256": sha,
        "beginner_scope_inventory_sha256": sha,
        "factuality_packet_sha256": hashlib.sha256(b"packet").hexdigest(),
        "beginner_packet_sha256": hashlib.sha256(b"beginner").hexdigest(),
    }


class GeneralReviewInventoryTests(unittest.TestCase):
    def test_identical_text_at_distinct_fields_has_distinct_occurrence_ids(self):
        occurrences = expected_general_text_occurrences(_artifact())
        matching = [row for row in occurrences if row["text"] == "The same phrase appears twice in this beginner lesson."]
        self.assertEqual(2, len(matching))
        self.assertNotEqual(matching[0]["occurrence_id"], matching[1]["occurrence_id"])
        self.assertEqual(
            {"lesson.intuition", "lesson.worked_example"},
            {row["location"] for row in matching},
        )

    def test_scopes_cover_ordered_lesson_and_integrated_answer_book(self):
        artifact = _artifact()
        occurrences = expected_general_text_occurrences(artifact)
        scopes = expected_general_beginner_scopes(artifact, occurrences)
        by_kind = {row["scope_kind"]: row for row in scopes}
        lesson_occurrences = {row["occurrence_id"] for row in occurrences if any(location.startswith("lesson.") for location in row["render_locations"])}
        exercise_occurrences = {row["occurrence_id"] for row in occurrences if row["field"].startswith("learning_check.")}
        self.assertEqual(lesson_occurrences, set(by_kind["ORDERED_LESSON"]["occurrence_ids"]))
        self.assertEqual(exercise_occurrences, set(by_kind["ANSWER_BOOK_EXERCISE"]["occurrence_ids"]))
        self.assertEqual(len(scopes), len({row["scope_id"] for row in scopes}))

    def test_factuality_report_requires_authority_and_rejects_coverage_changes(self):
        occurrences = expected_general_text_occurrences(_artifact())
        session = _session()
        positive = []
        for row in occurrences:
            item = {key: value for key, value in row.items() if key != "text"}
            item.update({"outcome": "NONFACTUAL_TEACHING", "authority_references": [], "findings": []})
            positive.append(item)
        positive[0]["outcome"] = "SUPPORTED_BY_AUTHORITY"
        positive[0]["authority_references"] = [{
            "reference_id": "ref-1", "title": "Official guide", "publisher": "Standards body",
            "locator": "https://authority.example/spec", "edition": "1.0", "authority_class": "STANDARD",
        }]
        from phase6_general_review import _report_binding
        session_raw = dumps_artifact(session).encode("utf-8")
        report = {
            "artifact_kind": "general-factuality-review", "schema_version": "1.0.0",
            "generated_at": session["generated_at"], **_report_binding(session, hashlib.sha256(session_raw).hexdigest(), ""),
            "reviewer_alias": "facts-reviewer", "reviewer_session_id": "factual-1",
            "fresh_context_isolated": True, "source_reference_inspection": True,
            "packet_sha256": hashlib.sha256(b"packet").hexdigest(), "results": positive,
        }
        validate_general_factuality_report(report, session, session_raw, b"packet", occurrences)
        bad = copy.deepcopy(report)
        bad["results"][0]["authority_references"] = []
        with self.assertRaises(Phase6GeneralReviewError):
            validate_general_factuality_report(bad, session, session_raw, b"packet", occurrences)
        bad = copy.deepcopy(report)
        bad["results"].pop()
        with self.assertRaises(Phase6GeneralReviewError):
            validate_general_factuality_report(bad, session, session_raw, b"packet", occurrences)

    def test_all_nonfactual_reviews_are_incomplete_not_positive(self):
        session = _session()
        session_raw = dumps_artifact(session).encode("utf-8")
        occurrences = expected_general_text_occurrences(_artifact())
        scopes = expected_general_beginner_scopes(_artifact(), occurrences)
        factual = {
            "reviewer_alias": "facts-reviewer", "reviewer_session_id": "factual-1",
            "fresh_context_isolated": True, "source_reference_inspection": True,
            "results": [
                {"outcome": "NONFACTUAL_TEACHING", "authority_references": [], "findings": []}
                for _ in occurrences
            ],
        }
        beginner = {
            "reviewer_alias": "beginner-reviewer", "reviewer_session_id": "beginner-1",
            "fresh_context_isolated": True,
            "results": [{"outcome": "FOLLOWABLE_FOR_BEGINNER", "findings": []} for _ in scopes],
        }
        context = {"facts": {"source_status": "PARTIAL", "unknown_files": 3}}
        result = aggregate_general_review_status(context, session, (factual, b"facts"), (beginner, b"beginner"), occurrences)
        self.assertEqual("REVIEW_INCOMPLETE", result["review_state"])

    def test_findings_cannot_echo_long_source_text(self):
        occurrences = expected_general_text_occurrences(_artifact())
        session = _session()
        session_raw = dumps_artifact(session).encode("utf-8")
        from phase6_general_review import _report_binding
        rows = []
        for occurrence in occurrences:
            row = {key: value for key, value in occurrence.items() if key != "text"}
            row.update({"outcome": "NONFACTUAL_TEACHING", "authority_references": [], "findings": []})
            rows.append(row)
        text = occurrences[0]["text"]
        rows[0]["outcome"] = "NEEDS_REVISION"
        rows[0]["findings"] = [{
            "issue_code": "UNSUPPORTED_GENERAL_FACT", "location_id": rows[0]["occurrence_id"],
            "severity": "MINOR", "recommended_action": "CITE_AUTHORITY",
            "focus": text[:60], "repair_guidance": "Narrow to a supportable general statement.",
        }]
        report = {
            "artifact_kind": "general-factuality-review", "schema_version": "1.0.0",
            "generated_at": session["generated_at"],
            **_report_binding(session, hashlib.sha256(session_raw).hexdigest(), hashlib.sha256(b"packet").hexdigest()),
            "reviewer_alias": "facts-reviewer", "reviewer_session_id": "factual-1",
            "fresh_context_isolated": True, "source_reference_inspection": True,
            "results": rows,
        }
        with self.assertRaises(Phase6GeneralReviewError) as caught:
            validate_general_factuality_report(report, session, session_raw, b"packet", occurrences)
        self.assertEqual("REPORT_PRIVACY_INVALID", caught.exception.code)

    def test_authority_backed_fact_allows_reviewed_draft_but_never_promotes_d2a(self):
        session = _session()
        occurrences = expected_general_text_occurrences(_artifact())
        scopes = expected_general_beginner_scopes(_artifact(), occurrences)
        reference = {
            "reference_id": "ref-1", "title": "Official guide", "publisher": "Standards body",
            "locator": "https://authority.example/spec", "edition": None, "authority_class": "STANDARD",
        }
        factual = {
            "reviewer_alias": "facts-reviewer", "reviewer_session_id": "factual-1",
            "fresh_context_isolated": True, "source_reference_inspection": True,
            "results": [
                {"outcome": "SUPPORTED_BY_AUTHORITY", "authority_references": [reference], "findings": []},
                *[{"outcome": "NONFACTUAL_TEACHING", "authority_references": [], "findings": []} for _ in occurrences[1:]],
            ],
        }
        beginner = {
            "reviewer_alias": "beginner-reviewer", "reviewer_session_id": "beginner-1",
            "fresh_context_isolated": True,
            "results": [{"outcome": "FOLLOWABLE_FOR_BEGINNER", "findings": []} for _ in scopes],
        }
        result = aggregate_general_review_status(
            {}, session, (factual, b"facts"), (beginner, b"beginner"), occurrences,
        )
        self.assertEqual("REVIEWED_DRAFT", result["review_state"])
        self.assertEqual("DRAFT", result["lesson_status"])
        self.assertEqual("PARTIAL", result["overall_status"])
        self.assertEqual("UNVERIFIED_TEACHING", result["epistemic_status"])
        self.assertEqual("NOT_RUN", result["general_fact_review"])


if __name__ == "__main__":
    unittest.main()
