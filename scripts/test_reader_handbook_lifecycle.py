#!/usr/bin/env python3
"""Contract tests for exact reader-chapter review and publication."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import curriculum_run_workflow as curriculum_workflow  # noqa: E402
import phase6_chapter as chapter  # noqa: E402
import reader_handbook_lifecycle as lifecycle  # noqa: E402
import reader_handbook_review as reader_review  # noqa: E402
import reader_handbook_workflow as reader_workflow  # noqa: E402
import reader_source_blocks  # noqa: E402
import test_curriculum_run_workflow as curriculum_tests  # noqa: E402
import test_phase6_chapter as phase6_tests  # noqa: E402


SOURCE_FILES = {
    "src/main/java/demo/Storage.java": (
        b"package demo;\n"
        b"public class Storage {\n"
        b"    public String save(String body) {\n"
        b"        return body.trim();\n"
        b"    }\n"
        b"}\n"
    ),
    "src/service.py": (
        b"def validate(payload):\n"
        b"    if not payload:\n"
        b"        raise ValueError('empty')\n"
        b"    return payload\n"
    ),
}


class ReaderHandbookLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_case = unittest.TestCase("runTest")
        original_fixture = phase6_tests._phase6_fixture

        def mixed_sources(testcase, **kwargs):
            return original_fixture(testcase, source_files=SOURCE_FILES, **kwargs)

        with patch.object(curriculum_tests, "_phase6_fixture", side_effect=mixed_sources):
            cls.fixture, cls.inputs, cls.curriculum = curriculum_tests._mixed_project_primer_fixture(
                cls.fixture_case,
            )
        work = cls.fixture["fixture"]["work"]
        cls.work = work
        cls.plan_path = work / "reader-lifecycle-plan.json"
        curriculum_workflow.plan_curriculum_run(cls.inputs, out=cls.plan_path)
        cls.auth_context = curriculum_workflow.authenticate_curriculum_run_plan(
            cls.inputs, plan_path=cls.plan_path,
        )
        cls.plan = json.loads(cls.auth_context.raw.decode("utf-8"))
        cls.unit = next(
            row for row in cls.plan["units"]
            if row["route"] == "PHASE6A_PROJECT_CLAIM"
        )
        cls.general_unit = next(
            row for row in cls.plan["units"]
            if row["route"] == "D2A_GENERAL_LEARNING"
        )
        cls.blueprint_path = work / "reader-lifecycle-blueprint.md"
        cls.blueprint_path.write_bytes(b"# Outcomes\n\nExplain the selected project flow.\n")
        cls.report_dir = work / "reader-lifecycle-reports"
        cls.report_dir.mkdir()
        cls.stage_parent = work / "reader-lifecycle-staging"
        cls.stage_parent.mkdir()
        cls.reader_dir = work / "reader-output"
        cls.reader_dir.mkdir()
        cls.receipt_dir = work / "reader-receipts"
        cls.receipt_dir.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_case.doCleanups()

    @staticmethod
    def _digest(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _attempt(
        self,
        name: str,
        *,
        embed_answers: bool = False,
        unknowns=None,
        unit=None,
        with_source_proofs: bool = True,
    ) -> Path:
        attempt = self.work / f"reader-lifecycle-{name}"
        reader_workflow.prepare_reader_theme(
            self.auth_context,
            [(unit or self.unit)["unit_identity"]["id"]],
            self.blueprint_path,
            attempt,
            f"writer-{name}",
        )
        answers = "## 完整答案\n\n输入先经过校验，再交给保存逻辑。\n".encode("utf-8")
        rendered_blocks = []
        source_blocks = []
        source_rows = (
            (("src/main/java/demo/Storage.java", "java"), ("src/service.py", "python"))
            if with_source_proofs else ()
        )
        for index, (path, language) in enumerate(
            source_rows,
            start=1,
        ):
            raw = chapter._read_snapshot_source(
                path,
                facts=self.plan,
                run_dir=self.inputs.run_dir,
                root=self.inputs.root,
            )
            line_count = len(raw.decode("utf-8").splitlines())
            rendered, proof = reader_source_blocks.render_source_block(
                raw,
                path=path,
                start_line=1,
                end_line=line_count,
                language=language,
                expected_source_sha256=self._digest(raw),
                expected_excerpt_sha256=self._digest(raw),
                revision=self.plan["repository_revision"],
            )
            rendered_blocks.append(rendered)
            source_blocks.append({
                "slot_id": f"lifecycle-source-{index}",
                "path": path,
                "start_line": 1,
                "end_line": line_count,
                "language": language,
                "annotations": {},
                "expected_source_sha256": proof["source_sha256"],
                "expected_excerpt_sha256": proof["excerpt_sha256"],
                "manuscript": "lesson.md",
                "supports": ["保存路径的输入处理"],
            })
        lesson = (
            "# 一条保存路径\n\n先看调用中的输入如何进入保存边界。\n\n"
            + "\n".join(rendered_blocks)
            + ("\n" + answers.decode("utf-8") if embed_answers else "")
        ).encode("utf-8")
        (attempt / "lesson.md").write_bytes(lesson)
        (attempt / "answers.md").write_bytes(answers)
        sources = {
            "artifact_kind": "reader-theme-sources",
            "version": "1.0",
            "source_revision": self.plan["repository_revision"],
            "source_blocks": source_blocks,
            "unknowns": [] if unknowns is None else unknowns,
            "general_references": [],
        }
        (attempt / "sources.json").write_text(
            json.dumps(sources, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return attempt

    def _context(self, name: str, *, mode: str = "append", embed_answers: bool = False, unknowns=None):
        attempt = self._attempt(name, embed_answers=embed_answers, unknowns=unknowns)
        base = reader_review.prepare_reader_review(self.auth_context, attempt)
        self.assertEqual(2, len(base.review_input["source_proofs"]))
        self.assertEqual(
            {"java", "python"},
            {proof["language"] for proof in base.review_input["source_proofs"]},
        )
        return attempt, lifecycle.prepare_reader_chapter_review(base, answer_mode=mode)

    def _stage(self, context, label: str):
        return lifecycle.write_reader_chapter_review_input(
            context,
            inputs=self.inputs,
            out_dir=self.stage_parent / label,
        )

    def _write_reports(
        self,
        context,
        label: str,
        *,
        verdict="ACCEPTED",
        source_session=None,
        teaching_session=None,
        assessment="逐段核对整章内容并记录具体判断依据。",
    ):
        source_session = source_session or f"source-reviewer-{label}"
        teaching_session = teaching_session or f"teaching-reviewer-{label}"
        common = {
            "version": "1.0",
            "verdict": verdict,
            "final_chapter_sha256": context.final_chapter_sha256,
            "review_input_sha256": context.review_input_sha256,
            "complete_manuscript_read": True,
            "assessment": assessment,
            "blocking_findings": [],
        }
        source = {
            "kind": "reader-source-evidence-review",
            **common,
            "reviewer_session_id": source_session,
            "evidence_assessment": "两段真实源码均与声明路径和摘要相符。",
            "limitation_assessment": "只覆盖本章引用的两个源码片段。",
        }
        teaching = {
            "kind": "reader-principal-teaching-review",
            **common,
            "reviewer_session_id": teaching_session,
            "normal_trace": "正常输入经校验后到达保存边界并返回结果。",
            "failure_trace": "空输入在保存前失败，答案说明了该分支。",
            "beginner_assessment": "前置概念先于首次依赖出现。",
            "source_explanation_assessment": "源码场景、关键语句和调用后果均有说明。",
            "answer_consistency": "完整答案与正文中的因果解释一致。",
        }
        source_path = self.report_dir / f"{label}-source.json"
        teaching_path = self.report_dir / f"{label}-teaching.json"
        source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        teaching_path.write_text(json.dumps(teaching, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return source_path, teaching_path

    def _publish(self, accepted, name: str):
        return lifecycle.publish_accepted_reader_chapter(
            accepted,
            inputs=self.inputs,
            chapter_out=self.reader_dir / f"{name}.md",
            receipt_out=self.receipt_dir / f"{name}.json",
        )

    def test_append_and_embedded_answers_publish_exact_reviewed_bytes(self):
        for label, mode, embedded in (
            ("append", "append", False),
            ("embedded", "embedded", True),
        ):
            with self.subTest(mode=mode):
                _attempt, context = self._context(label, mode=mode, embed_answers=embedded)
                context = self._stage(context, label)
                if mode == "append":
                    lesson = next(
                        raw for name, _path, raw in context.source_context._bound_files
                        if name == "lesson.md"
                    )
                    answers = next(
                        raw for name, _path, raw in context.source_context._bound_files
                        if name == "answers.md"
                    )
                    self.assertEqual(lesson + b"\n\n" + answers, context.final_chapter_bytes)
                    self.assertEqual(
                        1, context.final_chapter_bytes.count("## 完整答案".encode("utf-8")),
                    )
                    self.assertNotIn("## 练习与完整答案".encode("utf-8"), context.final_chapter_bytes)
                else:
                    answer = next(
                        raw for name, _path, raw in context.source_context._bound_files
                        if name == "answers.md"
                    )
                    self.assertEqual(1, context.final_chapter_bytes.count(answer))
                source_report, teaching_report = self._write_reports(
                    context,
                    label,
                    assessment=("源码与正文解释一致。" if mode == "append" else "逐段核对整章内容并记录具体判断依据。"),
                )
                if mode == "append":
                    original_report = source_report.read_bytes()
                    invalid_report = json.loads(original_report.decode("utf-8"))
                    invalid_report["assessment"] = " \t "
                    source_report.write_text(
                        json.dumps(invalid_report, ensure_ascii=False), encoding="utf-8",
                    )
                    with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
                        lifecycle.accept_reader_chapter_review(
                            context,
                            source_review_report=source_report,
                            teaching_review_report=teaching_report,
                        )
                    self.assertEqual("REVIEW_REPORT_INVALID", caught.exception.code)
                    source_report.write_bytes(original_report)
                accepted = lifecycle.accept_reader_chapter_review(
                    context,
                    source_review_report=source_report,
                    teaching_review_report=teaching_report,
                )
                receipt = self._publish(accepted, label)
                published = (self.reader_dir / f"{label}.md").read_bytes()
                self.assertEqual(context.final_chapter_bytes, published)
                self.assertEqual(self._digest(published), receipt["published_chapter_sha256"])
                self.assertEqual(
                    context.review_input["fingerprint_sha256"],
                    receipt["input_fingerprint_sha256"],
                )
                self.assertEqual("READER_REVIEWED_DRAFT", receipt["status"])
                self.assertEqual("PARTIAL", receipt["coverage"])

    def test_duplicate_answers_critical_unknown_and_reviewer_identity_block_acceptance(self):
        attempt = self._attempt("duplicate", embed_answers=True)
        base = reader_review.prepare_reader_review(self.auth_context, attempt)
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.prepare_reader_chapter_review(base, answer_mode="append")
        self.assertEqual("ANSWERS_DUPLICATE", caught.exception.code)

        _attempt, critical = self._context(
            "critical-unknown",
            unknowns=[{
                "learning_outcome": "未覆盖的关键路径",
                "reason": "缺少可以支持本章结论的项目事实。",
                "critical": True,
            }],
        )
        critical = self._stage(critical, "critical-unknown")
        source_report, teaching_report = self._write_reports(critical, "critical-unknown")
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.accept_reader_chapter_review(
                critical,
                source_review_report=source_report,
                teaching_review_report=teaching_report,
            )
        self.assertEqual("CRITICAL_UNKNOWN", caught.exception.code)

        _attempt, context = self._context("same-reviewer")
        context = self._stage(context, "same-reviewer")
        source_report, teaching_report = self._write_reports(
            context,
            "same-reviewer",
            source_session="same-session",
            teaching_session="same-session",
        )
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.accept_reader_chapter_review(
                context,
                source_review_report=source_report,
                teaching_review_report=teaching_report,
            )
        self.assertEqual("REVIEWER_NOT_INDEPENDENT", caught.exception.code)

    def test_stale_chapter_source_snapshot_and_review_report_are_rejected(self):
        attempt, context = self._context("stale-chapter")
        context = self._stage(context, "stale-chapter")
        source_report, teaching_report = self._write_reports(context, "stale-chapter")
        (attempt / "lesson.md").write_bytes(b"# Changed after capture\n")
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.accept_reader_chapter_review(
                context,
                source_review_report=source_report,
                teaching_review_report=teaching_report,
            )
        self.assertEqual("INPUT_CHANGED", caught.exception.code)

        attempt, context = self._context("changed-source")
        source_path = self.inputs.root / "src/service.py"
        original = source_path.read_bytes()
        try:
            source_path.write_bytes(original + b"\n# changed snapshot\n")
            with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                reader_review.prepare_reader_review(self.auth_context, attempt)
            self.assertIn(caught.exception.code, {"SOURCE_BLOCK_INVALID", "INPUT_CHANGED"})
        finally:
            source_path.write_bytes(original)

        _attempt, context = self._context("changed-report")
        context = self._stage(context, "changed-report")
        source_report, teaching_report = self._write_reports(context, "changed-report")
        accepted = lifecycle.accept_reader_chapter_review(
            context,
            source_review_report=source_report,
            teaching_review_report=teaching_report,
        )
        source_report.write_bytes(source_report.read_bytes() + b" ")
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            self._publish(accepted, "changed-report")
        self.assertEqual("REVIEW_BINDING_CHANGED", caught.exception.code)
        self.assertFalse((self.reader_dir / "changed-report.md").exists())

    def test_project_proof_bound_inputs_and_reader_output_boundaries(self):
        general_attempt = self._attempt(
            "general-needs-project-proof",
            unit=self.general_unit,
            with_source_proofs=False,
        )
        general_review = reader_review.prepare_reader_review(self.auth_context, general_attempt)
        self.assertEqual("D2A_GENERAL_LEARNING", general_review.review_input["selected_units"][0]["route"])
        self.assertEqual([], general_review.review_input["source_proofs"])
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.prepare_reader_chapter_review(general_review)
        self.assertEqual("SOURCE_PROOF_REQUIRED", caught.exception.code)

        attempt, context = self._context("bound-output-inputs")
        wrong_inputs = replace(self.inputs, root=self.inputs.root / "different-project")
        wrong_stage = self.stage_parent / "wrong-inputs"
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.write_reader_chapter_review_input(
                context, inputs=wrong_inputs, out_dir=wrong_stage,
            )
        self.assertEqual("INPUT_CONTEXT_MISMATCH", caught.exception.code)
        self.assertFalse(wrong_stage.exists())

        for label, out_dir in (
            ("stage-in-attempt", attempt / "nested-stage"),
            ("stage-in-skill", reader_workflow._SKILL_ROOT / "lifecycle-stage-blocked"),
        ):
            with self.subTest(label=label):
                with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
                    lifecycle.write_reader_chapter_review_input(
                        context, inputs=self.inputs, out_dir=out_dir,
                    )
                self.assertEqual("OUTPUT_INVALID", caught.exception.code)
                self.assertFalse(out_dir.exists())

        context = self._stage(context, "bound-output-inputs")
        source_report, teaching_report = self._write_reports(context, "bound-output-inputs")
        accepted = lifecycle.accept_reader_chapter_review(
            context,
            source_review_report=source_report,
            teaching_review_report=teaching_report,
        )
        mismatch_chapter = self.reader_dir / "mismatch.md"
        mismatch_receipt = self.receipt_dir / "mismatch.json"
        with self.assertRaises(lifecycle.ReaderHandbookLifecycleError) as caught:
            lifecycle.publish_accepted_reader_chapter(
                accepted,
                inputs=wrong_inputs,
                chapter_out=mismatch_chapter,
                receipt_out=mismatch_receipt,
            )
        self.assertEqual("INPUT_CONTEXT_MISMATCH", caught.exception.code)
        self.assertFalse(mismatch_chapter.exists())
        self.assertFalse(mismatch_receipt.exists())

        skill_root = reader_workflow._SKILL_ROOT.resolve(strict=True)
        existing = self.reader_dir / "existing-reader-file.md"
        existing_bytes = b"preserve this older reader file\n"
        existing.write_bytes(existing_bytes)
        publication_cases = (
            ("chapter-in-skill", skill_root / "lifecycle-chapter-blocked.md", self.receipt_dir / "skill-chapter.json"),
            ("receipt-in-skill", self.reader_dir / "skill-receipt-chapter.md", skill_root / "lifecycle-receipt-blocked.json"),
            ("chapter-in-attempt", attempt / "inside-attempt.md", self.receipt_dir / "attempt-chapter.json"),
            ("chapter-in-staging", context.staging_dir / "inside-staging.md", self.receipt_dir / "staging-chapter.json"),
            ("receipt-in-reader-folder", self.reader_dir / "receipt-folder-chapter.md", self.reader_dir / "in-reader-folder.json"),
            ("existing-output", existing, self.receipt_dir / "existing-output.json"),
        )
        for label, chapter_out, receipt_out in publication_cases:
            with self.subTest(label=label):
                with self.assertRaises(lifecycle.ReaderHandbookLifecycleError):
                    lifecycle.publish_accepted_reader_chapter(
                        accepted,
                        inputs=self.inputs,
                        chapter_out=chapter_out,
                        receipt_out=receipt_out,
                    )
                self.assertFalse(receipt_out.exists())
                if chapter_out == existing:
                    self.assertEqual(existing_bytes, existing.read_bytes())
                else:
                    self.assertFalse(chapter_out.exists())

    def test_cli_prepares_review_packet_and_publishes_with_exact_report_binding(self):
        attempt = self.work / "reader-lifecycle-cli-attempt"
        stage = self.stage_parent / "cli"
        chapter_out = self.reader_dir / "cli-chapter.md"
        receipt_out = self.receipt_dir / "cli-receipt.json"
        common_inputs = [
            "--phase4c-package", str(self.inputs.phase4c_package),
            "--claim-candidates", str(self.inputs.claim_candidates_path),
            "--claim-evidence", str(self.inputs.claim_evidence_path),
            "--claim-evidence-graph", str(self.inputs.claim_evidence_graph_path),
            "--prerequisite-candidates", str(self.inputs.prerequisite_candidates_path),
            "--prerequisite-graph", str(self.inputs.prerequisite_graph_path),
            "--curriculum-candidates", str(self.inputs.curriculum_candidates_path),
            "--curriculum", str(self.inputs.curriculum_path),
            "--run-dir", str(self.inputs.run_dir),
            "--root", str(self.inputs.root),
            "--plan", str(self.plan_path),
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, lifecycle.main([
                "prepare", *common_inputs,
                "--unit", self.unit["unit_identity"]["id"],
                "--blueprint", str(self.blueprint_path),
                "--writer-session", "writer-cli",
                "--out", str(attempt),
            ]))
        self._write_attempt_content(attempt, "cli")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, lifecycle.main([
                "review-input", *common_inputs,
                "--attempt-dir", str(attempt),
                "--answer-mode", "append",
                "--out", str(stage),
            ]))
        packet_raw = (stage / "review-input.json").read_bytes()
        packet = json.loads(packet_raw.decode("utf-8"))
        final_sha = packet["final_chapter_sha256"]
        review_sha = self._digest(packet_raw)
        common_report = {
            "version": "1.0",
            "verdict": "ACCEPTED",
            "final_chapter_sha256": final_sha,
            "review_input_sha256": review_sha,
            "complete_manuscript_read": True,
            "assessment": "已阅读完整正文和答案并记录核对依据。",
            "blocking_findings": [],
        }
        source_report = self.report_dir / "cli-source.json"
        teaching_report = self.report_dir / "cli-teaching.json"
        source_report.write_text(json.dumps({
            "kind": "reader-source-evidence-review",
            **common_report,
            "reviewer_session_id": "cli-source-reviewer",
            "evidence_assessment": "源码证据逐项支持其对应陈述。",
            "limitation_assessment": "范围限于本章冻结的证据。",
        }, ensure_ascii=False), encoding="utf-8")
        teaching_report.write_text(json.dumps({
            "kind": "reader-principal-teaching-review",
            **common_report,
            "reviewer_session_id": "cli-teaching-reviewer",
            "normal_trace": "代表输入经过校验后进入保存逻辑。",
            "failure_trace": "空输入在保存前被拒绝，后续保存逻辑不会执行。",
            "beginner_assessment": "必要概念先于首次使用，读者再依赖这些概念。",
            "source_explanation_assessment": "源码场景与语句后果均有解释。",
            "answer_consistency": "答案中的推理与正文讲解的因果关系保持一致。",
        }, ensure_ascii=False), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            result = lifecycle.main([
                "publish", *common_inputs,
                "--attempt-dir", str(attempt),
                "--review-input-dir", str(stage),
                "--source-review", str(source_report),
                "--teaching-review", str(teaching_report),
                "--chapter-out", str(chapter_out),
                "--receipt-out", str(receipt_out),
            ])
        self.assertEqual(0, result, output.getvalue())
        published = chapter_out.read_bytes()
        self.assertEqual(final_sha, self._digest(published))
        self.assertEqual("READER_REVIEWED_DRAFT", json.loads(receipt_out.read_text("utf-8"))["status"])

    def _write_attempt_content(self, attempt: Path, label: str):
        answers = "## 完整答案\n\n输入先经过校验，再交给保存逻辑。\n".encode("utf-8")
        rendered_blocks = []
        source_blocks = []
        for index, (path, language) in enumerate(
            (("src/main/java/demo/Storage.java", "java"), ("src/service.py", "python")),
            start=1,
        ):
            raw = chapter._read_snapshot_source(
                path, facts=self.plan, run_dir=self.inputs.run_dir, root=self.inputs.root,
            )
            lines = len(raw.decode("utf-8").splitlines())
            rendered, proof = reader_source_blocks.render_source_block(
                raw,
                path=path,
                start_line=1,
                end_line=lines,
                language=language,
                expected_source_sha256=self._digest(raw),
                expected_excerpt_sha256=self._digest(raw),
                revision=self.plan["repository_revision"],
            )
            rendered_blocks.append(rendered)
            source_blocks.append({
                "slot_id": f"{label}-source-{index}",
                "path": path,
                "start_line": 1,
                "end_line": lines,
                "language": language,
                "annotations": {},
                "expected_source_sha256": proof["source_sha256"],
                "expected_excerpt_sha256": proof["excerpt_sha256"],
                "manuscript": "lesson.md",
                "supports": ["保存路径的输入处理"],
            })
        (attempt / "lesson.md").write_bytes(
            (
                "# 一条保存路径\n\n先看调用中的输入如何进入保存边界。\n\n"
                + "\n".join(rendered_blocks)
            ).encode("utf-8"),
        )
        (attempt / "answers.md").write_bytes(answers)
        sources = {
            "artifact_kind": "reader-theme-sources",
            "version": "1.0",
            "source_revision": self.plan["repository_revision"],
            "source_blocks": source_blocks,
            "unknowns": [],
            "general_references": [],
        }
        (attempt / "sources.json").write_text(
            json.dumps(sources, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
