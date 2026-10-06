#!/usr/bin/env python3
"""Focused authenticated-source materialization integration checks."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import curriculum_run_workflow as curriculum_workflow  # noqa: E402
import reader_theme_sources as theme_sources  # noqa: E402
import test_phase6_chapter as chapter_tests  # noqa: E402
from reader_source_blocks import SourceBlockPlan  # noqa: E402


class ReaderThemeSourceMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_case = cls(methodName="runTest")
        context, cls.inputs, _unit_id, _output = chapter_tests._phase6_fixture(
            cls.fixture_case,
            source_files={
                "src/module.py": b"def main():\n    return 1\n",
                "web/app.ts": b"export function answer(): number {\n  return 1;\n}\n",
            },
            git_tree=True,
        )
        curriculum_tests = importlib.import_module("test_phase5_curriculum")
        candidate_units = curriculum_tests._starter_units(context)
        workflow_unit = next(unit for unit in candidate_units if unit["unit_key"] == "workflow-map")
        workflow_unit["introduces_concept_keys"] = []
        candidates = curriculum_tests._curriculum_candidates(context, candidate_units)
        curriculum_path = context["fixture"]["work"] / "theme-source-curriculum.json"
        context["curriculum_output_path"] = curriculum_path
        cls.inputs = replace(cls.inputs, curriculum_path=curriculum_path)
        context["curriculum"] = curriculum_tests._build(cls.fixture_case, context, candidates)
        cls.plan_path = context["fixture"]["work"] / "theme-source-run-plan.json"
        curriculum_workflow.plan_curriculum_run(cls.inputs, out=cls.plan_path)
        cls.auth_context = curriculum_workflow.authenticate_curriculum_run_plan(
            cls.inputs, plan_path=cls.plan_path,
        )
        cls.plan = json.loads(cls.auth_context.raw.decode("utf-8"))
        cls.source_path = "src/module.py"
        cls.other_source_path = "web/app.ts"
        cls.source_bytes = (cls.inputs.root / "src" / "module.py").read_bytes()
        cls.other_source_bytes = (cls.inputs.root / "web" / "app.ts").read_bytes()
        cls.index_path = cls.inputs.run_dir / "phase2" / "project-index.json"

    @classmethod
    def tearDownClass(cls):
        cls.fixture_case.doCleanups()

    def _plans(self, *, revision=None, expected_source_sha256=None):
        return [
            SourceBlockPlan(
                "entry",
                self.source_path,
                1,
                1,
                "python",
                revision=revision,
                expected_source_sha256=expected_source_sha256,
            ),
            SourceBlockPlan(
                "return",
                self.source_path,
                2,
                2,
                "python",
                annotations={2: "函数把这个值交给调用方。"},
                revision=revision,
                expected_source_sha256=expected_source_sha256,
            ),
            SourceBlockPlan(
                "client",
                self.other_source_path,
                1,
                3,
                "typescript",
                revision=revision,
                expected_source_sha256=expected_source_sha256,
            ),
        ]

    def _materialize(self, plans, *, frozen_sources=None):
        return theme_sources.materialize_reader_theme_sources(
            self.auth_context,
            b"# Route\n\n@@source:entry@@\n\n@@source:return@@\n\n@@source:client@@\n",
            plans,
            frozen_sources=frozen_sources,
        )

    def test_live_auth_cache_and_snapshot_fallback_match_and_read_each_path_once(self):
        plans = self._plans()
        cache = {
            self.source_path: self.source_bytes,
            self.other_source_path: self.other_source_bytes,
        }
        original_artifact_reader = theme_sources.chapter._read_artifact
        original_snapshot_reader = theme_sources._read_snapshot_bytes

        with (
            patch.object(
                theme_sources.chapter,
                "_read_artifact",
                wraps=original_artifact_reader,
            ) as artifact_reader,
            patch.object(
                theme_sources,
                "_read_snapshot_bytes",
                wraps=original_snapshot_reader,
            ) as snapshot_reader,
        ):
            cached_output, cached_metadata = self._materialize(plans, frozen_sources=cache)
            self.assertEqual(
                1,
                sum(call.args[0] == self.index_path for call in artifact_reader.call_args_list),
            )
            self.assertEqual(0, snapshot_reader.call_count)

            artifact_reader.reset_mock()
            snapshot_reader.reset_mock()
            fallback_output, fallback_metadata = self._materialize(plans)

        self.assertEqual(cached_output, fallback_output)
        self.assertEqual(
            1,
            sum(call.args[0] == self.index_path for call in artifact_reader.call_args_list),
        )
        self.assertEqual(2, snapshot_reader.call_count)
        self.assertEqual(
            {self.source_path, self.other_source_path},
            {call.args[0] for call in snapshot_reader.call_args_list},
        )
        self.assertEqual(["RENDERED", "NOT_REVIEWED"], [
            cached_metadata["status"], cached_metadata["review_status"],
        ])
        self.assertEqual(cached_metadata["source_blocks"], fallback_metadata["source_blocks"])
        self.assertEqual(
            hashlib.sha256(self.auth_context.raw).hexdigest(),
            cached_metadata["plan_sha256"],
        )
        self.assertEqual(
            self.plan["source_metadata"]["project_index_sha256"],
            cached_metadata["project_index_sha256"],
        )
        self.assertEqual(
            self.plan["repository_revision"],
            cached_metadata["source_anchor"]["repository_revision"],
        )
        self.assertEqual("NOT_PERFORMED", cached_metadata["semantic_review"])
        self.assertEqual("AUTHENTICATED_PROJECT_INDEX", cached_metadata["source_authentication"])
        self.assertEqual(
            "authenticated by curriculum-run-plan",
            cached_metadata["source_blocks"][0]["revision_trust"],
        )

    def test_stale_cache_fails_closed_without_snapshot_fallback(self):
        plans = self._plans()
        with patch.object(
            theme_sources,
            "_read_snapshot_bytes",
                side_effect=AssertionError("a stale cache must not fall through to the repository"),
            ):
            with self.assertRaises(theme_sources.ReaderThemeSourceError) as raised:
                self._materialize(plans, frozen_sources={
                    self.source_path: self.source_bytes + b"# stale\n",
                })
        self.assertEqual("SOURCE_CACHE_INVALID", raised.exception.code)

    def test_changed_project_index_digest_is_rejected(self):
        original_raw = self.index_path.read_bytes()
        try:
            # Keep the JSON valid while changing its exact authenticated byte digest.
            theme_sources.reader_handbook_workflow._recheck_context(self.auth_context)
            self.index_path.write_bytes(original_raw + b" ")
            with patch.object(theme_sources.reader_handbook_workflow, "_recheck_context"):
                with self.assertRaises(theme_sources.ReaderThemeSourceError) as raised:
                    self._materialize(self._plans())
            self.assertEqual("PROJECT_INDEX_CHANGED", raised.exception.code)
        finally:
            self.index_path.write_bytes(original_raw)

    def test_revision_conflict_is_rejected_before_source_read(self):
        with patch.object(
            theme_sources,
            "_read_snapshot_bytes",
            side_effect=AssertionError("revision conflict must fail before source access"),
        ):
            with self.assertRaises(theme_sources.ReaderThemeSourceError) as raised:
                self._materialize(self._plans(revision="different-revision"))
        self.assertEqual("SOURCE_PLAN_INVALID", raised.exception.code)

    def test_expected_source_digest_conflict_is_rejected_before_source_read(self):
        with patch.object(
            theme_sources,
            "_read_snapshot_bytes",
            side_effect=AssertionError("source digest conflict must fail before source access"),
        ):
            with self.assertRaises(theme_sources.ReaderThemeSourceError) as raised:
                self._materialize(self._plans(expected_source_sha256="0" * 64))
        self.assertEqual("SOURCE_PLAN_INVALID", raised.exception.code)

    def test_non_string_source_paths_return_the_fixed_plan_error(self):
        for invalid_path in (None, 123, ["src/module.py"]):
            plans = self._plans()
            plans[0] = replace(plans[0], path=invalid_path)
            with self.subTest(path_type=type(invalid_path).__name__):
                with self.assertRaises(theme_sources.ReaderThemeSourceError) as raised:
                    self._materialize(plans)
                self.assertEqual("SOURCE_PLAN_INVALID", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
