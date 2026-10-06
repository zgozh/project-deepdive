#!/usr/bin/env python3
"""Focused tests for source-backed Phase 4C semantic proposal import."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from test_phase4_graph import _run_package  # noqa: E402


CLI = SCRIPTS / "import_semantic_proposals.py"


def _tuple_sha256(parts):
    payload = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _proposal_id(graph, payload):
    snapshot_key = _tuple_sha256((
        graph["repository_revision"],
        graph["snapshot_kind"],
        graph["source_metadata"],
    ))
    return "PROP-" + _tuple_sha256(("proposal", snapshot_key, payload))


def _prepare(testcase, *, source_files=None):
    fixture, run_dir, _manifest = _run_package(
        testcase,
        source_files or {"src/module.py": b"def main():\n    return 1\n"},
    )
    graph_api = importlib.import_module("phase4_graph")
    pair_dir = fixture["work"] / "phase4a"
    graph_api.build_phase4_graph(run_dir, root=fixture["root"], out=pair_dir)
    graph_path = pair_dir / "knowledge-graph.json"
    evidence_path = pair_dir / "evidence.json"
    graph = load_artifact(graph_path)
    evidence = load_artifact(evidence_path)
    base_payload = {
        "category": "concept",
        "kind": "node",
        "label": "A provisional concept",
        "node_type": "Concept",
    }
    proposal = {"id": _proposal_id(graph, base_payload), **base_payload}
    input_records = [
        {
            "path": "phase4a/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.2.0",
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        },
        {
            "path": "phase4a/knowledge-graph.json",
            "artifact_kind": "knowledge-graph",
            "schema_version": "1.1.0",
            "sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        },
    ]
    candidate = {
        "artifact_kind": "semantic-proposals",
        "schema_version": "1.0.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": input_records,
        "proposals": [proposal],
    }
    proposals_path = fixture["work"] / "semantic-proposals.json"
    proposals_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
    return {
        "fixture": fixture,
        "run_dir": run_dir,
        "graph_path": graph_path,
        "evidence_path": evidence_path,
        "graph": graph,
        "evidence": evidence,
        "candidate": candidate,
        "proposals_path": proposals_path,
        "output": fixture["work"] / "phase4c-output",
    }


def _write_candidate(context):
    context["proposals_path"].write_text(
        dumps_artifact(context["candidate"]),
        encoding="utf-8",
        newline="\n",
    )


def _add_proposal(context, payload):
    proposal = {"id": _proposal_id(context["graph"], payload), **payload}
    context["candidate"]["proposals"].append(proposal)
    _write_candidate(context)
    return proposal


def _importer(testcase):
    try:
        module = importlib.import_module("phase4_proposals")
    except ModuleNotFoundError:
        testcase.fail("Phase 4C proposal importer is missing")
    testcase.assertTrue(hasattr(module, "import_semantic_proposals"))
    testcase.assertTrue(hasattr(module, "Phase4ProposalError"))
    return module


def _run_import(context, output=None, *, root=None):
    module = _importer(unittest.TestCase())
    return module.import_semantic_proposals(
        context["proposals_path"],
        graph_path=context["graph_path"],
        evidence_path=context["evidence_path"],
        run_dir=context["run_dir"],
        root=root or context["fixture"]["root"],
        out=output or context["output"],
    )


class Phase4ProposalImportTests(unittest.TestCase):
    def test_proposal_versions_bind_exact_phase4_pairs_and_documentation_run(self):
        context = _prepare(self)
        module = _importer(self)
        candidate = copy.deepcopy(context["candidate"])
        candidate["schema_version"] = "1.1.0"
        candidate["source_run"]["schema_version"] = "1.1.0"
        candidate["source_run"]["members"].append({
            "path": "documentation/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "sha256": "d" * 64,
        })
        candidate["source_run"]["members"].sort(key=lambda row: row["path"])
        candidate["phase4_inputs"] = [
            {
                "path": "phase4a/evidence.json",
                "artifact_kind": "evidence",
                "schema_version": "1.4.0",
                "sha256": "e" * 64,
            },
            {
                "path": "phase4a/knowledge-graph.json",
                "artifact_kind": "knowledge-graph",
                "schema_version": "1.2.0",
                "sha256": "f" * 64,
            },
        ]

        validate_artifact(candidate)
        accepted = module._input_records(candidate)
        self.assertEqual("1.2.0", accepted["knowledge-graph"]["schema_version"])
        self.assertEqual("1.4.0", accepted["evidence"]["schema_version"])

        mixed = copy.deepcopy(context["candidate"])
        next(row for row in mixed["phase4_inputs"] if row["artifact_kind"] == "evidence")["schema_version"] = "1.1.0"
        validate_artifact(mixed)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            module._input_records(mixed)
        self.assertEqual("UNSUPPORTED_VERSION", raised.exception.code)

        mixed_documentation_pair = copy.deepcopy(candidate)
        next(
            row for row in mixed_documentation_pair["phase4_inputs"]
            if row["artifact_kind"] == "evidence"
        )["schema_version"] = "1.2.0"
        next(
            row for row in mixed_documentation_pair["phase4_inputs"]
            if row["artifact_kind"] == "knowledge-graph"
        )["schema_version"] = "1.4.0"
        validate_artifact(mixed_documentation_pair)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            module._input_records(mixed_documentation_pair)
        self.assertEqual("UNSUPPORTED_VERSION", raised.exception.code)

    def test_legacy_phase4_pair_rejects_new_proposal_provenance_version(self):
        context = _prepare(self)
        _run_import(context)
        graph = load_artifact(context["output"] / "knowledge-graph.json")
        evidence = load_artifact(context["output"] / "evidence.json")
        graph["derived_inputs"][0]["schema_version"] = "1.1.0"
        evidence["derived_inputs"] = copy.deepcopy(graph["derived_inputs"])

        graph_api = importlib.import_module("phase4_graph")
        with self.assertRaises(graph_api.Phase4GraphError):
            graph_api.validate_phase4_graph_artifacts(graph, evidence)

    def test_nonpublishing_replay_matches_imported_extension(self):
        context = _prepare(self)
        module = _importer(self)
        self.assertTrue(
            hasattr(module, "reproject_phase4c_artifacts"),
            "the source-backed non-publishing Phase 4C replay API is required",
        )

        graph, evidence = module.reproject_phase4c_artifacts(
            context["proposals_path"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )

        self.assertFalse(context["output"].exists())
        _run_import(context)
        self.assertEqual(graph, load_artifact(context["output"] / "knowledge-graph.json"))
        self.assertEqual(evidence, load_artifact(context["output"] / "evidence.json"))

    def test_nonpublishing_replay_rejects_phase4a_input_digest_spoof(self):
        context = _prepare(self)
        module = _importer(self)
        self.assertTrue(
            hasattr(module, "reproject_phase4c_artifacts"),
            "the source-backed non-publishing Phase 4C replay API is required",
        )
        record = next(
            row for row in context["candidate"]["phase4_inputs"]
            if row["artifact_kind"] == "knowledge-graph"
        )
        record["sha256"] = "0" * 64
        _write_candidate(context)

        with self.assertRaises(module.Phase4ProposalError) as raised:
            module.reproject_phase4c_artifacts(
                context["proposals_path"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )

        self.assertEqual("INPUT_DIGEST_MISMATCH", raised.exception.code)

    def test_importer_uses_one_full_replay_and_bound_final_freshness_check(self):
        context = _prepare(self)
        module = _importer(self)
        phase3_run = importlib.import_module("phase3_run")
        expected_manifest_sha = context["graph"]["source_run"]["manifest_sha256"]

        with patch.object(module, "reproject_phase4_graph_artifacts", wraps=module.reproject_phase4_graph_artifacts) as full_replay:
            with patch.object(module, "verify_phase3_run_freshness", wraps=phase3_run.verify_phase3_run_freshness, create=True) as freshness:
                _run_import(context)

        self.assertEqual(1, full_replay.call_count)
        self.assertEqual(1, freshness.call_count)
        self.assertEqual(expected_manifest_sha, freshness.call_args.kwargs["expected_manifest_sha256"])
        self.assertEqual(
            load_artifact(context["run_dir"] / "phase3-run.json"),
            freshness.call_args.kwargs["expected_manifest"],
        )
        self.assertTrue(context["output"].is_dir())

    def test_final_freshness_and_input_mutations_fail_without_publication(self):
        cases = (
            ("missing_manifest", "SOURCE_RUN_INVALID"),
            ("malformed_manifest", "SOURCE_RUN_INVALID"),
            ("member_change", "SOURCE_RUN_INVALID"),
            ("worktree_edit", "SOURCE_RUN_INVALID"),
            ("g01_drift", "SOURCE_RUN_INVALID"),
            ("proposal_change", "INPUT_CHANGED"),
            ("graph_change", "INPUT_CHANGED"),
            ("evidence_change", "INPUT_CHANGED"),
        )
        module = _importer(self)
        for case, expected_code in cases:
            with self.subTest(case=case):
                context = _prepare(self)
                phase3_run = importlib.import_module("phase3_run")
                verify_real = getattr(phase3_run, "verify_phase3_run_freshness")
                manifest_path = context["run_dir"] / "phase3-run.json"
                manifest_bytes = manifest_path.read_bytes()
                source_path = context["fixture"]["root"] / "src/module.py"
                source_bytes = source_path.read_bytes()

                def run_final_check(run_dir, **kwargs):
                    if case == "missing_manifest":
                        manifest_path.unlink()
                    elif case == "malformed_manifest":
                        manifest_path.write_bytes(b"{")
                    elif case == "member_change":
                        member = context["run_dir"] / "phase2/project-index.json"
                        member.write_bytes(member.read_bytes() + b" ")
                    elif case == "worktree_edit":
                        source_path.write_bytes(b"edited after initial source replay\n")
                    elif case == "g01_drift":
                        audit_coverage = phase3_run.audit_coverage
                        audit_calls = 0

                        def drift_during_final_audit(*args, **audit_kwargs):
                            nonlocal audit_calls
                            result = audit_coverage(*args, **audit_kwargs)
                            audit_calls += 1
                            if audit_calls == 1:
                                source_path.write_bytes(b"edited between final G01 samples\n")
                            return result

                        with patch.object(phase3_run, "audit_coverage", side_effect=drift_during_final_audit):
                            return verify_real(run_dir, **kwargs)
                    elif case == "proposal_change":
                        context["proposals_path"].write_bytes(context["proposals_path"].read_bytes() + b" ")
                    elif case == "graph_change":
                        context["graph_path"].write_bytes(context["graph_path"].read_bytes() + b" ")
                    elif case == "evidence_change":
                        context["evidence_path"].write_bytes(context["evidence_path"].read_bytes() + b" ")
                    return verify_real(run_dir, **kwargs)

                with patch.object(module, "verify_phase3_run_freshness", side_effect=run_final_check, create=True):
                    with self.assertRaises(module.Phase4ProposalError) as raised:
                        _run_import(context)

                self.assertEqual(expected_code, raised.exception.code)
                self.assertFalse(context["output"].exists())
                if case in {"missing_manifest", "malformed_manifest"}:
                    manifest_path.write_bytes(manifest_bytes)
                if case in {"worktree_edit", "g01_drift"}:
                    source_path.write_bytes(source_bytes)

    def test_importer_publishes_source_backed_three_file_provisional_pair(self):
        context = _prepare(
            self,
            source_files={"src/module.py": b"def main():\n    return 1\n", "unclassified.oddity": b"unknown\n"},
        )
        concept = context["candidate"]["proposals"][0]
        project_id = next(node["id"] for node in context["graph"]["nodes"] if node["type"] == "Project")
        relationship = _add_proposal(context, {
            "category": "architecture",
            "kind": "edge",
            "label": "The concept is part of the project.",
            "edge_type": "PART_OF",
            "from": concept["id"],
            "to": project_id,
        })
        context["candidate"]["proposals"].append(copy.deepcopy(concept))
        _write_candidate(context)

        summary = _run_import(context)
        graph_path = context["output"] / "knowledge-graph.json"
        evidence_path = context["output"] / "evidence.json"
        proposals_copy = context["output"] / "semantic-proposals.json"
        self.assertEqual({"knowledge-graph.json", "evidence.json", "semantic-proposals.json"}, {path.name for path in context["output"].iterdir()})
        self.assertEqual("PARTIAL", summary["status"])
        self.assertEqual(2, summary["proposals"])
        self.assertEqual(context["proposals_path"].read_bytes(), proposals_copy.read_bytes())

        graph = load_artifact(graph_path)
        evidence = load_artifact(evidence_path)
        graph_api = importlib.import_module("phase4_graph")
        graph_api.validate_phase4_graph_artifacts(graph, evidence)
        self.assertEqual("PARTIAL", graph["status"])
        self.assertEqual(context["graph"]["source_run"], graph["source_run"])
        self.assertEqual(context["evidence"]["source_run"], evidence["source_run"])
        self.assertEqual(context["graph"]["source_metadata"], graph["source_metadata"])
        self.assertEqual(context["evidence"]["source_metadata"], evidence["source_metadata"])

        output_nodes = {node["id"]: node for node in graph["nodes"]}
        output_edges = {edge["id"]: edge for edge in graph["edges"]}
        output_evidence = {item["id"]: item for item in evidence["items"]}
        for node in context["graph"]["nodes"]:
            self.assertEqual(node, output_nodes[node["id"]])
        for edge in context["graph"]["edges"]:
            self.assertEqual(edge, output_edges[edge["id"]])
        for item in context["evidence"]["items"]:
            self.assertEqual(item, output_evidence[item["id"]])

        node = output_nodes[concept["id"]]
        node_evidence = next(item for item in evidence["items"] if item["level"] == "E6" and item["locator"]["observation"] == concept["id"])
        self.assertEqual("PROVISIONAL", node["properties"]["certainty"])
        self.assertEqual([node_evidence["id"]], node["evidence_ids"])
        self.assertEqual("EVID-INFERENCE-" + _tuple_sha256((
            "inference",
            _tuple_sha256((graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"])),
            {key: value for key, value in concept.items() if key != "id"},
        )), node_evidence["id"])

        edge = output_edges[relationship["id"]]
        self.assertEqual((concept["id"], project_id), (edge["from"], edge["to"]))
        self.assertEqual("PROVISIONAL", edge["certainty"])
        self.assertIsNone(edge["source_record"])
        self.assertEqual(["semantic-proposals.json"], edge["source_members"])
        self.assertEqual([{
            "path": "semantic-proposals.json",
            "artifact_kind": "semantic-proposals",
            "schema_version": "1.0.0",
            "sha256": hashlib.sha256(context["proposals_path"].read_bytes()).hexdigest(),
        }], graph["derived_inputs"])
        self.assertEqual(graph["derived_inputs"], evidence["derived_inputs"])
        self.assertNotIn(str(context["fixture"]["root"]), graph_path.read_text(encoding="utf-8"))

        second_output = context["fixture"]["work"] / "phase4c-output-repeat"
        _run_import(context, second_output)
        self.assertEqual(graph_path.read_bytes(), (second_output / "knowledge-graph.json").read_bytes())
        self.assertEqual(evidence_path.read_bytes(), (second_output / "evidence.json").read_bytes())

    def test_semantically_equal_noncanonical_4a_json_is_rejected_before_publication(self):
        context = _prepare(self)
        graph = load_artifact(context["graph_path"])
        noncanonical = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        context["graph_path"].write_bytes(noncanonical)
        graph_record = next(row for row in context["candidate"]["phase4_inputs"] if row["artifact_kind"] == "knowledge-graph")
        graph_record["sha256"] = hashlib.sha256(noncanonical).hexdigest()
        _write_candidate(context)

        module = _importer(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)
        self.assertFalse(context["output"].exists())

    def test_authentic_metadata_and_updated_digest_do_not_authenticate_fabricated_payload(self):
        context = _prepare(self)
        graph = load_artifact(context["graph_path"])
        file_node = next(node for node in graph["nodes"] if node["type"] == "File")
        file_node["label"] = "fabricated payload"
        forged = dumps_artifact(graph).encode("utf-8")
        context["graph_path"].write_bytes(forged)
        graph_record = next(row for row in context["candidate"]["phase4_inputs"] if row["artifact_kind"] == "knowledge-graph")
        graph_record["sha256"] = hashlib.sha256(forged).hexdigest()
        _write_candidate(context)

        module = _importer(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)
        self.assertFalse(context["output"].exists())

    def test_bad_input_digest_and_wrong_source_root_fail_without_publication(self):
        context = _prepare(self)
        context["candidate"]["phase4_inputs"][0]["sha256"] = "0" * 64
        _write_candidate(context)
        module = _importer(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("INPUT_DIGEST_MISMATCH", raised.exception.code)
        self.assertFalse(context["output"].exists())

        context = _prepare(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context, root=context["fixture"]["work"])
        self.assertEqual("SOURCE_RUN_INVALID", raised.exception.code)
        self.assertFalse(context["output"].exists())

        context = _prepare(self)
        context["candidate"]["generated_at"] = "2000-01-01T00:00:00Z"
        _write_candidate(context)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("SOURCE_IDENTITY_MISMATCH", raised.exception.code)
        self.assertFalse(context["output"].exists())

    def test_unsafe_logical_input_path_fails_without_publication(self):
        context = _prepare(self)
        raw = context["proposals_path"].read_bytes()
        raw = raw.replace(b"phase4a/evidence.json", b"phase4a/../evidence.json", 1)
        context["proposals_path"].write_bytes(raw)
        module = _importer(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("INPUT_INVALID", raised.exception.code)
        self.assertFalse(context["output"].exists())

    def test_conflicting_proposal_id_and_missing_endpoint_fail_closed(self):
        context = _prepare(self)
        conflicting = copy.deepcopy(context["candidate"]["proposals"][0])
        conflicting["label"] = "same id, different proposal"
        context["candidate"]["proposals"].append(conflicting)
        _write_candidate(context)
        module = _importer(self)
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("PROPOSAL_ID_CONFLICT", raised.exception.code)
        self.assertFalse(context["output"].exists())

        context = _prepare(self)
        edge = _add_proposal(context, {
            "category": "architecture",
            "kind": "edge",
            "label": "An unresolved proposal edge",
            "edge_type": "PART_OF",
            "from": "PROP-" + "f" * 64,
            "to": next(node["id"] for node in context["graph"]["nodes"] if node["type"] == "Project"),
        })
        with self.assertRaises(module.Phase4ProposalError) as raised:
            _run_import(context)
        self.assertEqual("REFERENCE_MISSING", raised.exception.code)
        self.assertFalse(context["output"].exists())
        self.assertTrue(edge["id"].startswith("PROP-"))

    def test_current_phase4b_consumer_rejects_extended_pair(self):
        context = _prepare(self)
        _run_import(context)
        graph_path = context["output"] / "knowledge-graph.json"
        evidence_path = context["output"] / "evidence.json"
        graph = load_artifact(graph_path)
        evidence = load_artifact(evidence_path)
        claims_path = context["fixture"]["work"] / "claim-candidates.json"
        claims = {
            "artifact_kind": "claim-candidates",
            "schema_version": "1.0.0",
            "repository_revision": graph["repository_revision"],
            "generated_at": graph["generated_at"],
            "snapshot_kind": graph["snapshot_kind"],
            "source_metadata": graph["source_metadata"],
            "source_run": graph["source_run"],
            "phase4_inputs": [
                {"path": "phase4c-output/evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest()},
                {"path": "phase4c-output/knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest()},
            ],
            "claims": [],
        }
        claims_path.write_text(dumps_artifact(claims), encoding="utf-8", newline="\n")
        report_path = context["fixture"]["work"] / "claim-evidence.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "audit_claim_evidence.py"),
                "--claims", str(claims_path),
                "--graph", str(graph_path),
                "--evidence", str(evidence_path),
                "--run-dir", str(context["run_dir"]),
                "--root", str(context["fixture"]["root"]),
                "--out", str(report_path),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("error=PROVENANCE_MISMATCH", result.stdout)
        self.assertFalse(report_path.exists())

    def test_cli_emits_redacted_counts_and_no_input_paths(self):
        context = _prepare(self)
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--proposals", str(context["proposals_path"]),
                "--graph", str(context["graph_path"]),
                "--evidence", str(context["evidence_path"]),
                "--run-dir", str(context["run_dir"]),
                "--root", str(context["fixture"]["root"]),
                "--out", str(context["output"]),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PASS", result.stdout)
        self.assertNotIn(str(context["fixture"]["root"]), result.stdout + result.stderr)
        self.assertNotIn(str(context["run_dir"]), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
