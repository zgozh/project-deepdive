#!/usr/bin/env python3
"""Focused contract tests for the documentation-bearing Phase 4E route."""

from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase4_graph import _run_documentation_package  # noqa: E402
from test_phase4_proposals import _proposal_id  # noqa: E402


CLAIM_TEXT = (
    "At the pinned revision, README.md describes Ragent as a Java AI application "
    "platform covering the path from document ingestion to intelligent Q&A."
)
SOURCE_LIMITS = [
    "SUPPORTED confirms referential coverage only; it does not establish semantic entailment.",
    "E2 repository-documentation evidence supports only what the cited document declares; it does not establish implementation or runtime behavior.",
]


def _claim_id(graph, text):
    snapshot_key = canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))
    return "CLAIM-" + canonical_tuple_sha256(("claim", snapshot_key, "project_fact", text.strip()))


def _documentation_case(testcase):
    fixture, run_dir, _manifest, _documentation = _run_documentation_package(
        testcase,
        {"README.md": b"# Ragent\nRagent is a Java AI application platform for intelligent Q&A.\n"},
        [("README.md", 2, 2)],
    )
    graph_api = __import__("phase4_graph")
    graph, evidence = graph_api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
    phase4a = fixture["work"] / "phase4a-documentation"
    phase4a.mkdir()
    graph_path = phase4a / "knowledge-graph.json"
    evidence_path = phase4a / "evidence.json"
    graph_path.write_text(dumps_artifact(graph), encoding="utf-8", newline="\n")
    evidence_path.write_text(dumps_artifact(evidence), encoding="utf-8", newline="\n")

    proposal_payload = {
        "category": "concept",
        "kind": "node",
        "label": "A pending documentation test concept",
        "node_type": "Concept",
    }
    proposal = {"id": _proposal_id(graph, proposal_payload), **proposal_payload}
    proposals = {
        "artifact_kind": "semantic-proposals",
        "schema_version": "1.1.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": sorted([
            {
                "path": relative_path,
                "artifact_kind": kind,
                "schema_version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path, relative_path, kind, version in (
                (evidence_path, "phase4a/evidence.json", "evidence", "1.4.0"),
                (graph_path, "phase4a/knowledge-graph.json", "knowledge-graph", "1.2.0"),
            )
        ], key=lambda row: (row["artifact_kind"], row["path"])),
        "proposals": [proposal],
    }
    proposals_path = fixture["work"] / "semantic-proposals-v1.1.json"
    proposals_path.write_text(dumps_artifact(proposals), encoding="utf-8", newline="\n")
    package = fixture["work"] / "phase4c-documentation-package"
    __import__("phase4_proposals").import_semantic_proposals(
        proposals_path,
        graph_path=graph_path,
        evidence_path=evidence_path,
        run_dir=run_dir,
        root=fixture["root"],
        out=package,
    )

    package_graph = load_artifact(package / "knowledge-graph.json")
    package_evidence = load_artifact(package / "evidence.json")
    readme_node = next(
        row for row in package_graph["nodes"]
        if row["type"] == "File" and row["properties"].get("path") == "README.md"
    )
    readme_evidence = next(
        row for row in package_evidence["items"]
        if row["kind"] == "repository_documentation"
    )
    candidate = {
        "artifact_kind": "claim-candidates",
        "schema_version": "1.2.0",
        "repository_revision": package_graph["repository_revision"],
        "generated_at": package_graph["generated_at"],
        "snapshot_kind": package_graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(package_graph["source_metadata"]),
        "source_run": copy.deepcopy(package_graph["source_run"]),
        "phase4_inputs": sorted([
            {
                "path": path.name,
                "artifact_kind": kind,
                "schema_version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path, kind, version in (
                (package / "evidence.json", "evidence", "1.4.0"),
                (package / "knowledge-graph.json", "knowledge-graph", "1.2.0"),
                (package / "semantic-proposals.json", "semantic-proposals", "1.1.0"),
            )
        ], key=lambda row: (row["artifact_kind"], row["path"])),
        "claims": [{
            "id": _claim_id(package_graph, CLAIM_TEXT),
            "text": CLAIM_TEXT,
            "category": "project_fact",
            "critical": True,
            "evidence_ids": [readme_evidence["id"]],
            "graph_node_ids": [readme_node["id"]],
            "graph_edge_ids": [],
            "citations": [{"kind": "file", "path": "README.md"}],
        }],
    }
    claims_path = fixture["work"] / "claim-candidates-v1.2.json"
    claims_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
    return {
        "fixture": fixture,
        "run_dir": run_dir,
        "package": package,
        "claims_path": claims_path,
        "candidate": candidate,
        "readme_evidence": readme_evidence,
        "readme_node": readme_node,
    }


