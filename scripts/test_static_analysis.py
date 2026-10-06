#!/usr/bin/env python3
"""Unit tests for the bounded, declaration-only Phase 3B1 Python analyzer."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402


FIXED_TIME = "2026-09-23T00:00:00Z"
REVISION = "a" * 40


def _analyzer(testcase):
    try:
        return importlib.import_module("python_static_analysis")
    except ImportError as exc:
        testcase.fail(f"Phase 3B1 analyzer is missing: {exc}")


def _phase2(sources, classifications=None, *, snapshot_kind="worktree"):
    classifications = classifications or {}
    files = []
    entries = []
    for path, payload in sorted(sources.items()):
        files.append({
            "path": path,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "tracked": True,
        })
        classification = classifications.get(path, "COVERED")
        entry = {
            "path": path,
            "surface": "vendor" if classification == "VENDOR" else "other",
            "classification": classification,
            "teaching_status": "LOCATED" if classification != "VENDOR" else "NOT_APPLICABLE",
            "reason": "Controlled Python AST regression fixture.",
        }
        entries.append(entry)

    unknown_count = sum(item["classification"] == "UNKNOWN" for item in entries)
    project_index = {
        "artifact_kind": "project-index",
        "schema_version": "1.0.0",
        "repository_revision": REVISION,
        "generated_at": FIXED_TIME,
        "project": {
            "name": "python-ast-fixture",
            "root": ".",
            "vcs": "git",
            "dirty": False,
            "snapshot_kind": snapshot_kind,
        },
        "file_count": len(files),
        "files": files,
    }
    coverage = {
        "artifact_kind": "coverage",
        "schema_version": "1.0.0",
        "repository_revision": REVISION,
        "generated_at": FIXED_TIME,
        "tracked_file_count": len(entries),
        "unknown_count": unknown_count,
        "entries": entries,
    }
    return project_index, coverage, unknown_count


def _three_a(project_index, coverage, sources, unknown_count):
    status = "PARTIAL" if unknown_count else "PASS"
    return build_stack_artifacts(
        project_index,
        coverage,
        lambda path, _entry: sources[path],
        generated_at=FIXED_TIME,
        regular_source_paths=frozenset(sources),
        g01_status=status,
        unknown_files=unknown_count,
    )


def _analyze(testcase, sources, classifications=None, *, snapshot_kind="worktree"):
    analyzer = _analyzer(testcase)
    project_index, coverage, unknown_count = _phase2(
        sources, classifications, snapshot_kind=snapshot_kind,
    )
    profile, evidence = _three_a(project_index, coverage, sources, unknown_count)
    status = "PARTIAL" if unknown_count else "PASS"
    static_analysis, merged_evidence = analyzer.analyze_python_artifacts(
        project_index,
        coverage,
        profile,
        evidence,
        lambda path, _entry: sources[path],
        frozenset(sources),
        g01_status=status,
        unknown_files=unknown_count,
    )
    return analyzer, project_index, coverage, profile, evidence, static_analysis, merged_evidence


def _analyze_b2(testcase, sources, classifications=None, *, snapshot_kind="worktree"):
    analyzer = _analyzer(testcase)
    project_index, coverage, unknown_count = _phase2(
        sources, classifications, snapshot_kind=snapshot_kind,
    )
    profile, evidence = _three_a(project_index, coverage, sources, unknown_count)
    status = "PARTIAL" if unknown_count else "PASS"
    static_analysis, merged_evidence = analyzer.analyze_python_artifacts_v11(
        project_index,
        coverage,
        profile,
        evidence,
        lambda path, _entry: sources[path],
        frozenset(sources),
        g01_status=status,
        unknown_files=unknown_count,
    )
    return analyzer, project_index, coverage, profile, evidence, static_analysis, merged_evidence


class PythonStaticAnalysisTests(unittest.TestCase):
    def test_v11_multiline_call_and_base_anchors_allow_reversed_columns_with_stable_e1_facts(self):
        source = (
            "class Base: pass\n"
            "def passthrough(\n"
            "    value\n"
            "):\n"
            "    return value\n"
            "def caller():\n"
            "    return passthrough(\n"
            "        Base\n"
            "    )\n"
            "class Child(passthrough(\n"
            "    Base\n"
            "    )\n"
            "):\n"
            "    pass\n"
        ).encode("utf-8")
        first = _analyze_b2(self, {"src/multiline.py": source})
        second = _analyze_b2(self, {"src/multiline.py": source})
        analysis, merged = first[-2:]
        calls = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        multiline_calls = [item for item in calls if item["line_start"] in {7, 10}]
        extends = [item for item in analysis["relations"] if item["kind"] == "EXTENDS"]
        multiline_extends = [item for item in extends if item["line_start"] == 10]

        self.assertEqual({7, 10}, {item["line_start"] for item in multiline_calls})
        self.assertEqual(1, len(multiline_extends))
        self.assertTrue(all(item["line_end"] > item["line_start"] for item in multiline_calls + multiline_extends))
        passthrough = next(item for item in analysis["symbols"]
                           if item["qualified_name"] == "src.multiline.passthrough")
        self.assertTrue(all(item["target_id"] == passthrough["id"] and item["certainty"] == "CANDIDATE"
                            for item in multiline_calls))
        self.assertIsNone(multiline_extends[0]["target_id"])
        self.assertEqual("UNRESOLVED", multiline_extends[0]["certainty"])
        self.assertEqual(
            [item["id"] for item in multiline_calls + multiline_extends],
            [item["id"] for item in second[-2]["relations"]
             if item["kind"] in {"CALL_CANDIDATE", "EXTENDS"}
             and item["line_start"] in {7, 10}],
        )
        evidence_by_id = {item["id"]: item for item in merged["items"]}
        for fact in multiline_calls + multiline_extends:
            self.assertTrue(fact["evidence_ids"])
            self.assertTrue(all(evidence_by_id[identifier]["level"] == "E1"
                                for identifier in fact["evidence_ids"]))

    def test_ast_span_anchor_accepts_cross_line_column_reversal_and_rejects_invalid_positions(self):
        analyzer = _analyzer(self)

        def node_with(*, start_line, end_line, start_column, end_column):
            node = ast.Call(func=ast.Name(id="f", ctx=ast.Load()), args=[], keywords=[])
            node.lineno = start_line
            node.end_lineno = end_line
            node.col_offset = start_column
            node.end_col_offset = end_column
            return node

        self.assertEqual((1, 2, (12, 4)), analyzer._ast_span_and_anchor(
            node_with(start_line=1, end_line=2, start_column=12, end_column=4),
        ))
        for invalid in (
            node_with(start_line=1, end_line=1, start_column=8, end_column=4),
            node_with(start_line=1, end_line=2, start_column=-1, end_column=4),
            node_with(start_line=1, end_line=2, start_column=8, end_column=-1),
        ):
            with self.assertRaisesRegex(analyzer.StaticAnalysisError, "source column anchor"):
                analyzer._ast_span_and_anchor(invalid)
        for invalid_span in (
            node_with(start_line=0, end_line=2, start_column=0, end_column=0),
            node_with(start_line=1, end_line=0, start_column=0, end_column=0),
        ):
            with self.assertRaisesRegex(analyzer.StaticAnalysisError, "one-based source span"):
                analyzer._ast_span_and_anchor(invalid_span)

    def test_v11_calls_resolve_only_unique_local_definitions_and_same_line_ids_are_stable(self):
        source = (
            "def target(): return 1\n"
            "def invoke():\n"
            "    target(); target()\n"
            "def shadow(target):\n"
            "    return target()\n"
            "def assigned():\n"
            "    target = 3\n"
            "    return target()\n"
            "def dynamic(obj):\n"
            "    obj.get('PRIVATE_SENTINEL')\n"
            "    (factory())()\n"
        ).encode("utf-8")
        first = _analyze_b2(self, {"src/calls.py": source})
        second = _analyze_b2(self, {"src/calls.py": source})
        analysis, merged = first[-2:]
        relations = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        same_line = [item for item in relations if item["line_start"] == 3]
        self.assertEqual(2, len(same_line))
        self.assertEqual(2, len({item["id"] for item in same_line}))
        self.assertEqual(
            [item["id"] for item in relations],
            [item["id"] for item in second[-2]["relations"] if item["kind"] == "CALL_CANDIDATE"],
        )
        self.assertEqual(dumps_artifact(analysis), dumps_artifact(second[-2]))
        target = next(item for item in analysis["symbols"] if item["qualified_name"] == "src.calls.target")
        self.assertTrue(all(item["target_id"] == target["id"] for item in same_line))
        shadowed = [item for item in relations if item["line_start"] in {5, 8}]
        self.assertEqual(2, len(shadowed))
        self.assertTrue(all(item["target_id"] is None for item in shadowed))
        self.assertTrue(all(item["certainty"] == "UNRESOLVED" and item["unresolved_target"] for item in shadowed))
        self.assertTrue(all("execut" not in item["unresolved_target"].lower() for item in relations if item["unresolved_target"]))
        self.assertNotIn("PRIVATE_SENTINEL", dumps_artifact(merged))
        self.assertTrue(all(item["evidence_ids"] for item in relations))

    def test_v11_shadowed_and_dynamic_calls_never_claim_a_local_target(self):
        source = (
            "def repeated(): return 1\n"
            "def repeated(): return 2\n"
            "def use():\n"
            "    repeated()\n"
            "    holder.get('PRIVATE_SENTINEL')\n"
            "    (factory())()\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, merged = _analyze_b2(
            self, {"src/shadow.py": source},
        )
        calls = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        self.assertEqual(4, len(calls))
        self.assertTrue(all(item["target_id"] is None for item in calls))
        self.assertTrue(all(item["certainty"] == "UNRESOLVED" for item in calls))
        self.assertTrue(all(item["unresolved_target"] for item in calls))
        self.assertNotIn("PRIVATE_SENTINEL", dumps_artifact(analysis) + dumps_artifact(merged))

    def test_v11_lambda_parameter_shadowing_never_claims_a_module_definition(self):
        source = (
            "def callback(): return 1\n"
            "first = lambda callback: callback()\n"
            "second = lambda f=callback(): f()\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, _merged = _analyze_b2(
            self, {"src/lambda_shadow.py": source},
        )
        calls = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        lambda_body_calls = [item for item in calls if item["line_start"] in {2, 3}]
        self.assertEqual(3, len(lambda_body_calls))
        by_line = {line: [item for item in lambda_body_calls if item["line_start"] == line] for line in {2, 3}}
        self.assertIsNone(by_line[2][0]["target_id"])
        lambda_parameter_call = next(item for item in by_line[3] if item["unresolved_target"] == "name:f")
        self.assertIsNone(lambda_parameter_call["target_id"])
        self.assertTrue(all(item["certainty"] == "UNRESOLVED" for item in (by_line[2][0], lambda_parameter_call)))
        callback = next(item for item in analysis["symbols"] if item["qualified_name"] == "src.lambda_shadow.callback")
        default_call = next(item for item in by_line[3] if item["target_id"] == callback["id"])
        self.assertEqual(callback["id"], default_call["target_id"])

    def test_v11_imported_call_target_remains_unresolved(self):
        source = (
            "from external_package import imported_target\n"
            "def use_import():\n"
            "    imported_target()\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, _merged = _analyze_b2(
            self, {"src/imported_call.py": source},
        )
        call = next(item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE")
        self.assertIsNone(call["target_id"])
        self.assertEqual("name:imported_target", call["unresolved_target"])
        self.assertEqual("UNRESOLVED", call["certainty"])

    def test_v11_inheritance_is_conservative_for_repeated_and_dynamic_bases(self):
        simple = (
            "class Base: pass\n"
            "class Child(Base): pass\n"
        ).encode("utf-8")
        ambiguous = (
            "class Base: pass\n"
            "class Base: pass\n"
            "class Ambiguous(Base): pass\n"
            "class Dynamic(make_base('BASE_SECRET')): pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, merged = _analyze_b2(
            self, {"src/simple.py": simple, "src/ambiguous.py": ambiguous},
        )
        extends = [item for item in analysis["relations"] if item["kind"] == "EXTENDS"]
        self.assertEqual(3, len(extends))
        child = next(item for item in extends if item["path"] == "src/simple.py")
        self.assertIsNotNone(child["target_id"])
        self.assertEqual("CANDIDATE", child["certainty"])
        unresolved = [item for item in extends if item["path"] == "src/ambiguous.py"]
        self.assertEqual(2, len(unresolved))
        self.assertTrue(all(item["target_id"] is None for item in unresolved))
        self.assertTrue(all(item["unresolved_target"] for item in unresolved))
        self.assertNotIn("BASE_SECRET", dumps_artifact(analysis) + dumps_artifact(merged))

    def test_v11_nested_class_bases_use_the_enclosing_lexical_scope(self):
        source = (
            "class Base: pass\n"
            "def from_parameter(Base):\n"
            "    class Child(Base): pass\n"
            "def from_assignment():\n"
            "    Base = 1\n"
            "    class Child(Base): pass\n"
            "def from_import():\n"
            "    from external import Base\n"
            "    class Child(Base): pass\n"
            "def from_local_class():\n"
            "    class LocalBase: pass\n"
            "    class Child(LocalBase): pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, _merged = _analyze_b2(
            self, {"src/nested_bases.py": source},
        )
        symbols = {item["id"]: item for item in analysis["symbols"]}
        extends = [item for item in analysis["relations"] if item["kind"] == "EXTENDS"]
        by_class = {symbols[item["source_id"]]["qualified_name"]: item for item in extends}
        shadowed = [
            by_class["src.nested_bases.from_parameter.Child"],
            by_class["src.nested_bases.from_assignment.Child"],
            by_class["src.nested_bases.from_import.Child"],
        ]
        self.assertTrue(all(item["target_id"] is None for item in shadowed))
        self.assertTrue(all(item["certainty"] == "UNRESOLVED" and item["unresolved_target"] for item in shadowed))
        nested = by_class["src.nested_bases.from_local_class.Child"]
        local_base = next(item for item in analysis["symbols"] if item["qualified_name"] == "src.nested_bases.from_local_class.LocalBase")
        self.assertEqual(local_base["id"], nested["target_id"])
        self.assertEqual("CANDIDATE", nested["certainty"])

    def test_v11_roles_require_source_markers_and_never_claim_test_execution_or_tables(self):
        source = (
            "from dataclasses import dataclass\n"
            "from pydantic import BaseModel as PydanticBase\n"
            "from sqlalchemy.orm import DeclarativeBase\n"
            "import unittest\n"
            "import pytest\n"
            "@dataclass\n"
            "class Record: pass\n"
            "class Payload(PydanticBase): pass\n"
            "class SqlRecord(DeclarativeBase): pass\n"
            "class PlainModel: pass\n"
            "class TestThing: pass\n"
            "class Suite(unittest.TestCase): pass\n"
            "def test_checkout(): pass\n"
            "@pytest.mark.parametrize('case', [1])\n"
            "def marked_check(): pass\n"
        ).encode("utf-8")
        false_positive = (
            "class Model: pass\n"
            "class TestThing: pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, _merged = _analyze_b2(
            self, {"tests/test_models.py": source, "tests/test_only_name.py": false_positive},
        )
        roles = analysis["roles"]
        self.assertEqual(6, len(roles))
        role_by_symbol = {
            next(symbol for symbol in analysis["symbols"] if symbol["id"] == role["symbol_id"])["name"]: role
            for role in roles
        }
        self.assertEqual({"Record", "Payload", "SqlRecord", "Suite", "test_checkout", "marked_check"}, set(role_by_symbol))
        self.assertEqual("data_model_candidate", role_by_symbol["Record"]["kind"])
        self.assertEqual("data_model_candidate", role_by_symbol["Payload"]["kind"])
        self.assertEqual("data_model_candidate", role_by_symbol["SqlRecord"]["kind"])
        self.assertEqual("test_candidate", role_by_symbol["Suite"]["kind"])
        self.assertEqual("test_candidate", role_by_symbol["test_checkout"]["kind"])
        self.assertTrue(all(role["certainty"] == "CANDIDATE" for role in roles))
        self.assertTrue(all(role["evidence_ids"] for role in roles))
        false_positive_ids = {
            symbol["id"] for symbol in analysis["symbols"] if symbol["path"] == "tests/test_only_name.py"
        }
        self.assertFalse(any(role["symbol_id"] in false_positive_ids for role in roles))

    def test_v11_multiline_test_and_model_roles_bind_to_their_own_symbols(self):
        source = (
            "from pydantic import BaseModel\n"
            "class Payload(\n"
            "    BaseModel,\n"
            "):\n"
            "    value: str\n"
            "def test_checkout(\n"
            "    value: str,\n"
            "): \n"
            "    return value\n"
            "class Unrelated: pass\n"
        ).encode("utf-8")
        analyzer, index, coverage, profile, original, analysis, merged = _analyze_b2(
            self, {"src/multiline_roles.py": source},
        )
        symbols = {item["name"]: item for item in analysis["symbols"]}
        roles = {role["kind"]: role for role in analysis["roles"]}
        self.assertEqual(symbols["Payload"]["id"], roles["data_model_candidate"]["symbol_id"])
        self.assertEqual(symbols["test_checkout"]["id"], roles["test_candidate"]["symbol_id"])
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        rebound = copy.deepcopy(analysis)
        model_role = next(item for item in rebound["roles"] if item["kind"] == "data_model_candidate")
        model_role["symbol_id"] = symbols["Unrelated"]["id"]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "role|marker|adjacent"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original, merged, rebound,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_v11_routes_require_known_framework_bindings_and_redact_all_route_values(self):
        source = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/private/ROUTE_SECRET?token=QUERY_SECRET')\n"
            "def safe_route(): pass\n"
            "@app.get(route_value)\n"
            "def dynamic_route(): pass\n"
            "class Unrelated:\n"
            "    @object().get('/unrelated')\n"
            "    def get(self): pass\n"
            "def build_with_shadowed_app(app):\n"
            "    @app.get('/SHADOWED_ROUTE_SECRET')\n"
            "    def local_handler(): pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, merged = _analyze_b2(
            self, {"src/routes.py": source},
        )
        routes = [item for item in analysis["relations"] if item["kind"] == "ROUTE_TO"]
        self.assertEqual(2, len(routes))
        self.assertTrue(all(item["certainty"] == "CANDIDATE" for item in routes))
        safe_output = dumps_artifact(analysis) + dumps_artifact(merged)
        for secret in ("ROUTE_SECRET", "QUERY_SECRET", "route_value", "/unrelated", "SHADOWED_ROUTE_SECRET"):
            self.assertNotIn(secret, safe_output)
        self.assertTrue(all("redacted" in item["unresolved_target"].lower() for item in routes))

    def test_v11_dynamic_route_argument_calls_are_recorded_without_expression_text(self):
        source = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get(SECRET_PATH_PROVIDER('ROUTE_ARGUMENT_VALUE'))\n"
            "def handler(): pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, merged = _analyze_b2(
            self, {"src/secret_routes.py": source},
        )
        route_argument_calls = [
            item for item in analysis["relations"]
            if item["kind"] == "CALL_CANDIDATE" and item["line_start"] == 3
        ]
        self.assertTrue(route_argument_calls)
        redacted_calls = [item for item in route_argument_calls
                          if item["unresolved_target"] == "redacted-route-argument-call"]
        self.assertEqual(1, len(redacted_calls))
        provider_call = redacted_calls[0]
        self.assertIsNone(provider_call["target_id"])
        self.assertEqual("UNRESOLVED", provider_call["certainty"])
        serialized = dumps_artifact(analysis) + dumps_artifact(merged)
        self.assertNotIn("SECRET_PATH_PROVIDER", serialized)
        self.assertNotIn("ROUTE_ARGUMENT_VALUE", serialized)

    def test_v11_flask_route_methods_are_allowlisted_and_dynamic_methods_are_redacted(self):
        source = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/private/FLASK_SECRET', methods=['GET', 'POST'])\n"
            "def static_methods(): pass\n"
            "@app.route(route_secret, methods=method_secret)\n"
            "def dynamic_methods(): pass\n"
            "@app.get('/private/SHORTCUT_SECRET')\n"
            "def shortcut_route(): pass\n"
        ).encode("utf-8")
        _analyzer_module, _index, _coverage, _profile, _evidence, analysis, merged = _analyze_b2(
            self, {"src/flask_routes.py": source},
        )
        routes = [item for item in analysis["relations"] if item["kind"] == "ROUTE_TO"]
        self.assertEqual(3, len(routes))
        self.assertTrue(all(item["unresolved_target"] == "route:flask:GET+POST:endpoint-redacted"
                            or item["unresolved_target"] == "route:flask:DYNAMIC-METHOD:endpoint-redacted"
                            or item["unresolved_target"] == "route:flask:GET:endpoint-redacted"
                            for item in routes))
        combined = dumps_artifact(analysis) + dumps_artifact(merged)
        for secret in ("FLASK_SECRET", "route_secret", "method_secret", "SHORTCUT_SECRET", "/private/"):
            self.assertNotIn(secret, combined)

    def test_v11_bundle_rejects_non_e1_runtime_evidence_and_wrong_call_owner(self):
        source = (
            "def unrelated(): pass\n"
            "def target(): pass\n"
            "def caller():\n"
            "    target()\n"
        ).encode("utf-8")
        analyzer, index, coverage, profile, original, analysis, merged = _analyze_b2(
            self, {"src/audit.py": source},
        )
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        call = next(item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE")
        unrelated = next(item for item in analysis["symbols"] if item["qualified_name"] == "src.audit.unrelated")
        wrong_owner = copy.deepcopy(analysis)
        next(item for item in wrong_owner["relations"] if item["id"] == call["id"])["source_id"] = unrelated["id"]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "source.*span|range"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original, merged, wrong_owner,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

        changed_evidence = copy.deepcopy(merged)
        call_evidence = next(item for item in analysis["relations"] if item["id"] == call["id"])["evidence_ids"][0]
        next(item for item in changed_evidence["items"] if item["id"] == call_evidence)["summary"] = "runtime execution was verified"
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "evidence"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original, changed_evidence, analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_v11_bundle_rejects_route_or_role_retargeted_to_another_declaration(self):
        source = (
            "from dataclasses import dataclass\n"
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@dataclass\n"
            "class Record: pass\n"
            "@app.get('/private')\n"
            "def handler(): pass\n"
            "def unrelated(): pass\n"
        ).encode("utf-8")
        analyzer, index, coverage, profile, original, analysis, merged = _analyze_b2(
            self, {"src/route_audit.py": source},
        )
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        unrelated = next(item for item in analysis["symbols"] if item["qualified_name"] == "src.route_audit.unrelated")
        changed_route = copy.deepcopy(analysis)
        next(item for item in changed_route["relations"] if item["kind"] == "ROUTE_TO")["source_id"] = unrelated["id"]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "adjacent handler"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original, merged, changed_route,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )
        changed_role = copy.deepcopy(analysis)
        next(item for item in changed_role["roles"] if item["kind"] == "data_model_candidate")["symbol_id"] = unrelated["id"]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "role kind|adjacent source"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original, merged, changed_role,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_nested_definitions_and_imports_have_stable_scoped_ids_and_spans(self):
        source = (
            "import os, sys as system\n"
            "from .internal import run as invoke\n"
            "from . import local\n"
            "from external import *\n"
            "class Outer:\n"
            "    def repeat(self):\n"
            "        def repeat():\n"
            "            return 1\n"
            "        class Inner:\n"
            "            async def repeat(self):\n"
            "                return 2\n"
            "        return repeat()\n"
            "def repeat():\n"
            "    import local_mod\n"
            "    return 3\n"
        ).encode("utf-8")
        (analyzer, _index, _coverage, _profile, _evidence,
         static_analysis, merged_evidence) = _analyze(
            self, {"src/service.py": source},
        )

        symbols = static_analysis["symbols"]
        by_qualified = {item["qualified_name"]: item for item in symbols}
        self.assertTrue({
            "src.service", "src.service.Outer", "src.service.Outer.repeat",
            "src.service.Outer.repeat.repeat", "src.service.Outer.repeat.Inner",
            "src.service.Outer.repeat.Inner.repeat", "src.service.repeat",
        } <= set(by_qualified))
        self.assertEqual("module", by_qualified["src.service"]["kind"])
        self.assertEqual("method", by_qualified["src.service.Outer.repeat"]["kind"])
        self.assertEqual("function", by_qualified["src.service.Outer.repeat.repeat"]["kind"])
        self.assertEqual("method", by_qualified["src.service.Outer.repeat.Inner.repeat"]["kind"])
        self.assertTrue(all(item["extraction_method"] == "python-ast" for item in symbols))
        self.assertTrue(all(item["certainty"] == "VERIFIED" for item in symbols))
        self.assertEqual(6, by_qualified["src.service.Outer.repeat"]["line_start"])
        self.assertEqual(13, by_qualified["src.service.repeat"]["line_start"])
        self.assertEqual(len(symbols), len({item["id"] for item in symbols}))

        imports = [item for item in static_analysis["relations"] if item["kind"] == "IMPORTS"]
        self.assertEqual(6, len(imports))
        self.assertTrue(all(item["target_id"] is None for item in imports))
        self.assertTrue(all(item["unresolved_target"] for item in imports))
        self.assertEqual(len(imports), len({item["id"] for item in imports}))
        self.assertEqual(
            {"os", "sys", ".internal.run", ".local", "external.*", "local_mod"},
            {item["unresolved_target"] for item in imports},
        )
        top_level_imports = [item for item in imports if item["unresolved_target"] != "local_mod"]
        self.assertTrue(all(item["source_id"] == by_qualified["src.service"]["id"] for item in top_level_imports))
        nested_import = next(item for item in imports if item["unresolved_target"] == "local_mod")
        self.assertEqual(by_qualified["src.service.repeat"]["id"], nested_import["source_id"])
        self.assertTrue(all(item["certainty"] == "UNRESOLVED" for item in imports))
        self.assertTrue(all(item["extraction_method"] == "python-ast" for item in imports))
        self.assertTrue(all(item["evidence_ids"] for item in symbols + static_analysis["relations"]))
        evidence_by_id = {item["id"]: item for item in merged_evidence["items"]}
        self.assertTrue(all(
            evidence_by_id[evidence_id]["level"] == "E1"
            for item in symbols + static_analysis["relations"]
            for evidence_id in item["evidence_ids"]
        ))

        repeated_index, repeated_coverage, repeated_unknowns = _phase2({"src/service.py": source})
        repeated_profile, repeated_evidence = _three_a(
            repeated_index, repeated_coverage, {"src/service.py": source}, repeated_unknowns,
        )
        repeated = analyzer.analyze_python_artifacts(
            repeated_index,
            repeated_coverage,
            repeated_profile,
            repeated_evidence,
            lambda path, _entry: source,
            frozenset({"src/service.py"}),
            g01_status="PASS",
            unknown_files=0,
        )
        self.assertEqual(dumps_artifact(static_analysis), dumps_artifact(repeated[0]))
        self.assertEqual(dumps_artifact(merged_evidence), dumps_artifact(repeated[1]))

    def test_source_bodies_docstrings_and_string_values_never_enter_artifacts(self):
        source = (
            '"""PRIVATE_DOCSTRING_CANARY"""\n'
            "def service():\n"
            "    # PRIVATE_COMMENT_CANARY\n"
            "    private_value = 'PRIVATE_STRING_CANARY'\n"
            "    return private_value\n"
        ).encode("utf-8")
        (_analyzer_module, _index, _coverage, _profile, _evidence,
         static_analysis, merged_evidence) = _analyze(self, {"src/service.py": source})
        output = dumps_artifact(static_analysis) + dumps_artifact(merged_evidence)

        for sentinel in (
            "PRIVATE_DOCSTRING_CANARY", "PRIVATE_COMMENT_CANARY", "PRIVATE_STRING_CANARY",
        ):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, output)
        self.assertNotIn(source.decode("utf-8"), output)

    def test_stable_ids_are_deterministic_but_scoped_to_snapshot_bytes(self):
        first_source = b"def stable(): pass\n"
        second_source = first_source + b"# same declaration, different snapshot bytes\n"
        first = _analyze(self, {"src/service.py": first_source})[5]
        second = _analyze(self, {"src/service.py": second_source})[5]
        first_ids = {item["qualified_name"]: item["id"] for item in first["symbols"]}
        second_ids = {item["qualified_name"]: item["id"] for item in second["symbols"]}

        self.assertEqual(set(first_ids), set(second_ids))
        self.assertNotEqual(first_ids["src.service.stable"], second_ids["src.service.stable"])

    def test_duplicate_import_targets_on_one_line_keep_distinct_stable_ids(self):
        (_analyzer_module, _index, _coverage, _profile, _evidence,
         static_analysis, _merged_evidence) = _analyze(
            self, {"src/service.py": b"import repeated as first, repeated as second\n"},
        )
        imports = [item for item in static_analysis["relations"] if item["kind"] == "IMPORTS"]

        self.assertEqual(["repeated", "repeated"], [item["unresolved_target"] for item in imports])
        self.assertEqual(2, len({item["id"] for item in imports}))

    def test_same_line_duplicate_import_statements_have_distinct_repeatable_ids(self):
        source = b"import os; import os\n"
        analyzer = _analyzer(self)
        try:
            first = _analyze(self, {"src/service.py": source})[5]
            second = _analyze(self, {"src/service.py": source})[5]
        except analyzer.StaticAnalysisError as exc:
            self.fail(f"legal same-line import statements must both analyze: {exc}")

        first_imports = [item for item in first["relations"] if item["kind"] == "IMPORTS"]
        second_import_ids = [
            item["id"] for item in second["relations"] if item["kind"] == "IMPORTS"
        ]
        first_import_ids = [item["id"] for item in first_imports]
        self.assertEqual(2, len(first_imports))
        self.assertEqual(["os", "os"], [item["unresolved_target"] for item in first_imports])
        self.assertEqual(2, len(set(first_import_ids)))
        self.assertEqual(first_import_ids, second_import_ids)

    def test_invalid_large_binary_and_excluded_sources_are_skipped_partially(self):
        sources = {
            "src/ok.py": b"def accepted():\n    return 1\n",
            "src/broken.py": b"def broken(:\n    return 'PRIVATE_SYNTAX_CANARY'\n",
            "src/large.py": b"#" + b"x" * (1024 * 1024),
            "src/binary.py": b"\x00\x01\xff",
            "src/unsupported.py": b"# coding: utf-16\ndef unsupported(): pass\n",
            "vendor/copied.py": b"def third_party(): pass\n",
            "src/unknown.py": b"def unclassified(): pass\n",
            "src/generated.py": b"def generated(): pass\n",
            "src/ignored.py": b"def ignored(): pass\n",
        }
        classifications = {
            "vendor/copied.py": "VENDOR",
            "src/unknown.py": "UNKNOWN",
            "src/generated.py": "GENERATED",
            "src/ignored.py": "IGNORED_WITH_REASON",
        }
        read_paths = []

        analyzer = _analyzer(self)
        project_index, coverage, unknown_count = _phase2(sources, classifications)
        profile, evidence = _three_a(project_index, coverage, sources, unknown_count)
        static_analysis, merged_evidence = analyzer.analyze_python_artifacts(
            project_index,
            coverage,
            profile,
            evidence,
            lambda path, _entry: (read_paths.append(path), sources[path])[1],
            frozenset(sources),
            g01_status="PARTIAL",
            unknown_files=unknown_count,
        )
        language = static_analysis["languages"][0]
        files = {item["path"]: item for item in language["files"]}

        self.assertEqual("PARTIAL", static_analysis["analysis_status"])
        self.assertEqual("PARTIAL", language["analysis_status"])
        self.assertEqual("ANALYZED", files["src/ok.py"]["status"])
        for path in ("src/broken.py", "src/large.py", "src/binary.py", "src/unsupported.py", "vendor/copied.py", "src/unknown.py", "src/generated.py", "src/ignored.py"):
            self.assertEqual("SKIPPED", files[path]["status"], path)
            self.assertTrue(files[path]["limitations"], path)
        # A legacy Phase 2 v1.0 index has no content_kind, so inspect a bounded
        # candidate before classifying NUL/bad-encoding content as unsupported.
        self.assertEqual({"src/ok.py", "src/broken.py", "src/binary.py", "src/unsupported.py"}, set(read_paths))
        self.assertNotIn("PRIVATE_SYNTAX_CANARY", dumps_artifact(static_analysis))
        self.assertNotIn("PRIVATE_SYNTAX_CANARY", dumps_artifact(merged_evidence))

    def test_all_unparseable_python_files_publish_a_partial_skip_record(self):
        (_analyzer_module, _index, _coverage, _profile, _evidence,
         static_analysis, _merged_evidence) = _analyze(
            self,
            {"src/broken.py": b"def broken(:\n", "src/large.py": b"x" * (1024 * 1024 + 1)},
        )

        self.assertEqual("PARTIAL", static_analysis["analysis_status"])
        self.assertEqual([], static_analysis["symbols"])
        self.assertEqual([], static_analysis["relations"])
        self.assertEqual(
            {"SKIPPED"},
            {item["status"] for item in static_analysis["languages"][0]["files"]},
        )

    def test_bundle_validator_rejects_bad_rows_dangling_targets_and_tampered_evidence(self):
        source = b"import external\n\ndef outer():\n    def inner():\n        pass\n"
        (analyzer, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze(self, {"src/service.py": source})
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)

        missing_metadata = copy.deepcopy(profile)
        del missing_metadata["source_metadata"]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "provenance|metadata"):
            analyzer.analyze_python_artifacts(
                index, coverage, missing_metadata, original_evidence,
                lambda _path, _entry: source, frozenset({"src/service.py"}),
                g01_status="PASS", unknown_files=0,
            )

        bad_row = copy.deepcopy(static_analysis)
        bad_row["symbols"][0]["line_end"] = 999
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "line"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, merged_evidence, bad_row,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

        bad_import = copy.deepcopy(static_analysis)
        next(item for item in bad_import["relations"] if item["kind"] == "IMPORTS")["target_id"] = "SYM-" + "0" * 24
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "unresolved|target"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, merged_evidence, bad_import,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

        bad_evidence = copy.deepcopy(merged_evidence)
        bad_evidence["items"] = [item for item in bad_evidence["items"] if item["id"] != original_evidence["items"][0]["id"]]
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "evidence|preserv"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, bad_evidence, static_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_bundle_validator_rejects_runtime_claim_in_new_merged_evidence(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze(
            self, {"src/service.py": b"def serve():\n    pass\n"},
        )
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        original_items = copy.deepcopy(original_evidence["items"])
        changed_merged = copy.deepcopy(merged_evidence)
        original_ids = {item["id"] for item in original_items}
        added_item = next(item for item in changed_merged["items"] if item["id"] not in original_ids)
        added_item["summary"] = "This function is running at runtime."

        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "evidence|runtime"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, changed_merged, static_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )
        self.assertEqual(original_items, original_evidence["items"])
        self.assertEqual(
            {item["id"]: item for item in original_items},
            {item["id"]: item for item in changed_merged["items"] if item["id"] in original_ids},
        )

    def test_runtime_and_production_qualified_names_remain_static_in_b1_and_b2(self):
        source = b"def production():\n    pass\n"
        inputs = {"backend/packages/harness/deerflow/runtime.py": source}
        for analyze, expected_version in (
            (_analyze, "1.0.0"),
            (_analyze_b2, "1.1.0"),
        ):
            with self.subTest(version=expected_version):
                first = analyze(self, inputs)
                second = analyze(self, inputs)
                static_analysis, merged_evidence = first[-2:]
                self.assertEqual(expected_version, static_analysis["schema_version"])
                self.assertEqual(static_analysis, second[-2])
                self.assertEqual(merged_evidence, second[-1])
                runtime_symbol = next(
                    item for item in static_analysis["symbols"]
                    if item["qualified_name"].endswith(".production")
                )
                self.assertEqual(
                    "backend.packages.harness.deerflow.runtime.production",
                    runtime_symbol["qualified_name"],
                )
                evidence_by_id = {item["id"]: item for item in merged_evidence["items"]}
                symbol_evidence = evidence_by_id[runtime_symbol["evidence_ids"][0]]
                expected_label = "Python function declaration backend.packages.harness.deerflow.runtime.production"
                self.assertEqual(expected_label, symbol_evidence["summary"])
                self.assertEqual(expected_label, symbol_evidence["locator"]["symbol"])

    def test_bundle_validator_rejects_forged_new_evidence_summary_or_locator_symbol(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze_b2(
            self, {"src/service.py": b"def serve():\n    pass\n"},
        )
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        original_ids = {item["id"] for item in original_evidence["items"]}
        new_evidence_id = next(
            item["id"] for item in merged_evidence["items"] if item["id"] not in original_ids
        )
        for field in ("summary", "locator.symbol"):
            with self.subTest(field=field):
                changed_merged = copy.deepcopy(merged_evidence)
                changed_item = next(
                    item for item in changed_merged["items"] if item["id"] == new_evidence_id
                )
                if field == "summary":
                    changed_item["summary"] = "Production runtime execution is active."
                else:
                    changed_item["locator"]["symbol"] = "Production runtime execution is active."

                with self.assertRaisesRegex(analyzer.StaticAnalysisError, "evidence|summary|symbol|runtime"):
                    analyzer.validate_analysis_bundle(
                        index, coverage, profile, original_evidence, changed_merged, static_analysis,
                        g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
                    )

    def test_bundle_validator_rejects_role_evidence_with_forged_locator_symbol(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze_b2(
            self, {"src/service.py": b"def test_serves():\n    pass\n"},
        )
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        role = static_analysis["roles"][0]
        changed_merged = copy.deepcopy(merged_evidence)
        role_evidence = next(
            item for item in changed_merged["items"]
            if item["id"] == role["evidence_ids"][0]
        )
        role_evidence["locator"]["symbol"] = "Production runtime execution is active."

        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "role evidence"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, changed_merged, static_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_bundle_validator_still_rejects_runtime_claim_in_original_phase3a_evidence(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze(
            self, {"src/service.py": b"def serve():\n    pass\n"},
        )
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        forged_original = copy.deepcopy(original_evidence)
        forged_original["items"][0]["summary"] = "Production runtime execution is active."
        forged_original_id = forged_original["items"][0]["id"]
        forged_merged = copy.deepcopy(merged_evidence)
        merged_original = next(
            item for item in forged_merged["items"] if item["id"] == forged_original_id
        )
        merged_original["summary"] = "Production runtime execution is active."

        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "Phase 3A|runtime|evidence"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, forged_original, forged_merged, static_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_bundle_validator_rejects_import_source_outside_its_line_span(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze(self, {
            "src/service.py": (
                b"def unrelated():\n"
                b"    pass\n"
                b"\n"
                b"def owner():\n"
                b"    import external\n"
            ),
        })
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        changed_analysis = copy.deepcopy(static_analysis)
        unrelated = next(item for item in changed_analysis["symbols"] if item["name"] == "unrelated")
        imported = next(item for item in changed_analysis["relations"] if item["kind"] == "IMPORTS")
        imported["source_id"] = unrelated["id"]

        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "source.*span|range|contain"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, merged_evidence, changed_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_bundle_validator_rejects_defines_source_outside_its_line_span(self):
        (_analyzer_module, index, coverage, profile, original_evidence,
         static_analysis, merged_evidence) = _analyze(self, {
            "src/service.py": (
                b"class Repeated:\n"
                b"    def first(self): pass\n"
                b"\n"
                b"class Repeated:\n"
                b"    def second(self): pass\n"
            ),
        })
        analyzer = _analyzer(self)
        expected = analyzer.expected_source_metadata(index, coverage, "PASS", 0)
        changed_analysis = copy.deepcopy(static_analysis)
        repeated_classes = sorted(
            (item for item in changed_analysis["symbols"] if item["kind"] == "class"),
            key=lambda item: item["line_start"],
        )
        second_method = next(
            item for item in changed_analysis["symbols"]
            if item["kind"] == "method" and item["name"] == "second"
        )
        defines_second = next(
            item for item in changed_analysis["relations"]
            if item["kind"] == "DEFINES" and item["target_id"] == second_method["id"]
        )
        defines_second["source_id"] = repeated_classes[0]["id"]

        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "source.*span|range|contain"):
            analyzer.validate_analysis_bundle(
                index, coverage, profile, original_evidence, merged_evidence, changed_analysis,
                g01_status="PASS", unknown_files=0, expected_source_metadata=expected,
            )

    def test_changed_phase2_source_bytes_and_duplicate_ids_fail_closed(self):
        source = b"def stable():\n    return 1\n"
        analyzer = _analyzer(self)
        index, coverage, unknown_count = _phase2({"src/service.py": source})
        profile, evidence = _three_a(index, coverage, {"src/service.py": source}, unknown_count)
        with self.assertRaisesRegex(analyzer.StaticAnalysisError, "snapshot|digest|bytes"):
            analyzer.analyze_python_artifacts(
                index, coverage, profile, evidence,
                lambda _path, _entry: source + b"# changed\n",
                frozenset({"src/service.py"}),
                g01_status="PASS", unknown_files=0,
            )

        collision_source = b"def first(): pass\ndef second(): pass\n"
        collision_index, collision_coverage, collision_unknowns = _phase2({"src/service.py": collision_source})
        collision_profile, collision_evidence = _three_a(
            collision_index, collision_coverage, {"src/service.py": collision_source}, collision_unknowns,
        )
        collision_builder = analyzer._stable_identifier
        self.assertTrue(callable(collision_builder), "the stable-ID generator must be explicit and testable")

        def collide_symbols(prefix, *parts):
            if prefix == "SYM":
                return "SYM-" + "0" * 24
            return collision_builder(prefix, *parts)

        with patch.object(analyzer, "_stable_identifier", side_effect=collide_symbols):
            with self.assertRaisesRegex(analyzer.StaticAnalysisError, "duplicate|collision|ID"):
                analyzer.analyze_python_artifacts(
                    collision_index, collision_coverage, collision_profile, collision_evidence,
                    lambda _path, _entry: collision_source,
                    frozenset({"src/service.py"}),
                    g01_status="PASS", unknown_files=collision_unknowns,
                )


if __name__ == "__main__":
    unittest.main()
