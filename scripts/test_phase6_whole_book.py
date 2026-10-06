"""Focused tests for deterministic D3a selection and rendering."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
try:
    import phase6_whole_book as whole_book
except ImportError:
    whole_book = None


def _identity(unit_id: str, order: int, *, title: str, prerequisites=(), origin="CANDIDATE", scope="GENERAL_LEARNING"):
    identity = {
        "id": unit_id,
        "order": order,
        "title": title,
        "kind": "prerequisite" if scope == "GENERAL_LEARNING" else "workflow",
        "scope": scope,
        "depth": 1,
        "content_status": "OUTLINE_ONLY",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "prerequisite_ids": list(prerequisites),
        "introduces_concept_keys": [f"concept.{order}"],
        "requires_concept_keys": [],
        "project_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
        "origin": origin,
    }
    if origin == "CANDIDATE":
        identity["unit_key"] = f"unit-{order}"
    else:
        identity.update({
            "primer_concept_key": "concept.primer",
            "prerequisite_node_id": "PREREQ-NODE-" + "f" * 64,
            "concept_epistemic_status": "UNVERIFIED_TEACHING",
        })
    return identity


def _digest_entry(role: str, position: int, attempt: str, directory: str, filename: str) -> dict:
    return {
        "role": role,
        "relative_path": f"units/{position:04d}/{attempt}/{directory}/{filename}",
        "sha256": hashlib.sha256(f"{role}:{position}:{attempt}".encode()).hexdigest(),
    }


def _plan_and_state(*, state_version="1.1.0"):
    first_id = "CURRICULUM-UNIT-" + "1" * 64
    project_id = "CURRICULUM-UNIT-" + "2" * 64
    omitted_id = "CURRICULUM-UNIT-" + "3" * 64
    first = _identity(first_id, 1, title="Prerequisite concept")
    project = _identity(project_id, 2, title="Project flow", prerequisites=[first_id], scope="PROJECT_SPECIFIC")
    omitted = _identity(omitted_id, 3, title="Independent lesson")
    plan_rows = [
        {"position": 1, "unit_identity": first, "route": "D2A_GENERAL_LEARNING", "state": "PLANNED", "reason_codes": ["ROUTE_GENERAL_CANDIDATE"], "blocked_by": []},
        {"position": 2, "unit_identity": project, "route": "PHASE6A_PROJECT_CLAIM", "state": "PLANNED", "reason_codes": ["ROUTE_PROJECT_CLAIM_SUPPORTED"], "blocked_by": []},
        {"position": 3, "unit_identity": omitted, "route": "D2A_GENERAL_LEARNING", "state": "PLANNED", "reason_codes": ["ROUTE_GENERAL_CANDIDATE"], "blocked_by": []},
    ]
    plan = {"artifact_kind": "curriculum-run-plan", "schema_version": "1.0.0", "units": plan_rows}

    first_attempts = [
        {
            "attempt_id": "attempt-0001", "execution_state": "BLOCKED_REVIEW", "disposition": "ORIGINAL",
            "predecessor_attempt_id": None, "predecessor_review_status_sha256": None,
            "predecessor_report_digests": [],
            "artifact_digests": [_digest_entry("general_review_status", 1, "attempt-0001", "general-review", "general-review-status.json")],
        },
        {
            "attempt_id": "attempt-0002", "execution_state": "REVIEWED_DRAFT", "disposition": "SAME_SNAPSHOT_REPAIR",
            "predecessor_attempt_id": "attempt-0001", "predecessor_review_status_sha256": "a" * 64,
            "predecessor_report_digests": [],
            "artifact_digests": [_digest_entry("general_review_status", 1, "attempt-0002", "general-review", "general-review-status.json")],
        },
    ]
    project_attempt = {
        "attempt_id": "attempt-0001", "execution_state": "REVIEWED_DRAFT", "disposition": "ORIGINAL",
        "predecessor_attempt_id": None, "predecessor_review_status_sha256": None,
        "predecessor_report_digests": [],
        "artifact_digests": [_digest_entry("chapter_review_status", 2, "attempt-0001", "phase6a-review", "chapter-review-status.json")],
    }
    state_rows = []
    for position, row in enumerate(plan_rows, start=1):
        identity = row["unit_identity"]
        attempts = first_attempts if position == 1 else ([project_attempt] if position == 2 else [])
        current = attempts[-1] if attempts else None
        state_row = {
            "position": position,
            "unit_id": identity["id"],
            "unit_identity_sha256": hashlib.sha256(json.dumps(identity, sort_keys=True, indent=2).encode()).hexdigest(),
            "route": row["route"],
            "plan_state": row["state"],
            "prerequisite_ids": identity["prerequisite_ids"],
            "reason_codes": row["reason_codes"],
            "blocked_by": row["blocked_by"],
            "execution_state": current["execution_state"] if current else "PLANNED",
            "repair_disposition": None,
            "attempt_id": current["attempt_id"] if current else None,
            "artifact_digests": [entry for attempt in attempts for entry in attempt["artifact_digests"]],
            "attempts": attempts,
        }
        state_rows.append(state_row)
    state = {"schema_version": state_version, "units": state_rows}
    return plan, state


class WholeBookSelectionTests(unittest.TestCase):
    def _api(self):
        self.assertIsNotNone(whole_book, "phase6_whole_book module is required for D3a")
        return whole_book

    def test_manifest_keeps_all_units_ordered_and_names_each_selected_attempt(self):
        api = self._api()
        plan, state = _plan_and_state()
        first_id = plan["units"][0]["unit_identity"]["id"]
        project_id = plan["units"][1]["unit_identity"]["id"]
        rows = api.validate_whole_book_selection(
            plan,
            [state],
            {first_id: "attempt-0002", project_id: "attempt-0001"},
        )
        self.assertEqual([row["unit_identity"]["id"] for row in plan["units"]], [row["unit_id"] for row in rows])
        self.assertEqual(["SELECTED", "SELECTED", "NOT_SELECTED"], [row["selection_state"] for row in rows])
        self.assertEqual(["attempt-0002", "attempt-0001", None], [row["attempt_id"] for row in rows])

    def test_selected_project_without_its_prerequisite_is_rejected(self):
        api = self._api()
        plan, state = _plan_and_state()
        project_id = plan["units"][1]["unit_identity"]["id"]
        with self.assertRaisesRegex(api.WholeBookError, "PREREQUISITE_NOT_SELECTED"):
            api.validate_whole_book_selection(plan, [state], {project_id: "attempt-0001"})

    def test_v10_state_cannot_select_a_repair_attempt(self):
        api = self._api()
        plan, state = _plan_and_state()
        state["schema_version"] = "1.0.0"
        first = state["units"][0]
        first.update({
            "attempt_id": "attempt-0001",
            "execution_state": "REVIEWED_DRAFT",
            "artifact_digests": first["attempts"][0]["artifact_digests"],
        })
        del first["repair_disposition"]
        del first["attempts"]
        project = state["units"][1]
        project.update({"attempt_id": "attempt-0001", "execution_state": "REVIEWED_DRAFT"})
        del project["repair_disposition"]
        del project["attempts"]
        first_id = plan["units"][0]["unit_identity"]["id"]
        project_id = plan["units"][1]["unit_identity"]["id"]
        with self.assertRaisesRegex(api.WholeBookError, "ATTEMPT_NOT_AVAILABLE"):
            api.validate_whole_book_selection(
                plan, [state], {first_id: "attempt-0002", project_id: "attempt-0001"},
            )

    def test_whole_book_notice_binds_source_snapshot_and_plan_without_claiming_verified(self):
        api = self._api()
        plan, state = _plan_and_state()
        plan.update({
            "snapshot_kind": "GIT_COMMIT",
            "repository_revision": "a" * 40,
            "run_plan_id": "CURRICULUM-RUN-PLAN-" + "b" * 64,
            "source_status": "PARTIAL",
            "unknown_files": 1,
        })
        unit_id = plan["units"][0]["unit_identity"]["id"]
        rows = api.validate_whole_book_selection(plan, [state], {unit_id: "attempt-0002"})

        rendered = api.render_whole_book(plan, rows, {unit_id: "# Lesson\n"})

        self.assertIn("Snapshot kind: `GIT_COMMIT`", rendered)
        self.assertIn("repository revision: `" + "a" * 40 + "`", rendered)
        self.assertIn("run plan: `CURRICULUM-RUN-PLAN-" + "b" * 64 + "`", rendered)
        self.assertNotIn("verified", rendered.casefold())

    def test_quality_report_discloses_unreviewed_sensitive_writer_text(self):
        api = self._api()
        plan, state = _plan_and_state()
        plan.update({"source_status": "PARTIAL", "unknown_files": 1})
        unit_id = plan["units"][0]["unit_identity"]["id"]
        rows = api.validate_whole_book_selection(plan, [state], {unit_id: "attempt-0002"})

        rendered = api.render_quality_and_gaps(plan, rows)

        self.assertIn("Writer-authored Markdown", rendered)
        self.assertIn("sensitive", rendered.casefold())
        self.assertIn("privacy", rendered.casefold())


if __name__ == "__main__":
    unittest.main()
