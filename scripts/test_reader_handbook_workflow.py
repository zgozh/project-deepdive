#!/usr/bin/env python3
"""Focused tests for reader-handbook writer-input preparation."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import curriculum_run_workflow as curriculum_workflow  # noqa: E402
import reader_handbook_review as reader_review  # noqa: E402
import reader_handbook_workflow as reader_workflow  # noqa: E402
import test_curriculum_run_workflow as curriculum_tests  # noqa: E402


class ReaderHandbookPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_case = cls(methodName="runTest")
        cls.fixture, cls.inputs, _curriculum = curriculum_tests._mixed_project_primer_fixture(cls.fixture_case)
        work = cls.fixture["fixture"]["work"]
        cls.plan_path = work / "reader-curriculum-run-plan.json"
        curriculum_workflow.plan_curriculum_run(cls.inputs, out=cls.plan_path)
        cls.auth_context = curriculum_workflow.authenticate_curriculum_run_plan(
            cls.inputs, plan_path=cls.plan_path,
        )
        cls.plan = json.loads(cls.auth_context.raw.decode("utf-8"))
        cls.blueprint_dir = work / "reader-blueprint-input"
        cls.blueprint_dir.mkdir()
        cls.blueprint_path = cls.blueprint_dir / "teaching-blueprint.md"
        cls.blueprint_raw = b"# Outcomes\n\nExplain the selected project flow.\n"
        cls.blueprint_path.write_bytes(cls.blueprint_raw)
        cls.output_parent = work / "reader-packet-output"
        cls.output_parent.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_case.doCleanups()

    def _out(self, name: str = "prepared") -> Path:
        return self.output_parent / name

    def _prepare(self, selected_ids, *, out_name="prepared", blueprint_path=None, book_plan_path=None):
        book_plan_kwargs = {} if book_plan_path is None else {"book_plan_path": book_plan_path}
        return reader_workflow.prepare_reader_theme(
            self.auth_context,
            selected_ids,
            blueprint_path or self.blueprint_path,
            self._out(out_name),
            "reader-writer-session-1",
            **book_plan_kwargs,
        )

    def _review_attempt(self, name, *, book_plan_path=None):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        self._prepare([selected_id], out_name=name, book_plan_path=book_plan_path)
        attempt = self._out(name)
        (attempt / "lesson.md").write_bytes(b"# Reader lesson\n\nA supported lesson body.\n")
        (attempt / "answers.md").write_bytes(b"# Internal answer\n\nA reasoned answer.\n")
        sources = {
            "artifact_kind": "reader-theme-sources",
            "version": "1.0",
            "source_revision": self.plan["repository_revision"],
            "source_blocks": [],
            "unknowns": [],
            "general_references": [],
        }
        (attempt / "sources.json").write_bytes(
            (json.dumps(sources, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return attempt

    def _legacy_review_attempt(self, name, version):
        book_plan_path = None
        if version == "1.1":
            book_plan_path = self.blueprint_dir / f"{name}-book-plan.md"
            book_plan_path.write_text("# Frozen reader route\n\nProject before feature.\n", encoding="utf-8")
        attempt = self._review_attempt(name, book_plan_path=book_plan_path)
        manifest_path = attempt / "reader-input-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan = reader_workflow._parse_authenticated_raw(self.auth_context)
        selected_ids = [row["unit_identity"]["id"] for row in manifest["selected_units"]]
        selected = reader_workflow._selected_units(plan, selected_ids)
        input_records = reader_workflow._verified_input_records(plan, self.auth_context)
        blueprint_path = Path(manifest["blueprint_path"])
        blueprint_raw = (attempt / "teaching-blueprint.md").read_bytes()
        prompt_path = reader_workflow._WRITER_PROMPT
        prompt_raw = prompt_path.read_bytes()
        book_plan_text = None
        book_plan_sha256 = None
        if version == "1.1":
            book_plan_raw = (attempt / "reader-book-plan.md").read_bytes()
            book_plan_text = book_plan_raw.decode("utf-8")
            book_plan_sha256 = hashlib.sha256(book_plan_raw).hexdigest()
        packet = reader_workflow._writer_packet(
            plan,
            selected,
            input_records,
            blueprint_raw.decode("utf-8"),
            prompt_raw.decode("utf-8"),
            blueprint_path=blueprint_path,
            prompt_path=prompt_path,
            plan_path=Path(manifest["run_plan_path"]),
            plan_sha256=manifest["plan_sha256"],
            blueprint_sha256=manifest["blueprint_sha256"],
            prompt_sha256=manifest["writer_prompt_sha256"],
            writer_session_id=manifest["writer_session_id"],
            book_plan_text=book_plan_text,
            book_plan_path=book_plan_path,
            book_plan_sha256=book_plan_sha256,
        )
        (attempt / "writer-packet.md").write_bytes(packet)
        for _key, _relative_path, output_path in reader_workflow._AUTHORING_REFERENCES:
            (attempt / output_path).unlink()
        outputs = {
            key: manifest["outputs"][key]
            for key in ("writer_packet", "teaching_blueprint", "writer_prompt")
        }
        if version == "1.1":
            outputs["reader_book_plan"] = manifest["outputs"]["reader_book_plan"]
        else:
            manifest.pop("book_plan", None)
            (attempt / "reader-book-plan.md").unlink(missing_ok=True)
        manifest["version"] = version
        manifest["outputs"] = outputs
        manifest["outputs"]["writer_packet"]["sha256"] = hashlib.sha256(packet).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return attempt

    def test_multi_unit_packet_preserves_payload_and_exact_input_bytes(self):
        rows = self.plan["units"]
        selected_ids = [row["unit_identity"]["id"] for row in rows[:2]]
        original_recheck = type(self.auth_context).recheck

        with patch.object(
            type(self.auth_context), "recheck", autospec=True, side_effect=original_recheck,
        ) as recheck:
            manifest = self._prepare(selected_ids)

        self.assertEqual(2, recheck.call_count)
        output = self._out()
        self.assertEqual(
            {
                "writer-packet.md",
                "teaching-blueprint.md",
                "reader-handbook-writer.md",
                "reader-input-manifest.json",
                "source-topic-writing-card.md",
                "reader-handbook-contract.md",
                "learner-ai-collaboration-card.md",
            },
            {path.name for path in output.iterdir()},
        )
        self.assertEqual(self.blueprint_raw, (output / "teaching-blueprint.md").read_bytes())
        prompt_raw = reader_workflow._WRITER_PROMPT.read_bytes()
        self.assertEqual(prompt_raw, (output / "reader-handbook-writer.md").read_bytes())
        self.assertEqual("phase6-reader-handbook-writer.md", reader_workflow._WRITER_PROMPT.name)

        expected_units = []
        for row in rows[:2]:
            selected = copy.deepcopy(row)
            selected["source_status"] = self.plan["source_status"]
            selected["unknown_files"] = copy.deepcopy(self.plan["unknown_files"])
            expected_units.append(selected)
        self.assertEqual(expected_units, manifest["selected_units"])
        self.assertEqual(self.plan["source_metadata"], manifest["source_anchor"]["source_metadata"])
        self.assertEqual(self.plan["snapshot_kind"], manifest["source_anchor"]["snapshot_kind"])
        self.assertEqual(self.plan["source_status"], manifest["source_status"])
        for actual, expected in zip(manifest["selected_units"], rows[:2]):
            self.assertEqual(expected["unit_identity"], actual["unit_identity"])
            self.assertEqual(expected["route"], actual["route"])
            self.assertEqual(expected["unit_identity"]["origin"], actual["unit_identity"]["origin"])
        self.assertEqual("PREPARED", manifest["status"])
        self.assertEqual("NOT_REVIEWED", manifest["review_status"])
        self.assertEqual("1.2", manifest["version"])
        self.assertEqual(
            {
                "writer_packet", "teaching_blueprint", "writer_prompt",
                "source_topic_writing_card", "reader_contract", "learner_ai_collaboration_card",
            },
            set(manifest["outputs"]),
        )
        self.assertEqual(
            hashlib.sha256(self.blueprint_raw).hexdigest(), manifest["blueprint_sha256"],
        )
        self.assertEqual(hashlib.sha256(prompt_raw).hexdigest(), manifest["writer_prompt_sha256"])
        self.assertEqual(
            hashlib.sha256((output / "writer-packet.md").read_bytes()).hexdigest(),
            manifest["outputs"]["writer_packet"]["sha256"],
        )
        packet_text = (output / "writer-packet.md").read_text(encoding="utf-8")
        self.assertLess(
            packet_text.index("## 完整阅读随包核心规范"),
            packet_text.index("## 写作规划提示（非学习结果）"),
        )
        for reference_name in (
            "source-topic-writing-card.md",
            "reader-handbook-contract.md",
            "learner-ai-collaboration-card.md",
        ):
            self.assertIn(reference_name, packet_text)
        self.assertIn("部分显示不代表全文已读", packet_text)
        self.assertLess(
            packet_text.index("## 必需的 Skill 核心参考全文"),
            packet_text.index("## 当前作者提示全文"),
        )
        for manifest_key, relative_path, output_path in reader_workflow._AUTHORING_REFERENCES:
            raw = (reader_workflow._SKILL_ROOT / relative_path).read_bytes()
            self.assertEqual(raw, (output / output_path).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["outputs"][manifest_key]["sha256"])
            self.assertIn(raw.decode("utf-8"), packet_text)
            self.assertIn(hashlib.sha256(raw).hexdigest(), packet_text)
        self.assertEqual("PREPARED", manifest["status"])
        self.assertEqual("NOT_REVIEWED", manifest["review_status"])
        self.assertFalse((output / "lesson.md").exists())

    def test_book_plan_binds_exact_bytes_hash_and_writer_packet_before_blueprint(self):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        plan_raw = "# 读者路线\n\n先讲项目，再沿着一次请求追踪入库。".encode("utf-8")
        book_plan_path = self.blueprint_dir / "reader-book-plan.md"
        book_plan_path.write_bytes(plan_raw)

        manifest = self._prepare([selected_id], out_name="with-book-plan", book_plan_path=book_plan_path)

        output = self._out("with-book-plan")
        digest = hashlib.sha256(plan_raw).hexdigest()
        self.assertEqual("1.2", manifest["version"])
        for reference_key in ("source_topic_writing_card", "reader_contract", "learner_ai_collaboration_card"):
            self.assertIn(reference_key, manifest["outputs"])
        self.assertEqual({"path": str(book_plan_path.resolve()), "sha256": digest}, manifest["book_plan"])
        self.assertEqual(
            {"path": "reader-book-plan.md", "sha256": digest},
            manifest["outputs"]["reader_book_plan"],
        )
        self.assertEqual(plan_raw, (output / "reader-book-plan.md").read_bytes())
        packet_text = (output / "writer-packet.md").read_text(encoding="utf-8")
        self.assertLess(packet_text.index("# 读者路线"), packet_text.index("## 教学蓝图全文"))
        self.assertIn("控制读者手册的阅读顺序与覆盖范围", packet_text)
        self.assertIn("教学蓝图规定学习结果", packet_text)
        self.assertIn("已认证课程单元只是证据单元，不是读者目录", packet_text)
        self.assertIn(hashlib.sha256(self.blueprint_raw).hexdigest(), packet_text)
        self.assertEqual(
            hashlib.sha256((output / "writer-packet.md").read_bytes()).hexdigest(),
            manifest["outputs"]["writer_packet"]["sha256"],
        )

    def test_invalid_or_blank_book_plan_is_rejected_without_output(self):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        for label, raw in (("invalid-utf8", b"\xff"), ("blank", b" \r\n\t")):
            with self.subTest(label=label):
                plan_path = self.blueprint_dir / f"{label}-book-plan.md"
                plan_path.write_bytes(raw)
                out_name = f"invalid-book-plan-{label}"
                with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                    self._prepare([selected_id], out_name=out_name, book_plan_path=plan_path)
                self.assertEqual("BOOK_PLAN_INVALID", caught.exception.code)
                self.assertFalse(self._out(out_name).exists())

    def test_changed_book_plan_is_rejected_before_output_commit(self):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        book_plan_path = self.blueprint_dir / "changing-book-plan.md"
        book_plan_path.write_bytes(b"# Original route\n")
        original_packet = reader_workflow._writer_packet

        def mutate_after_capture(*args, **kwargs):
            book_plan_path.write_bytes(b"# Changed route\n")
            return original_packet(*args, **kwargs)

        with patch.object(reader_workflow, "_writer_packet", side_effect=mutate_after_capture):
            with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                self._prepare([selected_id], out_name="changed-book-plan", book_plan_path=book_plan_path)

        self.assertEqual("INPUT_CHANGED", caught.exception.code)
        self.assertFalse(self._out("changed-book-plan").exists())

    def test_review_keeps_legacy_v10_prepared_input_set(self):
        attempt = self._legacy_review_attempt("review-v10", "1.0")

        context = reader_review.prepare_reader_review(self.auth_context, attempt)

        self.assertEqual("REVIEW_INPUT_READY", context.status)
        self.assertEqual("NOT_REVIEWED", context.review_status)
        self.assertEqual("1.0", context.review_input["version"])
        self.assertNotIn("book_plan", context.review_input)
        self.assertEqual(
            {
                "reader-input-manifest.json",
                "writer-packet.md",
                "reader-handbook-writer.md",
                "teaching-blueprint.md",
            },
            set(context.review_input["prepared_inputs"]),
        )

    def test_review_keeps_legacy_v11_book_plan_input_set(self):
        attempt = self._legacy_review_attempt("review-v11-legacy", "1.1")

        context = reader_review.prepare_reader_review(self.auth_context, attempt)

        self.assertEqual("1.1", context.review_input["version"])
        self.assertIn("book_plan", context.review_input)
        self.assertIn("reader-book-plan.md", context.review_input["prepared_inputs"])
        self.assertNotIn("authoring_references", context.review_input)

    def test_review_rebuilds_route_bound_packet_and_freezes_plan_copy(self):
        plan_raw = "# 全书路线\n\n从请求入口走到入库。".encode("utf-8")
        plan_path = self.blueprint_dir / "review-book-plan.md"
        plan_path.write_bytes(plan_raw)
        attempt = self._review_attempt("review-v12", book_plan_path=plan_path)

        context = reader_review.prepare_reader_review(self.auth_context, attempt)

        digest = hashlib.sha256(plan_raw).hexdigest()
        self.assertEqual("REVIEW_INPUT_READY", context.status)
        self.assertEqual("NOT_REVIEWED", context.review_status)
        review_input = context.review_input
        self.assertEqual("1.2", review_input["version"])
        self.assertEqual("NOT_REVIEWED", review_input["review_status"])
        self.assertEqual(3, len(review_input["authoring_references"]))
        for item, (manifest_key, relative_path, output_path) in zip(
            review_input["authoring_references"], reader_workflow._AUTHORING_REFERENCES,
        ):
            raw = (reader_workflow._SKILL_ROOT / relative_path).read_bytes()
            self.assertEqual(relative_path, item["path"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(raw.decode("utf-8"), item["text"])
            self.assertIn(output_path, review_input["prepared_inputs"])
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                review_input["prepared_inputs"][output_path],
            )
        self.assertEqual(
            {"source_path": str(plan_path.resolve()), "sha256": digest, "text": plan_raw.decode("utf-8")},
            review_input["book_plan"],
        )
        self.assertEqual(
            self.blueprint_raw.decode("utf-8"),
            review_input["files"]["teaching-blueprint.md"],
        )
        self.assertIn(
            "Compare the lesson's and answers' actual order and scope with the frozen reader-book plan",
            review_input["review_tasks"]["principal_teaching"]["instructions"],
        )
        self.assertIn(
            "Apply the exact frozen source-topic writing card, reader-handbook contract, and learner AI collaboration card",
            review_input["review_tasks"]["principal_teaching"]["instructions"],
        )
        self.assertEqual(plan_raw, (attempt / "reader-book-plan.md").read_bytes())
        self.assertEqual(
            digest,
            review_input["prepared_inputs"]["reader-book-plan.md"],
        )
        (attempt / "reader-book-plan.md").write_bytes(b"# Changed route\n")
        with self.assertRaises(reader_review.ReaderHandbookReviewError) as caught:
            context.recheck()
        self.assertEqual("INPUT_CHANGED", caught.exception.code)

    def test_review_rejects_tampered_plan_digest_or_version_before_source_review(self):
        for tamper in ("copy", "digest", "version"):
            with self.subTest(tamper=tamper):
                plan_path = self.blueprint_dir / f"tampered-{tamper}.md"
                plan_path.write_bytes(b"# Bound route\n")
                attempt = self._review_attempt(f"tampered-review-{tamper}", book_plan_path=plan_path)
                manifest_path = attempt / "reader-input-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if tamper == "copy":
                    (attempt / "reader-book-plan.md").write_bytes(b"# Replaced route\n")
                elif tamper == "digest":
                    manifest["book_plan"]["sha256"] = "0" * 64
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                else:
                    manifest["version"] = "1.3"
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                with self.assertRaises(reader_review.ReaderHandbookReviewError):
                    reader_review.prepare_reader_review(self.auth_context, attempt)

    def test_review_rejects_omitted_or_tampered_authoring_references(self):
        for tamper in ("missing", "copy", "manifest", "packet"):
            with self.subTest(tamper=tamper):
                attempt = self._review_attempt(f"tampered-reference-{tamper}")
                manifest_path = attempt / "reader-input-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                key, _relative_path, output_path = reader_workflow._AUTHORING_REFERENCES[0]
                if tamper == "missing":
                    (attempt / output_path).unlink()
                elif tamper == "copy":
                    (attempt / output_path).write_bytes((attempt / output_path).read_bytes() + b"\nchanged")
                elif tamper == "manifest":
                    manifest["outputs"].pop(key)
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                else:
                    packet_path = attempt / "writer-packet.md"
                    packet_path.write_bytes(packet_path.read_bytes() + b"\nchanged")
                with self.assertRaises(reader_review.ReaderHandbookReviewError):
                    reader_review.prepare_reader_review(self.auth_context, attempt)

    def test_authenticated_role_mapping_is_independent_of_chapter_consumer_profiles(self):
        expected_roles = {record["role"] for record in self.plan["input_digests"]}
        self.assertEqual(set(reader_workflow._AUTHENTICATED_INPUT_ROLE_PATHS), expected_roles)

        with patch.dict(reader_workflow.chapter._CHAPTER_INPUT_PROFILES, {}, clear=True):
            verified = reader_workflow._verified_input_records(self.plan, self.auth_context)

        self.assertEqual(expected_roles, {record["role"] for record in verified})
        self.assertEqual(10, len(verified))

    def test_context_plan_mutation_does_not_change_authenticated_raw_selection(self):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        original_plan = copy.deepcopy(self.auth_context.plan)
        try:
            self.auth_context.plan["units"].clear()
            manifest = self._prepare([selected_id], out_name="ctx-plan-mutated")
            self.assertEqual([selected_id], [row["unit_identity"]["id"] for row in manifest["selected_units"]])
            self.assertEqual(self.plan["units"][0]["unit_identity"], manifest["selected_units"][0]["unit_identity"])
        finally:
            self.auth_context.plan.clear()
            self.auth_context.plan.update(original_plan)

    def test_blueprint_parent_can_contain_a_new_attempt_directory(self):
        selected_id = self.plan["units"][0]["unit_identity"]["id"]
        output = self.blueprint_path.parent / "attempt-001"

        manifest = reader_workflow.prepare_reader_theme(
            self.auth_context,
            [selected_id],
            self.blueprint_path,
            output,
            "reader-writer-session-1",
        )

        self.assertEqual("PREPARED", manifest["status"])
        self.assertEqual(self.blueprint_raw, self.blueprint_path.read_bytes())
        self.assertEqual(self.blueprint_raw, (output / "teaching-blueprint.md").read_bytes())
        self.assertTrue((output / "reader-input-manifest.json").is_file())

    def test_unknown_and_duplicate_unit_selections_are_rejected(self):
        known_id = self.plan["units"][0]["unit_identity"]["id"]
        for selection, expected_code in (
            (["unknown-unit-id"], "UNIT_UNKNOWN"),
            ([known_id, known_id], "UNIT_SELECTION_DUPLICATE"),
        ):
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                    self._prepare(selection, out_name=f"invalid-{expected_code}")
                self.assertEqual(expected_code, caught.exception.code)
                self.assertFalse(self._out(f"invalid-{expected_code}").exists())

    def test_empty_and_invalid_utf8_blueprints_are_rejected(self):
        for label, raw in (("empty", b""), ("invalid-utf8", b"\xff\xfe")):
            with self.subTest(label=label):
                invalid_blueprint = self.blueprint_dir / f"{label}.md"
                invalid_blueprint.write_bytes(raw)
                with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                    self._prepare(
                        [self.plan["units"][0]["unit_identity"]["id"]],
                        out_name=f"invalid-blueprint-{label}",
                        blueprint_path=invalid_blueprint,
                    )
                self.assertEqual("BLUEPRINT_INVALID", caught.exception.code)
                self.assertFalse(self._out(f"invalid-blueprint-{label}").exists())

    def test_existing_output_is_rejected_without_overwriting_files(self):
        output = self._out("existing")
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_bytes(b"preserve this file")

        with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
            self._prepare([self.plan["units"][0]["unit_identity"]["id"]], out_name="existing")

        self.assertEqual("OUTPUT_EXISTS", caught.exception.code)
        self.assertEqual(b"preserve this file", sentinel.read_bytes())
        self.assertEqual([sentinel], list(output.iterdir()))

    def test_stale_plan_is_rejected_before_creating_output(self):
        original = self.plan_path.read_bytes()
        self.plan_path.write_bytes(original + b" ")
        output = self._out("stale-plan")
        try:
            with self.assertRaises(reader_workflow.ReaderHandbookWorkflowError) as caught:
                self._prepare([self.plan["units"][0]["unit_identity"]["id"]], out_name="stale-plan")
        finally:
            self.plan_path.write_bytes(original)

        self.assertEqual("INPUT_CHANGED", caught.exception.code)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
