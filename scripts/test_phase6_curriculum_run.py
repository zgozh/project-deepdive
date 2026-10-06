#!/usr/bin/env python3
"""Focused tests for deterministic Phase 6D2B1 curriculum routing."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact
from phase4_graph import canonical_tuple_sha256
from phase6_curriculum_run import (
    Phase6CurriculumRunError,
    build_curriculum_run_plan,
    route_curriculum_units,
)


def _unit(
    order: int,
    *,
    origin: str = "CANDIDATE",
    scope: str = "GENERAL_LEARNING",
    refs: bool = False,
    prerequisites: list[str] | None = None,
    valid_metadata: bool = True,
) -> dict:
    unit_id = f"CURRICULUM-{'PRIMER' if origin == 'PREREQUISITE_PRIMER' else 'UNIT'}-{order:064x}"
    unit = {
        "id": unit_id,
        "order": order,
        "title": f"Unit {order}",
        "kind": "prerequisite" if origin == "PREREQUISITE_PRIMER" else "workflow",
        "scope": scope,
        "depth": 1,
        "content_status": "OUTLINE_ONLY",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "prerequisite_ids": list(prerequisites or []),
        "introduces_concept_keys": [f"concept.{order}"],
        "requires_concept_keys": [],
        "project_refs": {
            "graph_node_ids": [f"NODE-{order}"] if refs else [],
            "graph_edge_ids": [],
            "evidence_ids": [],
            "file_paths": [],
        },
        "origin": origin,
    }
    if origin == "CANDIDATE":
        if valid_metadata:
            unit["unit_key"] = f"unit-{order}"
    else:
        unit.update({
            "primer_concept_key": f"concept.{order}",
            "prerequisite_node_id": f"PREREQ-NODE-{order:064x}",
            "concept_epistemic_status": "UNVERIFIED_TEACHING",
        })
    return unit


def _curriculum(units: list[dict], *, source_status: str = "PASS") -> dict:
    return {
        "artifact_kind": "curriculum",
        "schema_version": "1.1.0",
        "repository_revision": "1" * 40,
        "generated_at": "2026-09-29T00:00:00Z",
        "snapshot_kind": "worktree",
        "source_metadata": {
            "snapshot_kind": "worktree",
            "g01_status": source_status,
            "unknown_files": 3 if source_status == "PARTIAL" else 0,
            "project_index_sha256": "a" * 64,
            "coverage_sha256": "b" * 64,
        },
        "source_status": source_status,
        "source_run_manifest_sha256": "c" * 64,
        "units": units,
    }


class CurriculumRunRoutingTests(unittest.TestCase):
    def test_routes_every_current_unit_form_and_keeps_exact_order_and_identity(self):
        units = [
            _unit(1),
            _unit(2, origin="PREREQUISITE_PRIMER", scope="GENERAL_LEARNING"),
            _unit(3, origin="PREREQUISITE_PRIMER", scope="PROJECT_SPECIFIC"),
            _unit(4, scope="PROJECT_SPECIFIC", refs=True),
            _unit(5, scope="PROJECT_SPECIFIC"),
            _unit(6, scope="PROJECT_SPECIFIC", refs=True),
            _unit(7, scope="PROJECT_SPECIFIC", refs=True),
            _unit(8, refs=True),
            _unit(9, origin="PREREQUISITE_PRIMER", scope="PROJECT_SPECIFIC", refs=True),
            _unit(10, valid_metadata=False),
        ]
        readiness = {
            units[3]["id"]: "READY",
            units[5]["id"]: "NO_QUALIFYING_SUPPORTED_CLAIM",
            units[6]["id"]: "CLAIM_SCOPE_AMBIGUOUS",
        }

        rows = route_curriculum_units(_curriculum(units), readiness, source_status="PASS")

        self.assertEqual(units, [row["unit_identity"] for row in rows])
        self.assertEqual(list(range(1, 11)), [row["position"] for row in rows])
        self.assertEqual(
            [
                ("D2A_GENERAL_LEARNING", "PLANNED"),
                ("D2A_GENERAL_LEARNING", "PLANNED"),
                ("D2A_GENERAL_LEARNING", "PLANNED"),
                ("PHASE6A_PROJECT_CLAIM", "PLANNED"),
                ("UPSTREAM_REQUIRED", "BLOCKED"),
                ("UPSTREAM_REQUIRED", "BLOCKED"),
                ("BLOCKED_UNSUPPORTED", "BLOCKED"),
                ("BLOCKED_UNSUPPORTED", "BLOCKED"),
                ("BLOCKED_UNSUPPORTED", "BLOCKED"),
                ("BLOCKED_UNSUPPORTED", "BLOCKED"),
            ],
            [(row["route"], row["state"]) for row in rows],
        )
        self.assertEqual(["ROUTE_PREREQUISITE_PRIMER"], rows[2]["reason_codes"])
        self.assertEqual(["PROJECT_REFERENCES_REQUIRED"], rows[4]["reason_codes"])
        self.assertEqual(["NO_QUALIFYING_SUPPORTED_CLAIM"], rows[5]["reason_codes"])
        self.assertEqual(["CLAIM_SCOPE_AMBIGUOUS"], rows[6]["reason_codes"])
        self.assertEqual(["GENERAL_PROJECT_REFERENCES_PRESENT"], rows[7]["reason_codes"])
        self.assertEqual(["PRIMER_PROJECT_REFERENCES_PRESENT"], rows[8]["reason_codes"])
        self.assertEqual(["D2A_METADATA_INVALID"], rows[9]["reason_codes"])

    def test_partial_source_is_preserved_and_never_upgraded(self):
        units = [_unit(1), _unit(2, origin="PREREQUISITE_PRIMER")]
        curriculum = _curriculum(units, source_status="PARTIAL")

        rows = route_curriculum_units(curriculum, {}, source_status="PARTIAL")

        self.assertEqual("PARTIAL", curriculum["source_status"])
        self.assertEqual(["ROUTE_GENERAL_CANDIDATE", "SOURCE_PARTIAL"], rows[0]["reason_codes"])
        self.assertTrue(all(row["state"] == "PLANNED" for row in rows))

    def test_blocked_prerequisite_propagates_without_changing_intended_route(self):
        first = _unit(1, valid_metadata=False)
        second = _unit(2, prerequisites=[first["id"]])
        third = _unit(3, prerequisites=[second["id"]])

        rows = route_curriculum_units(_curriculum([first, second, third]), {}, source_status="PASS")

        self.assertEqual("BLOCKED_UNSUPPORTED", rows[0]["route"])
        self.assertEqual(("D2A_GENERAL_LEARNING", "BLOCKED"), (rows[1]["route"], rows[1]["state"]))
        self.assertEqual([first["id"]], rows[1]["blocked_by"])
        self.assertEqual(["PREREQUISITE_NOT_READY", "ROUTE_GENERAL_CANDIDATE"], rows[1]["reason_codes"])
        self.assertEqual([second["id"]], rows[2]["blocked_by"])

    def test_invalid_order_dependencies_and_unknown_readiness_fail_closed(self):
        general = [_unit(1), _unit(2)]
        with self.assertRaises(Phase6CurriculumRunError):
            route_curriculum_units(_curriculum(general), {}, source_status="UNKNOWN")

        forward = [_unit(1, prerequisites=[_unit(2)["id"]]), _unit(2)]
        with self.assertRaises(Phase6CurriculumRunError) as caught:
            route_curriculum_units(_curriculum(forward), {}, source_status="PASS")
        self.assertEqual("CURRICULUM_DEPENDENCY_INVALID", caught.exception.code)

        project = _unit(1, scope="PROJECT_SPECIFIC", refs=True)
        with self.assertRaises(Phase6CurriculumRunError) as caught:
            route_curriculum_units(_curriculum([project, _unit(2)]), {project["id"]: "MAYBE"}, source_status="PASS")
        self.assertEqual("READINESS_INVALID", caught.exception.code)

    def test_run_plan_binds_ten_digests_and_is_byte_deterministic(self):
        units = [_unit(1), _unit(2, origin="PREREQUISITE_PRIMER", scope="PROJECT_SPECIFIC")]
        curriculum = _curriculum(units, source_status="PARTIAL")
        authenticated = SimpleNamespace(
            graph={
                "artifact_kind": "knowledge-graph",
                "schema_version": "1.1.0",
                "derived_inputs": [{
                    "artifact_kind": "semantic-proposals",
                    "schema_version": "1.0.0",
                }],
            },
            evidence={"artifact_kind": "evidence", "schema_version": "1.2.0"},
            report={"artifact_kind": "claim-evidence", "schema_version": "1.1.0"},
            overlay={
                "artifact_kind": "claim-evidence-graph",
                "schema_version": "1.0.0",
                "input_digests": [
                    {
                        "role": "claim_candidates",
                        "artifact_kind": "claim-candidates",
                        "schema_version": "1.1.0",
                    },
                    {
                        "role": "claim_evidence",
                        "artifact_kind": "claim-evidence",
                        "schema_version": "1.1.0",
                    },
                ],
            },
            input_sha256={
                "knowledge-graph": "1" * 64,
                "evidence": "2" * 64,
                "semantic-proposals": "3" * 64,
                "claim_candidates": "4" * 64,
                "claim_evidence": "5" * 64,
                "claim_evidence_graph": "6" * 64,
            },
        )
        reads = {
            "prerequisite_candidates": SimpleNamespace(
                artifact={"artifact_kind": "prerequisite-candidates", "schema_version": "1.0.0"},
                sha256="7" * 64,
            ),
            "prerequisite_graph": SimpleNamespace(
                artifact={"artifact_kind": "prerequisite-graph", "schema_version": "1.0.0"},
                sha256="8" * 64,
            ),
            "curriculum_candidates": SimpleNamespace(
                artifact={"artifact_kind": "curriculum-candidates", "schema_version": "1.0.0"},
                sha256="9" * 64,
            ),
            "curriculum": SimpleNamespace(
                artifact={"artifact_kind": "curriculum", "schema_version": "1.1.0"},
                sha256="a" * 64,
            ),
        }

        plan = build_curriculum_run_plan(authenticated, reads, {}, curriculum, {})
        repeat = build_curriculum_run_plan(authenticated, reads, {}, curriculum, {})

        self.assertEqual(dumps_artifact(plan), dumps_artifact(repeat))
        self.assertEqual(10, len(plan["input_digests"]))
        self.assertEqual(sorted(item["role"] for item in plan["input_digests"]), [item["role"] for item in plan["input_digests"]])
        self.assertEqual(2, plan["unit_count"])
        self.assertEqual(units, [row["unit_identity"] for row in plan["units"]])
        self.assertEqual(curriculum["generated_at"], plan["generated_at"])
        self.assertEqual("PARTIAL", plan["source_status"])
        self.assertEqual(3, plan["unknown_files"])
        expected_id = "CURRICULUM-RUN-PLAN-" + canonical_tuple_sha256((
            curriculum["repository_revision"], curriculum["snapshot_kind"],
            curriculum["source_run_manifest_sha256"], reads["curriculum"].sha256,
            plan["input_digests"], [unit["id"] for unit in units],
        ))
        self.assertEqual(expected_id, plan["run_plan_id"])


if __name__ == "__main__":
    unittest.main()
