#!/usr/bin/env python3
"""Focused tests for deterministic Phase 4A graph and evidence projection."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from phase3_bundle_audit import _build_snapshot_inputs  # noqa: E402
from phase3_run import MEMBER_PATHS, assemble_phase3_run  # noqa: E402
import phase3_run as phase3_run_module  # noqa: E402
from repository_documentation_evidence import build_repository_documentation_evidence  # noqa: E402
from test_phase3_bundle_audit import _fixture  # noqa: E402
from test_phase3_run import _write_inputs  # noqa: E402


def _api(testcase):
    testcase.assertIsNotNone(importlib.util.find_spec("phase4_graph"), "Phase 4A graph builder is missing")
    return importlib.import_module("phase4_graph")


def _run_package(testcase, source_files, *, python_v11=False):
    fixture = _fixture(testcase, source_files)
    if python_v11:
        from python_static_analysis import analyze_python_artifacts_v11

        g01 = audit_coverage(fixture["project_index"], fixture["coverage"], fixture["root"])
        analysis, evidence = analyze_python_artifacts_v11(
            fixture["project_index"],
            fixture["coverage"],
            fixture["stack_profile"],
            fixture["phase3a_evidence"],
            lambda path, _entry: fixture["sources"][path],
            frozenset(fixture["sources"]),
            g01_status=g01.status,
            unknown_files=g01.measurements["unknown_files"],
        )
        fixture["python_analysis"] = analysis
        fixture["python_evidence"] = evidence

    input_paths, _contents = _write_inputs(testcase, fixture)
    run_dir = fixture["work"] / "phase3-run"
    manifest = assemble_phase3_run(root=fixture["root"], input_paths=input_paths, out=run_dir)
    return fixture, run_dir, manifest


def _run_documentation_package(testcase, source_files, selections):
    fixture = _fixture(testcase, source_files)
    documentation = build_repository_documentation_evidence(
        root=fixture["root"],
        project_index=fixture["project_index"],
        coverage=fixture["coverage"],
        selections=selections,
        generated_at="2026-10-01T00:00:00Z",
    )
    input_paths, _contents = _write_inputs(
        testcase,
        fixture,
        {"documentation_evidence": documentation},
    )
    run_dir = fixture["work"] / "phase3-run-with-documentation"
    manifest = assemble_phase3_run(root=fixture["root"], input_paths=input_paths, out=run_dir)
    return fixture, run_dir, manifest, documentation


def _minimal_evidence(identifier="EVID-1", *, level="E1", summary="source fact"):
    return {
        "id": identifier,
        "level": level,
        "kind": "source" if level == "E1" else "config",
        "summary": summary,
        "confidence": 1.0,
        "locator": {"path": "src/module.py", "line_start": 1},
    }


def _symbol(identifier, name, path="src/module.py"):
    return {
        "id": identifier,
        "language": "python",
        "kind": "function",
        "name": name,
        "qualified_name": f"src.module.{name}",
        "path": path,
        "extraction_method": "python-ast",
        "certainty": "VERIFIED",
        "line_start": 1,
        "line_end": 1,
        "evidence_ids": ["EVID-1"],
    }


def _relation(identifier, kind, source_id, target_id, unresolved_target, certainty, line):
    return {
        "id": identifier,
        "language": "python",
        "kind": kind,
        "source_id": source_id,
        "target_id": target_id,
        "unresolved_target": unresolved_target,
        "path": "src/module.py",
        "extraction_method": "python-ast",
        "certainty": certainty,
        "line_start": line,
        "line_end": line,
        "evidence_ids": ["EVID-1"],
    }


class CanonicalTupleIdentityTests(unittest.TestCase):
    def test_tuple_digest_is_json_array_utf8_without_a_trailing_newline(self):
        api = _api(self)
        parts = ("雪", {"z": 2, "a": 1})
        expected_bytes = '["雪",{"a":1,"z":2}]'.encode("utf-8")

        self.assertEqual(hashlib.sha256(expected_bytes).hexdigest(), api.canonical_tuple_sha256(parts))

    def test_tuple_digest_is_stable_for_mapping_order_and_changes_with_tuple_content(self):
        api = _api(self)

        left = api.canonical_tuple_sha256(("repo", {"a": 1, "b": 2}))
        right = api.canonical_tuple_sha256(("repo", {"b": 2, "a": 1}))

        self.assertEqual(left, right)
        self.assertNotEqual(left, api.canonical_tuple_sha256(("repo", {"a": 1, "b": 3})))

    def test_tuple_digest_rejects_non_tuples_non_finite_and_non_json_values(self):
        api = _api(self)
        for value in (
            [],
            (float("nan"),),
            (float("inf"),),
            (object(),),
            ({1: "non-string key"},),
            ((1, 2),),
        ):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                api.canonical_tuple_sha256(value)


class Phase4EvidenceFoldTests(unittest.TestCase):
    def test_identical_evidence_payloads_deduplicate_with_sorted_source_members(self):
        api = _api(self)
        item = _minimal_evidence()

        folded = api._fold_evidence_members([
            ("python/evidence.json", {"items": [item]}),
            ("phase3a/evidence.json", {"items": [copy.deepcopy(item)]}),
        ])

        self.assertEqual(1, len(folded))
        self.assertEqual(
            ["phase3a/evidence.json", "python/evidence.json"],
            folded[0]["source_members"],
        )

    def test_conflicting_duplicate_evidence_and_unexpected_levels_fail(self):
        api = _api(self)
        original = _minimal_evidence()
        conflict = {**original, "summary": "different payload"}

        with self.assertRaises(api.Phase4GraphError) as raised:
            api._fold_evidence_members([
                ("phase3a/evidence.json", {"items": [original]}),
                ("python/evidence.json", {"items": [conflict]}),
            ])
        self.assertEqual("DUPLICATE_ID_CONFLICT", raised.exception.code)

        with self.assertRaises(api.Phase4GraphError) as raised:
            api._fold_evidence_members([
                ("phase3a/evidence.json", {"items": [_minimal_evidence(level="E6")]}),
            ])
        self.assertEqual("EVIDENCE_LEVEL_INVALID", raised.exception.code)


class Phase4StaticProjectionTests(unittest.TestCase):
    def test_all_source_relation_kinds_and_role_families_are_retained(self):
        api = _api(self)
        source_id = "SYM-" + "1" * 24
        target_id = "SYM-" + "2" * 24
        kinds = [
            "DEFINES", "IMPORTS", "CALL_CANDIDATE", "EXTENDS", "ROUTE_TO",
            "IMPLEMENTS", "EXPORTS", "USES_API",
        ]
        relations = [
            _relation(
                f"REL-{index:024x}", kind, source_id,
                target_id if kind in {"DEFINES", "CALL_CANDIDATE", "EXTENDS", "IMPLEMENTS"} else None,
                "route:fastapi:GET:endpoint-redacted" if kind == "ROUTE_TO" else (
                    "unresolved:module" if kind in {"IMPORTS", "EXPORTS", "USES_API"} else None
                ),
                "CANDIDATE" if kind in {"CALL_CANDIDATE", "ROUTE_TO", "USES_API"} else "VERIFIED",
                index,
            )
            for index, kind in enumerate(kinds, start=1)
        ]
        roles = [
            {
                "id": f"ROLE-{index:024x}", "language": "python", "kind": role_kind,
                "symbol_id": source_id, "path": "src/module.py", "extraction_method": "python-ast",
                "certainty": "CANDIDATE", "line_start": index, "line_end": index,
                "evidence_ids": ["EVID-1"],
            }
            for index, role_kind in enumerate((
                "data_model_candidate", "test_candidate", "component_candidate",
                "hook_candidate", "page_candidate", "store_candidate", "api_client_candidate",
            ), start=1)
        ]
        python_source, python_target = source_id, target_id
        java_source, java_target = "SYM-" + "3" * 24, "SYM-" + "4" * 24
        frontend_source, frontend_target = "SYM-" + "5" * 24, "SYM-" + "6" * 24
        python_relations = [
            {**row, "source_id": python_source, "target_id": python_target if row["target_id"] else None}
            for row in relations[:5]
        ]
        java_relation = {**relations[5], "language": "java", "source_id": java_source, "target_id": java_target}
        frontend_relations = [
            {**row, "language": "typescript", "source_id": frontend_source}
            for row in relations[6:]
        ]
        python_symbols = [_symbol(python_source, "python_source"), _symbol(python_target, "python_target")]
        java_symbols = [_symbol(java_source, "java_source"), _symbol(java_target, "java_target")]
        frontend_symbols = [_symbol(frontend_source, "frontend_source"), _symbol(frontend_target, "frontend_target")]
        frontend_roles = [
            {**role, "language": "typescript", "symbol_id": frontend_source}
            for role in roles[2:]
        ]
        for symbol in java_symbols:
            symbol["language"] = "java"
        for symbol in frontend_symbols:
            symbol["language"] = "typescript"
        python_analysis = {
            "artifact_kind": "static-analysis", "schema_version": "1.1.0",
            "symbols": python_symbols, "relations": python_relations, "roles": roles[:2],
        }
        java_analysis = {
            "artifact_kind": "static-analysis", "schema_version": "1.2.0",
            "symbols": java_symbols, "relations": [java_relation], "roles": [],
        }
        frontend_analysis = {
            "artifact_kind": "static-analysis", "schema_version": "1.5.0",
            "symbols": frontend_symbols, "relations": frontend_relations, "roles": frontend_roles,
        }

        facts = api._collect_static_facts([
            ("python/static-analysis.json", python_analysis),
            ("java-v12/static-analysis.json", java_analysis),
            ("frontend/static-analysis.json", frontend_analysis),
        ], {"src/module.py"}, {"EVID-1"})

        self.assertEqual(set(kinds), {row["kind"] for row in facts["relations"].values()})
        self.assertEqual(set(role["kind"] for role in roles), {
            role["kind"] for group in facts["roles"].values() for role in group
        })
        candidate = next(row for row in facts["relations"].values() if row["kind"] == "CALL_CANDIDATE")
        self.assertEqual(target_id, candidate["target_id"])
        self.assertEqual("CANDIDATE", candidate["certainty"])
        route = next(row for row in facts["relations"].values() if row["kind"] == "ROUTE_TO")
        self.assertIsNone(route["target_id"])
        self.assertEqual("route:fastapi:GET:endpoint-redacted", route["unresolved_target"])

    def test_conflicting_symbol_and_relation_ids_fail_instead_of_overwriting(self):
        api = _api(self)
        source_id = "SYM-" + "1" * 24
        target_id = "SYM-" + "2" * 24
        relation = _relation("REL-" + "1" * 24, "DEFINES", source_id, target_id, None, "VERIFIED", 1)
        base = {
            "artifact_kind": "static-analysis", "schema_version": "1.1.0",
            "symbols": [_symbol(source_id, "source"), _symbol(target_id, "target")],
            "relations": [relation], "roles": [],
        }
        changed_symbol = copy.deepcopy(base)
        changed_symbol["symbols"][0]["name"] = "conflicting"
        with self.assertRaises(api.Phase4GraphError) as raised:
            api._collect_static_facts([
                ("python/static-analysis.json", base),
                ("python/static-analysis.json", changed_symbol),
            ], {"src/module.py"}, {"EVID-1"})
        self.assertEqual("DUPLICATE_ID_CONFLICT", raised.exception.code)

        changed_relation = copy.deepcopy(base)
        changed_relation["relations"][0]["certainty"] = "CANDIDATE"
        with self.assertRaises(api.Phase4GraphError) as raised:
            api._collect_static_facts([
                ("python/static-analysis.json", base),
                ("python/static-analysis.json", changed_relation),
            ], {"src/module.py"}, {"EVID-1"})
        self.assertEqual("DUPLICATE_ID_CONFLICT", raised.exception.code)

    def test_missing_symbol_or_evidence_references_fail_closed(self):
        api = _api(self)
        source_id = "SYM-" + "1" * 24
        dangling = _relation("REL-" + "1" * 24, "DEFINES", source_id, "SYM-" + "9" * 24, None, "VERIFIED", 1)
        artifact = {
            "artifact_kind": "static-analysis", "schema_version": "1.1.0",
            "symbols": [_symbol(source_id, "source")], "relations": [dangling], "roles": [],
        }

        with self.assertRaises(api.Phase4GraphError) as raised:
            api._collect_static_facts([("python/static-analysis.json", artifact)], {"src/module.py"}, {"EVID-1"})
        self.assertEqual("SYMBOL_MISSING", raised.exception.code)

        artifact["relations"] = []
        artifact["symbols"][0]["evidence_ids"] = ["EVID-missing"]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api._collect_static_facts([("python/static-analysis.json", artifact)], {"src/module.py"}, {"EVID-1"})
        self.assertEqual("EVIDENCE_MISSING", raised.exception.code)

class Phase4GraphBuilderTests(unittest.TestCase):
    def _build(self, fixture, run_dir, name="phase4-output"):
        api = _api(self)
        output = fixture["work"] / name
        result = api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        return api, output, result

    def test_reproject_phase4_graph_artifacts_is_validated_and_non_publishing(self):
        fixture, run_dir, _manifest = _run_package(self, {"src/module.py": b"def main():\n    return 1\n"})
        api = _api(self)

        with patch.object(api, "validate_phase3_run", wraps=api.validate_phase3_run) as full_validation:
            graph, evidence = api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
        self.assertEqual(1, full_validation.call_count)
        api.validate_phase4_graph_artifacts(graph, evidence)

        output = fixture["work"] / "reprojection-must-not-publish"
        self.assertFalse(output.exists())
        self.assertEqual("phase3-run", graph["source_run"]["artifact_kind"])
        self.assertEqual(graph["source_run"], evidence["source_run"])

        member = run_dir / MEMBER_PATHS["project_index"]
        member.write_bytes(member.read_bytes() + b" ")
        with self.assertRaises(api.Phase4GraphError):
            api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
        self.assertFalse(output.exists())

    def test_build_uses_one_full_phase3_semantic_validation(self):
        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"small pinned snapshot\n"})
        api = _api(self)
        output = fixture["work"] / "single-semantic-validation"

        with patch.object(api, "validate_phase3_run", wraps=api.validate_phase3_run) as full_validation:
            api.build_phase4_graph(run_dir, root=fixture["root"], out=output)

        self.assertEqual(1, full_validation.call_count)
        self.assertTrue((output / "knowledge-graph.json").is_file())
        self.assertTrue((output / "evidence.json").is_file())

    def test_pass_build_preserves_run_identity_metadata_and_all_indexed_files(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"A small source snapshot.\n"})
        api, output, summary = self._build(fixture, run_dir)
        graph = load_artifact(output / "knowledge-graph.json")
        evidence = load_artifact(output / "evidence.json")

        self.assertEqual({"knowledge-graph.json", "evidence.json"}, {path.name for path in output.iterdir()})
        self.assertEqual("PASS", summary["status"])
        self.assertEqual("PASS", graph["status"])
        self.assertEqual("PASS", evidence["status"])
        self.assertEqual(manifest["repository_revision"], graph["repository_revision"])
        self.assertEqual(manifest["snapshot_kind"], graph["snapshot_kind"])
        self.assertEqual(manifest["source_metadata"], graph["source_metadata"])
        self.assertEqual(manifest["source_metadata"], evidence["source_metadata"])
        self.assertEqual(manifest["generated_at"], graph["generated_at"])
        self.assertEqual(manifest["generated_at"], evidence["generated_at"])
        self.assertEqual([], graph["derived_inputs"])
        self.assertEqual([], evidence["derived_inputs"])
        self.assertEqual(manifest["e1_audit"], graph["source_run"]["e1_audit"])
        self.assertEqual(hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(), graph["source_run"]["manifest_sha256"])
        self.assertEqual(graph["source_run"], evidence["source_run"])
        self.assertEqual(sorted(manifest["members"], key=lambda row: row["path"]), graph["source_run"]["members"])

        index = load_artifact(run_dir / MEMBER_PATHS["project_index"])
        file_nodes = [node for node in graph["nodes"] if node["type"] == "File"]
        self.assertEqual({row["path"] for row in index["files"]}, {node["properties"]["path"] for node in file_nodes})
        self.assertTrue(all("reason" not in node["properties"] for node in file_nodes))
        self.assertTrue(all(item["level"] in {"E1", "E2"} for item in evidence["items"]))
        self.assertTrue(all(item["source_members"] == sorted(set(item["source_members"])) for item in evidence["items"]))
        snapshot_key = api.canonical_tuple_sha256((
            manifest["repository_revision"], manifest["snapshot_kind"], manifest["source_metadata"],
        ))
        project_id = "PROJ-" + api.canonical_tuple_sha256(("project", snapshot_key))
        self.assertEqual(project_id, next(node["id"] for node in graph["nodes"] if node["type"] == "Project"))
        index_row = index["files"][0]
        file_node = next(node for node in file_nodes if node["properties"]["path"] == index_row["path"])
        self.assertIn("vcs_object_id", index_row)
        self.assertEqual(index_row["vcs_object_id"], file_node["properties"]["vcs_object_id"])
        self.assertEqual(
            "FILE-" + api.canonical_tuple_sha256(("file", project_id, index_row["path"], index_row["sha256"])),
            file_node["id"],
        )
        contains_edge = next(edge for edge in graph["edges"] if edge["type"] == "CONTAINS")
        self.assertTrue(all(edge["source_members"] == sorted(set(edge["source_members"])) for edge in graph["edges"]))
        self.assertTrue(all(edge["source_members"] for edge in graph["edges"]))
        self.assertEqual(
            "EDGE-" + api.canonical_tuple_sha256(("contains", contains_edge["from"], contains_edge["to"])),
            contains_edge["id"],
        )

    def test_documentation_run_folds_redacted_e2_onto_the_indexed_file_and_cli(self):
        secret_text = "Repository says this project is an example service."
        fixture, run_dir, manifest, source_documentation = _run_documentation_package(
            self,
            {"README.md": f"# Example\n{secret_text}\n".encode("utf-8")},
            [("README.md", 2, 2)],
        )
        api = _api(self)
        output = fixture["work"] / "phase4-documentation"
        api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        graph = load_artifact(output / "knowledge-graph.json")
        evidence = load_artifact(output / "evidence.json")

        self.assertEqual("1.1.0", manifest["schema_version"])
        self.assertEqual(("knowledge-graph", "1.2.0"), (graph["artifact_kind"], graph["schema_version"]))
        self.assertEqual(("evidence", "1.4.0"), (evidence["artifact_kind"], evidence["schema_version"]))
        self.assertEqual("1.1.0", graph["source_run"]["schema_version"])
        self.assertEqual(graph["source_run"], evidence["source_run"])
        self.assertIn("documentation/evidence.json", {row["path"] for row in graph["source_run"]["members"]})

        self.assertEqual(1, len(source_documentation["items"]))
        source_item = source_documentation["items"][0]
        folded_item = next(item for item in evidence["items"] if item["id"] == source_item["id"])
        self.assertEqual("E2", folded_item["level"])
        self.assertEqual("repository_documentation", folded_item["kind"])
        self.assertEqual(["documentation/evidence.json"], folded_item["source_members"])
        self.assertEqual(source_item["source_bytes"], folded_item["source_bytes"])
        self.assertEqual(source_item["locator"], folded_item["locator"])
        self.assertNotIn(secret_text, (output / "evidence.json").read_text(encoding="utf-8"))

        readme_node = next(
            node for node in graph["nodes"]
            if node["type"] == "File" and node["properties"]["path"] == "README.md"
        )
        self.assertEqual([source_item["id"]], readme_node["evidence_ids"])
        self.assertFalse(any(node["type"] == "BusinessCapability" for node in graph["nodes"]))
        api.validate_phase4_graph_artifacts(graph, evidence)

        cli_output = fixture["work"] / "phase4-documentation-cli"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "build_phase4_graph.py"), "--run-dir", str(run_dir),
             "--root", str(fixture["root"]), "--out", str(cli_output)],
            cwd=SKILL_ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PASS", result.stdout)
        self.assertEqual((output / "knowledge-graph.json").read_bytes(), (cli_output / "knowledge-graph.json").read_bytes())
        self.assertEqual((output / "evidence.json").read_bytes(), (cli_output / "evidence.json").read_bytes())

    def test_forged_documentation_member_on_legacy_run_is_rejected(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"legacy source\n"})
        forged = copy.deepcopy(manifest)
        forged["members"].append({
            "path": "documentation/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "sha256": "a" * 64,
        })
        forged_text = json.dumps(forged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        (run_dir / "phase3-run.json").write_text(forged_text, encoding="utf-8", newline="\n")
        output = fixture["work"] / "forged-documentation"

        with self.assertRaises(_api(self).Phase4GraphError) as raised:
            _api(self).build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertEqual("RUN_INVALID", raised.exception.code)
        self.assertFalse(output.exists())

    def test_documentation_pair_rejects_unlinked_evidence_and_version_mix(self):
        fixture, run_dir, _manifest, _source_documentation = _run_documentation_package(
            self, {"README.md": b"# Example\nDeclaration line.\n"}, [("README.md", 2, 2)],
        )
        api = _api(self)
        graph, evidence = api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
        graph = copy.deepcopy(graph)
        readme_node = next(
            node for node in graph["nodes"]
            if node["type"] == "File" and node["properties"]["path"] == "README.md"
        )
        readme_node["evidence_ids"] = []
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

        graph, evidence = api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
        invalid_evidence = copy.deepcopy(evidence)
        invalid_evidence["schema_version"] = "1.2.0"
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, invalid_evidence)
        self.assertEqual("SCHEMA_INVALID", raised.exception.code)

        invalid_graph = copy.deepcopy(graph)
        invalid_graph["schema_version"] = "1.1.0"
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(invalid_graph, evidence)
        self.assertEqual("SCHEMA_INVALID", raised.exception.code)

    def test_java_v12_and_v13_evidence_is_folded_with_both_source_members(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("JDK compiler is not installed")
        fixture = _fixture(self, {"src/Api.java": b'class Api { String value() { return "ok"; } }\n'})
        g01 = audit_coverage(fixture["project_index"], fixture["coverage"], fixture["root"])
        source_reader, regular_paths = _build_snapshot_inputs(fixture["root"], fixture["project_index"])
        java = importlib.import_module("java_static_analysis")
        framework = importlib.import_module("java_framework_static_analysis")
        v12_analysis, v12_evidence = java.analyze_java_artifacts(
            fixture["project_index"], fixture["coverage"], fixture["stack_profile"],
            fixture["phase3a_evidence"], source_reader, regular_paths,
            g01_status=g01.status, unknown_files=g01.measurements["unknown_files"],
        )
        v13_analysis, v13_evidence = framework.analyze_java_framework_candidates(
            fixture["project_index"], fixture["coverage"], fixture["stack_profile"],
            fixture["phase3a_evidence"], v12_analysis, v12_evidence,
            source_reader, regular_paths, g01_status=g01.status,
            unknown_files=g01.measurements["unknown_files"],
        )
        input_paths, _ = _write_inputs(self, fixture, {
            "java_analysis_v12": v12_analysis,
            "java_evidence_v12": v12_evidence,
            "java_analysis_v13": v13_analysis,
            "java_evidence_v13": v13_evidence,
        })
        run_dir = fixture["work"] / "java-phase3-run"
        manifest = assemble_phase3_run(root=fixture["root"], input_paths=input_paths, out=run_dir)
        api = _api(self)

        graph, evidence = api.reproject_phase4_graph_artifacts(run_dir, root=fixture["root"])
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        referenced_java_ids = {
            evidence_id
            for analysis in (v12_analysis, v13_analysis)
            for fact in analysis["symbols"] + analysis["relations"] + analysis["roles"]
            for evidence_id in fact["evidence_ids"]
        }
        self.assertTrue(referenced_java_ids)
        self.assertTrue(referenced_java_ids <= set(evidence_by_id))

        phase3a_ids = {item["id"] for item in fixture["phase3a_evidence"]["items"]}
        v13_evidence_ids = {item["id"] for item in v13_evidence["items"]}
        shared_id = next(identifier for identifier in referenced_java_ids
                         if identifier in v13_evidence_ids and identifier not in phase3a_ids)
        source_members = evidence_by_id[shared_id]["source_members"]
        self.assertEqual(sorted(source_members), source_members)
        self.assertTrue({
            MEMBER_PATHS["java_evidence_v12"], MEMBER_PATHS["java_evidence_v13"],
        } <= set(source_members))
        graph_evidence_ids = {identifier for record in [*graph["nodes"], *graph["edges"]]
                              for identifier in record["evidence_ids"]}
        self.assertTrue(graph_evidence_ids <= set(evidence_by_id))

        members = api._load_run_members(run_dir, manifest)
        members[MEMBER_PATHS["java_evidence_v12"]]["items"] = [
            item for item in members[MEMBER_PATHS["java_evidence_v12"]]["items"]
            if item["id"] != shared_id
        ]
        members[MEMBER_PATHS["java_evidence_v13"]]["items"] = [
            item for item in members[MEMBER_PATHS["java_evidence_v13"]]["items"]
            if item["id"] != shared_id
        ]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api._phase4_artifacts(
                manifest,
                hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(),
                members,
            )
        self.assertEqual("EVIDENCE_MISSING", raised.exception.code)

    def test_file_projection_does_not_invent_an_absent_vcs_object_id(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"optional identity\n"})
        api = _api(self)
        members = api._load_run_members(run_dir, manifest)
        index = members[MEMBER_PATHS["project_index"]]
        index["files"][0].pop("vcs_object_id")

        graph, _evidence = api._phase4_artifacts(
            manifest,
            hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(),
            members,
        )

        file_node = next(node for node in graph["nodes"] if node["type"] == "File")
        self.assertNotIn("vcs_object_id", file_node["properties"])

    def test_partial_build_preserves_unknown_files_and_primary_secondary_surfaces(self):
        fixture, run_dir, manifest = _run_package(self, {
            "backend/tests/fixtures/user.json": b"{\"id\": 1}\n",
            "unclassified.oddity": b"unknown file\n",
        })
        api, output, _summary = self._build(fixture, run_dir)
        graph = load_artifact(output / "knowledge-graph.json")
        index = load_artifact(run_dir / MEMBER_PATHS["project_index"])
        coverage = load_artifact(run_dir / MEMBER_PATHS["coverage"])
        files = {node["properties"]["path"]: node for node in graph["nodes"] if node["type"] == "File"}
        surfaces = {node["label"]: node["id"] for node in graph["nodes"] if node["type"] == "RepositorySurface"}
        contains = [edge for edge in graph["edges"] if edge["type"] == "CONTAINS"]

        self.assertEqual("PARTIAL", manifest["status"])
        self.assertEqual("PARTIAL", graph["status"])
        self.assertEqual("PARTIAL", load_artifact(output / "evidence.json")["status"])
        self.assertEqual({row["path"] for row in index["files"]}, set(files))
        fixture_path = "backend/tests/fixtures/user.json"
        coverage_row = next(row for row in coverage["entries"] if row["path"] == fixture_path)
        fixture_props = files[fixture_path]["properties"]
        self.assertEqual(coverage_row["surface"], fixture_props["surface"])
        self.assertEqual(coverage_row["secondary_surfaces"], fixture_props["secondary_surfaces"])
        self.assertTrue(set(coverage_row["secondary_surfaces"]) & set(surfaces))
        self.assertIn("unclassified.oddity", files)
        self.assertEqual("UNKNOWN", files["unclassified.oddity"]["properties"]["classification"])
        file_id = files[fixture_path]["id"]
        surface_edges = [edge for edge in contains if edge["to"] == file_id]
        self.assertEqual(
            {surfaces[name] for name in [coverage_row["surface"], *coverage_row["secondary_surfaces"]]},
            {edge["from"] for edge in surface_edges},
        )
        self.assertNotIn(str(fixture["root"]), dumps_artifact(graph))

    def test_python_relations_roles_and_repeated_evidence_keep_source_identity(self):
        source = (
            "from dataclasses import dataclass\n"
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n"
            "from unittest import TestCase\n"
            "app = FastAPI()\n"
            "@dataclass\n"
            "class Record: pass\n"
            "class Payload(BaseModel): pass\n"
            "class Suite(TestCase): pass\n"
            "def helper(): return 1\n"
            "@app.get('/private/path')\n"
            "def endpoint(): return helper()\n"
            "def test_checkout(): return Record\n"
        ).encode("utf-8")
        fixture, run_dir, _manifest = _run_package(self, {"src/service.py": source}, python_v11=True)
        _api, output, _summary = self._build(fixture, run_dir)
        graph = load_artifact(output / "knowledge-graph.json")
        evidence = load_artifact(output / "evidence.json")
        evidence_ids = {item["id"] for item in evidence["items"]}
        edges = {edge["id"]: edge for edge in graph["edges"]}
        symbols = {node["id"]: node for node in graph["nodes"] if node["type"] == "Symbol"}

        analysis = load_artifact(run_dir / MEMBER_PATHS["python_analysis"])
        self.assertTrue({"DEFINES", "IMPORTS", "CALL_CANDIDATE", "EXTENDS", "ROUTE_TO"} <= {
            edge["type"] for edge in edges.values()
        })
        for relation in analysis["relations"]:
            edge = edges[relation["id"]]
            self.assertEqual(relation, edge["source_record"])
            self.assertEqual((relation["kind"], relation["source_id"], relation["target_id"]),
                             (edge["type"], edge["from"], edge["to"]))
            self.assertEqual(relation["certainty"], edge["certainty"])
            self.assertEqual(relation["unresolved_target"], edge["unresolved_target"])
            self.assertEqual(["python/static-analysis.json"], edge["source_members"])
        route = next(edge for edge in edges.values() if edge["type"] == "ROUTE_TO")
        self.assertIsNone(route["to"])
        self.assertIn("endpoint-redacted", route["unresolved_target"])

        self.assertTrue(any(role["kind"] == "data_model_candidate" for node in symbols.values() for role in node["properties"]["roles"]))
        self.assertTrue(any(role["kind"] == "test_candidate" for node in symbols.values() for role in node["properties"]["roles"]))
        for symbol in symbols.values():
            source_record = symbol["properties"]["source_record"]
            self.assertIn(source_record["path"], {
                node["properties"]["path"] for node in graph["nodes"] if node["type"] == "File"
            })
        for record in [*graph["nodes"], *graph["edges"]]:
            self.assertLessEqual(set(record["evidence_ids"]), evidence_ids)

        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        shared_ids = {item["id"] for item in load_artifact(run_dir / MEMBER_PATHS["phase3a_evidence"])["items"]}
        shared_id = next(identifier for identifier in shared_ids if identifier in evidence_by_id)
        self.assertEqual(
            ["phase3a/evidence.json", "python/evidence.json"],
            evidence_by_id[shared_id]["source_members"],
        )

    def test_duplicate_java_relation_keeps_both_sorted_source_members(self):
        fixture, run_dir, manifest = _run_package(self, {"src/Service.java": b"class Service {}\n"})
        api = _api(self)
        members = api._load_run_members(run_dir, manifest)
        source_id = "SYM-JAVA-SOURCE"
        target_id = "SYM-JAVA-TARGET"
        source = {**_symbol(source_id, "Service", "src/Service.java"), "language": "java", "evidence_ids": []}
        target = {**_symbol(target_id, "invoke", "src/Service.java"), "language": "java", "evidence_ids": []}
        relation = {
            **_relation("REL-JAVA-DUPLICATE", "CALL_CANDIDATE", source_id, target_id, None, "CANDIDATE", 1),
            "language": "java",
            "path": "src/Service.java",
            "evidence_ids": [],
        }
        revision = manifest["repository_revision"]
        # Deliberately insert the later member first; output lineage must sort by member path.
        v13_path = MEMBER_PATHS["java_analysis_v13"]
        v12_path = MEMBER_PATHS["java_analysis_v12"]
        members[v13_path] = {
            "artifact_kind": "static-analysis", "schema_version": "1.3.0",
            "repository_revision": revision, "symbols": [], "relations": [copy.deepcopy(relation)], "roles": [],
        }
        members[v12_path] = {
            "artifact_kind": "static-analysis", "schema_version": "1.2.0",
            "repository_revision": revision, "symbols": [source, target], "relations": [copy.deepcopy(relation)], "roles": [],
        }
        manifest = copy.deepcopy(manifest)
        manifest["members"].extend([
            {"path": v13_path, "artifact_kind": "static-analysis", "schema_version": "1.3.0", "sha256": "1" * 64},
            {"path": v12_path, "artifact_kind": "static-analysis", "schema_version": "1.2.0", "sha256": "2" * 64},
        ])

        graph, evidence = api._phase4_artifacts(
            manifest,
            hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(),
            members,
        )

        edge = next(edge for edge in graph["edges"] if edge["id"] == relation["id"])
        self.assertEqual([v12_path, v13_path], edge["source_members"])
        self.assertNotIn("source_member", edge)
        api.validate_phase4_graph_artifacts(graph, evidence)

        invalid = copy.deepcopy(graph)
        next(edge for edge in invalid["edges"] if edge["id"] == relation["id"])["source_members"] = [v13_path, v12_path]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(invalid, evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

        invalid = copy.deepcopy(graph)
        next(edge for edge in invalid["edges"] if edge["id"] == relation["id"])["source_members"] = ["phase3a/evidence.json"]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(invalid, evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

        invalid = copy.deepcopy(graph)
        next(edge for edge in invalid["edges"] if edge["type"] == "CONTAINS")["source_members"] = ["phase3a/stack-profile.json"]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(invalid, evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

    def test_wrong_root_member_tampering_output_inside_or_existing_target_and_cleanup_fail_safely(self):
        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"safe\n"})
        api = _api(self)
        output = fixture["work"] / "must-not-publish"
        wrong_root = _fixture(self, {"README.md": b"different repo\n"})["root"]

        with self.assertRaises(api.Phase4GraphError) as raised:
            api.build_phase4_graph(run_dir, root=wrong_root, out=output)
        self.assertEqual("RUN_INVALID", raised.exception.code)
        self.assertFalse(output.exists())

        member = run_dir / MEMBER_PATHS["project_index"]
        member.write_bytes(member.read_bytes() + b" ")
        with self.assertRaises(api.Phase4GraphError):
            api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertFalse(output.exists())

        inside = fixture["root"] / "phase4-output"
        with self.assertRaises(api.Phase4GraphError):
            api.build_phase4_graph(run_dir, root=fixture["root"], out=inside)
        existing = fixture["work"] / "existing"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(api.Phase4GraphError):
            api.build_phase4_graph(run_dir, root=fixture["root"], out=existing)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_unsafe_manifest_path_and_member_link_fail_before_publication(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"safe\n"})
        api = _api(self)
        output = fixture["work"] / "unsafe-input-output"
        manifest_path = run_dir / "phase3-run.json"
        changed_manifest = copy.deepcopy(manifest)
        changed_manifest["members"][0]["path"] = "../outside.json"
        manifest_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
        with self.assertRaises(api.Phase4GraphError):
            api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertFalse(output.exists())

        # Restore this task-owned fixture package before replacing one member with a symlink.
        manifest_path.write_text(dumps_artifact(manifest), encoding="utf-8")
        member = run_dir / MEMBER_PATHS["project_index"]
        outside = fixture["work"] / "outside-index.json"
        outside.write_bytes(member.read_bytes())
        original = member.read_bytes()
        member.unlink()
        try:
            member.symlink_to(outside)
        except OSError:
            member.write_bytes(original)
            self.skipTest("filesystem symlinks are unavailable")
        with self.assertRaises(api.Phase4GraphError):
            api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertFalse(output.exists())

    def test_failed_publish_and_final_freshness_failures_remove_only_staging(self):
        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"safe\n"})
        api = _api(self)
        output = fixture["work"] / "publish-fails"

        with patch.object(api, "_publish_directory", side_effect=OSError("controlled publish failure")):
            with self.assertRaises(api.Phase4GraphError):
                api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertFalse(output.exists())
        self.assertEqual([], list(fixture["work"].glob(".publish-fails.phase4-staging-*")))

        for mutation_kind in ("member", "manifest"):
            fixture, run_dir, _manifest = _run_package(self, {"README.md": b"safe\n"})
            output = fixture["work"] / f"changed-{mutation_kind}-run"
            project = api._phase4_artifacts

            def mutate_package_after_projection(manifest, manifest_sha, members):
                graph, evidence = project(manifest, manifest_sha, members)
                if mutation_kind == "member":
                    path = run_dir / MEMBER_PATHS["project_index"]
                else:
                    path = run_dir / "phase3-run.json"
                path.write_bytes(path.read_bytes() + b" ")
                return graph, evidence

            with patch.object(api, "_phase4_artifacts", side_effect=mutate_package_after_projection):
                with self.assertRaises(api.Phase4GraphError):
                    api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
            self.assertFalse(output.exists())
            self.assertEqual([], list(fixture["work"].glob(f".{output.name}.phase4-staging-*")))

        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"safe\n"})
        output = fixture["work"] / "g01-drift-during-final-check"
        source = fixture["root"] / "README.md"
        real_audit = phase3_run_module.audit_coverage
        calls = 0

        def drift_between_final_g01_samples(*args, **kwargs):
            nonlocal calls
            result = real_audit(*args, **kwargs)
            calls += 1
            if calls == 1:
                source.write_bytes(b"changed while final freshness check was running\n")
            return result

        with patch.object(phase3_run_module, "audit_coverage", side_effect=drift_between_final_g01_samples):
            with self.assertRaises(api.Phase4GraphError):
                api.build_phase4_graph(run_dir, root=fixture["root"], out=output)
        self.assertEqual(2, calls)
        self.assertFalse(output.exists())
        self.assertEqual([], list(fixture["work"].glob(".g01-drift-during-final-check.phase4-staging-*")))

    def test_repeat_build_is_byte_identical_and_cli_smoke_is_redacted(self):
        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"stable\n"})
        api, first, _summary = self._build(fixture, run_dir, "first")
        second = fixture["work"] / "second"
        api.build_phase4_graph(run_dir, root=fixture["root"], out=second)
        self.assertEqual((first / "knowledge-graph.json").read_bytes(), (second / "knowledge-graph.json").read_bytes())
        self.assertEqual((first / "evidence.json").read_bytes(), (second / "evidence.json").read_bytes())

        cli_output = fixture["work"] / "cli-output"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "build_phase4_graph.py"), "--run-dir", str(run_dir),
             "--root", str(fixture["root"]), "--out", str(cli_output)],
            cwd=SKILL_ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PASS", result.stdout)
        self.assertNotIn(str(fixture["root"]), result.stdout)
        self.assertNotIn(str(fixture["root"]), result.stderr)

    def test_output_semantic_validator_rejects_dangling_evidence(self):
        fixture, run_dir, _manifest = _run_package(self, {"README.md": b"small\n"})
        api, output, _summary = self._build(fixture, run_dir)
        graph = load_artifact(output / "knowledge-graph.json")
        evidence = load_artifact(output / "evidence.json")
        graph = copy.deepcopy(graph)
        file_node = next(node for node in graph["nodes"] if node["type"] == "File")
        file_node["evidence_ids"] = ["EVID-does-not-exist"]

        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, evidence)
        self.assertEqual("EVIDENCE_MISSING", raised.exception.code)

        graph = load_artifact(output / "knowledge-graph.json")
        evidence = copy.deepcopy(load_artifact(output / "evidence.json"))
        evidence["snapshot_kind"] = "git-tree"
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, evidence)
        self.assertEqual("METADATA_MISMATCH", raised.exception.code)

    def test_semantic_validator_accepts_only_declared_provisional_e6_records(self):
        fixture, run_dir, _manifest = _run_package(self, {"src/module.py": b"def main():\n    return 1\n"})
        api, output, _summary = self._build(fixture, run_dir)
        graph = copy.deepcopy(load_artifact(output / "knowledge-graph.json"))
        evidence = copy.deepcopy(load_artifact(output / "evidence.json"))
        proposal_path = "semantic-proposals.json"
        graph["derived_inputs"] = [{
            "path": proposal_path,
            "artifact_kind": "semantic-proposals",
            "schema_version": "1.0.0",
            "sha256": "a" * 64,
        }]
        evidence["derived_inputs"] = copy.deepcopy(graph["derived_inputs"])

        proposal_node_id = "PROP-" + "1" * 64
        node_evidence_id = "EVID-INFERENCE-" + "2" * 64
        graph["nodes"].append({
            "id": proposal_node_id,
            "type": "Concept",
            "label": "A provisional concept",
            "evidence_ids": [node_evidence_id],
            "properties": {
                "certainty": "PROVISIONAL",
                "proposal_id": proposal_node_id,
                "proposal_category": "concept",
                "source_members": [proposal_path],
            },
        })
        evidence["items"].append({
            "id": node_evidence_id,
            "level": "E6",
            "kind": "inference",
            "summary": "A provisional concept",
            "confidence": 0.0,
            "locator": {"path": proposal_path, "observation": proposal_node_id},
            "source_members": [proposal_path],
        })

        proposal_edge_id = "PROP-" + "3" * 64
        edge_evidence_id = "EVID-INFERENCE-" + "4" * 64
        project_id = next(node["id"] for node in graph["nodes"] if node["type"] == "Project")
        graph["edges"].append({
            "id": proposal_edge_id,
            "type": "PART_OF",
            "from": proposal_node_id,
            "to": project_id,
            "certainty": "PROVISIONAL",
            "unresolved_target": None,
            "evidence_ids": [edge_evidence_id],
            "source_members": [proposal_path],
            "source_record": None,
        })
        evidence["items"].append({
            "id": edge_evidence_id,
            "level": "E6",
            "kind": "inference",
            "summary": "A provisional relationship",
            "confidence": 0.0,
            "locator": {"path": proposal_path, "observation": proposal_edge_id},
            "source_members": [proposal_path],
        })

        try:
            api.validate_phase4_graph_artifacts(graph, evidence)
        except api.Phase4GraphError as exc:
            self.fail(f"valid provisional E6 pair was rejected with {exc.code}")

        changed_evidence = copy.deepcopy(evidence)
        source_item = next(item for item in changed_evidence["items"] if item["level"] == "E1")
        source_item["source_members"] = [proposal_path]
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, changed_evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

        changed_evidence = copy.deepcopy(evidence)
        inference_item = next(item for item in changed_evidence["items"] if item["level"] == "E6")
        inference_item["confidence"] = 0.5
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(graph, changed_evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

        changed_graph = copy.deepcopy(graph)
        changed_evidence = copy.deepcopy(evidence)
        extra = {
            "path": "untrusted.json",
            "artifact_kind": "semantic-proposals",
            "schema_version": "1.0.0",
            "sha256": "b" * 64,
        }
        changed_graph["derived_inputs"].append(extra)
        changed_evidence["derived_inputs"].append(copy.deepcopy(extra))
        with self.assertRaises(api.Phase4GraphError) as raised:
            api.validate_phase4_graph_artifacts(changed_graph, changed_evidence)
        self.assertEqual("INPUT_INVALID", raised.exception.code)

    def test_input_member_metadata_must_match_the_validated_run(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"metadata\n"})
        api = _api(self)
        members = api._load_run_members(run_dir, manifest)
        changed = copy.deepcopy(members)
        changed[MEMBER_PATHS["phase3a_evidence"]]["source_metadata"]["g01_status"] = "PARTIAL"

        with self.assertRaises(api.Phase4GraphError) as raised:
            api._phase4_artifacts(manifest, hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(), changed)
        self.assertEqual("METADATA_MISMATCH", raised.exception.code)

    def test_uncovered_indexed_file_remains_visible_without_a_surface(self):
        fixture, run_dir, manifest = _run_package(self, {"README.md": b"visible\n"})
        api = _api(self)
        members = api._load_run_members(run_dir, manifest)
        coverage = copy.deepcopy(members[MEMBER_PATHS["coverage"]])
        coverage["entries"] = []
        members[MEMBER_PATHS["coverage"]] = coverage

        graph, evidence = api._phase4_artifacts(
            manifest,
            hashlib.sha256((run_dir / "phase3-run.json").read_bytes()).hexdigest(),
            members,
        )
        file_node = next(node for node in graph["nodes"] if node["type"] == "File")
        file_id = file_node["id"]
        self.assertEqual("README.md", file_node["properties"]["path"])
        self.assertNotIn("surface", file_node["properties"])
        self.assertFalse(any(edge["type"] == "CONTAINS" and edge["to"] == file_id for edge in graph["edges"]))
        api.validate_phase4_graph_artifacts(graph, evidence)


if __name__ == "__main__":
    unittest.main()
