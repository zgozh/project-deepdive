#!/usr/bin/env python3
"""Regression tests for parse-only Phase 3C1 Java static analysis."""

from __future__ import annotations

import copy
import hashlib
import importlib
import shutil
import sys
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402


FIXED_TIME = "2026-09-23T00:00:00Z"
REVISION = "b" * 40


def _analyzer(testcase):
    try:
        return importlib.import_module("java_static_analysis")
    except ImportError as exc:
        testcase.fail(f"Phase 3C1 Java analyzer is missing: {exc}")


def _inputs(sources, classifications=None, *, snapshot_kind="worktree"):
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
        entries.append({
            "path": path,
            "surface": "other",
            "classification": classification,
            "teaching_status": "LOCATED" if classification != "VENDOR" else "NOT_APPLICABLE",
            "reason": "Controlled Java parse regression fixture.",
        })
    unknown_count = sum(item["classification"] == "UNKNOWN" for item in entries)
    project_index = {
        "artifact_kind": "project-index",
        "schema_version": "1.0.0",
        "repository_revision": REVISION,
        "generated_at": FIXED_TIME,
        "project": {
            "name": "java-parse-fixture",
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
    g01_status = "PARTIAL" if unknown_count else "PASS"
    profile, evidence = build_stack_artifacts(
        project_index,
        coverage,
        lambda path, _entry: sources[path],
        generated_at=FIXED_TIME,
        regular_source_paths=frozenset(sources),
        g01_status=g01_status,
        unknown_files=unknown_count,
    )
    return project_index, coverage, profile, evidence, g01_status, unknown_count


def _analyze(testcase, sources, classifications=None, *, snapshot_kind="worktree"):
    analyzer = _analyzer(testcase)
    index, coverage, profile, evidence, g01_status, unknown_files = _inputs(
        sources, classifications, snapshot_kind=snapshot_kind,
    )
    analysis, merged_evidence = analyzer.analyze_java_artifacts(
        index,
        coverage,
        profile,
        evidence,
        lambda path, _entry: sources[path],
        frozenset(sources),
        g01_status=g01_status,
        unknown_files=unknown_files,
    )
    return (analyzer, index, coverage, profile, evidence, analysis,
            merged_evidence, g01_status, unknown_files)


@unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
class JavaStaticAnalysisTests(unittest.TestCase):
    def test_extracts_package_imports_types_methods_and_unresolved_type_edges(self):
        source = b'''package sample.deep;
import java.util.List;
import static java.util.Collections.emptyList;
import java.util.*;
public class Service extends Base implements Runnable {
  public Service() {}
  public Service(int size) {}
  public void work() {}
  public void work(int size) {}
  class Nested extends Parent {}
}
interface Contract extends OtherContract {}
enum Mode { FIRST }
record Data(String name) {}
@interface Marker {}
'''
        (analyzer, index, coverage, profile, evidence, analysis, merged,
         g01_status, unknown_files) = _analyze(self, {"src/Service.java": source})

        self.assertEqual("1.2.0", analysis["schema_version"])
        self.assertEqual(["java"], [item["language"] for item in analysis["languages"]])
        self.assertEqual("PASS", analysis["analysis_status"])
        kinds = {(item["kind"], item["name"]) for item in analysis["symbols"]}
        self.assertIn(("package", "sample.deep"), kinds)
        self.assertIn(("class", "Service"), kinds)
        self.assertIn("sample.deep.Service", {
            item["qualified_name"] for item in analysis["symbols"]
        })
        self.assertIn(("class", "Nested"), kinds)
        self.assertIn(("interface", "Contract"), kinds)
        self.assertIn(("enum", "Mode"), kinds)
        self.assertIn(("record", "Data"), kinds)
        self.assertIn(("annotation", "Marker"), kinds)
        self.assertEqual(2, sum(
            item["kind"] == "constructor" and item["name"] == "Service"
            for item in analysis["symbols"]
        ))
        self.assertEqual(2, sum(
            item["kind"] == "method" and item["name"] == "work"
            for item in analysis["symbols"]
        ))
        self.assertEqual(
            len({item["id"] for item in analysis["symbols"]}),
            len(analysis["symbols"]),
        )
        relation_kinds = {item["kind"] for item in analysis["relations"]}
        self.assertTrue({"DEFINES", "IMPORTS", "EXTENDS", "IMPLEMENTS"} <= relation_kinds)
        for relation in analysis["relations"]:
            if relation["kind"] != "DEFINES":
                self.assertIsNone(relation["target_id"])
                self.assertTrue(relation["unresolved_target"])

        evidence_by_id = {item["id"]: item for item in merged["items"]}
        old_by_id = {item["id"]: item for item in evidence["items"]}
        self.assertEqual(old_by_id, {key: evidence_by_id[key] for key in old_by_id})
        facts = analysis["symbols"] + analysis["relations"]
        self.assertTrue(all(fact["evidence_ids"] for fact in facts))
        for fact in facts:
            for evidence_id in fact["evidence_ids"]:
                citation = evidence_by_id[evidence_id]
                self.assertEqual("E1", citation["level"])
                self.assertEqual(fact["path"], citation["locator"]["path"])
                self.assertEqual(fact["line_start"], citation["locator"]["line_start"])
                self.assertEqual(fact["line_end"], citation["locator"]["line_end"])
        analyzer.validate_java_analysis_bundle(
            index, coverage, profile, evidence, merged, analysis,
            g01_status=g01_status, unknown_files=unknown_files,
        )

    def test_ids_are_byte_stable_for_overloads_and_same_line_facts(self):
        source = b"package stable; class Same { Same() {} Same(int n) {} void run() {} void run(int n) {} }\n"
        first = _analyze(self, {"Same.java": source})
        second = _analyze(self, {"Same.java": source})

        first_ids = [fact["id"] for fact in first[5]["symbols"] + first[5]["relations"]]
        second_ids = [fact["id"] for fact in second[5]["symbols"] + second[5]["relations"]]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(first_ids), len(set(first_ids)))

    def test_invalid_java_is_honestly_skipped_and_source_text_is_not_emitted(self):
        source = b"class Broken { void run( { String secret = \"PRIVATE_JAVA_CANARY\"; }\n"
        result = _analyze(self, {"Broken.java": source})

        analysis, merged = result[5], result[6]
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        self.assertEqual([], analysis["symbols"])
        self.assertEqual([], analysis["relations"])
        self.assertEqual("SKIPPED", analysis["languages"][0]["files"][0]["status"])
        self.assertEqual("UNSUPPORTED_SYNTAX", analysis["languages"][0]["files"][0]["limitations"][0]["code"])
        self.assertNotIn("PRIVATE_JAVA_CANARY", repr(analysis) + repr(merged))

    def test_one_invalid_source_does_not_hide_valid_files_in_the_same_batch(self):
        result = _analyze(self, {
            "src/Good.java": b"class GoodJava {}\n",
            "src/Bad.java": b"class BadJava { void run( }\n",
        })
        analysis = result[5]

        records = {item["path"]: item for item in analysis["languages"][0]["files"]}
        self.assertEqual("ANALYZED", records["src/Good.java"]["status"])
        self.assertEqual("SKIPPED", records["src/Bad.java"]["status"])
        self.assertIn("GoodJava", {item["name"] for item in analysis["symbols"]})
        self.assertNotIn("BadJava", {item["name"] for item in analysis["symbols"]})

    def test_anonymous_class_member_scope_is_reported_as_partial(self):
        source = (
            b"class Outer { void register() { new Runnable() { public void run() {} }; } }\n"
        )
        result = _analyze(self, {"Outer.java": source})
        analysis = result[5]

        self.assertEqual("PARTIAL", analysis["analysis_status"])
        file_record = analysis["languages"][0]["files"][0]
        self.assertEqual("ANALYZED", file_record["status"])
        self.assertEqual("OMITTED_ANONYMOUS_CLASS_MEMBERS", file_record["limitations"][0]["code"])
        self.assertNotIn("run", {item["name"] for item in analysis["symbols"]})
        self.assertIn("register", {item["name"] for item in analysis["symbols"]})

    def test_ast_node_limit_skips_the_file_without_partial_facts(self):
        source = ("class Large { void run() {" + "work();" * 18_000 + "} }\n").encode("utf-8")
        result = _analyze(self, {"Large.java": source})

        analysis = result[5]
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        self.assertEqual([], analysis["symbols"])
        self.assertEqual([], analysis["relations"])
        self.assertEqual("AST_TOO_LARGE", analysis["languages"][0]["files"][0]["limitations"][0]["code"])

    def test_java_inputs_are_read_and_released_one_bounded_batch_at_a_time(self):
        analyzer = _analyzer(self)
        sources = {
            f"src/C{index}.java": f"class C{index} {{}}\n".encode("utf-8")
            for index in range(5)
        }
        index, coverage, profile, evidence, status, unknown = _inputs(sources)[:6]
        reads = []
        decoded_refs = []
        observed_batches = []
        last_read = [None]
        original_decode = analyzer._decode_source
        original_popen = analyzer.subprocess.Popen

        class ObservedText(str):
            pass

        def read_source(path, _entry):
            reads.append(path)
            last_read[0] = path
            return sources[path]

        def track_decode(payload):
            decoded = ObservedText(original_decode(payload))
            decoded.source_path = last_read[0]
            decoded_refs.append(weakref.ref(decoded))
            return decoded

        def observe_parser_start(command, *args, **kwargs):
            parts = [str(item) for item in command]
            if "DeepDiveJavaParser" in parts:
                live_paths = sorted(
                    reference().source_path
                    for reference in decoded_refs
                    if reference() is not None
                )
                observed_batches.append((list(reads), live_paths))
            return original_popen(command, *args, **kwargs)

        with (
            patch.object(analyzer, "MAX_BATCH_FILES", 2),
            patch.object(analyzer, "MAX_BATCH_BYTES", 4096),
            patch.object(analyzer, "_decode_source", side_effect=track_decode),
            patch.object(analyzer.subprocess, "Popen", side_effect=observe_parser_start),
        ):
            analysis, _merged = analyzer.analyze_java_artifacts(
                index, coverage, profile, evidence, read_source, frozenset(sources),
                g01_status=status, unknown_files=unknown,
            )

        self.assertEqual("PASS", analysis["analysis_status"])
        self.assertEqual(
            [
                (["src/C0.java", "src/C1.java"], ["src/C0.java", "src/C1.java"]),
                (["src/C0.java", "src/C1.java", "src/C2.java", "src/C3.java"],
                 ["src/C2.java", "src/C3.java"]),
                (["src/C0.java", "src/C1.java", "src/C2.java", "src/C3.java", "src/C4.java"],
                 ["src/C4.java"]),
            ],
            observed_batches,
        )

    def test_target_static_initializer_is_never_executed_and_body_literals_are_not_emitted(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "target-executed.marker"
            source = (
                "class NeverRun {\n"
                " static { try { java.nio.file.Files.createFile(java.nio.file.Path.of(\""
                + str(marker).replace("\\", "\\\\")
                + "\")); } catch (Exception ignored) {} }\n"
                " String value() { return \"PRIVATE_BODY_CANARY\"; }\n"
                "}\n"
            ).encode("utf-8")
            result = _analyze(self, {"NeverRun.java": source})

            self.assertFalse(marker.exists())
            self.assertNotIn("PRIVATE_BODY_CANARY", repr(result[5]) + repr(result[6]))
            self.assertNotIn(str(marker), repr(result[5]) + repr(result[6]))

    def test_missing_jdk_yields_no_jdk_partial_without_facts(self):
        result = _analyze(self, {"src/One.java": b"class One {}\n"})
        analyzer, index, coverage, profile, evidence = result[:5]
        with patch.object(analyzer.shutil, "which", return_value=None):
            analysis, merged = analyzer.analyze_java_artifacts(
                index, coverage, profile, evidence,
                lambda path, _entry: b"class One {}\n", frozenset({"src/One.java"}),
                g01_status="PASS", unknown_files=0,
            )
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        self.assertEqual([], analysis["symbols"])
        self.assertEqual("NO_JDK", analysis["languages"][0]["files"][0]["limitations"][0]["code"])
        self.assertEqual(evidence["items"], merged["items"])

    def test_non_regular_snapshot_entry_is_not_read_or_parsed(self):
        result = _analyze(self, {"src/Link.java": b"class Link {}\n"})
        analyzer, index, coverage, profile, evidence = result[:5]
        analysis, _merged = analyzer.analyze_java_artifacts(
            index, coverage, profile, evidence,
            lambda _path, _entry: self.fail("non-regular Git entries must not be read"),
            frozenset(), g01_status="PASS", unknown_files=0,
        )
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        self.assertEqual("NOT_REGULAR_SOURCE", analysis["languages"][0]["files"][0]["limitations"][0]["code"])

    def test_source_digest_and_semantic_evidence_tampering_fail_closed(self):
        result = _analyze(self, {"src/One.java": b"class One { void run() {} }\n"})
        analyzer, index, coverage, profile, evidence, analysis, merged, g01_status, unknown_files = result
        wrong_digest_reader = lambda _path, _entry: b"class Changed {}\n"
        with self.assertRaises(analyzer.StaticAnalysisError):
            analyzer.analyze_java_artifacts(
                index, coverage, profile, evidence, wrong_digest_reader,
                frozenset({"src/One.java"}), g01_status=g01_status,
                unknown_files=unknown_files,
            )

        tampered = copy.deepcopy(merged)
        new_ids = {eid for fact in analysis["symbols"] + analysis["relations"] for eid in fact["evidence_ids"]}
        old_ids = {item["id"] for item in evidence["items"]}
        new_id = next(iter(new_ids - old_ids))
        next(item for item in tampered["items"] if item["id"] == new_id)["summary"] = "runtime execution observed"
        with self.assertRaises(analyzer.StaticAnalysisError):
            analyzer.validate_java_analysis_bundle(
                index, coverage, profile, evidence, tampered, analysis,
                g01_status=g01_status, unknown_files=unknown_files,
            )

    def test_relation_cannot_be_retargeted_to_a_symbol_outside_the_source_scope(self):
        result = _analyze(self, {"src/One.java": b"class One { void run() {} }\n"})
        analyzer, index, coverage, profile, evidence, analysis, merged, g01_status, unknown_files = result
        corrupted = copy.deepcopy(analysis)
        method = next(item for item in corrupted["symbols"] if item["kind"] == "method")
        relation = next(
            item for item in corrupted["relations"]
            if item["kind"] == "DEFINES" and item["target_id"] == method["id"]
        )
        module_symbol = next(item for item in corrupted["symbols"] if item["kind"] == "compilation_unit")
        relation["source_id"] = module_symbol["id"]
        with self.assertRaises(analyzer.StaticAnalysisError):
            analyzer.validate_java_analysis_bundle(
                index, coverage, profile, evidence, merged, corrupted,
                g01_status=g01_status, unknown_files=unknown_files,
            )

    def test_v12_java_bundle_rejects_phase3c2_roles(self):
        result = _analyze(self, {"src/One.java": b"class One {}\n"})
        analyzer, index, coverage, profile, evidence, analysis, merged, g01_status, unknown_files = result
        corrupted = copy.deepcopy(analysis)
        symbol = corrupted["symbols"][0]
        corrupted["roles"] = [{
            "id": "ROLE-" + "a" * 24,
            "language": "java",
            "kind": "test_candidate",
            "symbol_id": symbol["id"],
            "path": symbol["path"],
            "extraction_method": "jdk-javac-parse",
            "certainty": "CANDIDATE",
            "line_start": 1,
            "line_end": 1,
            "evidence_ids": symbol["evidence_ids"],
        }]

        with self.assertRaises(analyzer.StaticAnalysisError):
            analyzer.validate_java_analysis_bundle(
                index, coverage, profile, evidence, merged, corrupted,
                g01_status=g01_status, unknown_files=unknown_files,
            )

    def test_phase3a_provenance_mismatch_fails_before_parsing(self):
        result = _analyze(self, {"src/One.java": b"class One {}\n"})
        analyzer, index, coverage, profile, evidence = result[:5]
        wrong_profile = copy.deepcopy(profile)
        wrong_profile["source_metadata"]["coverage_sha256"] = "0" * 64
        with self.assertRaises(analyzer.StaticAnalysisError):
            analyzer.analyze_java_artifacts(
                index, coverage, wrong_profile, evidence,
                lambda _path, _entry: self.fail("provenance must be checked before source reads"),
                frozenset({"src/One.java"}), g01_status="PASS", unknown_files=0,
            )

    def test_excluded_non_java_binary_and_oversized_sources_are_not_parsed(self):
        sources = {
            "src/Good.java": b"class Good {}\n",
            "vendor/Excluded.java": b"class Excluded {}\n",
            "src/Bad.java": b"\x00class Bad {}\n",
            "src/Large.java": b" " * (1024 * 1024 + 1),
            "src/readme.txt": b"class NotJava {}\n",
        }
        result = _analyze(
            self, sources,
            {"vendor/Excluded.java": "VENDOR"},
        )
        analysis = result[5]
        records = {item["path"]: item for item in analysis["languages"][0]["files"]}
        self.assertEqual("ANALYZED", records["src/Good.java"]["status"])
        self.assertEqual("SKIPPED", records["vendor/Excluded.java"]["status"])
        self.assertEqual("SKIPPED", records["src/Bad.java"]["status"])
        self.assertEqual("SKIPPED", records["src/Large.java"]["status"])
        self.assertNotIn("src/readme.txt", records)
        names = {item["name"] for item in analysis["symbols"]}
        self.assertEqual({"Good"}, names)
