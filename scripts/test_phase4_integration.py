#!/usr/bin/env python3
"""Focused Phase 4A-to-4B-to-4C command-line integration contract."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import unicodedata
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase4_graph import _run_package  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_records(pair_dir: Path) -> list[dict[str, str]]:
    return [
        {
            "path": f"{pair_dir.name}/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.2.0",
            "sha256": _sha256(pair_dir / "evidence.json"),
        },
        {
            "path": f"{pair_dir.name}/knowledge-graph.json",
            "artifact_kind": "knowledge-graph",
            "schema_version": "1.1.0",
            "sha256": _sha256(pair_dir / "knowledge-graph.json"),
        },
    ]


def _assert_cli_valid(testcase: unittest.TestCase, path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_artifact.py"), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    testcase.assertEqual(0, result.returncode, result.stdout + result.stderr)


class Phase4IntegrationTests(unittest.TestCase):
    def test_source_backed_4a_4b_4c_flow_and_extended_pair_rejection(self):
        fixture, run_dir, _manifest = _run_package(self, {
            "src/module.py": b"def main():\n    return 1\n",
            "unclassified.oddity": b"unknown file\n",
        })
        root = fixture["root"]
        work = fixture["work"]
        phase4a_dir = work / "phase4a"

        build = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "build_phase4_graph.py"),
                "--run-dir", str(run_dir),
                "--root", str(root),
                "--out", str(phase4a_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, build.returncode, build.stdout + build.stderr)
        graph_path = phase4a_dir / "knowledge-graph.json"
        evidence_path = phase4a_dir / "evidence.json"
        graph = load_artifact(graph_path)
        evidence = load_artifact(evidence_path)
        validate_artifact(graph)
        validate_artifact(evidence)
        _assert_cli_valid(self, graph_path)
        _assert_cli_valid(self, evidence_path)
        self.assertEqual(("knowledge-graph", "1.1.0"), (graph["artifact_kind"], graph["schema_version"]))
        self.assertEqual(("evidence", "1.2.0"), (evidence["artifact_kind"], evidence["schema_version"]))
        self.assertEqual("PARTIAL", graph["status"])
        self.assertEqual(graph["status"], evidence["status"])
        self.assertTrue(all(item["level"] in {"E1", "E2"} for item in evidence["items"]))

        symbol = next(node for node in graph["nodes"] if node["type"] == "Symbol")
        symbol_path = symbol["properties"]["source_record"]["path"]
        file_node = next(
            node for node in graph["nodes"]
            if node["type"] == "File" and node["properties"]["path"] == symbol_path
        )
        contains_edge = next(
            edge for edge in graph["edges"]
            if edge["type"] == "CONTAINS" and edge["to"] == symbol["id"]
        )
        evidence_item = next(item for item in evidence["items"] if item["level"] in {"E1", "E2"})
        claim_text = "The sample snapshot contains a function symbol."
        category = "project_fact"
        snapshot_key = canonical_tuple_sha256((
            graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
        ))
        normalized_text = unicodedata.normalize("NFC", claim_text.strip())
        claim = {
            "id": "CLAIM-" + canonical_tuple_sha256(("claim", snapshot_key, category, normalized_text)),
            "text": claim_text,
            "category": category,
            "critical": True,
            "evidence_ids": [evidence_item["id"]],
            "graph_node_ids": [symbol["id"]],
            "graph_edge_ids": [contains_edge["id"]],
            "citations": [
                {"kind": "file", "path": file_node["properties"]["path"]},
                {"kind": "symbol", "id": symbol["id"]},
            ],
        }
        candidates_path = work / "claim-candidates.json"
        candidates = {
            "artifact_kind": "claim-candidates",
            "schema_version": "1.0.0",
            "repository_revision": graph["repository_revision"],
            "generated_at": graph["generated_at"],
            "snapshot_kind": graph["snapshot_kind"],
            "source_metadata": graph["source_metadata"],
            "source_run": graph["source_run"],
            "phase4_inputs": _input_records(phase4a_dir),
            "claims": [claim],
        }
        candidates_path.write_text(dumps_artifact(candidates), encoding="utf-8", newline="\n")
        claim_report_path = work / "claim-evidence.json"
        audited = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "audit_claim_evidence.py"),
                "--claims", str(candidates_path),
                "--graph", str(graph_path),
                "--evidence", str(evidence_path),
                "--run-dir", str(run_dir),
                "--root", str(root),
                "--out", str(claim_report_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, audited.returncode, audited.stdout + audited.stderr)
        report = load_artifact(claim_report_path)
        validate_artifact(report)
        _assert_cli_valid(self, claim_report_path)
        self.assertEqual("claim-evidence", report["artifact_kind"])
        self.assertEqual("1.0.0", report["schema_version"])
        self.assertEqual("PASS", report["audit_status"])
        self.assertEqual("SUPPORTED", report["claims"][0]["disposition"])

        proposal_payload = {
            "category": "concept",
            "kind": "node",
            "label": "Integration provisional concept",
            "node_type": "Concept",
        }
        proposal_id = "PROP-" + canonical_tuple_sha256(("proposal", snapshot_key, proposal_payload))
        proposals_path = work / "semantic-proposals.json"
        proposals_path.write_text(dumps_artifact({
            "artifact_kind": "semantic-proposals",
            "schema_version": "1.0.0",
            "repository_revision": graph["repository_revision"],
            "generated_at": graph["generated_at"],
            "snapshot_kind": graph["snapshot_kind"],
            "source_metadata": graph["source_metadata"],
            "source_run": graph["source_run"],
            "phase4_inputs": _input_records(phase4a_dir),
            "proposals": [{"id": proposal_id, **proposal_payload}],
        }), encoding="utf-8", newline="\n")
        phase4c_dir = work / "phase4c"
        imported = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "import_semantic_proposals.py"),
                "--proposals", str(proposals_path),
                "--graph", str(graph_path),
                "--evidence", str(evidence_path),
                "--run-dir", str(run_dir),
                "--root", str(root),
                "--out", str(phase4c_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, imported.returncode, imported.stdout + imported.stderr)
        self.assertEqual(
            {"evidence.json", "knowledge-graph.json", "semantic-proposals.json"},
            {path.name for path in phase4c_dir.iterdir()},
        )
        extended_graph = load_artifact(phase4c_dir / "knowledge-graph.json")
        extended_evidence = load_artifact(phase4c_dir / "evidence.json")
        copied_proposals = load_artifact(phase4c_dir / "semantic-proposals.json")
        for artifact in (extended_graph, extended_evidence, copied_proposals):
            validate_artifact(artifact)
        for artifact_path in (
            phase4c_dir / "knowledge-graph.json",
            phase4c_dir / "evidence.json",
            phase4c_dir / "semantic-proposals.json",
        ):
            _assert_cli_valid(self, artifact_path)
        self.assertEqual("PARTIAL", extended_graph["status"])
        self.assertEqual("PARTIAL", extended_evidence["status"])
        self.assertIn(proposal_id, {node["id"] for node in extended_graph["nodes"]})
        self.assertEqual(["E6"], [item["level"] for item in extended_evidence["items"] if item["level"] == "E6"])
        self.assertTrue(all(item["level"] != "E6" for item in evidence["items"]))

        rejected_report = work / "extended-claim-evidence.json"
        rejected = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "audit_claim_evidence.py"),
                "--claims", str(candidates_path),
                "--graph", str(phase4c_dir / "knowledge-graph.json"),
                "--evidence", str(phase4c_dir / "evidence.json"),
                "--run-dir", str(run_dir),
                "--root", str(root),
                "--out", str(rejected_report),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertFalse(rejected_report.exists())


if __name__ == "__main__":
    unittest.main()
