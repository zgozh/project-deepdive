#!/usr/bin/env python3
"""Focused tests for the authenticated Phase 5A prerequisite graph slice."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase4_proposals import _prepare  # noqa: E402


CLI = SCRIPTS / "build_prerequisite_graph.py"


def _module(testcase, name):
    testcase.assertIsNotNone(importlib.util.find_spec(name), f"{name} is missing")
    return importlib.import_module(name)


def _phase4c_context(testcase, *, source_files=None):
    context = _prepare(testcase, source_files=source_files)
    proposals = _module(testcase, "phase4_proposals")
    proposals.import_semantic_proposals(
        context["proposals_path"],
        graph_path=context["graph_path"],
        evidence_path=context["evidence_path"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
        out=context["output"],
    )
    context["package"] = context["output"]
    context["graph"] = load_artifact(context["package"] / "knowledge-graph.json")
    context["evidence"] = load_artifact(context["package"] / "evidence.json")
    context["candidate_path"] = context["fixture"]["work"] / "prerequisite-candidates.json"
    context["result_path"] = context["fixture"]["work"] / "prerequisite-graph.json"
    return context


def _snapshot_key(graph):
    return canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))


def _node(context, concept_key, *, label=None, scope="PROJECT_SPECIFIC", kind="Concept", status="UNVERIFIED_TEACHING", refs=None):
    graph = context["graph"]
    digest = canonical_tuple_sha256((
        "prerequisite-node", _snapshot_key(graph), concept_key, kind, scope,
    ))
    return {
        "id": "PREREQ-NODE-" + digest,
        "concept_key": concept_key,
        "type": kind,
        "label": label or concept_key.replace("-", " "),
        "scope": scope,
        "epistemic_status": status,
        "source_refs": refs or {
            "graph_node_ids": [], "graph_edge_ids": [], "evidence_ids": [], "file_paths": [],
        },
    }


def _edge(context, from_node, to_node):
    digest = canonical_tuple_sha256((
        "prerequisite-edge", _snapshot_key(context["graph"]), from_node["id"], to_node["id"],
    ))
    return {
        "id": "PREREQ-EDGE-" + digest,
        "type": "REQUIRES",
        "from": from_node["id"],
        "to": to_node["id"],
        "epistemic_status": "UNVERIFIED_TEACHING",
    }


def _candidate(context, nodes, edges=()):
    graph = context["graph"]
    rows = []
    for name, kind, version in (
        ("evidence.json", "evidence", "1.2.0"),
        ("knowledge-graph.json", "knowledge-graph", "1.1.0"),
        ("semantic-proposals.json", "semantic-proposals", "1.0.0"),
    ):
        path = context["package"] / name
        rows.append({
            "path": name,
            "artifact_kind": kind,
            "schema_version": version,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {
        "artifact_kind": "prerequisite-candidates",
        "schema_version": "1.0.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "source_status": graph["status"],
        "phase4_inputs": rows,
        "nodes": list(nodes),
        "edges": list(edges),
    }


def _write_candidate(context, candidate):
    context["candidate_path"].write_text(
        dumps_artifact(candidate), encoding="utf-8", newline="\n",
    )


def _e1_reference(context):
    symbol = next(node for node in context["graph"]["nodes"] if node["type"] == "Symbol")
    item = next(item for item in context["evidence"]["items"] if item["level"] in {"E1", "E2"})
    return {
        "graph_node_ids": [symbol["id"]],
        "graph_edge_ids": [],
        "evidence_ids": [item["id"]],
        "file_paths": [],
    }


def _e6_reference(context):
    item = next(item for item in context["evidence"]["items"] if item["level"] == "E6")
    graph_node = next(node for node in context["graph"]["nodes"] if item["id"] in node["evidence_ids"])
    return graph_node, item


def _project(testcase, context, candidate):
    api = _module(testcase, "phase5_prerequisites")
    raw = dumps_artifact(candidate).encode("utf-8")
    return api.project_prerequisite_graph(
        candidate,
        graph=context["graph"],
        evidence=context["evidence"],
        candidate_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _build(testcase, context, candidate, *, out=None):
    _write_candidate(context, candidate)
    api = _module(testcase, "build_prerequisite_graph")
    return api.build_prerequisite_graph(
        context["candidate_path"],
        package_dir=context["package"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
        out=out or context["result_path"],
    )


class Phase5PrerequisiteProjectionTests(unittest.TestCase):
    def test_concept_key_keeps_identity_when_label_context_and_status_change(self):
        context = _phase4c_context(self)
        source_node, source_evidence = _e6_reference(context)
        first = _node(context, "runtime-concept", label="First display name", refs=_e1_reference(context))
        second = _node(
            context,
            "runtime-concept",
            label="Revised display name",
            status="E6_PROVISIONAL",
            refs={
                "graph_node_ids": [source_node["id"]],
                "graph_edge_ids": [],
                "evidence_ids": [source_evidence["id"]],
                "file_paths": [],
            },
        )

        first_result = _project(self, context, _candidate(context, [first]))
        second_result = _project(self, context, _candidate(context, [second]))

        self.assertEqual(first_result["nodes"][0]["id"], second_result["nodes"][0]["id"])
        self.assertNotEqual(first_result["nodes"][0]["label"], second_result["nodes"][0]["label"])

    def test_requires_edge_is_unverified_and_has_no_independent_evidence_refs(self):
        context = _phase4c_context(self)
        provisional, evidence = _e6_reference(context)
        provisional_node = _node(
            context,
            "provisional-node",
            status="E6_PROVISIONAL",
            refs={"graph_node_ids": [provisional["id"]], "graph_edge_ids": [], "evidence_ids": [evidence["id"]], "file_paths": []},
        )
        general_node = _node(context, "general-language-concept", scope="GENERAL_LEARNING")
        edge = _edge(context, provisional_node, general_node)

        result = _project(self, context, _candidate(context, [provisional_node, general_node], [edge]))

        self.assertEqual("UNVERIFIED_TEACHING", result["edges"][0]["epistemic_status"])
        self.assertEqual({"id", "type", "from", "to", "epistemic_status"}, set(result["edges"][0]))

    def test_e6_node_requires_its_exact_cited_provisional_evidence_id(self):
        context = _phase4c_context(self)
        provisional, _evidence = _e6_reference(context)
        malformed = _node(
            context,
            "unsupported-e6-link",
            status="E6_PROVISIONAL",
            refs={"graph_node_ids": [provisional["id"]], "graph_edge_ids": [], "evidence_ids": [], "file_paths": []},
        )

        api = _module(self, "phase5_prerequisites")
        with self.assertRaises(api.Phase5PrerequisiteError) as raised:
            _project(self, context, _candidate(context, [malformed]))

        self.assertEqual("E6_LINK_INVALID", raised.exception.code)

    def test_a_provisional_node_cannot_be_downgraded_by_omitting_its_e6_id(self):
        context = _phase4c_context(self)
        provisional, _evidence = _e6_reference(context)
        malformed = _node(
            context,
            "downgraded-e6",
            refs={"graph_node_ids": [provisional["id"]], "graph_edge_ids": [], "evidence_ids": [], "file_paths": []},
        )

        api = _module(self, "phase5_prerequisites")
        with self.assertRaises(api.Phase5PrerequisiteError) as raised:
            _project(self, context, _candidate(context, [malformed]))

        self.assertEqual("E6_LINK_INVALID", raised.exception.code)

    def test_reordered_and_identically_duplicated_candidates_keep_projection(self):
        context = _phase4c_context(self)
        first = _node(context, "project-node", refs=_e1_reference(context))
        second = _node(context, "general-node", scope="GENERAL_LEARNING")
        edge = _edge(context, first, second)

        forward = _project(self, context, _candidate(context, [first, second], [edge]))
        reordered = _project(self, context, _candidate(context, [second, first, first], [edge, edge]))

        self.assertEqual(forward["nodes"], reordered["nodes"])
        self.assertEqual(forward["edges"], reordered["edges"])
        self.assertNotEqual(
            forward["candidate_input"]["sha256"],
            reordered["candidate_input"]["sha256"],
        )
        self.assertEqual(1, len(reordered["edges"]))
        self.assertEqual(sorted(node["id"] for node in reordered["nodes"]), [node["id"] for node in reordered["nodes"]])

    def test_unicode_labels_normalize_before_projection(self):
        context = _phase4c_context(self)
        decomposed = _node(
            context,
            "unicode-label",
            scope="GENERAL_LEARNING",
            label="Cafe\u0301",
        )
        composed = _node(
            context,
            "unicode-label",
            scope="GENERAL_LEARNING",
            label="Caf\u00e9",
        )

        first = _project(self, context, _candidate(context, [decomposed]))
        second = _project(self, context, _candidate(context, [composed]))

        self.assertEqual(first["nodes"][0]["id"], second["nodes"][0]["id"])
        self.assertEqual("Caf\u00e9", first["nodes"][0]["label"])
        self.assertEqual(first["nodes"][0], second["nodes"][0])

    def test_repeated_concept_key_with_conflicting_semantic_identity_fails(self):
        context = _phase4c_context(self)
        first = _node(context, "one-key", kind="Concept", refs=_e1_reference(context))
        conflict = _node(context, "one-key", kind="Command", refs=_e1_reference(context))

        api = _module(self, "phase5_prerequisites")
        with self.assertRaises(api.Phase5PrerequisiteError) as raised:
            _project(self, context, _candidate(context, [first, conflict]))

        self.assertEqual("IDENTITY_CONFLICT", raised.exception.code)

    def test_missing_reference_and_dependency_cycle_fail_closed(self):
        context = _phase4c_context(self)
        missing = _node(
            context,
            "missing-reference",
            refs={"graph_node_ids": ["MISSING-NODE"], "graph_edge_ids": [], "evidence_ids": [], "file_paths": []},
        )
        api = _module(self, "phase5_prerequisites")
        with self.assertRaises(api.Phase5PrerequisiteError) as missing_error:
            _project(self, context, _candidate(context, [missing]))
        self.assertEqual("REFERENCE_MISSING", missing_error.exception.code)

        first = _node(context, "cycle-a", scope="GENERAL_LEARNING")
        second = _node(context, "cycle-b", scope="GENERAL_LEARNING")
        with self.assertRaises(api.Phase5PrerequisiteError) as cycle_error:
            _project(self, context, _candidate(context, [first, second], [_edge(context, first, second), _edge(context, second, first)]))
        self.assertEqual("GRAPH_INVALID", cycle_error.exception.code)

        missing_endpoint = copy.deepcopy(_edge(context, first, second))
        missing_endpoint["to"] = "PREREQ-NODE-" + "0" * 64
        with self.assertRaises(api.Phase5PrerequisiteError) as endpoint_error:
            _project(self, context, _candidate(context, [first, second], [missing_endpoint]))
        self.assertEqual("REFERENCE_MISSING", endpoint_error.exception.code)

        self_edge = _edge(context, first, first)
        with self.assertRaises(api.Phase5PrerequisiteError) as self_error:
            _project(self, context, _candidate(context, [first], [self_edge]))
        self.assertEqual("GRAPH_INVALID", self_error.exception.code)

    def test_partial_status_and_unknown_count_are_carried_without_adapter_summary(self):
        context = _phase4c_context(self)
        graph = copy.deepcopy(context["graph"])
        evidence = copy.deepcopy(context["evidence"])
        for artifact in (graph, evidence):
            artifact["status"] = "PARTIAL"
            artifact["source_metadata"]["g01_status"] = "PARTIAL"
            artifact["source_metadata"]["unknown_files"] = 3
            artifact["source_run"]["status"] = "PARTIAL"
        partial_node = _node(context, "partial-node", scope="GENERAL_LEARNING")
        partial_node["id"] = "PREREQ-NODE-" + canonical_tuple_sha256((
            "prerequisite-node", _snapshot_key(graph), partial_node["concept_key"],
            partial_node["type"], partial_node["scope"],
        ))
        candidate = _candidate(context, [partial_node])
        candidate["source_status"] = "PARTIAL"
        candidate["source_metadata"] = copy.deepcopy(graph["source_metadata"])
        candidate["source_run"] = copy.deepcopy(graph["source_run"])

        api = _module(self, "phase5_prerequisites")
        raw = dumps_artifact(candidate).encode("utf-8")
        result = api.project_prerequisite_graph(
            candidate,
            graph=graph,
            evidence=evidence,
            candidate_sha256=hashlib.sha256(raw).hexdigest(),
        )

        self.assertEqual("PARTIAL", result["source_status"])
        self.assertEqual(3, result["source_metadata"]["unknown_files"])
        self.assertEqual(graph["source_run"]["manifest_sha256"], result["source_run_manifest_sha256"])
        self.assertNotIn("source_run", result)


class Phase5PrerequisiteBuildTests(unittest.TestCase):
    def test_build_intake_accepts_only_the_exact_legacy_or_documentation_profile(self):
        context = _phase4c_context(self)
        builder = _module(self, "build_prerequisite_graph")
        legacy = _candidate(context, [])
        package_digests = {
            row["path"]: row["sha256"]
            for row in legacy["phase4_inputs"]
        }
        builder._check_candidate_inputs(legacy, package_digests)

        documentation_profile = copy.deepcopy(legacy)
        documentation_profile["schema_version"] = "1.1.0"
        documentation_profile["phase4_inputs"] = [
            {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.4.0", "sha256": package_digests["evidence.json"]},
            {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.2.0", "sha256": package_digests["knowledge-graph.json"]},
            {"path": "semantic-proposals.json", "artifact_kind": "semantic-proposals", "schema_version": "1.1.0", "sha256": package_digests["semantic-proposals.json"]},
        ]
        builder._check_candidate_inputs(documentation_profile, package_digests)

        mixed_version = copy.deepcopy(documentation_profile)
        mixed_version["phase4_inputs"][0]["schema_version"] = "1.2.0"
        with self.assertRaises(builder.PrerequisiteBuildError) as raised:
            builder._check_candidate_inputs(mixed_version, package_digests)
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)

        wrong_digest = copy.deepcopy(documentation_profile)
        wrong_digest["phase4_inputs"][1]["sha256"] = "0" * 64
        with self.assertRaises(builder.PrerequisiteBuildError) as raised:
            builder._check_candidate_inputs(wrong_digest, package_digests)
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)

        api = _module(self, "phase5_prerequisites")
        with self.assertRaises(api.Phase5PrerequisiteError) as raised:
            api._phase4_input_identity(documentation_profile, context["graph"], context["evidence"])
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)

        documentation_pair_graph = copy.deepcopy(context["graph"])
        documentation_pair_evidence = copy.deepcopy(context["evidence"])
        documentation_pair_graph["schema_version"] = "1.2.0"
        documentation_pair_evidence["schema_version"] = "1.4.0"
        with self.assertRaises(api.Phase5PrerequisiteError) as raised:
            api._phase4_input_identity(legacy, documentation_pair_graph, documentation_pair_evidence)
        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)

    def test_root_link_component_is_rejected_before_resolution(self):
        context = _phase4c_context(self)
        candidate = _candidate(context, [_node(context, "general", scope="GENERAL_LEARNING")])
        _write_candidate(context, candidate)
        builder = _module(self, "build_prerequisite_graph")
        root_alias = context["fixture"]["root"].parent / "root-alias"
        real_resolve = Path.resolve

        def resolve_with_symlink_alias(path, strict=False):
            if Path(path) == root_alias:
                return real_resolve(context["fixture"]["root"], strict=True)
            return real_resolve(path, strict=strict)

        with patch.object(
            builder, "_is_link", side_effect=lambda path: Path(path) == root_alias,
        ), patch.object(Path, "resolve", new=resolve_with_symlink_alias):
            with self.assertRaises(builder.PrerequisiteBuildError) as raised:
                builder.build_prerequisite_graph(
                    context["candidate_path"], package_dir=context["package"], run_dir=context["run_dir"],
                    root=root_alias, out=context["result_path"],
                )

        self.assertEqual("INPUT_INVALID", raised.exception.code)
        self.assertFalse(context["result_path"].exists())

    def test_publish_write_failure_cleans_staging_and_preserves_concurrent_owner(self):
        builder = _module(self, "build_prerequisite_graph")
        original_write = Path.write_bytes

        for owner_payload in (None, b"concurrent-owner"):
            with self.subTest(owner=owner_payload is not None):
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    output = directory / "prerequisite-graph.json"

                    def fail_staged_write(path, payload):
                        if path.name == output.name and path.parent.parent == directory:
                            original_write(path, b"partial-staging-bytes")
                            if owner_payload is not None:
                                original_write(output, owner_payload)
                            raise OSError("injected staging write failure")
                        return original_write(path, payload)

                    with patch.object(Path, "write_bytes", new=fail_staged_write):
                        with self.assertRaises(builder.PrerequisiteBuildError) as raised:
                            builder._publish_new_file(output, b"complete-artifact")

                    self.assertEqual("OUTPUT_FAILED", raised.exception.code)
                    if owner_payload is None:
                        self.assertFalse(output.exists())
                        self.assertEqual([], list(directory.iterdir()))
                    else:
                        self.assertEqual(owner_payload, output.read_bytes())
                        self.assertEqual([output], list(directory.iterdir()))

    def test_builder_uses_one_source_replay_and_one_final_freshness_check(self):
        context = _phase4c_context(self)
        node = _node(context, "project-node", refs=_e1_reference(context))
        candidate = _candidate(context, [node])
        _write_candidate(context, candidate)
        builder = _module(self, "build_prerequisite_graph")
        proposals = _module(self, "phase4_proposals")

        with patch.object(proposals, "reproject_phase4c_artifacts", wraps=proposals.reproject_phase4c_artifacts) as replay:
            with patch.object(proposals, "verify_phase4c_source_freshness", wraps=proposals.verify_phase4c_source_freshness) as freshness:
                result = builder.build_prerequisite_graph(
                    context["candidate_path"],
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=context["result_path"],
                )

        self.assertEqual(1, replay.call_count)
        self.assertEqual(1, freshness.call_count)
        self.assertEqual(result, load_artifact(context["result_path"]))
        self.assertEqual("prerequisite-graph", result["artifact_kind"])
        self.assertEqual(1, len(result["nodes"]))

    def test_canonical_phase4_pair_spoof_fails_without_publication(self):
        context = _phase4c_context(self)
        node = _node(context, "project-node", refs=_e1_reference(context))
        candidate = _candidate(context, [node])
        graph_path = context["package"] / "knowledge-graph.json"
        graph_path.write_bytes(graph_path.read_bytes() + b" ")
        candidate["phase4_inputs"][1]["sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        _write_candidate(context, candidate)
        builder = _module(self, "build_prerequisite_graph")
        errors = _module(self, "phase5_prerequisites")

        with self.assertRaises(errors.Phase5PrerequisiteError) as raised:
            builder.build_prerequisite_graph(
                context["candidate_path"], package_dir=context["package"], run_dir=context["run_dir"],
                root=context["fixture"]["root"], out=context["result_path"],
            )

        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)
        self.assertFalse(context["result_path"].exists())

    def test_final_input_change_after_source_freshness_fails_without_publication(self):
        context = _phase4c_context(self)
        candidate = _candidate(context, [_node(context, "project-node", refs=_e1_reference(context))])
        _write_candidate(context, candidate)
        builder = _module(self, "build_prerequisite_graph")
        proposals = _module(self, "phase4_proposals")
        original = proposals.verify_phase4c_source_freshness
        evidence_path = context["package"] / "evidence.json"

        def mutate_after_freshness(*args, **kwargs):
            original(*args, **kwargs)
            evidence_path.write_bytes(evidence_path.read_bytes() + b" ")
            context["candidate_path"].write_bytes(
                context["candidate_path"].read_bytes() + b" ",
            )

        with patch.object(proposals, "verify_phase4c_source_freshness", side_effect=mutate_after_freshness):
            with self.assertRaises(_module(self, "phase5_prerequisites").Phase5PrerequisiteError) as raised:
                builder.build_prerequisite_graph(
                    context["candidate_path"], package_dir=context["package"], run_dir=context["run_dir"],
                    root=context["fixture"]["root"], out=context["result_path"],
                )

        self.assertEqual("INPUT_CHANGED", raised.exception.code)
        self.assertFalse(context["result_path"].exists())

    def test_extra_package_member_is_rejected_before_source_replay(self):
        context = _phase4c_context(self)
        candidate = _candidate(context, [_node(context, "project-node", refs=_e1_reference(context))])
        _write_candidate(context, candidate)
        (context["package"] / "unexpected.json").write_text("{}", encoding="utf-8")
        builder = _module(self, "build_prerequisite_graph")
        errors = _module(self, "phase5_prerequisites")
        proposals = _module(self, "phase4_proposals")

        with patch.object(proposals, "reproject_phase4c_artifacts") as replay:
            with self.assertRaises(errors.Phase5PrerequisiteError) as raised:
                builder.build_prerequisite_graph(
                    context["candidate_path"], package_dir=context["package"], run_dir=context["run_dir"],
                    root=context["fixture"]["root"], out=context["result_path"],
                )

        self.assertEqual("INPUT_INVALID", raised.exception.code)
        replay.assert_not_called()
        self.assertFalse(context["result_path"].exists())

    def test_output_inside_target_is_rejected_without_publication(self):
        context = _phase4c_context(self)
        candidate = _candidate(context, [_node(context, "general", scope="GENERAL_LEARNING")])
        _write_candidate(context, candidate)
        output = context["fixture"]["root"] / "prerequisite-graph.json"
        builder = _module(self, "build_prerequisite_graph")

        with self.assertRaises(builder.PrerequisiteBuildError) as raised:
            builder.build_prerequisite_graph(
                context["candidate_path"], package_dir=context["package"], run_dir=context["run_dir"],
                root=context["fixture"]["root"], out=output,
            )

        self.assertEqual("OUTPUT_INVALID", raised.exception.code)
        self.assertFalse(output.exists())

    def test_cli_reports_only_fixed_error_code(self):
        context = _phase4c_context(self)
        context["candidate_path"].write_text("SECRET_CANDIDATE_BODY", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(CLI), "--candidates", str(context["candidate_path"]),
                "--package", str(context["package"]), "--run-dir", str(context["run_dir"]),
                "--root", str(context["fixture"]["root"]), "--out", str(context["result_path"]),
            ],
            cwd=SKILL_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("INPUT_INVALID", result.stderr)
        self.assertNotIn("SECRET_CANDIDATE_BODY", result.stdout + result.stderr)
        self.assertNotIn(str(context["candidate_path"]), result.stdout + result.stderr)
        self.assertFalse(context["result_path"].exists())
