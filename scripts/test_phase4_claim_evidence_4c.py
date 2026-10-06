#!/usr/bin/env python3
"""Focused tests for the authenticated Phase 4C claim-audit entry."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import sys
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase4_proposals import _prepare as _prepare_proposals, _proposal_id, _run_import  # noqa: E402


def _claim_id(graph, text, category="project_fact"):
    snapshot_key = canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))
    normalized = unicodedata.normalize("NFC", text.strip())
    return "CLAIM-" + canonical_tuple_sha256(("claim", snapshot_key, category, normalized))


def _claim(graph, *, text, evidence_ids, graph_node_ids=(), graph_edge_ids=(), critical=True):
    return {
        "id": _claim_id(graph, text),
        "text": text,
        "category": "project_fact",
        "critical": critical,
        "evidence_ids": list(evidence_ids),
        "graph_node_ids": list(graph_node_ids),
        "graph_edge_ids": list(graph_edge_ids),
        "citations": [],
    }


def _case(testcase, claim_specs):
    context = _prepare_proposals(testcase)
    _run_import(context)
    package = context["output"]
    graph_path = package / "knowledge-graph.json"
    evidence_path = package / "evidence.json"
    graph = load_artifact(graph_path)
    evidence = load_artifact(evidence_path)
    proposals_path = package / "semantic-proposals.json"
    provisional_node = next(
        node for node in graph["nodes"]
        if node["properties"].get("certainty") == "PROVISIONAL"
    )
    e1_id = next(item["id"] for item in evidence["items"] if item["level"] == "E1")
    e6_id = provisional_node["evidence_ids"][0]
    candidate = {
        "artifact_kind": "claim-candidates",
        "schema_version": "1.1.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": sorted([
            {
                "path": path.name,
                "artifact_kind": kind,
                "schema_version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path, kind, version in (
                (graph_path, "knowledge-graph", "1.1.0"),
                (evidence_path, "evidence", "1.2.0"),
                (proposals_path, "semantic-proposals", "1.0.0"),
            )
        ], key=lambda row: (row["artifact_kind"], row["path"])),
        "claims": [
            _claim(
                graph,
                text=spec["text"],
                evidence_ids=spec["evidence_ids"],
                graph_node_ids=spec.get("graph_node_ids", ()),
                graph_edge_ids=spec.get("graph_edge_ids", ()),
                critical=spec.get("critical", True),
            )
            for spec in claim_specs
        ],
    }
    claims_path = context["fixture"]["work"] / "claim-candidates-1.1.json"
    claims_path.write_text(dumps_artifact(candidate), encoding="utf-8", newline="\n")
    context.update({
        "package": package,
        "graph": graph,
        "evidence": evidence,
        "provisional_node": provisional_node,
        "e1_id": e1_id,
        "e6_id": e6_id,
        "candidate": candidate,
        "claims_path": claims_path,
    })
    return context


def _audit_api(testcase):
    module = importlib.import_module("phase4_claim_evidence")
    testcase.assertTrue(
        hasattr(module, "audit_claim_candidates_4c"),
        "the source-backed 1.1.0 audit API is required",
    )
    return module.audit_claim_candidates_4c


def _save_candidate(context):
    context["claims_path"].write_text(dumps_artifact(context["candidate"]), encoding="utf-8", newline="\n")


def _run_cli(context, out, *, root=None):
    cli = SCRIPTS / "audit_claim_evidence_4c.py"
    return subprocess.run(
        [
            sys.executable,
            str(cli),
            "--claims", str(context["claims_path"]),
            "--package", str(context["package"]),
            "--run-dir", str(context["run_dir"]),
            "--root", str(root or context["fixture"]["root"]),
            "--out", str(out),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_graph_cli(context, report_path, out):
    cli = SCRIPTS / "build_claim_evidence_graph.py"
    return subprocess.run(
        [
            sys.executable,
            str(cli),
            "--phase4c-package", str(context["package"]),
            "--claims", str(context["claims_path"]),
            "--audit-report", str(report_path),
            "--run-dir", str(context["run_dir"]),
            "--root", str(context["fixture"]["root"]),
            "--out", str(out),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class Phase4ClaimEvidence4CTests(unittest.TestCase):
    def test_phase6_authentication_context_replays_once_and_checks_canonical_pair(self):
        context = _case(self, [{"text": "This E1 claim is bound to the selected source symbol.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        module = importlib.import_module("phase4_claim_evidence")
        report = _audit_api(self)(
            context["claims_path"], package_dir=context["package"],
            run_dir=context["run_dir"], root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "phase6-claim-evidence.json"
        report_path.write_bytes(dumps_artifact(report).encode("utf-8"))
        overlay_path = context["fixture"]["work"] / "phase6-claim-evidence-graph.json"
        module.build_claim_evidence_graph_4c(
            context["claims_path"], report_path, package_dir=context["package"],
            run_dir=context["run_dir"], root=context["fixture"]["root"], out=overlay_path,
        )
        real_replay = module.reproject_phase4c_artifacts

        self.assertTrue(
            hasattr(module, "authenticate_claim_evidence_bundle_4c"),
            "Phase 6 requires the additive non-publishing authenticated context API",
        )
        with patch.object(module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            authenticated = module.authenticate_claim_evidence_bundle_4c(
                context["claims_path"], report_path, overlay_path,
                package_dir=context["package"], run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual(context["graph"], authenticated.graph)
        self.assertEqual(context["evidence"], authenticated.evidence)
        self.assertEqual(report, authenticated.report)
        self.assertEqual(load_artifact(overlay_path), authenticated.overlay)
        authenticated.recheck()

    def test_phase6_authentication_context_rejects_noncanonical_overlay_bytes(self):
        context = _case(self, [{"text": "Canonical overlay bytes are provenance-bound.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        module = importlib.import_module("phase4_claim_evidence")
        report = _audit_api(self)(
            context["claims_path"], package_dir=context["package"],
            run_dir=context["run_dir"], root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "phase6-noncanonical-report.json"
        report_path.write_bytes(dumps_artifact(report).encode("utf-8"))
        overlay_path = context["fixture"]["work"] / "phase6-noncanonical-overlay.json"
        module.build_claim_evidence_graph_4c(
            context["claims_path"], report_path, package_dir=context["package"],
            run_dir=context["run_dir"], root=context["fixture"]["root"], out=overlay_path,
        )
        overlay_path.write_bytes(overlay_path.read_bytes() + b" ")

        self.assertTrue(
            hasattr(module, "authenticate_claim_evidence_bundle_4c"),
            "Phase 6 requires raw canonical overlay verification",
        )
        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            module.authenticate_claim_evidence_bundle_4c(
                context["claims_path"], report_path, overlay_path,
                package_dir=context["package"], run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)

    def test_authenticated_overlay_reuses_one_replay_and_publishes_exact_claim_link(self):
        context = _case(self, [{"text": "A source-backed fact has one exact citation.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        module = importlib.import_module("phase4_claim_evidence")
        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "claim-evidence-1.1.json"
        report_path.write_text(dumps_artifact(report), encoding="utf-8", newline="\n")
        out = context["fixture"]["work"] / "claim-evidence-graph.json"
        real_reproject = module.reproject_phase4c_artifacts

        with patch.object(module, "reproject_phase4c_artifacts", wraps=real_reproject) as replay:
            overlay = module.build_claim_evidence_graph_4c(
                context["claims_path"],
                report_path,
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
                out=out,
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual("PASS", overlay["audit_status"])
        self.assertEqual(1, sum(node["type"] == "Claim" for node in overlay["nodes"]))
        self.assertEqual(1, sum(node["type"] == "Evidence" for node in overlay["nodes"]))
        self.assertEqual(1, len(overlay["edges"]))
        self.assertEqual({"base_evidence", "base_graph", "claim_candidates", "claim_evidence", "semantic_proposals"}, {row["role"] for row in overlay["input_digests"]})
        digests = {row["role"]: row["sha256"] for row in overlay["input_digests"]}
        for role, path in (
            ("base_graph", context["package"] / "knowledge-graph.json"),
            ("base_evidence", context["package"] / "evidence.json"),
            ("semantic_proposals", context["package"] / "semantic-proposals.json"),
            ("claim_candidates", context["claims_path"]),
            ("claim_evidence", report_path),
        ):
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digests[role])
        self.assertEqual(overlay, load_artifact(out))

    def test_noncanonical_semantically_equal_1_1_report_fails_without_overlay(self):
        context = _case(self, [{"text": "The supplied matrix must be canonical.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "pretty-claim-evidence-1.1.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        out = context["fixture"]["work"] / "noncanonical-overlay.json"
        module = importlib.import_module("phase4_claim_evidence")

        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            module.build_claim_evidence_graph_4c(
                context["claims_path"],
                report_path,
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
                out=out,
            )

        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)
        self.assertFalse(out.exists())

    def test_audit_report_change_during_final_freshness_fails_without_overlay(self):
        context = _case(self, [{"text": "The exact matrix bytes remain bound through publish.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "claim-evidence-race.json"
        report_path.write_text(dumps_artifact(report), encoding="utf-8", newline="\n")
        out = context["fixture"]["work"] / "race-overlay.json"
        module = importlib.import_module("phase4_claim_evidence")
        verify_real = module.verify_phase4c_source_freshness

        def mutate_report(*args, **kwargs):
            report_path.write_bytes(report_path.read_bytes() + b" ")
            return verify_real(*args, **kwargs)

        with patch.object(module, "verify_phase4c_source_freshness", side_effect=mutate_report):
            with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
                module.build_claim_evidence_graph_4c(
                    context["claims_path"],
                    report_path,
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=out,
                )

        self.assertEqual("INPUT_CHANGED", raised.exception.code)
        self.assertFalse(out.exists())

    def test_overlay_output_inside_target_root_is_rejected_without_publication(self):
        context = _case(self, [{"text": "Derived overlays stay outside the source repository.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "claim-evidence-for-owned-output.json"
        report_path.write_text(dumps_artifact(report), encoding="utf-8", newline="\n")
        out = context["fixture"]["root"] / "src" / "claim-evidence-graph.json"
        module = importlib.import_module("phase4_claim_evidence")

        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            module.build_claim_evidence_graph_4c(
                context["claims_path"],
                report_path,
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
                out=out,
            )

        self.assertEqual("OUTPUT_INVALID", raised.exception.code)
        self.assertFalse(out.exists())

    def test_cli_publishes_complete_fail_overlay_and_exits_nonzero(self):
        context = _case(self, [{
            "text": "An unresolved citation stays in the graph inventory.",
            "evidence_ids": [],
            "critical": False,
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"], "EVID-not-in-ledger"]
        _save_candidate(context)
        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )
        report_path = context["fixture"]["work"] / "failed-claim-evidence-1.1.json"
        report_path.write_text(dumps_artifact(report), encoding="utf-8", newline="\n")
        out = context["fixture"]["work"] / "failed-claim-evidence-graph.json"

        result = _run_graph_cli(context, report_path, out)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        overlay = load_artifact(out)
        self.assertEqual("FAIL", overlay["audit_status"])
        self.assertEqual(1, sum(node["type"] == "Claim" for node in overlay["nodes"]))
        self.assertEqual(1, sum(node["type"] == "Evidence" for node in overlay["nodes"]))
        self.assertEqual(1, len(overlay["edges"]))
        claim_node = next(node for node in overlay["nodes"] if node["type"] == "Claim")
        self.assertEqual("UNVERIFIED", claim_node["properties"]["disposition"])
        self.assertIn("EVID-not-in-ledger", claim_node["properties"]["cited_evidence_ids"])

    def test_mixed_e1_and_e6_is_inference_and_cannot_pass_critical_audit(self):
        context = _case(self, [{
            "text": "A provisional concept is connected to source evidence.",
            "evidence_ids": [],
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"], context["e6_id"]]
        context["candidate"]["claims"][0]["graph_node_ids"] = [context["provisional_node"]["id"]]
        _save_candidate(context)

        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )

        self.assertEqual("FAIL", report["audit_status"])
        self.assertEqual("INFERENCE", report["claims"][0]["disposition"])
        self.assertEqual(["E1", "E6"], report["claims"][0]["resolved_evidence_levels"])

    def test_provisional_graph_reference_requires_its_exact_e6_citation(self):
        context = _case(self, [{
            "text": "A provisional concept cannot be promoted by unrelated E1 evidence.",
            "evidence_ids": [],
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        context["candidate"]["claims"][0]["graph_node_ids"] = [context["provisional_node"]["id"]]
        _save_candidate(context)

        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )

        self.assertEqual("FAIL", report["audit_status"])
        self.assertEqual("UNVERIFIED", report["claims"][0]["disposition"])
        self.assertIn("PROVISIONAL_EVIDENCE_NOT_CITED", report["claims"][0]["reason_codes"])

    def test_noncritical_provisional_reference_without_e6_fails_published_audit(self):
        context = _case(self, [{
            "text": "A noncritical provisional citation still requires its E6 proof.",
            "evidence_ids": [],
            "critical": False,
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        context["candidate"]["claims"][0]["graph_node_ids"] = [context["provisional_node"]["id"]]
        _save_candidate(context)
        out = context["fixture"]["work"] / "noncritical-provisional-claim-evidence.json"

        result = _run_cli(context, out)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertTrue(out.is_file())
        report = load_artifact(out)
        self.assertEqual("FAIL", report["audit_status"])
        self.assertEqual("UNVERIFIED", report["claims"][0]["disposition"])
        self.assertIn("PROVISIONAL_EVIDENCE_NOT_CITED", report["claims"][0]["reason_codes"])

    def test_e1_only_claim_is_referentially_supported_by_the_authenticated_package(self):
        context = _case(self, [{
            "text": "A source-backed fact is referenced.",
            "evidence_ids": [],
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)

        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )

        self.assertEqual("PASS", report["audit_status"])
        self.assertEqual("SUPPORTED", report["claims"][0]["disposition"])
        self.assertEqual(3, len(report["phase4_inputs"]))

    def test_authenticated_e6_only_noncritical_claim_is_inference_and_partial(self):
        context = _case(self, [{
            "text": "A user proposal is provisional.",
            "evidence_ids": [],
            "critical": False,
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e6_id"]]
        _save_candidate(context)

        report = _audit_api(self)(
            context["claims_path"],
            package_dir=context["package"],
            run_dir=context["run_dir"],
            root=context["fixture"]["root"],
        )

        self.assertEqual("PARTIAL", report["audit_status"])
        self.assertEqual("INFERENCE", report["claims"][0]["disposition"])
        self.assertEqual(["E6"], report["claims"][0]["resolved_evidence_levels"])

    def test_well_formed_unresolved_inventory_publishes_complete_fail_matrix(self):
        context = _case(self, [
            {"text": "A valid claim remains in the inventory.", "evidence_ids": []},
            {"text": "A missing E1 reference is not supported.", "evidence_ids": ["EVID-missing"], "critical": False},
        ])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        out = context["fixture"]["work"] / "claim-evidence-1.1.json"

        result = _run_cli(context, out)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        report = load_artifact(out)
        self.assertEqual("FAIL", report["audit_status"])
        self.assertEqual(2, len(report["claims"]))
        self.assertEqual("UNVERIFIED", next(row for row in report["claims"] if row["text"].startswith("A missing"))["disposition"])

    def test_updated_client_digest_cannot_authenticate_a_fabricated_extended_graph(self):
        context = _case(self, [{
            "text": "A graph payload change cannot retain source provenance.",
            "evidence_ids": [],
        }])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        graph_path = context["package"] / "knowledge-graph.json"
        graph = load_artifact(graph_path)
        graph["nodes"][0]["label"] = "fabricated but schema-valid label"
        graph_path.write_text(dumps_artifact(graph), encoding="utf-8", newline="\n")
        for record in context["candidate"]["phase4_inputs"]:
            if record["artifact_kind"] == "knowledge-graph":
                record["sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        _save_candidate(context)
        out = context["fixture"]["work"] / "spoofed-report.json"

        result = _run_cli(context, out)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=PROVENANCE_MISMATCH", result.stdout)
        self.assertFalse(out.exists())

    def test_declared_phase4c_digest_mismatch_fails_without_report(self):
        context = _case(self, [{"text": "Declared package digest is checked.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        context["candidate"]["phase4_inputs"][0]["sha256"] = "0" * 64
        _save_candidate(context)
        out = context["fixture"]["work"] / "digest-mismatch.json"

        result = _run_cli(context, out)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=INPUT_DIGEST_MISMATCH", result.stdout)
        self.assertFalse(out.exists())

    def test_wrong_original_phase3_root_fails_without_report(self):
        context = _case(self, [{"text": "A source replay must use its original root.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        out = context["fixture"]["work"] / "wrong-source-root.json"

        result = _run_cli(context, out, root=context["fixture"]["work"])

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=SOURCE_RUN_INVALID", result.stdout)
        self.assertFalse(out.exists())

    def test_changed_proposal_with_updated_client_digest_fails_without_report(self):
        context = _case(self, [{"text": "Proposals must reproduce the extended pair.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        proposal_path = context["package"] / "semantic-proposals.json"
        proposals = load_artifact(proposal_path)
        old = proposals["proposals"][0]
        payload = {key: value for key, value in old.items() if key != "id"}
        payload["label"] = "A changed proposal with the same source snapshot"
        proposals["proposals"][0] = {"id": _proposal_id(context["graph"], payload), **payload}
        proposal_path.write_text(dumps_artifact(proposals), encoding="utf-8", newline="\n")
        for record in context["candidate"]["phase4_inputs"]:
            if record["artifact_kind"] == "semantic-proposals":
                record["sha256"] = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        _save_candidate(context)
        out = context["fixture"]["work"] / "proposal-mismatch.json"

        result = _run_cli(context, out)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=PROVENANCE_MISMATCH", result.stdout)
        self.assertFalse(out.exists())

    def test_extra_package_member_fails_closed(self):
        context = _case(self, [{"text": "The package directory is exact.", "evidence_ids": []}])
        (context["package"] / "unexpected.txt").write_text("not a package member", encoding="utf-8")

        module = importlib.import_module("phase4_claim_evidence")
        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            _audit_api(self)(
                context["claims_path"],
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )

        self.assertEqual("PACKAGE_INVALID", raised.exception.code)

    def test_missing_package_member_fails_closed(self):
        context = _case(self, [{"text": "All package members are required.", "evidence_ids": []}])
        (context["package"] / "evidence.json").unlink()

        module = importlib.import_module("phase4_claim_evidence")
        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            _audit_api(self)(
                context["claims_path"],
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )

        self.assertEqual("PACKAGE_INVALID", raised.exception.code)

    def test_symlink_package_member_fails_closed_when_supported(self):
        context = _case(self, [{"text": "Package members must be regular files.", "evidence_ids": []}])
        source = context["fixture"]["work"] / "outside-evidence.json"
        source.write_bytes((context["package"] / "evidence.json").read_bytes())
        member = context["package"] / "evidence.json"
        member.unlink()
        try:
            member.symlink_to(source)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {type(exc).__name__}")

        module = importlib.import_module("phase4_claim_evidence")
        with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
            _audit_api(self)(
                context["claims_path"],
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
            )

        self.assertEqual("PACKAGE_INVALID", raised.exception.code)

    def test_final_source_freshness_race_rechecks_package_before_publication(self):
        context = _case(self, [{"text": "The final input bytes remain bound.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        module = importlib.import_module("phase4_claim_evidence")
        self.assertTrue(
            hasattr(module, "audit_and_publish_claim_evidence_4c"),
            "the CLI needs one source-backed audit/publication context",
        )
        evidence_path = context["package"] / "evidence.json"
        out = context["fixture"]["work"] / "must-not-publish.json"
        verify_real = module.verify_phase4c_source_freshness

        def mutate_after_audit(*args, **kwargs):
            evidence_path.write_bytes(evidence_path.read_bytes() + b" ")
            return verify_real(*args, **kwargs)

        with patch.object(module, "verify_phase4c_source_freshness", side_effect=mutate_after_audit):
            with self.assertRaises(module.Phase4ClaimEvidenceError) as raised:
                module.audit_and_publish_claim_evidence_4c(
                    context["claims_path"],
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=out,
                )

        self.assertEqual("INPUT_CHANGED", raised.exception.code)
        self.assertFalse(out.exists())

    def test_output_cannot_be_inside_package_or_target(self):
        context = _case(self, [{"text": "The report has separate ownership.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        cli_outs = (
            context["package"] / "claim-evidence.json",
            context["fixture"]["root"] / "claim-evidence.json",
        )
        for out in cli_outs:
            with self.subTest(out=out.name):
                result = _run_cli(context, out)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertFalse(out.exists())

    def test_existing_output_is_never_overwritten(self):
        context = _case(self, [{"text": "An existing report is protected.", "evidence_ids": []}])
        context["candidate"]["claims"][0]["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        out = context["fixture"]["work"] / "existing-report.json"
        out.write_text("keep existing", encoding="utf-8")

        result = _run_cli(context, out)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("error=OUTPUT_EXISTS", result.stdout)
        self.assertEqual("keep existing", out.read_text(encoding="utf-8"))

    def test_multiple_claims_have_a_complete_deterministic_matrix(self):
        context = _case(self, [
            {"text": "A second E1-backed fact.", "evidence_ids": []},
            {"text": "A first E1-backed fact.", "evidence_ids": []},
        ])
        for claim in context["candidate"]["claims"]:
            claim["evidence_ids"] = [context["e1_id"]]
        _save_candidate(context)
        audit = _audit_api(self)

        first = audit(context["claims_path"], package_dir=context["package"], run_dir=context["run_dir"], root=context["fixture"]["root"])
        context["candidate"]["claims"].reverse()
        _save_candidate(context)
        second = audit(context["claims_path"], package_dir=context["package"], run_dir=context["run_dir"], root=context["fixture"]["root"])

        self.assertEqual(first["claims"], second["claims"])
        self.assertEqual(sorted(row["id"] for row in first["claims"]), [row["id"] for row in first["claims"]])


if __name__ == "__main__":
    unittest.main()
