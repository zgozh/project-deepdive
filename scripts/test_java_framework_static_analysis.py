#!/usr/bin/env python3
"""Regression tests for Phase 3C2 Java framework candidates."""

from __future__ import annotations

import copy
import base64
import importlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact, validate_artifact  # noqa: E402
from java_static_analysis import analyze_java_artifacts, validate_java_analysis_bundle  # noqa: E402
from test_java_static_analysis import _inputs  # noqa: E402


def _framework(testcase):
    try:
        return importlib.import_module("java_framework_static_analysis")
    except ImportError as exc:
        testcase.fail(f"Phase 3C2 comparison/parser API is not implemented: {exc}")


def _c1(source: bytes, *, path="src/Api.java"):
    sources = {path: source}
    index, coverage, profile, original_evidence, g01_status, unknown_files = _inputs(sources)
    source_reader = lambda selected, _entry: sources[selected]
    regular_paths = frozenset(sources)
    analysis, merged = analyze_java_artifacts(
        index, coverage, profile, original_evidence, source_reader, regular_paths,
        g01_status=g01_status, unknown_files=unknown_files,
    )
    return (sources, index, coverage, profile, original_evidence, analysis, merged,
            g01_status, unknown_files, source_reader, regular_paths)


def _run_bridge(source: bytes | list[bytes], *protocol_args: str):
    bridge = Path(__file__).resolve().parent / "java_parse_bridge" / "DeepDiveJavaParser.java"
    temporary = tempfile.TemporaryDirectory(prefix="pdj2-protocol-")
    root = Path(temporary.name)
    classes = root / "classes"
    classes.mkdir()
    sources = source if isinstance(source, list) else [source]
    units = []
    for index, payload in enumerate(sources):
        unit = root / f"unit-{index}.java"
        unit.write_bytes(payload)
        units.append(unit)
    compiled = subprocess.run(
        [shutil.which("javac"), "-proc:none", "-encoding", "UTF-8", "-d", str(classes), str(bridge)],
        cwd=root, capture_output=True, check=False,
    )
    if compiled.returncode:
        diagnostic = compiled.stderr.decode("utf-8", "replace")
        temporary.cleanup()
        raise AssertionError(f"project-owned parse bridge compilation failed: {compiled.returncode}: {diagnostic}")
    result = subprocess.run(
        [shutil.which("java"), "-Xms16m", "-Xmx256m", "-cp", str(classes),
         "DeepDiveJavaParser", *protocol_args, *(str(unit) for unit in units)],
        cwd=root, capture_output=True, check=False,
    )
    return temporary, result


def _c2(testcase, source: bytes, *, path="src/Api.java"):
    (sources, index, coverage, profile, original_evidence, c1_analysis, c1_evidence,
     g01_status, unknown_files, source_reader, regular_paths) = _c1(source, path=path)
    framework = _framework(testcase)
    analysis, evidence = framework.analyze_java_framework_candidates(
        index, coverage, profile, original_evidence, c1_analysis, c1_evidence,
        source_reader, regular_paths, g01_status=g01_status, unknown_files=unknown_files,
    )
    return (framework, sources, index, coverage, profile, original_evidence,
            c1_analysis, c1_evidence, analysis, evidence, g01_status,
            unknown_files, source_reader, regular_paths)


class JavaFrameworkAnalysisTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_rebuild_v12_pair_matches_locked_snapshot(self):
        source = b"package sample; class Api extends Base { void get() {} }\n"
        (sources, index, coverage, profile, evidence, expected_analysis, expected_evidence,
         status, unknown, reader, regular_paths) = _c1(source)
        framework = _framework(self)

        actual_analysis, actual_evidence = framework.rebuild_v12_bundle(
            index, coverage, profile, evidence, reader, regular_paths,
            g01_status=status, unknown_files=unknown,
        )

        self.assertEqual(dumps_artifact(expected_analysis), dumps_artifact(actual_analysis))
        self.assertEqual(dumps_artifact(expected_evidence), dumps_artifact(actual_evidence))

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_compare_supplied_v12_bundle_rejects_forged_extends_target(self):
        source = b"package sample; class Api extends RealBase {}\n"
        (_sources, index, coverage, profile, original_evidence, analysis, merged,
         status, unknown, _reader, regular_paths) = _c1(source)
        forged_analysis = copy.deepcopy(analysis)
        forged_evidence = copy.deepcopy(merged)
        fact = next(row for row in forged_analysis["relations"] if row["kind"] == "EXTENDS")
        fact["unresolved_target"] = "java-type:ForgedLegacyBase"
        citation = next(row for row in forged_evidence["items"]
                        if row["id"] == fact["evidence_ids"][0])
        citation["locator"]["symbol"] = "java-type:ForgedLegacyBase"

        # The existing C1 shape and evidence-reference validator accepts this
        # coordinated source fabrication; C2 must compare it to a fresh rebuild.
        validate_artifact(forged_analysis)
        validate_artifact(forged_evidence)
        validate_java_analysis_bundle(
            index, coverage, profile, original_evidence, forged_evidence,
            forged_analysis, g01_status=status, unknown_files=unknown,
            regular_source_paths=regular_paths,
        )
        framework = _framework(self)

        with self.assertRaisesRegex(framework.StaticAnalysisError, "C1_REEXTRACTION_MISMATCH"):
            framework.compare_supplied_v12_bundle(analysis, merged, forged_analysis, forged_evidence)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_compare_supplied_v12_bundle_rejects_forged_evidence_locator(self):
        source = b"package sample; class Api extends RealBase {}\n"
        (_sources, _index, _coverage, _profile, _evidence, analysis, merged,
         _status, _unknown, _reader, _regular_paths) = _c1(source)
        forged_evidence = copy.deepcopy(merged)
        fact = next(row for row in analysis["relations"] if row["kind"] == "EXTENDS")
        citation = next(row for row in forged_evidence["items"]
                        if row["id"] == fact["evidence_ids"][0])
        citation["locator"]["symbol"] = "java-type:ForgedEvidenceBase"
        validate_artifact(analysis)
        validate_artifact(forged_evidence)
        self.assertIn(citation["id"], fact["evidence_ids"])
        framework = _framework(self)

        with self.assertRaisesRegex(framework.StaticAnalysisError, "C1_REEXTRACTION_MISMATCH"):
            framework.compare_supplied_v12_bundle(analysis, merged, analysis, forged_evidence)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_v1_0_phase2_input_rebuild_preserves_c1_compatibility(self):
        source = b"package sample; class LegacyApi { void run() {} }\n"
        (_sources, index, coverage, profile, evidence, analysis, merged,
         status, unknown, reader, regular_paths) = _c1(source)
        index = copy.deepcopy(index)
        index["schema_version"] = "1.0.0"
        for item in index["files"]:
            for field in ("content_kind", "media_type", "extension", "vcs_object_id"):
                item.pop(field, None)
        coverage = copy.deepcopy(coverage)
        coverage["schema_version"] = "1.0.0"
        coverage.pop("classification_policy_version", None)
        for item in coverage["entries"]:
            item.pop("secondary_surfaces", None)
            item.pop("rule_id", None)
        # Rebuild the matching legacy Phase 3A pair as well.
        stack_detection = importlib.import_module("stack_detection")
        profile, evidence = stack_detection.build_stack_artifacts(
            index, coverage, reader, generated_at=profile["generated_at"],
            regular_source_paths=regular_paths, g01_status=status,
            unknown_files=unknown,
        )
        analysis, merged = analyze_java_artifacts(
            index, coverage, profile, evidence, reader, regular_paths,
            g01_status=status, unknown_files=unknown,
        )
        framework = _framework(self)

        rebuilt_analysis, rebuilt_evidence = framework.rebuild_v12_bundle(
            index, coverage, profile, evidence, reader, regular_paths,
            g01_status=status, unknown_files=unknown,
        )

        self.assertEqual(dumps_artifact(analysis), dumps_artifact(rebuilt_analysis))
        self.assertEqual(dumps_artifact(merged), dumps_artifact(rebuilt_evidence))

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_emits_spring_mapping_only_inside_directly_marked_controller_class(self):
        source = b'''package sample;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import jakarta.persistence.Entity;
import org.junit.jupiter.api.Test;
@Controller @RequestMapping("/SECRET_BASE") class Api {
  @GetMapping(value = SECRET_PATH_PROVIDER()) String list() { return SECRET_VALUE; }
  class Nested { @GetMapping("/not-a-controller") String hidden() { return "x"; } }
}
@Entity class OrderRecord {}
class ApiTest { @Test void listsOrders() {} }
'''
        result = _c2(self, source)
        analysis, evidence = result[8], result[9]
        routes = [row for row in analysis["relations"] if row["kind"] == "ROUTE_TO"]
        roles = analysis["roles"]
        self.assertEqual(1, len(routes))
        self.assertEqual("CANDIDATE", routes[0]["certainty"])
        self.assertIsNone(routes[0]["target_id"])
        self.assertEqual("spring-route-declaration:org.springframework.web.bind.annotation.GetMapping",
                         routes[0]["unresolved_target"])
        self.assertEqual({"data_model_candidate", "test_candidate"}, {row["kind"] for row in roles})
        evidence_by_id = {row["id"]: row for row in evidence["items"]}
        for fact in [*routes, *roles]:
            citation = evidence_by_id[fact["evidence_ids"][0]]
            self.assertEqual("E1", citation["level"])
            self.assertEqual(fact["path"], citation["locator"]["path"])
        serialized = dumps_artifact(analysis) + dumps_artifact(evidence)
        for canary in ("SECRET_BASE", "SECRET_PATH_PROVIDER", "SECRET_VALUE", "/not-a-controller"):
            self.assertNotIn(canary, serialized)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_resolves_only_exact_imports_and_qualified_annotation_names(self):
        source = b'''package sample;
import org.springframework.web.bind.annotation.*;
@RestController class WildcardApi { @GetMapping void no() {} }
@org.springframework.web.bind.annotation.RestController
class QualifiedApi { @org.springframework.web.bind.annotation.GetMapping void yes() {} }
@interface GetMapping {}
class ShadowedApi { @org.springframework.web.bind.annotation.RestController
  @GetMapping void no() {} }
class PlainApi { @org.springframework.web.bind.annotation.GetMapping void no() {} }
'''
        result = _c2(self, source)
        analysis = result[8]
        routes = [row for row in analysis["relations"] if row["kind"] == "ROUTE_TO"]
        self.assertEqual(1, len(routes))
        symbols = {row["id"]: row for row in analysis["symbols"]}
        self.assertEqual("sample.QualifiedApi.yes", symbols[routes[0]["source_id"]]["qualified_name"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_exact_allowlist_in_all_annotation_families_emits_candidates_only(self):
        source = b'''package sample;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.RestController;
import javax.persistence.Entity;
import javax.persistence.Embeddable;
import javax.persistence.MappedSuperclass;
import org.junit.Test;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.params.ParameterizedTest;
@Controller class ControllerApi {
 @RequestMapping void a() {} @GetMapping void b() {} @PostMapping void c() {}
 @PutMapping void d() {} @PatchMapping void e() {} @DeleteMapping void f() {}
}
@RestController class RestApi { @GetMapping void g() {} }
@Entity class Model1 {} @Embeddable class Model2 {} @MappedSuperclass class Model3 {}
@jakarta.persistence.Entity class Model4 {} @jakarta.persistence.Embeddable class Model5 {}
@jakarta.persistence.MappedSuperclass class Model6 {}
class Tests { @Test void a() {} @org.junit.jupiter.api.Test void b() {}
 @RepeatedTest void c() {} @TestFactory void d() {} @ParameterizedTest void e() {} }
'''
        result = _c2(self, source)
        analysis = result[8]
        routes = [row for row in analysis["relations"] if row["kind"] == "ROUTE_TO"]
        self.assertEqual(7, len(routes))
        roles = analysis["roles"]
        self.assertEqual(11, len(roles))
        self.assertEqual(6, sum(row["kind"] == "data_model_candidate" for row in roles))
        self.assertEqual(5, sum(row["kind"] == "test_candidate" for row in roles))

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_names_and_file_paths_do_not_create_model_or_test_roles(self):
        source = b"class Entity { void testSomething() {} }\n"
        result = _c2(self, source, path="src/test/java/Entity.java")
        self.assertEqual([], result[8]["roles"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_same_line_routes_and_roles_have_stable_noncolliding_ids(self):
        source = (
            b"package sample; "
            b"import org.springframework.web.bind.annotation.RestController; "
            b"import org.springframework.web.bind.annotation.GetMapping; "
            b"import org.springframework.web.bind.annotation.PostMapping; "
            b"@RestController class Api { @GetMapping void a(){} @PostMapping void b(){} } "
            b"@jakarta.persistence.Entity class Entity {}"
        )
        first = _c2(self, source)
        second = _c2(self, source)

        def routes_by_method(result):
            analysis = result[8]
            symbols = {row["id"]: row for row in analysis["symbols"]}
            routes = [row for row in analysis["relations"] if row["kind"] == "ROUTE_TO"]
            self.assertEqual(2, len(routes))
            by_method = {symbols[row["source_id"]]["qualified_name"]: row for row in routes}
            self.assertEqual({"sample.Api.a", "sample.Api.b"}, set(by_method))
            return by_method

        first_routes = routes_by_method(first)
        second_routes = routes_by_method(second)
        first_ids_by_method = {name: row["id"] for name, row in first_routes.items()}
        second_ids_by_method = {name: row["id"] for name, row in second_routes.items()}
        self.assertEqual(first_ids_by_method, second_ids_by_method)
        self.assertEqual(2, len(set(first_ids_by_method.values())))

        evidence_by_id = {row["id"]: row for row in first[9]["items"]}
        expected_annotations = {
            "sample.Api.a": "org.springframework.web.bind.annotation.GetMapping",
            "sample.Api.b": "org.springframework.web.bind.annotation.PostMapping",
        }
        for method_name, route in first_routes.items():
            self.assertEqual("CANDIDATE", route["certainty"])
            self.assertEqual(1, route["line_start"])
            self.assertEqual(1, route["line_end"])
            self.assertEqual(1, len(route["evidence_ids"]))
            citation = evidence_by_id[route["evidence_ids"][0]]
            self.assertEqual("E1", citation["level"])
            self.assertEqual("src/Api.java", citation["locator"]["path"])
            annotation = expected_annotations[method_name]
            self.assertEqual(f"spring-route-declaration:{annotation}", citation["locator"]["symbol"])
            self.assertEqual(f"Java method has allowlisted route annotation {annotation}.", citation["summary"])
            self.assertEqual(1, citation["locator"]["line_start"])
            self.assertEqual(1, citation["locator"]["line_end"])
            self.assertEqual(route["unresolved_target"], citation["locator"]["symbol"])

        first_facts = first[8]["relations"] + first[8]["roles"]
        second_facts = second[8]["relations"] + second[8]["roles"]
        first_ids = [fact["id"] for fact in first_facts]
        self.assertEqual(first_ids, [fact["id"] for fact in second_facts])
        self.assertEqual(len(first_ids), len(set(first_ids)))
        self.assertEqual(1, sum(row["kind"] == "data_model_candidate" for row in first[8]["roles"]))

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_wildcard_spring_imports_do_not_create_route_candidates(self):
        source = (
            b"package sample; import org.springframework.web.bind.annotation.*; "
            b"@RestController class Api { @GetMapping void list(){} }"
        )
        result = _c2(self, source)
        routes = [row for row in result[8]["relations"] if row["kind"] == "ROUTE_TO"]

        self.assertEqual([], routes)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_roles_and_routes_require_e1_and_reject_retargeting_to_unrelated_symbol(self):
        source = b"import jakarta.persistence.Entity; @Entity class Model {} class Unrelated {}"
        result = _c2(self, source)
        framework, index, coverage, profile, original_evidence = (
            result[0], result[2], result[3], result[4], result[5]
        )
        c1_analysis, c1_evidence, analysis, evidence = result[6], result[7], result[8], result[9]
        role = analysis["roles"][0]
        unrelated = next(row for row in analysis["symbols"] if row["name"] == "Unrelated")
        tampered = copy.deepcopy(analysis)
        tampered["roles"][0]["symbol_id"] = unrelated["id"]
        with self.assertRaises(framework.StaticAnalysisError):
            framework.validate_java_framework_bundle(
                index, coverage, profile, original_evidence, c1_analysis, c1_evidence,
                tampered, evidence, result[12],
                g01_status=result[10], unknown_files=result[11],
                regular_source_paths=result[13],
            )
        self.assertTrue(role["evidence_ids"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_v13_rejects_each_mismatched_input_digest(self):
        source = b"import jakarta.persistence.Entity; @Entity class Model {}\n"
        result = _c2(self, source)
        framework, _sources, index, coverage, profile, original_evidence = result[:6]
        c1_analysis, c1_evidence, analysis, evidence = result[6:10]
        digests = (
            "stack_profile_sha256", "phase3a_evidence_sha256",
            "java_analysis_v12_sha256", "java_evidence_v12_sha256",
        )
        for field in digests:
            tampered = copy.deepcopy(analysis)
            tampered[field] = "0" * 64
            with self.subTest(field=field), self.assertRaisesRegex(
                framework.StaticAnalysisError, "digest mismatch",
            ):
                framework.validate_java_framework_bundle(
                    index, coverage, profile, original_evidence,
                    c1_analysis, c1_evidence, tampered, evidence, result[12],
                    g01_status=result[10], unknown_files=result[11],
                    regular_source_paths=result[13],
                )


@unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
class JavaFrameworkProtocolTests(unittest.TestCase):
    def test_pdj1_default_protocol_matches_captured_baseline(self):
        source = b"package sample; class Baseline { void ping() {} }\n"
        temporary, result = _run_bridge(source)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", "replace"))
        expected = base64.b64decode(
            "UERKMQ0KRklMRQkwCU9LCTEJNTANClNZTUJPTAkwCXBhY2thZ2UJYzJGdGNHeGwJYzJGdGNHeGwJMQkxCTAJMTUJY0dGamEyRm5aVG93T2pFMQlZMjl0Y0dsc1lYUnBiMjVmZFc1cGREb3dPalV3DQpTWU1CT0wJMAljbGFzcwlRbUZ6Wld4cGJtVT0JYzJGdGNHeGxMa0poYzJWc2FXNWwJMQkxCTE2CTQ5CVkyeGhjM002TVRZNk5Eaz0JWTI5dGNHbHNZWFJwYjI1ZmRXNXBkRG93T2pVdw0KU1lNQk9MCTAJbWV0aG9kCWNHbHVadz09CWMyRnRjR3hsTGtKaGMyVnNhVzVsTG5CcGJtYz0JMQkxCTMzCTQ3CWJXVjBhRzlrT2pNek9qUTMJWTJ4aGMzTTZNVFk2TkRrPQ0K"
        )
        self.assertEqual(expected, result.stdout)

    def test_pdj2_keeps_allowlisted_annotation_identity_and_utf16_offsets_only(self):
        source = (
            'package sample; import org.springframework.web.bind.annotation.GetMapping; '
            'import org.springframework.web.bind.annotation.RestController; '
            '@RestController class Api { String marker = "😀"; '
            '@GetMapping(value = {"/SECRET_ROUTE", SECRET_PATH_PROVIDER(SECRET_PARAMETER)}) '
            'String list() { return "SECRET_VALUE"; } }'
        ).encode("utf-8")
        second_source = b"@jakarta.persistence.Entity class Model {}\n"
        temporary, result = _run_bridge([source, second_source], "--protocol", "PDJ2")
        self.addCleanup(temporary.cleanup)
        output = result.stdout.decode("utf-8", "replace")
        self.assertEqual(0, result.returncode, output + result.stderr.decode("utf-8", "replace"))
        self.assertEqual("PDJ2", output.splitlines()[0])
        for canary in ("SECRET_PATH_PROVIDER", "SECRET_PARAMETER", "SECRET_ROUTE", "SECRET_VALUE", "GetMapping(value", "\"😀\""):
            self.assertNotIn(canary, output)
        framework = _framework(self)
        parsed = framework._parse_framework_protocol(
            output, [
                {"path": "Api.java", "text": source.decode("utf-8")},
                {"path": "Model.java", "text": second_source.decode("utf-8")},
            ],
        )
        annotations = parsed[0]["annotations"] + parsed[1]["annotations"]
        self.assertEqual(3, len(annotations))
        route = next(row for row in annotations if row["annotation_fqn"].endswith(".GetMapping"))
        self.assertGreaterEqual(route["start_offset"], 0)
        self.assertGreater(route["end_offset"], route["start_offset"])
        source_length = len(source.decode("utf-8").encode("utf-16-le")) // 2
        self.assertLessEqual(route["end_offset"], source_length)
        annotation_start = source.decode("utf-8").index("@GetMapping")
        expected_start = len(source.decode("utf-8")[:annotation_start].encode("utf-16-le")) // 2
        self.assertEqual(expected_start, route["start_offset"])

    def test_pdj1_and_pdj2_parsers_reject_each_others_protocol(self):
        framework = _framework(self)
        java_analyzer = importlib.import_module("java_static_analysis")
        files = [{"path": "Api.java", "text": ""}]
        with self.assertRaises(java_analyzer.StaticAnalysisError):
            java_analyzer._parse_protocol("PDJ2\n", files)
        with self.assertRaises(framework.StaticAnalysisError):
            framework._parse_framework_protocol("PDJ1\n", files)

    def test_pdj2_malformed_rows_positions_and_duplicate_ids_fail_closed(self):
        framework = _framework(self)
        files = [{"path": "Api.java", "text": "class Api {}"}]
        annotation = (
            "ANNOTATION\t0\t" + base64.b64encode(b"org.springframework.web.bind.annotation.GetMapping").decode("ascii")
            + "\t" + base64.b64encode(b"compilation_unit:0:12").decode("ascii")
            + "\t" + base64.b64encode(b"method:0:12").decode("ascii")
            + "\t1\t0\t5"
        )
        malformed = (
            "PDJ2\nUNKNOWN\n",
            "PDJ1\n",
            "PDJ2\nFILE\t0\tOK\t1\t11\nFILE\t0\tOK\t1\t11\n",
            "PDJ2\nFILE\t0\tOK\t1\t12\n" + annotation + "\n" + annotation + "\n",
            "PDJ2\nFILE\t0\tOK\t1\t12\n" + annotation[:-1] + "\t13\n",
            "PDJ2\nFILE\t0\tOK\t1\t12\nANNOTATION\t0\t***\n",
        )
        for output in malformed:
            with self.subTest(output=output), self.assertRaises(framework.StaticAnalysisError):
                framework._parse_framework_protocol(output, files)

    def test_pdj2_oversized_numeric_field_fails_with_fixed_error(self):
        framework = _framework(self)
        files = [{"path": "Api.java", "text": "class Api {}"}]
        oversized_index = "9" * 5000
        output = f"PDJ2\nFILE\t{oversized_index}\tOK\t1\t12\n"

        with self.assertRaisesRegex(framework.StaticAnalysisError, "PDJ2 contains invalid file index"):
            framework._parse_framework_protocol(output, files)
