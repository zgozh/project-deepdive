#!/usr/bin/env python3
"""Temp-Git integration tests for the Phase 6D1 prepare/build boundary."""

from __future__ import annotations

import copy
import contextlib
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from test_phase6_answer_book import _completed_candidate, _reviewed_package  # noqa: E402


def _input_args(inputs):
    return [
        "--root", str(inputs.root),
        "--run-dir", str(inputs.run_dir),
        "--phase4c-package", str(inputs.phase4c_package),
        "--claim-candidates", str(inputs.claim_candidates_path),
        "--claim-evidence", str(inputs.claim_evidence_path),
        "--claim-evidence-graph", str(inputs.claim_evidence_graph_path),
        "--prerequisite-candidates", str(inputs.prerequisite_candidates_path),
        "--prerequisite-graph", str(inputs.prerequisite_graph_path),
        "--curriculum-candidates", str(inputs.curriculum_candidates_path),
        "--curriculum", str(inputs.curriculum_path),
    ]


class AnswerBookWorkflowTests(unittest.TestCase):
    def test_prepare_and_build_publish_stable_bound_outputs_once_per_replay(self):
        module = importlib.import_module("phase6_answer_book")
        workflow = importlib.import_module("answer_book_workflow")
        claim_module = importlib.import_module("phase4_claim_evidence")
        inputs, chapter_dir, review_dir, package = _reviewed_package(self)
        output_root = review_dir.parent
        prepare_dir = output_root / "answer-writer-handoff"
        build_one = output_root / "answer-book-one"
        build_two = output_root / "answer-book-two"
        review_before = {path.name: path.read_bytes() for path in review_dir.iterdir()}
        bank_before = (chapter_dir / "exercise-bank.json").read_bytes()

        prepare_args = [
            "prepare", "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
            "--writer-alias", "writer-local", "--out-dir", str(prepare_dir), *_input_args(inputs),
        ]
        real_replay = claim_module.reproject_phase4c_artifacts
        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, workflow.main(prepare_args))
        self.assertEqual(1, replay.call_count)
        self.assertEqual({"answer-writer-packet.md", "answer-candidates-template.json"}, {path.name for path in prepare_dir.iterdir()})
        template = json.loads((prepare_dir / "answer-candidates-template.json").read_text(encoding="utf-8"))
        self.assertEqual("answer-candidates", template["artifact_kind"])
        self.assertIn("[FILL_IN]", (prepare_dir / "answer-candidates-template.json").read_text(encoding="utf-8"))
        self.assertEqual(review_before, {path.name: path.read_bytes() for path in review_dir.iterdir()})

        candidate = _completed_candidate(module, package)
        candidate_path = output_root / "answer-candidates.json"
        candidate_path.write_bytes(dumps_artifact(candidate).encode("utf-8"))
        build_args = [
            "build", "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
            "--answer-candidates", str(candidate_path), "--out-dir", str(build_one), *_input_args(inputs),
        ]
        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, workflow.main(build_args))
        self.assertEqual(1, replay.call_count)
        self.assertEqual({"exercise-bank.json", "ANSWER-BOOK.md"}, {path.name for path in build_one.iterdir()})
        bank = load_artifact(build_one / "exercise-bank.json")
        self.assertEqual("1.1.0", bank["schema_version"])
        self.assertEqual("PARTIAL", bank["overall_status"])
        self.assertEqual("NOT_RUN", bank["answer_evidence_review"])
        self.assertEqual(bank_before, (chapter_dir / "exercise-bank.json").read_bytes())
        legacy = load_artifact(chapter_dir / "exercise-bank.json")
        self.assertEqual("1.0.0", legacy["schema_version"])
        self.assertTrue(all(record["answer_status"] == "NOT_AUTHORED" for record in legacy["records"]))

        build_args[build_args.index(str(build_one))] = str(build_two)
        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, workflow.main(build_args))
        self.assertEqual(1, replay.call_count)
        self.assertEqual(
            {name: (build_one / name).read_bytes() for name in ("exercise-bank.json", "ANSWER-BOOK.md")},
            {name: (build_two / name).read_bytes() for name in ("exercise-bank.json", "ANSWER-BOOK.md")},
        )

    def test_stale_candidate_snapshot_or_review_fails_before_publication(self):
        module = importlib.import_module("phase6_answer_book")
        workflow = importlib.import_module("answer_book_workflow")
        inputs, chapter_dir, review_dir, package = _reviewed_package(self)
        output_root = review_dir.parent
        candidate = _completed_candidate(module, package)
        candidate_path = output_root / "answer-candidates.json"
        candidate["snapshot_kind"] = "git-tree" if candidate["snapshot_kind"] == "worktree" else "worktree"
        candidate_path.write_bytes(dumps_artifact(candidate).encode("utf-8"))
        mismatched_output = output_root / "answer-book-mixed-snapshot"
        args = [
            "build", "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
            "--answer-candidates", str(candidate_path), "--out-dir", str(mismatched_output), *_input_args(inputs),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, workflow.main(args))
        self.assertFalse(mismatched_output.exists())

        candidate["snapshot_kind"] = package.context.snapshot_kind
        candidate_path.write_bytes(dumps_artifact(candidate).encode("utf-8"))
        (review_dir / "beginner-critic-packet.md").write_bytes(b"stale review package")
        stale_output = output_root / "answer-book-stale-review"
        args[args.index(str(mismatched_output))] = str(stale_output)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, workflow.main(args))
        self.assertFalse(stale_output.exists())

    def test_template_or_invalid_candidate_does_not_publish(self):
        module = importlib.import_module("phase6_answer_book")
        workflow = importlib.import_module("answer_book_workflow")
        inputs, chapter_dir, review_dir, package = _reviewed_package(self)
        output_root = review_dir.parent
        _packet, template = module.build_writer_material(package, "writer-local")
        candidate_path = output_root / "answer-candidates.json"
        candidate_path.write_bytes(dumps_artifact(template).encode("utf-8"))
        output = output_root / "answer-book-unfilled"
        args = [
            "build", "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
            "--answer-candidates", str(candidate_path), "--out-dir", str(output), *_input_args(inputs),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, workflow.main(args))
        self.assertFalse(output.exists())

    def test_partial_publication_rolls_back_only_files_owned_by_this_run(self):
        module = importlib.import_module("phase6_answer_book")
        workflow = importlib.import_module("answer_book_workflow")
        chapter = importlib.import_module("phase6_chapter")
        inputs, chapter_dir, review_dir, _package = _reviewed_package(self)
        output = review_dir.parent / "answer-writer-raced-output"
        args = [
            "prepare", "--chapter-dir", str(chapter_dir), "--review-dir", str(review_dir),
            "--writer-alias", "writer-local", "--out-dir", str(output), *_input_args(inputs),
        ]
        real_publish = workflow._publish_new_file
        call_count = 0

        def publish_with_raced_second_file(path, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return real_publish(path, payload)
            path.write_bytes(b"owned by another process")
            raise chapter.Phase6ChapterError("OUTPUT_EXISTS")

        with patch.object(workflow, "_publish_new_file", side_effect=publish_with_raced_second_file):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, workflow.main(args))
        self.assertFalse((output / "answer-writer-packet.md").exists())
        self.assertEqual(b"owned by another process", (output / "answer-candidates-template.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
