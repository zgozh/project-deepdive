#!/usr/bin/env python3
"""Focused contract tests for append-only D2B2 run state."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import copy
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact  # noqa: E402
import phase6_curriculum_run_state as state_workflow  # noqa: E402
from phase6_curriculum_run_state import (  # noqa: E402
    CurriculumRunStateError,
    advance_revision,
    create_state_dir,
    initial_state,
    load_state_chain,
    publish_revision,
    sha256,
    validate_state_against_plan,
)
from test_artifact_contract import minimal_artifacts  # noqa: E402


class CurriculumRunStateTests(unittest.TestCase):
    def setUp(self):
        self.plan = minimal_artifacts()["curriculum-run-plan"]
        self.plan_raw = dumps_artifact(self.plan).encode("utf-8")

    def test_initial_state_binds_all_rows_partial_source_and_ten_inputs(self):
        plan = dict(self.plan)
        plan["source_status"] = "PARTIAL"
        plan["unknown_files"] = 3
        plan["source_metadata"] = {**plan["source_metadata"], "g01_status": "PARTIAL", "unknown_files": 3}
        raw = dumps_artifact(plan).encode("utf-8")
        first = initial_state(plan, raw, generated_at="2026-09-29T00:00:00Z")
        second = initial_state(plan, raw, generated_at="2026-09-30T00:00:00Z")
        self.assertEqual(first["run_state_id"], second["run_state_id"])
        self.assertEqual("PARTIAL", first["source_status"])
        self.assertEqual(3, first["unknown_files"])
        self.assertEqual(10, len(first["input_digests"]))
        self.assertEqual(len(plan["units"]), len(first["units"]))
        validate_state_against_plan(first, plan, raw)

    def test_documentation_curriculum_metadata_and_profile_digests_stay_bound_to_plan(self):
        plan = copy.deepcopy(self.plan)
        plan["curriculum_schema_version"] = "1.2.0"
        documentation_inputs = {
            "phase4_base_graph": ("knowledge-graph", "1.2.0"),
            "phase4_base_evidence": ("evidence", "1.4.0"),
            "phase4_semantic_proposals": ("semantic-proposals", "1.1.0"),
            "phase4_claim_candidates": ("claim-candidates", "1.2.0"),
            "phase4_claim_evidence": ("claim-evidence", "1.2.0"),
            "phase4_claim_evidence_graph": ("claim-evidence-graph", "1.1.0"),
            "phase5_prerequisite_candidates": ("prerequisite-candidates", "1.1.0"),
            "phase5_prerequisite_graph": ("prerequisite-graph", "1.1.0"),
            "phase5_curriculum_candidates": ("curriculum-candidates", "1.1.0"),
            "phase5_curriculum": ("curriculum", "1.2.0"),
        }
        for row in plan["input_digests"]:
            row["artifact_kind"], row["schema_version"] = documentation_inputs[row["role"]]
        plan_raw = dumps_artifact(plan).encode("utf-8")

        first = initial_state(plan, plan_raw, generated_at="2026-10-01T00:00:00Z")
        self.assertEqual("1.2.0", first["curriculum_schema_version"])
        validate_state_against_plan(first, plan, plan_raw)

        tampered = copy.deepcopy(first)
        tampered["curriculum_schema_version"] = "1.1.0"
        with self.assertRaises(CurriculumRunStateError) as caught:
            validate_state_against_plan(tampered, plan, plan_raw)
        self.assertEqual("STATE_BINDING_INVALID", caught.exception.code)

        first_raw = dumps_artifact(first).encode("utf-8")
        revision_v1_1 = state_workflow.upgrade_to_v1_1(
            first,
            first_raw,
            plan,
            plan_raw,
            generated_at="2026-10-01T00:00:01Z",
        )
        self.assertEqual("1.2.0", revision_v1_1["curriculum_schema_version"])
        validate_state_against_plan(revision_v1_1, plan, plan_raw)
        tampered_v1_1 = copy.deepcopy(revision_v1_1)
        tampered_v1_1["curriculum_schema_version"] = "1.1.0"
        with self.assertRaises(CurriculumRunStateError) as caught:
            validate_state_against_plan(tampered_v1_1, plan, plan_raw)
        self.assertEqual("STATE_BINDING_INVALID", caught.exception.code)

    def test_append_chain_is_canonical_and_exclusive(self):
        first = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        second_rows = [dict(row) for row in first["units"]]
        second_rows[0]["execution_state"] = "PREPARED"
        second_rows[0]["attempt_id"] = "attempt-0001"
        second = advance_revision(first, dumps_artifact(first).encode("utf-8"), second_rows,
                                  generated_at="2026-09-29T00:00:01Z")
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = create_state_dir(Path(temporary) / "state")
            first_raw = publish_revision(state_dir, first)
            self.assertEqual(dumps_artifact(first).encode("utf-8"), first_raw)
            second_raw = publish_revision(state_dir, second)
            states, raws = load_state_chain(state_dir, self.plan, self.plan_raw)
            self.assertEqual([1, 2], [row["state_revision"] for row in states])
            self.assertEqual(sha256(first_raw), second["previous_state_sha256"])
            self.assertEqual(second_raw, raws[1])
            with self.assertRaises(CurriculumRunStateError) as caught:
                publish_revision(state_dir, second)
            self.assertEqual("STATE_CHANGED", caught.exception.code)

    def test_legacy_chain_migrates_by_appending_v1_1_without_changing_old_attempt(self):
        first = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        first_raw = dumps_artifact(first).encode("utf-8")
        prepared_rows = [copy.deepcopy(row) for row in first["units"]]
        prepared_rows[0]["execution_state"] = "PREPARED"
        prepared_rows[0]["attempt_id"] = "attempt-0001"
        legacy_head = advance_revision(first, first_raw, prepared_rows, generated_at="2026-09-29T00:00:01Z")
        legacy_raw = dumps_artifact(legacy_head).encode("utf-8")

        try:
            migrated = state_workflow.upgrade_to_v1_1(
                legacy_head, legacy_raw, self.plan, self.plan_raw, generated_at="2026-09-29T00:00:02Z",
            )
        except AttributeError as exc:
            self.fail(f"valid D2B2a state must have an append-only 1.1.0 migration: {exc}")

        self.assertEqual("1.1.0", migrated["schema_version"])
        self.assertEqual(3, migrated["state_revision"])
        self.assertEqual(sha256(legacy_raw), migrated["previous_state_sha256"])
        self.assertEqual(legacy_head["run_state_id"], migrated["run_state_id"])
        self.assertEqual(legacy_head["units"][0]["artifact_digests"], migrated["units"][0]["artifact_digests"])
        self.assertEqual("attempt-0001", migrated["units"][0]["attempts"][0]["attempt_id"])
        self.assertEqual("PREPARED", migrated["units"][0]["attempts"][0]["execution_state"])

        with tempfile.TemporaryDirectory() as temporary:
            state_dir = create_state_dir(Path(temporary) / "state")
            first_written = publish_revision(state_dir, first)
            legacy_written = publish_revision(state_dir, legacy_head)
            migrated_written = publish_revision(state_dir, migrated)
            self.assertEqual(first_raw, (state_dir / "run-state-000001.json").read_bytes())
            self.assertEqual(legacy_raw, (state_dir / "run-state-000002.json").read_bytes())
            states, raws = load_state_chain(state_dir, self.plan, self.plan_raw)
            self.assertEqual(["1.0.0", "1.0.0", "1.1.0"], [item["schema_version"] for item in states])
            self.assertEqual([first_written, legacy_written, migrated_written], raws)

    def test_v1_1_keeps_existing_prerequisite_block_transition(self):
        first = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        first_raw = dumps_artifact(first).encode("utf-8")
        migrated = state_workflow.upgrade_to_v1_1(
            first, first_raw, self.plan, self.plan_raw, generated_at="2026-09-29T00:00:01Z",
        )
        migrated_raw = dumps_artifact(migrated).encode("utf-8")
        rows = copy.deepcopy(migrated["units"])
        rows[0]["execution_state"] = "BLOCKED_PREREQUISITE"
        blocked = advance_revision(migrated, migrated_raw, rows, generated_at="2026-09-29T00:00:02Z")

        with tempfile.TemporaryDirectory() as temporary:
            state_dir = create_state_dir(Path(temporary) / "state")
            publish_revision(state_dir, first)
            publish_revision(state_dir, migrated)
            publish_revision(state_dir, blocked)
            states, _raws = load_state_chain(state_dir, self.plan, self.plan_raw)
        self.assertEqual("BLOCKED_PREREQUISITE", states[-1]["units"][0]["execution_state"])

    def test_v1_1_can_record_finalized_incomplete_review_as_a_stop(self):
        row = {
            "position": 1, "unit_id": "CURRICULUM-PRIMER-" + "a" * 64,
            "unit_identity_sha256": "b" * 64, "route": "D2A_GENERAL_LEARNING", "plan_state": "PLANNED",
            "prerequisite_ids": [], "reason_codes": [], "blocked_by": [],
            "execution_state": "AWAITING_REVIEW", "repair_disposition": None,
            "attempt_id": "attempt-0001", "artifact_digests": [],
            "attempts": [{
                "attempt_id": "attempt-0001", "execution_state": "AWAITING_REVIEW", "disposition": "ORIGINAL",
                "predecessor_attempt_id": None, "predecessor_review_status_sha256": None,
                "predecessor_report_digests": [], "artifact_digests": [],
            }],
        }
        after = copy.deepcopy(row)
        after["execution_state"] = "REVIEW_INCOMPLETE_STOP"
        after["repair_disposition"] = "REVIEW_INCOMPLETE"
        after["attempts"][-1]["execution_state"] = "REVIEW_INCOMPLETE_STOP"

        self.assertTrue(state_workflow._valid_v1_1_transition([row], [after]))

    def test_chain_rejects_unlinked_or_extra_state(self):
        first = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = create_state_dir(Path(temporary) / "state")
            publish_revision(state_dir, first)
            (state_dir / "untracked.txt").write_text("no", encoding="utf-8")
            with self.assertRaises(CurriculumRunStateError) as caught:
                load_state_chain(state_dir, self.plan, self.plan_raw)
            self.assertEqual("STATE_CHAIN_INVALID", caught.exception.code)

    def test_revision_is_invisible_until_complete_staged_bytes_are_linked(self):
        state = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        expected = dumps_artifact(state).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = create_state_dir(Path(temporary) / "state")
            target = state_dir / "run-state-000001.json"
            real_link = os.link

            def check_staged_then_link(source, destination, *args, **kwargs):
                source_path = Path(source)
                self.assertEqual(target, Path(destination))
                self.assertFalse(target.exists())
                self.assertFalse(source_path.is_relative_to(state_dir))
                self.assertEqual(expected, source_path.read_bytes())
                return real_link(source, destination, *args, **kwargs)

            with patch.object(state_workflow.os, "link", side_effect=check_staged_then_link) as link:
                published = publish_revision(state_dir, state)
            self.assertEqual(1, link.call_count)
            self.assertEqual(expected, published)
            self.assertEqual(expected, target.read_bytes())
            self.assertEqual([], list(Path(temporary).glob(".curriculum-run-state-*.tmp")))

    def test_stage_failure_does_not_delete_another_writers_revision(self):
        state = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            state_dir = create_state_dir(parent / "state")
            target = state_dir / "run-state-000001.json"
            competing_bytes = b"another writer's complete revision"

            def competing_writer_then_fail(_descriptor):
                if target.exists():
                    target.unlink()
                target.write_bytes(competing_bytes)
                raise OSError("injected stage fsync failure")

            with patch.object(state_workflow.os, "fsync", side_effect=competing_writer_then_fail):
                with self.assertRaises(CurriculumRunStateError) as caught:
                    publish_revision(state_dir, state)
            self.assertEqual("STATE_PUBLISH_FAILED", caught.exception.code)
            self.assertEqual(competing_bytes, target.read_bytes())
            self.assertEqual([], list(parent.glob(".curriculum-run-state-*.tmp")))

    def test_publish_collision_preserves_the_winning_revision(self):
        state = initial_state(self.plan, self.plan_raw, generated_at="2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            state_dir = create_state_dir(parent / "state")
            target = state_dir / "run-state-000001.json"
            winning_bytes = b"winner published first"

            def another_writer_wins(_source, destination, *_args, **_kwargs):
                self.assertFalse(target.exists())
                Path(destination).write_bytes(winning_bytes)
                raise FileExistsError(destination)

            with patch.object(state_workflow.os, "link", side_effect=another_writer_wins):
                with self.assertRaises(CurriculumRunStateError) as caught:
                    publish_revision(state_dir, state)
            self.assertEqual("STATE_CHANGED", caught.exception.code)
            self.assertEqual(winning_bytes, target.read_bytes())
            self.assertEqual([], list(parent.glob(".curriculum-run-state-*.tmp")))


if __name__ == "__main__":
    unittest.main()
