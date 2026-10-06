#!/usr/bin/env python3
"""Focused tests for source-backed Phase 4B claim auditing."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unicodedata
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from test_phase4_graph import _run_package  # noqa: E402


CLI = SCRIPTS / "audit_claim_evidence.py"


def _canonical_tuple_sha256(parts):
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _claim_id(graph, category, text):
    snapshot_key = _canonical_tuple_sha256((
        graph["repository_revision"],
        graph["snapshot_kind"],
        graph["source_metadata"],
    ))
    normalized = unicodedata.normalize("NFC", text.strip())
    return "CLAIM-" + _canonical_tuple_sha256(("claim", snapshot_key, category, normalized))


def _claim(
    graph,
    *,
    text,
    critical,
    evidence_ids,
    node_id,
    edge_id,
    file_path,
    symbol_id,
    graph_node_ids=None,
    graph_edge_ids=None,
    citations=None,
):
    category = "project_fact"
    return {
        "id": _claim_id(graph, category, text),
        "text": text,
        "category": category,
        "critical": critical,
        "evidence_ids": evidence_ids,
        "graph_node_ids": [node_id] if graph_node_ids is None else graph_node_ids,
        "graph_edge_ids": [edge_id] if graph_edge_ids is None else graph_edge_ids,
        "citations": citations if citations is not None else [
            {"kind": "file", "path": file_path},
            {"kind": "symbol", "id": symbol_id},
        ],
    }


def _prepare(testcase, claims):
    fixture, run_dir, manifest = _run_package(
        testcase,
        {"src/module.py": b"def main():\n    return 1\n"},
    )
    graph_api = __import__("phase4_graph")
    pair_dir = fixture["work"] / "phase4-pair"
    graph_api.build_phase4_graph(run_dir, root=fixture["root"], out=pair_dir)
    graph_path = pair_dir / "knowledge-graph.json"
    evidence_path = pair_dir / "evidence.json"
    graph = load_artifact(graph_path)
    evidence = load_artifact(evidence_path)

    symbol = next(node for node in graph["nodes"] if node["type"] == "Symbol")
    file_node = next(node for node in graph["nodes"] if node["type"] == "File")
    edge = next(edge for edge in graph["edges"] if edge["type"] == "CONTAINS" and edge["to"] == symbol["id"])
    evidence_id = evidence["items"][0]["id"]
    ready_claims = [
        _claim(
            graph,
            text=spec["text"],
            critical=spec["critical"],
            evidence_ids=spec.get("evidence_ids", [evidence_id]),
            node_id=spec.get("node_id", symbol["id"]),
            edge_id=spec.get("edge_id", edge["id"]),
            file_path=spec.get("file_path", file_node["properties"]["path"]),
            symbol_id=spec.get("symbol_id", symbol["id"]),
            graph_node_ids=spec.get("graph_node_ids"),
            graph_edge_ids=spec.get("graph_edge_ids"),
            citations=spec.get("citations"),
        )
        for spec in claims
    ]
    candidate_path = fixture["work"] / "claim-candidates.json"
    phase4_inputs = [
        {
            "path": "phase4-pair/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.2.0",
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        },
        {
            "path": "phase4-pair/knowledge-graph.json",
            "artifact_kind": "knowledge-graph",
            "schema_version": "1.1.0",
            "sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        },
    ]
    candidate = {
        "artifact_kind": "claim-candidates",
        "schema_version": "1.0.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": phase4_inputs,
        "claims": ready_claims,
    }
    candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
    report_path = fixture["work"] / "claim-evidence.json"
    return fixture, run_dir, graph_path, evidence_path, candidate_path, report_path, graph, evidence, candidate


def _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path):
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--claims", str(candidate_path),
            "--graph", str(graph_path),
            "--evidence", str(evidence_path),
            "--run-dir", str(run_dir),
            "--root", str(fixture["root"]),
            "--out", str(report_path),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class Phase4ClaimEvidenceTests(unittest.TestCase):
    def test_existing_v10_cli_rejects_opt_in_v11_claim_inventory(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, candidate) = _prepare(
            self,
            [{"text": "The existing CLI remains Phase 4A-only.", "critical": True}],
        )
        candidate["schema_version"] = "1.1.0"
        candidate["phase4_inputs"].append({
            "path": "semantic-proposals.json",
            "artifact_kind": "semantic-proposals",
            "schema_version": "1.0.0",
            "sha256": "a" * 64,
        })
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=INPUT_INVALID", result.stdout)
        self.assertFalse(report_path.exists())

    def test_supported_claim_requires_replayed_evidence_and_resolved_references(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, _candidate) = _prepare(
            self,
            [{"text": "The main function is indexed.", "critical": True}],
        )

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = load_artifact(report_path)
        self.assertEqual("PASS", report["audit_status"])
        self.assertEqual("SUPPORTED", report["claims"][0]["disposition"])
        self.assertEqual(["E1"], report["claims"][0]["resolved_evidence_levels"])
        self.assertTrue(all(row["resolved"] for row in report["claims"][0]["citation_results"]))
        validate_artifact(report)

    def test_critical_claim_with_unresolved_e6_id_is_unverified_and_fails(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, candidate) = _prepare(
            self,
            [
                {"text": "A supported inventory claim.", "critical": False},
                {"text": "A critical claim with an unresolved E6 reference.", "critical": True, "evidence_ids": ["EVID-future-e6"]},
            ],
        )

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        report = load_artifact(report_path)
        self.assertEqual("FAIL", report["audit_status"])
        self.assertEqual(len(candidate["claims"]), len(report["claims"]))
        by_text = {claim["text"]: claim for claim in report["claims"]}
        unsupported = by_text["A critical claim with an unresolved E6 reference."]
        self.assertEqual("UNVERIFIED", unsupported["disposition"])
        self.assertEqual(["EVIDENCE_NOT_FOUND"], unsupported["reason_codes"])
        self.assertEqual([], unsupported["resolved_evidence_levels"])
        validate_artifact(report)

    def test_authentic_metadata_cannot_authenticate_fabricated_graph_payload(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, graph, evidence, candidate) = _prepare(
            self,
            [{"text": "A claim with authentic source identity.", "critical": True}],
        )
        graph["nodes"][0]["label"] = "fabricated but schema-valid label"
        graph_path.write_text(dumps_artifact(graph), encoding="utf-8", newline="\n")
        candidate["phase4_inputs"][1]["sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")

        graph_api = __import__("phase4_graph")
        graph_api.validate_phase4_graph_artifacts(load_artifact(graph_path), evidence)
        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PROVENANCE_MISMATCH", result.stdout + result.stderr)
        self.assertFalse(report_path.exists(), "invalid source provenance must fail closed without a report")

    def test_injected_e6_payload_is_not_authenticated_by_updated_input_digest(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, evidence, candidate) = _prepare(
            self,
            [{"text": "A fabricated inference must not be supported.", "critical": True}],
        )
        evidence["items"].append({
            "id": "EVID-fabricated-e6",
            "level": "E6",
            "kind": "inference",
            "summary": "fabricated inference",
            "confidence": 0.5,
            "locator": {"observation": "unverified proposal"},
            "source_members": ["phase3a/evidence.json"],
        })
        evidence_path.write_text(dumps_artifact(evidence), encoding="utf-8", newline="\n")
        candidate["phase4_inputs"] = [
            {**record, "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest()}
            if record["artifact_kind"] == "evidence" else record
            for record in candidate["phase4_inputs"]
        ]
        candidate["claims"][0]["evidence_ids"] = ["EVID-fabricated-e6"]
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")

        graph_api = __import__("phase4_graph")
        graph_api.validate_phase4_graph_artifacts(load_artifact(graph_path), load_artifact(evidence_path))
        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PROVENANCE_MISMATCH", result.stdout + result.stderr)
        self.assertFalse(report_path.exists())

    def test_missing_file_symbol_node_and_edge_references_fail_the_full_matrix(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, _candidate) = _prepare(
            self,
            [{
                "text": "A claim with invalid citations.",
                "critical": False,
                "graph_node_ids": ["SYM-not-present"],
                "graph_edge_ids": ["REL-not-present"],
                "citations": [
                    {"kind": "file", "path": "src/missing.py"},
                    {"kind": "symbol", "id": "SYM-not-present"},
                ],
            }],
        )

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        report = load_artifact(report_path)
        self.assertEqual("FAIL", report["audit_status"])
        claim = report["claims"][0]
        self.assertEqual("UNVERIFIED", claim["disposition"])
        self.assertEqual(
            ["FILE_NOT_FOUND", "GRAPH_EDGE_NOT_FOUND", "GRAPH_NODE_NOT_FOUND", "SYMBOL_NOT_FOUND"],
            claim["reason_codes"],
        )

    def test_stale_source_run_identity_fails_closed(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, candidate) = _prepare(
            self,
            [{"text": "A stale source-run identity must be rejected.", "critical": True}],
        )
        candidate["source_run"]["manifest_sha256"] = "f" * 64
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("SOURCE_IDENTITY_MISMATCH", result.stdout + result.stderr)
        self.assertFalse(report_path.exists())

    def test_declared_input_paths_and_raw_digests_are_checked_before_audit(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, candidate) = _prepare(
            self,
            [{"text": "A valid source-backed claim.", "critical": True}],
        )
        evidence_record = next(
            row for row in candidate["phase4_inputs"] if row["artifact_kind"] == "evidence"
        )
        evidence_record["path"] = "phase4-pair/knowledge-graph.json"
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
        path_report = fixture["work"] / "path-mismatch.json"
        path_result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, path_report)
        self.assertEqual(2, path_result.returncode, path_result.stdout + path_result.stderr)
        self.assertIn("INPUT_PATH_MISMATCH", path_result.stdout + path_result.stderr)
        self.assertFalse(path_report.exists())

        evidence_record["path"] = "phase4-pair/evidence.json"
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
        evidence_path.write_bytes(evidence_path.read_bytes() + b" ")
        digest_report = fixture["work"] / "digest-mismatch.json"
        digest_result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, digest_report)
        self.assertEqual(2, digest_result.returncode, digest_result.stdout + digest_result.stderr)
        self.assertIn("INPUT_DIGEST_MISMATCH", digest_result.stdout + digest_result.stderr)
        self.assertFalse(digest_report.exists())

    def test_noncritical_claim_without_evidence_is_partial_not_supported(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, _graph, _evidence, _candidate) = _prepare(
            self,
            [{
                "text": "A noncritical claim with no evidence.",
                "critical": False,
                "evidence_ids": [],
                "graph_node_ids": [],
                "graph_edge_ids": [],
                "citations": [],
            }],
        )

        result = _run_cli(fixture, run_dir, graph_path, evidence_path, candidate_path, report_path)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = load_artifact(report_path)
        self.assertEqual("PARTIAL", report["audit_status"])
        self.assertEqual("UNVERIFIED", report["claims"][0]["disposition"])
        self.assertEqual(["NO_QUALIFYING_EVIDENCE"], report["claims"][0]["reason_codes"])

    def test_output_inside_target_or_existing_output_fails_without_overwrite(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         _report_path, _graph, _evidence, _candidate) = _prepare(
            self,
            [{"text": "A valid claim for output safety checks.", "critical": True}],
        )
        inside_target = fixture["root"] / "claim-evidence.json"
        inside_result = _run_cli(
            fixture,
            run_dir,
            graph_path,
            evidence_path,
            candidate_path,
            inside_target,
        )
        self.assertEqual(2, inside_result.returncode, inside_result.stdout + inside_result.stderr)
        self.assertIn("OUTPUT_INVALID", inside_result.stdout + inside_result.stderr)
        self.assertFalse(inside_target.exists())

        existing = fixture["work"] / "caller-owned-report.json"
        existing.write_text("keep", encoding="utf-8")
        existing_result = _run_cli(
            fixture,
            run_dir,
            graph_path,
            evidence_path,
            candidate_path,
            existing,
        )
        self.assertEqual(2, existing_result.returncode, existing_result.stdout + existing_result.stderr)
        self.assertIn("OUTPUT_EXISTS", existing_result.stdout + existing_result.stderr)
        self.assertEqual("keep", existing.read_text(encoding="utf-8"))

    def test_claim_ids_normalize_nfc_and_trim_and_matrix_order_is_stable(self):
        (fixture, run_dir, graph_path, evidence_path, candidate_path,
         report_path, graph, _evidence, candidate) = _prepare(
            self,
            [
                {"text": "  Cafe\u0301 function is indexed.  ", "critical": False},
                {"text": "  Cafe\u0301 function is indexed.  ", "critical": False},
                {"text": "The module has an indexed function.", "critical": False},
            ],
        )
        expected_id = _claim_id(graph, "project_fact", "  Cafe\u0301 function is indexed.  ")
        self.assertEqual(expected_id, candidate["claims"][0]["id"])

        api = __import__("phase4_claim_evidence")
        first = api.audit_claim_candidates(
            candidate_path,
            graph_path=graph_path,
            evidence_path=evidence_path,
            run_dir=run_dir,
            root=fixture["root"],
        )
        candidate["claims"].reverse()
        candidate["phase4_inputs"].reverse()
        candidate_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
        second = api.audit_claim_candidates(
            candidate_path,
            graph_path=graph_path,
            evidence_path=evidence_path,
            run_dir=run_dir,
            root=fixture["root"],
        )

        self.assertEqual(first, second)
        self.assertEqual(2, len(first["claims"]))
        self.assertEqual(sorted(row["id"] for row in first["claims"]), [row["id"] for row in first["claims"]])
        normalized_claim = next(row for row in first["claims"] if row["id"] == expected_id)
        self.assertEqual("  Cafe\u0301 function is indexed.  ", normalized_claim["text"])


if __name__ == "__main__":
    unittest.main()
