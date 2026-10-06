#!/usr/bin/env python3
"""Contracts for the Phase 6D1 pure answer projection and renderer."""

from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact  # noqa: E402
from test_phase6_review import _prepare_review, _report_pair  # noqa: E402


def _reviewed_package(testcase):
    workflow = importlib.import_module("chapter_review_workflow")
    _context, inputs, chapter_dir, review_dir, _unit_id, _session = _prepare_review(testcase)
    evidence, beginner = _report_pair(testcase, inputs, chapter_dir, review_dir)
    (review_dir / "evidence-review.json").write_bytes(dumps_artifact(evidence).encode("utf-8"))
    (review_dir / "beginner-review.json").write_bytes(dumps_artifact(beginner).encode("utf-8"))
    workflow.finalize_chapter_review(
        inputs,
        chapter_dir=chapter_dir,
        review_dir=review_dir,
        evidence_report_path=review_dir / "evidence-review.json",
        beginner_report_path=review_dir / "beginner-review.json",
        out_status=review_dir / "chapter-review-status.json",
    )
    return inputs, chapter_dir, review_dir, workflow.authenticate_chapter_review_package(
        inputs, chapter_dir=chapter_dir, review_dir=review_dir,
    )


def _completed_candidate(module, package, *, writer_alias="writer-local", use_claim=True):
    _packet, candidate = module.build_writer_material(package, writer_alias)
    selected_claim_id = package.context.bank["records"][0]["claim_ids"][0]
    for index, record in enumerate(candidate["records"]):
        worked = [{"kind": "GENERAL", "text": "Name the question's inputs before following each step."}]
        if use_claim and index == 0:
            worked.append({
                "kind": "CLAIM",
                "text": "The selected source reference supports this part of the explanation.",
                "claim_id": selected_claim_id,
            })
        record.update({
            "worked_steps": worked,
            "progressive_hints": [
                {"level": 1, "fragments": [{"kind": "GENERAL", "text": "Start with the named inputs."}]},
                {"level": 2, "fragments": [{"kind": "GENERAL", "text": "Trace what changes at each step."}]},
            ],
            "common_mistakes": [{"kind": "GENERAL", "text": "Do not skip a step without explaining why."}],
            "rubric": [{"kind": "GENERAL", "text": "A clear answer connects the inputs, steps, and result."}],
            "acceptable_tradeoffs": [],
        })
    return candidate