def _run_claim_cli(context, output):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "audit_claim_evidence_4c.py"),
            "--claims", str(context["claims_path"]),
            "--package", str(context["package"]),
            "--run-dir", str(context["run_dir"]),
            "--root", str(context["fixture"]["root"]),
            "--out", str(output),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_overlay_cli(context, report_path, output):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "build_claim_evidence_graph.py"),
            "--phase4c-package", str(context["package"]),
            "--claims", str(context["claims_path"]),
            "--audit-report", str(report_path),
            "--run-dir", str(context["run_dir"]),
            "--root", str(context["fixture"]["root"]),
            "--out", str(output),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class DocumentationPhase4ClaimTests(unittest.TestCase):
    def test_documentation_pair_audits_readme_declaration_and_builds_limited_overlay(self):
        context = _documentation_case(self)
        report_path = context["fixture"]["work"] / "claim-evidence-v1.2.json"

        result = _run_claim_cli(context, report_path)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = load_artifact(report_path)
        self.assertEqual("1.2.0", report["schema_version"])
        self.assertEqual("PASS", report["audit_status"])
        self.assertEqual("SUPPORTED", report["claims"][0]["disposition"])
        self.assertEqual(["E2"], report["claims"][0]["resolved_evidence_levels"])
        self.assertEqual(SOURCE_LIMITS, report["source_limits"])
        citations = report["claims"][0]["citation_results"]
        self.assertTrue(all(row["resolved"] for row in citations))
        self.assertIn(
            {"kind": "evidence", "value": context["readme_evidence"]["id"], "resolved": True, "reason_code": "OK"},
            citations,
        )

        overlay_path = context["fixture"]["work"] / "claim-evidence-graph-v1.1.json"
        overlay_result = _run_overlay_cli(context, report_path, overlay_path)
        self.assertEqual(0, overlay_result.returncode, overlay_result.stdout + overlay_result.stderr)
        overlay = load_artifact(overlay_path)
        self.assertEqual("1.1.0", overlay["schema_version"])
        self.assertEqual(SOURCE_LIMITS, overlay["source_limits"])
        self.assertEqual(
            {"base_graph", "base_evidence", "semantic_proposals", "claim_candidates", "claim_evidence"},
            {row["role"] for row in overlay["input_digests"]},
        )

    def test_documentation_route_rejects_mixed_pair_and_replayed_graph_spoof(self):
        context = _documentation_case(self)
        candidate = context["candidate"]
        evidence_record = next(row for row in candidate["phase4_inputs"] if row["artifact_kind"] == "evidence")
        evidence_record["schema_version"] = "1.2.0"
        context["claims_path"].write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
        mixed_report = context["fixture"]["work"] / "mixed-pair-report.json"

        mixed = _run_claim_cli(context, mixed_report)

        self.assertEqual(2, mixed.returncode, mixed.stdout + mixed.stderr)
        self.assertFalse(mixed_report.exists())

        evidence_record["schema_version"] = "1.4.0"
        graph_path = context["package"] / "knowledge-graph.json"
        graph = load_artifact(graph_path)
        graph["nodes"][0]["label"] = "tampered graph label"
        graph_path.write_text(dumps_artifact(graph), encoding="utf-8", newline="\n")
        graph_record = next(row for row in candidate["phase4_inputs"] if row["artifact_kind"] == "knowledge-graph")
        graph_record["sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        context["claims_path"].write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
        spoof_report = context["fixture"]["work"] / "spoof-report.json"

        spoof = _run_claim_cli(context, spoof_report)

        self.assertEqual(2, spoof.returncode, spoof.stdout + spoof.stderr)
        self.assertIn("PROVENANCE_MISMATCH", spoof.stdout + spoof.stderr)
        self.assertFalse(spoof_report.exists())


if __name__ == "__main__":
    unittest.main()
