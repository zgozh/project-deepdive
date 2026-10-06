#!/usr/bin/env python3
"""Focused tests for the one-round Phase 6B1 independent review boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from test_phase6_chapter import (  # noqa: E402
    _add_v2_excerpt_evidence,
    _phase6_fixture,
    _valid_chapter,
    _valid_v2_chapter,
)


def _valid_draft(testcase, *, repeated_claim=False):
    chapter = importlib.import_module("phase6_chapter")
    context, inputs, unit_id, chapter_dir = _phase6_fixture(testcase)
    facts, _packet = chapter.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=chapter_dir)
    markdown = _valid_chapter(facts, context["claim"]["text"])
    if repeated_claim:
        marker = f"[^CLAIM-{context['claim']['id'].removeprefix('CLAIM-')}]"
        repeated = f"Claim: {context['claim']['text']} {marker}"
        gap = next(line for line in markdown.splitlines() if line.startswith("> Evidence gap "))
        markdown = markdown.replace(gap, repeated, 1)
    markdown_path = chapter_dir / "chapter.md"
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
    chapter.verify_chapter_draft(
        inputs,
        unit_id=unit_id,
        facts_path=chapter_dir / "chapter-facts.json",
        markdown_path=markdown_path,
        out_dir=chapter_dir,
    )
    return context, inputs, chapter_dir, unit_id


def _prepare_review(testcase):
    workflow = importlib.import_module("chapter_review_workflow")
    context, inputs, chapter_dir, unit_id = _valid_draft(testcase)
    review_dir = chapter_dir.parent / "phase6b-review"
    result = workflow.prepare_chapter_review(
        inputs,
        chapter_dir=chapter_dir,
        review_dir=review_dir,
        writer_alias="writer-local",
    )
    return context, inputs, chapter_dir, review_dir, unit_id, result


def _report_pair(testcase, inputs, chapter_dir, review_dir):
    session = load_artifact(review_dir / "chapter-review-session.json")
    evidence_report = json.loads((review_dir / "evidence-review-template.json").read_text(encoding="utf-8"))
    beginner_report = json.loads((review_dir / "beginner-review-template.json").read_text(encoding="utf-8"))
    evidence_report.update({
        "generated_at": "2026-09-27T00:00:00Z",
        "reviewer_alias": "verifier-local",
        "reviewer_session_id": "verifier-session-01",
        "fresh_context_isolated": True,
        "direct_source_inspection": True,
    })
    for result in evidence_report["results"]:
        result["outcome"] = "CONSISTENT_WITH_CITED_EVIDENCE" if result["kind"] == "CLAIM" else "GENERAL_TEACHING"
        result["findings"] = []
    beginner_report.update({
        "generated_at": "2026-09-27T00:00:00Z",
        "reviewer_alias": "critic-local",
        "reviewer_session_id": "critic-session-01",
        "fresh_context_isolated": True,
    })
    for result in beginner_report["results"]:
        result["outcome"] = "FOLLOWABLE_FOR_DECLARED_BEGINNER"
        result["findings"] = []
    return evidence_report, beginner_report


class Phase6ReviewTests(unittest.TestCase):
    def test_v2_occurrences_cover_every_prose_line_and_keep_repeats_distinct(self):
        chapter = importlib.import_module("phase6_chapter")
        review = importlib.import_module("phase6_review")
        fixture, inputs, unit_id, chapter_dir = _phase6_fixture(self)
        facts, _packet = chapter.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=chapter_dir)
        _add_v2_excerpt_evidence(fixture, facts)
        markdown = _valid_v2_chapter(facts, fixture["claim"]["text"], inputs)
        raw = markdown.encode("utf-8")
        context = SimpleNamespace(
            facts=facts,
            chapter_raw=raw,
            chapter_sha256=hashlib.sha256(raw).hexdigest(),
        )

        occurrences = review.expected_occurrences(context)

        marker = f"[^CLAIM-{fixture['claim']['id'].removeprefix('CLAIM-')}]"
        expected_lines = [
            "A request can cross several layers. This chapter follows one selected source unit.",
            "A repository snapshot is a fixed view of files. A locator names one bounded range。",
            f"{fixture['claim']['text']} {marker}",
            f"- {fixture['claim']['text']} {marker}",
            "Each function can be read as a small input-to-output transformation！",
            "A focused test changes one input at a time. Its result can be compared with the expected behavior?",
            "A hypothetical change can be checked with a focused test. A failure narrows where the change broke.",
        ]
        physical_lines = markdown.splitlines()
        expected_numbers = [physical_lines.index(line) + 1 for line in expected_lines]
        self.assertEqual(expected_numbers, [row["line_number"] for row in occurrences])
        self.assertEqual(7, len(occurrences))
        self.assertEqual(["CLAIM", "CLAIM"], [row["kind"] for row in occurrences if row["kind"] == "CLAIM"])
        self.assertEqual(5, sum(row["kind"] == "GENERAL" for row in occurrences))
        self.assertEqual(7, len({row["occurrence_id"] for row in occurrences}))
        for row, line in zip(occurrences, expected_lines, strict=True):
            self.assertEqual(hashlib.sha256(line.encode("utf-8")).hexdigest(), row["line_sha256"])

    def test_v2_occurrence_extraction_rejects_unrecognized_nonempty_lines(self):
        chapter = importlib.import_module("phase6_chapter")
        review = importlib.import_module("phase6_review")
        fixture, inputs, unit_id, chapter_dir = _phase6_fixture(self)
        facts, _packet = chapter.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=chapter_dir)
        _add_v2_excerpt_evidence(fixture, facts)
        markdown = _valid_v2_chapter(facts, fixture["claim"]["text"], inputs)
        marker = f"[^CLAIM-{fixture['claim']['id'].removeprefix('CLAIM-')}]"
        malformed = markdown.replace(
            "## Project use\n",
            f"## Project use\nClaim: {fixture['claim']['text']} {marker}\n",
            1,
        ).replace(
            "A hypothetical change can be checked with a focused test.",
            "not a prose line\nA hypothetical change can be checked with a focused test.",
        )
        raw = malformed.encode("utf-8")
        context = SimpleNamespace(
            facts=facts,
            chapter_raw=raw,
            chapter_sha256=hashlib.sha256(raw).hexdigest(),
        )

        with self.assertRaises(review.Phase6ReviewError) as raised:
            review.expected_occurrences(context)

        self.assertEqual("CHAPTER_INVALID", raised.exception.code)

    def test_repeated_claim_lines_get_distinct_evidence_review_occurrences(self):
        workflow = importlib.import_module("chapter_review_workflow")
        context, inputs, chapter_dir, _unit_id = _valid_draft(self, repeated_claim=True)
        review_dir = chapter_dir.parent / "phase6b-repeated-claim-review"

        workflow.prepare_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            writer_alias="writer-local",
        )
        evidence = json.loads((review_dir / "evidence-review-template.json").read_text(encoding="utf-8"))
        claim_occurrences = [row for row in evidence["results"] if row["kind"] == "CLAIM"]

        self.assertEqual(2, len(claim_occurrences))
        self.assertEqual({context["claim"]["id"]}, {row["claim_id"] for row in claim_occurrences})
        self.assertEqual(2, len({row["occurrence_id"] for row in claim_occurrences}))
        self.assertEqual(2, len({row["line_number"] for row in claim_occurrences}))

    def test_report_reader_accepts_pretty_printed_schema_valid_json(self):
        workflow = importlib.import_module("chapter_review_workflow")
        from test_artifact_contract import minimal_artifacts

        report = minimal_artifacts()["chapter-evidence-review"]
        raw = json.dumps(report, ensure_ascii=False, indent=4).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(raw)

            actual, actual_raw = workflow._read_review_artifact(path, "chapter-evidence-review")

        self.assertEqual(report, actual)
        self.assertEqual(raw, actual_raw)

    def test_report_reader_rejects_duplicate_json_keys(self):
        workflow = importlib.import_module("chapter_review_workflow")
        raw = b'{"artifact_kind":"chapter-evidence-review","artifact_kind":"chapter-beginner-review"}'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(raw)
            with self.assertRaises(importlib.import_module("phase6_review").Phase6ReviewError) as raised:
                workflow._read_review_artifact(path, "chapter-evidence-review")
        self.assertEqual("INPUT_INVALID", raised.exception.code)

    def test_authenticated_context_replays_once_and_binds_all_phase6a_bytes(self):
        chapter = importlib.import_module("phase6_chapter")
        _context, inputs, chapter_dir, _unit_id = _valid_draft(self)
        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts

        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            auth = chapter.authenticate_chapter_for_review(
                inputs,
                facts_path=chapter_dir / "chapter-facts.json",
                markdown_path=chapter_dir / "chapter.md",
                exercise_bank_path=chapter_dir / "exercise-bank.json",
                draft_status_path=chapter_dir / "chapter-draft-status.json",
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual(_context["graph"]["source_metadata"]["g01_status"], auth.source_status)
        self.assertEqual(0, auth.unknown_files)
        self.assertEqual(10, len(auth.input_digests))
        self.assertEqual(hashlib.sha256((chapter_dir / "chapter.md").read_bytes()).hexdigest(), auth.chapter_sha256)
        auth.recheck()
        with (chapter_dir / "chapter.md").open("ab") as draft:
            draft.write(b"\n")
        with self.assertRaises(chapter.Phase6ChapterError) as changed:
            auth.recheck()
        self.assertEqual("INPUT_CHANGED", changed.exception.code)

    def test_prepare_publishes_session_role_packets_and_templates_after_one_replay(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, _unit_id = _valid_draft(self)
        review_dir = chapter_dir.parent / "phase6b-review"
        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts

        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            result = workflow.prepare_chapter_review(
                inputs,
                chapter_dir=chapter_dir,
                review_dir=review_dir,
                writer_alias="writer-local",
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual("chapter-review-session", result["artifact_kind"])
        self.assertEqual({
            "chapter-review-session.json", "evidence-verifier-packet.md", "beginner-critic-packet.md",
            "evidence-review-template.json", "beginner-review-template.json",
        }, {path.name for path in review_dir.iterdir()})
        session = load_artifact(review_dir / "chapter-review-session.json")
        self.assertEqual(0, session["review_round"])
        self.assertEqual(10, len(session["input_digests"]))
        self.assertTrue(session["phase3_member_digests"])
        expected_members = sorted((
            {key: member[key] for key in ("path", "artifact_kind", "schema_version", "sha256")}
            for member in _context["graph"]["source_run"]["members"]
        ), key=lambda member: member["path"])
        self.assertEqual(expected_members, session["phase3_member_digests"])
        self.assertNotIn(_context["claim"]["text"], (review_dir / "evidence-verifier-packet.md").read_text(encoding="utf-8"))

    def test_prepare_templates_prefill_all_deterministic_bindings_and_coverage(self):
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        session_raw = (review_dir / "chapter-review-session.json").read_bytes()
        session = load_artifact(review_dir / "chapter-review-session.json")
        evidence_template_path = review_dir / "evidence-review-template.json"
        beginner_template_path = review_dir / "beginner-review-template.json"
        self.assertTrue(evidence_template_path.is_file())
        self.assertTrue(beginner_template_path.is_file())
        evidence = json.loads(evidence_template_path.read_text(encoding="utf-8"))
        beginner = json.loads(beginner_template_path.read_text(encoding="utf-8"))
        session_sha = hashlib.sha256(session_raw).hexdigest()

        for template, kind, packet_name in (
            (evidence, "chapter-evidence-review", "evidence-verifier-packet.md"),
            (beginner, "chapter-beginner-review", "beginner-critic-packet.md"),
        ):
            self.assertEqual(kind, template["artifact_kind"])
            self.assertEqual(session["review_session_id"], template["review_session_id"])
            self.assertEqual(session_sha, template["review_session_sha256"])
            self.assertEqual(0, template["review_round"])
            self.assertEqual(session["repository_revision"], template["repository_revision"])
            self.assertEqual(session["snapshot_kind"], template["snapshot_kind"])
            self.assertEqual(session["source_run_manifest_sha256"], template["source_run_manifest_sha256"])
            self.assertEqual(session["chapter_id"], template["chapter_id"])
            self.assertEqual(session["chapter_sha256"], template["chapter_sha256"])
            self.assertEqual(session["chapter_facts_sha256"], template["chapter_facts_sha256"])
            self.assertEqual(session["exercise_bank_sha256"], template["exercise_bank_sha256"])
            packet = (review_dir / packet_name).read_text(encoding="utf-8")
            self.assertEqual(hashlib.sha256((review_dir / packet_name).read_bytes()).hexdigest(), template["packet_sha256"])
            self.assertIn(template["review_session_id"], packet)
            with self.assertRaises(ArtifactValidationError):
                validate_artifact(template)
            self.assertEqual("FILL_IN_UTC_TIMESTAMP", template["generated_at"])
            self.assertEqual("", template["reviewer_alias"])
            self.assertEqual("", template["reviewer_session_id"])
            self.assertEqual("FILL_IN_BOOLEAN", template["fresh_context_isolated"])

        chapter = importlib.import_module("phase6_chapter")
        review_api = importlib.import_module("phase6_review")
        auth = chapter.authenticate_chapter_for_review(
            inputs,
            facts_path=chapter_dir / "chapter-facts.json",
            markdown_path=chapter_dir / "chapter.md",
            exercise_bank_path=chapter_dir / "exercise-bank.json",
            draft_status_path=chapter_dir / "chapter-draft-status.json",
        )
        evidence_packet = (review_dir / "evidence-verifier-packet.md").read_text(encoding="utf-8")
        beginner_packet = (review_dir / "beginner-critic-packet.md").read_text(encoding="utf-8")
        expected_occurrences = review_api.expected_occurrences(auth)
        expected_scopes = review_api.expected_section_scopes(auth)
        self.assertEqual(expected_occurrences, [{key: row[key] for key in expected_occurrences[0]} for row in evidence["results"]])
        self.assertEqual(expected_scopes, [{key: row[key] for key in expected_scopes[0]} for row in beginner["results"]])
        for row in evidence["results"]:
            self.assertIn(row["occurrence_id"], evidence_packet)
            self.assertIn(row["line_sha256"], evidence_packet)
            self.assertEqual("FILL_IN_REVIEWER_OUTCOME", row["outcome"])
            self.assertIsNone(row["findings"])
        for row in beginner["results"]:
            self.assertIn(row["scope_id"], beginner_packet)
            self.assertIn(row["line_sha256"], beginner_packet)
            self.assertEqual("FILL_IN_REVIEWER_OUTCOME", row["outcome"])
            self.assertIsNone(row["findings"])
        for name in ("evidence-review-template.json", "beginner-review-template.json"):
            self.assertNotIn(_context["claim"]["text"], (review_dir / name).read_text(encoding="utf-8"))

    def test_finalize_accepts_pretty_printed_schema_valid_reports(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        evidence_raw = json.dumps(evidence, ensure_ascii=False, indent=4).encode("utf-8")
        beginner_raw = json.dumps(beginner, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        (review_dir / "evidence-review.json").write_bytes(evidence_raw)
        (review_dir / "beginner-review.json").write_bytes(beginner_raw)

        status = workflow.finalize_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=review_dir / "chapter-review-status.json",
        )

        self.assertEqual("REVIEWED_DRAFT", status["review_state"])
        self.assertEqual(hashlib.sha256(evidence_raw).hexdigest(), status["evidence_report_sha256"])
        self.assertEqual(hashlib.sha256(beginner_raw).hexdigest(), status["beginner_report_sha256"])

    def test_finalize_rejects_tampered_report_template_without_status(self):
        workflow = importlib.import_module("chapter_review_workflow")
        review_api = importlib.import_module("phase6_review")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        template_path = review_dir / "evidence-review-template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        template["results"][0]["line_sha256"] = "0" * 64
        template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        status_path = review_dir / "chapter-review-status.json"

        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            workflow.finalize_chapter_review(
                inputs,
                chapter_dir=chapter_dir,
                review_dir=review_dir,
                evidence_report_path=review_dir / "evidence-review.json",
                beginner_report_path=review_dir / "beginner-review.json",
                out_status=status_path,
            )

        self.assertEqual("SESSION_BINDING_INVALID", raised.exception.code)
        self.assertFalse(status_path.exists())

    def test_finalize_missing_or_unfilled_report_publishes_no_status(self):
        workflow = importlib.import_module("chapter_review_workflow")
        review_api = importlib.import_module("phase6_review")
        for unfilled in (False, True):
            with self.subTest(unfilled=unfilled):
                _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
                if unfilled:
                    (review_dir / "evidence-review.json").write_bytes(
                        (review_dir / "evidence-review-template.json").read_bytes()
                    )
                    evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
                    (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
                    expected_code = "INPUT_INVALID"
                else:
                    evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
                    (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
                    expected_code = "REVIEW_PACKAGE_INVALID"
                status_path = review_dir / "chapter-review-status.json"
                with self.assertRaises(review_api.Phase6ReviewError) as raised:
                    workflow.finalize_chapter_review(
                        inputs,
                        chapter_dir=chapter_dir,
                        review_dir=review_dir,
                        evidence_report_path=review_dir / "evidence-review.json",
                        beginner_report_path=review_dir / "beginner-review.json",
                        out_status=status_path,
                    )
                self.assertEqual(expected_code, raised.exception.code)
                self.assertFalse(status_path.exists())

    def test_finalize_accepts_complete_separate_reports_without_promoting_partial_status(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        evidence_path = review_dir / "evidence-review.json"
        beginner_path = review_dir / "beginner-review.json"
        evidence_path.write_bytes(dumps_artifact(evidence).encode("utf-8"))
        beginner_path.write_bytes(dumps_artifact(beginner).encode("utf-8"))
        out_status = review_dir / "chapter-review-status.json"
        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts

        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            status = workflow.finalize_chapter_review(
                inputs,
                chapter_dir=chapter_dir,
                review_dir=review_dir,
                evidence_report_path=evidence_path,
                beginner_report_path=beginner_path,
                out_status=out_status,
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual("REVIEWED_DRAFT", status["review_state"])
        self.assertEqual("PARTIAL", status["overall_status"])
        self.assertEqual(_context["graph"]["source_metadata"]["g01_status"], status["source_status"])
        self.assertNotIn("PASS", status["review_state"])
        self.assertEqual(dumps_artifact(status).encode("utf-8"), out_status.read_bytes())

    def test_authenticated_review_package_reuses_one_replay_and_is_read_only(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        status = workflow.finalize_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=review_dir / "chapter-review-status.json",
        )
        before = {path.name: path.read_bytes() for path in review_dir.iterdir()}
        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts

        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            authenticated = workflow.authenticate_chapter_review_package(
                inputs, chapter_dir=chapter_dir, review_dir=review_dir,
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual(status, authenticated.status)
        self.assertEqual("PARTIAL", authenticated.status["overall_status"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in review_dir.iterdir()})
        authenticated.recheck()

    def test_authenticated_review_package_rejects_tampering_without_writing(self):
        workflow = importlib.import_module("chapter_review_workflow")
        review_api = importlib.import_module("phase6_review")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        evidence_raw = dumps_artifact(evidence).encode("utf-8")
        beginner_raw = dumps_artifact(beginner).encode("utf-8")
        (review_dir / "evidence-review.json").write_bytes(evidence_raw)
        (review_dir / "beginner-review.json").write_bytes(beginner_raw)
        workflow.finalize_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=review_dir / "chapter-review-status.json",
        )
        (review_dir / "evidence-review.json").write_bytes(evidence_raw + b"\n")
        before = {path.name: path.read_bytes() for path in review_dir.iterdir()}

        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            workflow.authenticate_chapter_review_package(
                inputs, chapter_dir=chapter_dir, review_dir=review_dir,
            )

        self.assertEqual("REVIEW_STATUS_INVALID", raised.exception.code)
        self.assertEqual(before, {path.name: path.read_bytes() for path in review_dir.iterdir()})

        (review_dir / "evidence-review.json").write_bytes(evidence_raw)
        (review_dir / "chapter-review-status.json").unlink()
        incomplete_before = {path.name: path.read_bytes() for path in review_dir.iterdir()}
        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            workflow.authenticate_chapter_review_package(
                inputs, chapter_dir=chapter_dir, review_dir=review_dir,
            )
        self.assertEqual("REVIEW_PACKAGE_INVALID", raised.exception.code)
        self.assertEqual(incomplete_before, {path.name: path.read_bytes() for path in review_dir.iterdir()})

    def test_nonisolated_role_is_incomplete_and_does_not_promote_claim(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        evidence["fresh_context_isolated"] = False
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))

        status = workflow.finalize_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=review_dir / "chapter-review-status.json",
        )

        self.assertEqual("REVIEW_INCOMPLETE", status["review_state"])
        self.assertEqual("PARTIAL", status["overall_status"])
        self.assertEqual("UNABLE_TO_ASSESS", status["evidence_outcome"])

    def test_snapshot_drift_at_final_freshness_check_publishes_no_review_package(self):
        workflow = importlib.import_module("chapter_review_workflow")
        chapter = importlib.import_module("phase6_chapter")
        context, inputs, chapter_dir, _unit_id = _valid_draft(self)
        facts = load_artifact(chapter_dir / "chapter-facts.json")
        relative = facts["evidence"][0]["locator"]["path"]
        if facts["snapshot_kind"] != "worktree":
            self.skipTest("fixture does not exercise worktree G01 drift")
        source_path = context["fixture"]["root"].joinpath(*relative.split("/"))
        original = source_path.read_bytes()
        real_recheck = chapter.AuthenticatedChapterReviewContext.recheck

        def drift_then_recheck(auth):
            source_path.write_bytes(original + b"\n# drift\n")
            return real_recheck(auth)

        review_dir = chapter_dir.parent / "phase6b-drift-review"
        with patch.object(chapter.AuthenticatedChapterReviewContext, "recheck", drift_then_recheck):
            with self.assertRaises(importlib.import_module("phase6_review").Phase6ReviewError) as raised:
                workflow.prepare_chapter_review(
                    inputs,
                    chapter_dir=chapter_dir,
                    review_dir=review_dir,
                    writer_alias="writer-local",
                )
        self.assertEqual("SOURCE_RUN_INVALID", raised.exception.code)
        self.assertFalse(review_dir.exists())

    def test_incomplete_occurrence_matrix_fails_closed_without_status(self):
        workflow = importlib.import_module("chapter_review_workflow")
        _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(self)
        evidence, beginner = _report_pair(self, inputs, chapter_dir, review_dir)
        missing = evidence["results"].pop()
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        out_status = review_dir / "chapter-review-status.json"

        review_api = importlib.import_module("phase6_review")
        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            workflow.finalize_chapter_review(
                inputs,
                chapter_dir=chapter_dir,
                review_dir=review_dir,
                evidence_report_path=review_dir / "evidence-review.json",
                beginner_report_path=review_dir / "beginner-review.json",
                out_status=out_status,
            )

        self.assertFalse(out_status.exists())
        self.assertEqual("REPORT_COVERAGE_INVALID", raised.exception.code)

        evidence["results"].append(missing)
        beginner["results"][0]["outcome"] = "NEEDS_REVISION"
        beginner["results"][0]["findings"] = [{
            "location_kind": "section",
            "location_id": beginner["results"][0]["scope_id"],
            "issue_code": "UNDEFINED_JARGON",
            "severity": "MAJOR",
            "recommended_action": "DEFINE_TERM_BEFORE_USE",
        }]
        (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
        (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
        status = workflow.finalize_chapter_review(
            inputs,
            chapter_dir=chapter_dir,
            review_dir=review_dir,
            evidence_report_path=review_dir / "evidence-review.json",
            beginner_report_path=review_dir / "beginner-review.json",
            out_status=out_status,
        )
        self.assertEqual("REPAIR_REQUIRED", status["review_state"])
        self.assertEqual("CONSISTENT_WITH_CITED_EVIDENCE", status["evidence_outcome"])
        self.assertEqual("NEEDS_REVISION", status["beginner_outcome"])

    def test_e6_cannot_receive_positive_verifier_outcome(self):
        review_api = importlib.import_module("phase6_review")
        from test_artifact_contract import minimal_artifacts

        claim_id = "CLAIM-" + "1" * 64
        evidence_id = "EVID-provisional"
        line = f"Claim: This is only a provisional semantic assertion. [^{claim_id}]"
        raw = line.encode("utf-8")
        facts = {
            "selected_unit": {"chapter_id": "CHAPTER-" + "2" * 64},
            "claims": [{
                "id": claim_id,
                "disposition": "SUPPORTED",
                "resolved_evidence_levels": ["E6"],
                "evidence_ids": [evidence_id],
            }],
            "evidence": [{"id": evidence_id, "level": "E6"}],
        }
        context = SimpleNamespace(
            facts=facts,
            chapter_raw=raw,
            chapter_sha256=hashlib.sha256(raw).hexdigest(),
        )
        occurrence = review_api.expected_occurrences(context)[0]
        report = minimal_artifacts()["chapter-evidence-review"]
        report["results"] = [{
            **occurrence,
            "outcome": "CONSISTENT_WITH_CITED_EVIDENCE",
            "findings": [],
        }]
        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            review_api.validate_evidence_report(report, context, report["packet_sha256"])
        self.assertEqual("REPORT_INVALID", raised.exception.code)

    def test_general_project_fact_requires_fixed_marking_finding(self):
        review_api = importlib.import_module("phase6_review")
        from test_artifact_contract import minimal_artifacts

        claim_id = "CLAIM-" + "3" * 64
        evidence_id = "EVID-fixture"
        chapter_id = "CHAPTER-" + "4" * 64
        raw = (
            f"Claim: A source-backed statement. [^{claim_id}]\n"
            "General: This is a project-specific fact.\n"
        ).encode("utf-8")
        context = SimpleNamespace(
            facts={
                "selected_unit": {"chapter_id": chapter_id},
                "claims": [{
                    "id": claim_id,
                    "disposition": "SUPPORTED",
                    "resolved_evidence_levels": ["E1"],
                    "evidence_ids": [evidence_id],
                }],
                "evidence": [{"id": evidence_id, "level": "E1"}],
            },
            chapter_raw=raw,
            chapter_sha256=hashlib.sha256(raw).hexdigest(),
        )
        report = minimal_artifacts()["chapter-evidence-review"]
        occurrences = review_api.expected_occurrences(context)
        report["results"] = [
            {**occurrences[0], "outcome": "CONSISTENT_WITH_CITED_EVIDENCE", "findings": []},
            {**occurrences[1], "outcome": "PROJECT_FACT_UNMARKED", "findings": []},
        ]
        with self.assertRaises(review_api.Phase6ReviewError) as raised:
            review_api.validate_evidence_report(report, context, report["packet_sha256"])
        self.assertEqual("REPORT_INVALID", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