class Phase6AnswerBookTests(unittest.TestCase):
    def test_projection_preserves_questions_and_keeps_answer_review_pending(self):
        module = importlib.import_module("phase6_answer_book")
        _inputs, _chapter_dir, _review_dir, package = _reviewed_package(self)
        packet, template = module.build_writer_material(package, "writer-local")
        self.assertEqual(template["writer_packet_sha256"], module.sha256(packet))
        self.assertIn(f"Source status: {package.context.facts['source_status']}", packet.decode("utf-8"))
        self.assertIn(f"unknown files: {package.context.facts['unknown_files']}", packet.decode("utf-8"))
        self.assertIn("[FILL_IN]", dumps_artifact(template))

        candidate = _completed_candidate(module, package)
        candidate_raw = dumps_artifact(candidate).encode("utf-8")
        bank = module.build_answer_bank(package, candidate, candidate_raw)

        self.assertEqual("1.1.0", bank["schema_version"])
        self.assertEqual("DRAFT", bank["answer_book_status"])
        self.assertEqual("PARTIAL", bank["overall_status"])
        self.assertEqual(package.context.facts["source_status"], bank["source_status"])
        self.assertEqual(package.context.facts["unknown_files"], bank["unknown_files"])
        self.assertEqual("NOT_RUN", bank["answer_evidence_review"])
        self.assertEqual("NOT_RUN", bank["answer_beginner_review"])
        self.assertEqual(
            {"chapter", "chapter_facts", "exercise_bank", "review_session", "review_status", "writer_packet", "answer_candidates"},
            {item["role"] for item in bank["input_digests"]},
        )
        self.assertEqual([item["id"] for item in package.context.bank["records"]], [item["id"] for item in bank["records"]])
        for original, enriched in zip(package.context.bank["records"], bank["records"]):
            expected = copy.deepcopy(original)
            expected["answer_status"] = "AUTHORED"
            for key in ("worked_steps", "progressive_hints", "common_mistakes", "rubric", "acceptable_tradeoffs"):
                expected[key] = next(item[key] for item in candidate["records"] if item["question_id"] == original["id"])
            self.assertEqual(expected, enriched)
        self.assertEqual(["claim_id", "evidence"], sorted(bank["claim_evidence_catalog"][0]))
        self.assertNotIn("statement", bank["claim_evidence_catalog"][0])

    def test_projection_rejects_bad_question_coverage_shapes_and_claims(self):
        module = importlib.import_module("phase6_answer_book")
        _inputs, _chapter_dir, _review_dir, package = _reviewed_package(self)
        base = _completed_candidate(module, package)

        invalid_candidates = []
        duplicate = copy.deepcopy(base)
        duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        invalid_candidates.append(duplicate)
        missing = copy.deepcopy(base)
        missing["records"].pop()
        invalid_candidates.append(missing)
        extra = copy.deepcopy(base)
        extra["records"][0]["question_id"] = "EX-CHAPTER-" + "f" * 64 + "-extra"
        invalid_candidates.append(extra)
        stale_prompt = copy.deepcopy(base)
        stale_prompt["records"][0]["prompt_sha256"] = "f" * 64
        invalid_candidates.append(stale_prompt)
        bad_general = copy.deepcopy(base)
        bad_general["records"][0]["worked_steps"][0]["claim_id"] = "CLAIM-" + "e" * 64
        invalid_candidates.append(bad_general)
        missing_claim_id = copy.deepcopy(base)
        missing_claim_id["records"][0]["worked_steps"][1].pop("claim_id")
        invalid_candidates.append(missing_claim_id)
        unordered_hints = copy.deepcopy(base)
        unordered_hints["records"][0]["progressive_hints"][1]["level"] = 1
        invalid_candidates.append(unordered_hints)
        blank_prose = copy.deepcopy(base)
        blank_prose["records"][0]["rubric"][0]["text"] = "   "
        invalid_candidates.append(blank_prose)
        unknown_claim = copy.deepcopy(base)
        unknown_claim["records"][0]["worked_steps"][1]["claim_id"] = "CLAIM-" + "e" * 64
        invalid_candidates.append(unknown_claim)

        for candidate in invalid_candidates:
            with self.subTest(candidate=candidate):
                raw = json.dumps(candidate, ensure_ascii=False).encode("utf-8")
                with self.assertRaises(module.Phase6AnswerBookError):
                    module.build_answer_bank(package, candidate, raw)

    def test_e6_evidence_is_not_an_eligible_answer_claim_reference(self):
        module = importlib.import_module("phase6_answer_book")
        _inputs, _chapter_dir, _review_dir, package = _reviewed_package(self)
        facts = package.context.facts
        claim = facts["claims"][0]
        claim["resolved_evidence_levels"] = ["E6"]
        for evidence in facts["evidence"]:
            if evidence["id"] in claim["evidence_ids"]:
                evidence["level"] = "E6"
        context = SimpleNamespace(
            facts=facts,
            bank=package.context.bank,
            chapter_raw=package.context.chapter_raw,
            _facts_raw=package.context._facts_raw,
            _bank_raw=package.context._bank_raw,
            _packet_raw=package.context._packet_raw,
        )
        altered_package = SimpleNamespace(
            context=context,
            session=package.session,
            status=package.status,
            package_raw=package.package_raw,
        )
        candidate = _completed_candidate(module, altered_package)
        raw = dumps_artifact(candidate).encode("utf-8")
        with self.assertRaises(module.Phase6AnswerBookError):
            module.build_answer_bank(altered_package, candidate, raw)

    def test_renderer_is_deterministic_structured_and_escapes_prose(self):
        module = importlib.import_module("phase6_answer_book")
        _inputs, _chapter_dir, _review_dir, package = _reviewed_package(self)
        candidate = _completed_candidate(module, package)
        candidate["records"][0]["worked_steps"][0]["text"] = "<script>bad()</script>\n## Forged section"
        raw = dumps_artifact(candidate).encode("utf-8")
        bank = module.build_answer_bank(package, candidate, raw)

        rendered = module.render_answer_book(bank)
        self.assertEqual(rendered, module.render_answer_book(bank))
        self.assertIn("Which source-backed statement appears in this chapter?", rendered)
        self.assertIn("Worked steps", rendered)
        self.assertIn("Progressive hints", rendered)
        self.assertIn("Common mistakes", rendered)
        self.assertIn("Rubric", rendered)
        self.assertIn("Acceptable tradeoffs", rendered)
        self.assertIn("Answer status: **DRAFT / PARTIAL**.", rendered)
        self.assertIn(
            f"Source status: {package.context.facts['source_status']}; "
            f"unknown files: {package.context.facts['unknown_files']}.",
            rendered,
        )
        self.assertIn("Answer evidence review and beginner review remain `NOT_RUN`.", rendered)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("> \\#\\# Forged section", rendered)
        citation = bank["claim_evidence_catalog"][0]["evidence"][0]
        self.assertIn(citation["id"], rendered)
        self.assertIn(citation["level"], rendered)
        self.assertNotIn("source statement", rendered)


if __name__ == "__main__":
    unittest.main()
