"""Behavioral tests for the compact authenticated Claim/Evidence overlay."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import ArtifactValidationError  # noqa: E402
from phase4_claim_graph import (  # noqa: E402
    Phase4ClaimGraphError,
    project_claim_evidence_graph_4c,
    validate_claim_evidence_graph_4c,
)


def _inputs():
    revision = "a" * 40
    metadata = {
        "snapshot_kind": "worktree",
        "g01_status": "PARTIAL",
        "unknown_files": 1,
        "project_index_sha256": "b" * 64,
        "coverage_sha256": "c" * 64,
    }
    source_run = {
        "artifact_kind": "phase3-run",
        "schema_version": "1.0.0",
        "manifest_sha256": "d" * 64,
        "status": "PARTIAL",
        "members": [
            {"path": "phase2/project-index.json", "artifact_kind": "project-index", "schema_version": "1.1.0", "sha256": "e" * 64},
            {"path": "phase2/coverage.json", "artifact_kind": "coverage", "schema_version": "1.1.0", "sha256": "f" * 64},
            {"path": "phase3a/stack-profile.json", "artifact_kind": "stack-profile", "schema_version": "1.1.0", "sha256": "0" * 64},
            {"path": "phase3a/evidence.json", "artifact_kind": "evidence", "schema_version": "1.1.0", "sha256": "1" * 64},
        ],
    }
    graph = {
        "artifact_kind": "knowledge-graph",
        "schema_version": "1.1.0",
        "repository_revision": revision,
        "generated_at": "2026-09-27T00:00:00Z",
        "status": "PARTIAL",
        "snapshot_kind": "worktree",
        "source_metadata": metadata,
        "source_run": source_run,
    }
    claim1 = "CLAIM-" + "1" * 64
    claim2 = "CLAIM-" + "2" * 64
    e1 = "EVID-source-1"
    e6 = "EVID-proposal-1"
    missing = "EVID-does-not-exist"
    report = {
        "artifact_kind": "claim-evidence",
        "schema_version": "1.1.0",
        "repository_revision": revision,
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": metadata,
        "source_run": source_run,
        "phase4_inputs": [
            {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "2" * 64},
            {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "3" * 64},
            {"path": "semantic-proposals.json", "artifact_kind": "semantic-proposals", "schema_version": "1.0.0", "sha256": "6" * 64},
        ],
        "audit_status": "FAIL",
        "claims": [
            {
                "id": claim1,
                "text": "A source-backed claim includes a provisional citation.",
                "category": "architecture",
                "critical": False,
                "evidence_ids": [e1, e6],
                "graph_node_ids": [],
                "graph_edge_ids": [],
                "citations": [],
                "resolved_evidence_levels": ["E1", "E6"],
                "citation_results": [
                    {"kind": "evidence", "value": e1, "resolved": True, "reason_code": "OK"},
                    {"kind": "evidence", "value": e6, "resolved": True, "reason_code": "OK"},
                ],
                "disposition": "INFERENCE",
                "reason_codes": ["E6_INFERENCE"],
            },
            {
                "id": claim2,
                "text": "A critical claim retains an unresolved citation.",
                "category": "project_fact",
                "critical": True,
                "evidence_ids": [e1, missing],
                "graph_node_ids": [],
                "graph_edge_ids": [],
                "citations": [],
                "resolved_evidence_levels": ["E1"],
                "citation_results": [
                    {"kind": "evidence", "value": e1, "resolved": True, "reason_code": "OK"},
                    {"kind": "evidence", "value": missing, "resolved": False, "reason_code": "EVIDENCE_NOT_FOUND"},
                ],
                "disposition": "UNVERIFIED",
                "reason_codes": ["EVIDENCE_NOT_FOUND"],
            },
        ],
    }
    evidence_by_id = {
        e1: {"id": e1, "level": "E1", "summary": "must not be copied", "locator": {"path": "src/private.py"}},
        e6: {"id": e6, "level": "E6", "summary": "proposal text must not be copied", "locator": {}},
    }
    input_digests = [
        {"role": "base_evidence", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "2" * 64},
        {"role": "base_graph", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "3" * 64},
        {"role": "claim_candidates", "artifact_kind": "claim-candidates", "schema_version": "1.1.0", "sha256": "4" * 64},
        {"role": "claim_evidence", "artifact_kind": "claim-evidence", "schema_version": "1.1.0", "sha256": "5" * 64},
        {"role": "semantic_proposals", "artifact_kind": "semantic-proposals", "schema_version": "1.0.0", "sha256": "6" * 64},
    ]
    return graph, evidence_by_id, report, input_digests, (claim1, claim2, e1, e6, missing)


class Phase4ClaimGraphTests(unittest.TestCase):
    def test_overlay_keeps_fail_inventory_and_only_resolved_cited_evidence(self):
        graph, evidence_by_id, report, digests, ids = _inputs()
        claim1, claim2, e1, e6, missing = ids

        overlay = project_claim_evidence_graph_4c(graph, evidence_by_id, report, digests)

        self.assertEqual("FAIL", overlay["audit_status"])
        self.assertEqual([claim1, claim2, e6, e1], [node["id"] for node in overlay["nodes"]])
        self.assertEqual(
            {claim1: [e1, e6], claim2: [e1, missing]},
            {node["id"]: node["properties"].get("cited_evidence_ids") for node in overlay["nodes"] if node["type"] == "Claim"},
        )
        self.assertEqual(
            {e1: {"level": "E1", "provisional": False}, e6: {"level": "E6", "provisional": True}},
            {node["id"]: node["properties"] for node in overlay["nodes"] if node["type"] == "Evidence"},
        )
        self.assertEqual(
            [
                {"id": "CLAIM-EVIDENCE-01f6c07f82ff18f688205544201396324d8f9a857d1e3c3f34dd1a3a46f51f34", "type": "EVIDENCED_BY", "from": claim1, "to": e1},
                {"id": "CLAIM-EVIDENCE-5a29e75a26556b9958826ef02d2a11634c600378aa79a599f8b662497da9efac", "type": "EVIDENCED_BY", "from": claim2, "to": e1},
                {"id": "CLAIM-EVIDENCE-c7ecc9be83cd34a815de3104ae13552c6b8c0b5a1d4c01bb6974696b082f0aa2", "type": "EVIDENCED_BY", "from": claim1, "to": e6},
            ],
            overlay["edges"],
        )
        self.assertIs(overlay, validate_claim_evidence_graph_4c(overlay, graph, evidence_by_id, report, digests))

    def test_semantic_validator_rejects_unreferenced_evidence_or_dangling_edge(self):
        graph, evidence_by_id, report, digests, _ = _inputs()
        overlay = project_claim_evidence_graph_4c(graph, evidence_by_id, report, digests)

        extra_node = copy.deepcopy(overlay)
        extra_node["nodes"].append({
            "id": "EVID-unreferenced",
            "type": "Evidence",
            "label": "E1",
            "properties": {"level": "E1", "provisional": False},
        })
        with self.assertRaises((ArtifactValidationError, ValueError)):
            validate_claim_evidence_graph_4c(extra_node, graph, evidence_by_id, report, digests)

        dangling_edge = copy.deepcopy(overlay)
        dangling_edge["edges"][0]["to"] = "EVID-does-not-exist"
        with self.assertRaises((ArtifactValidationError, ValueError)):
            validate_claim_evidence_graph_4c(dangling_edge, graph, evidence_by_id, report, digests)

    def test_projection_rejects_digest_role_with_wrong_artifact_version(self):
        graph, evidence_by_id, report, digests, _ = _inputs()
        wrong_digests = copy.deepcopy(digests)
        wrong_digests[1]["schema_version"] = "1.2.0"

        with self.assertRaises(Phase4ClaimGraphError):
            project_claim_evidence_graph_4c(graph, evidence_by_id, report, wrong_digests)

    def test_projection_rejects_report_digest_that_disagrees_with_overlay_digest(self):
        graph, evidence_by_id, report, digests, _ = _inputs()
        report["phase4_inputs"][0]["sha256"] = "f" * 64

        with self.assertRaises(Phase4ClaimGraphError):
            project_claim_evidence_graph_4c(graph, evidence_by_id, report, digests)


if __name__ == "__main__":
    unittest.main()
