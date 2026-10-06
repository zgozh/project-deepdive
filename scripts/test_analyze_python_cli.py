#!/usr/bin/env python3
"""Integration and fail-closed tests for the Phase 3B1 Python CLI."""

from __future__ import annotations

import copy
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import AuditViolation  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_repository_scan import (  # noqa: E402
    create_junction_or_skip,
    init_git_repo,
)
import scan_repository as scan_repository_cli  # noqa: E402


FIXED_TIME = "2026-09-23T00:00:00Z"
CLI = SKILL_ROOT / "scripts" / "analyze_python.py"
STACK_CLI = SKILL_ROOT / "scripts" / "detect_stack.py"


def _cli_module(testcase):
    try:
        return importlib.import_module("analyze_python")
    except ImportError as exc:
        testcase.fail(f"Phase 3B1 CLI module is missing: {exc}")


def _scan(root, snapshot="worktree"):
    return scan_repository(ScanOptions(
        root=Path(root), snapshot_kind=snapshot, generated_at=FIXED_TIME,
    ))


def _write_phase2_inputs(work, artifacts, *, legacy=False):
    index = copy.deepcopy(artifacts.project_index)
    coverage = copy.deepcopy(artifacts.coverage)
    if legacy:
        index["schema_version"] = "1.0.0"
        for item in index["files"]:
            for field in ("content_kind", "media_type", "extension", "vcs_object_id"):
                item.pop(field, None)
        coverage["schema_version"] = "1.0.0"
        coverage.pop("classification_policy_version", None)
        for item in coverage["entries"]:
            item.pop("secondary_surfaces", None)
            item.pop("rule_id", None)
    index_path = work / "project-index.json"
    coverage_path = work / "coverage.json"
    index_path.write_text(dumps_artifact(index), encoding="utf-8")
    coverage_path.write_text(dumps_artifact(coverage), encoding="utf-8")
    return index_path, coverage_path


def _run_stack(root, index, coverage, out):
    return subprocess.run(
        [sys.executable, str(STACK_CLI), "--root", str(root), "--index", str(index),
         "--coverage", str(coverage), "--out", str(out), "--generated-at", FIXED_TIME],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_python(root, index, coverage, profile, evidence, out, *, analysis_version=None):
    command = [sys.executable, str(CLI), "--root", str(root), "--index", str(index),
               "--coverage", str(coverage), "--stack-profile", str(profile),
               "--evidence", str(evidence), "--out", str(out)]
    if analysis_version is not None:
        command.extend(["--analysis-version", analysis_version])
    return subprocess.run(
        command,
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class AnalyzePythonCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "repo"

    def _prepare(self, sources, *, snapshot="worktree", legacy=False):
        init_git_repo(self.root, sources)
        artifacts = _scan(self.root, snapshot=snapshot)
        index, coverage = _write_phase2_inputs(self.work, artifacts, legacy=legacy)
        inputs = self.work / "phase3a-inputs"
        phase3a = _run_stack(self.root, index, coverage, inputs)
        self.assertEqual(0, phase3a.returncode, phase3a.stdout + phase3a.stderr)
        return index, coverage, inputs

    def test_worktree_cli_publishes_deterministic_bundle_and_preserves_stack_profile(self):
        source = (
            "import os, sys as system\n"
            "from . import helper\n"
            "class Service:\n"
            "    def handle(self):\n"
            "        def nested():\n"
            "            return 'PRIVATE_BODY_CANARY'\n"
            "        return nested()\n"
        ).encode("utf-8")
        index, coverage, inputs = self._prepare({"src/service.py": source})
        original_profile_bytes = (inputs / "stack-profile.json").read_bytes()
        original_profile = load_artifact(inputs / "stack-profile.json")
        original_evidence = load_artifact(inputs / "evidence.json")
        out_one = self.work / "out-one"
        out_two = self.work / "out-two"

        first = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out_one,
        )
        second = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out_two,
        )

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(original_profile_bytes, (inputs / "stack-profile.json").read_bytes())
        self.assertEqual(original_profile_bytes, (out_one / "stack-profile.json").read_bytes())
        for filename in ("stack-profile.json", "evidence.json", "static-analysis.json"):
            self.assertEqual((out_one / filename).read_bytes(), (out_two / filename).read_bytes())

        profile = load_artifact(out_one / "stack-profile.json")
        evidence = load_artifact(out_one / "evidence.json")
        static_analysis = load_artifact(out_one / "static-analysis.json")
        self.assertEqual(original_profile, profile)
        self.assertEqual(profile["repository_revision"], static_analysis["repository_revision"])
        self.assertEqual(profile["generated_at"], static_analysis["generated_at"])
        self.assertEqual(profile["source_metadata"], static_analysis["source_metadata"])
        self.assertEqual(profile["source_metadata"], evidence["source_metadata"])
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        old_by_id = {item["id"]: item for item in original_evidence["items"]}
        self.assertTrue(set(old_by_id) <= set(evidence_by_id))
        self.assertEqual(old_by_id, {key: evidence_by_id[key] for key in old_by_id})
        self.assertTrue(all(
            evidence_by_id[evidence_id]["level"] == "E1"
            for item in static_analysis["symbols"] + static_analysis["relations"]
            for evidence_id in item["evidence_ids"]
        ))
        self.assertTrue(all(
            relation["target_id"] is None
            for relation in static_analysis["relations"]
            if relation["kind"] == "IMPORTS"
        ))
        self.assertFalse(any(
            relation["kind"] in {"CALL_CANDIDATE", "ROUTE_TO", "MODEL"}
            for relation in static_analysis["relations"]
        ))
        combined = "".join(path.read_text(encoding="utf-8") for path in out_one.glob("*.json"))
        self.assertNotIn("PRIVATE_BODY_CANARY", combined)

    def test_v11_cli_is_explicit_and_default_v10_output_stays_unchanged(self):
        source = (
            "def target(): return 1\n"
            "def handler():\n"
            "    target()\n"
            "    return 'ROUTE_OR_SOURCE_SECRET'\n"
        ).encode("utf-8")
        index, coverage, inputs = self._prepare({"src/handler.py": source})
        original_evidence = load_artifact(inputs / "evidence.json")
        out_v10 = self.work / "out-v10"
        out_v11 = self.work / "out-v11"
        legacy_result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out_v10,
        )
        extended_result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out_v11,
            analysis_version="1.1.0",
        )
        self.assertEqual(0, legacy_result.returncode, legacy_result.stdout + legacy_result.stderr)
        self.assertEqual(0, extended_result.returncode, extended_result.stdout + extended_result.stderr)
        v10 = load_artifact(out_v10 / "static-analysis.json")
        v11 = load_artifact(out_v11 / "static-analysis.json")
        v10_evidence = load_artifact(out_v10 / "evidence.json")
        v11_evidence = load_artifact(out_v11 / "evidence.json")
        self.assertEqual("1.0.0", v10["schema_version"])
        self.assertFalse(any(item["kind"] == "CALL_CANDIDATE" for item in v10["relations"]))
        self.assertEqual("1.1.0", v11["schema_version"])
        self.assertTrue(any(item["kind"] == "CALL_CANDIDATE" for item in v11["relations"]))
        self.assertEqual(v10["symbols"], v11["symbols"])
        self.assertEqual(v10["relations"], [
            item for item in v11["relations"] if item["kind"] in {"DEFINES", "IMPORTS"}
        ])
        self.assertEqual(v10["source_metadata"], v11["source_metadata"])
        old_records = {
            item["id"]: item for item in v10_evidence["items"]
        }
        self.assertEqual(old_records, {
            item["id"]: item for item in v11_evidence["items"] if item["id"] in old_records
        })
        b2_fact_evidence_ids = {
            evidence_id for fact in v11["relations"] + v11["roles"]
            if fact["kind"] not in {"DEFINES", "IMPORTS"}
            for evidence_id in fact["evidence_ids"]
        }
        v11_evidence_by_id = {item["id"]: item for item in v11_evidence["items"]}
        self.assertTrue(all(v11_evidence_by_id[identifier]["level"] == "E1"
                            for identifier in b2_fact_evidence_ids))
        self.assertEqual(
            {item["id"]: item for item in original_evidence["items"]},
            {item["id"]: item for item in v11_evidence["items"] if item["id"] in {
                old["id"] for old in original_evidence["items"]
            }},
        )
        self.assertNotIn("ROUTE_OR_SOURCE_SECRET", (
            (out_v11 / "static-analysis.json").read_text(encoding="utf-8")
            + (out_v11 / "evidence.json").read_text(encoding="utf-8")
        ))

    def test_v11_cli_publishes_multiline_call_and_base_with_stable_e1_facts(self):
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
        index, coverage, inputs = self._prepare({"src/multiline.py": source})
        outputs = [self.work / "out-multiline-one", self.work / "out-multiline-two"]
        results = [
            _run_python(
                self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", output,
                analysis_version="1.1.0",
            )
            for output in outputs
        ]
        self.assertTrue(all(result.returncode == 0 for result in results),
                        "\n".join(result.stdout + result.stderr for result in results))
        analyses = [load_artifact(output / "static-analysis.json") for output in outputs]
        self.assertTrue(all((output / "stack-profile.json").is_file()
                            and (output / "evidence.json").is_file()
                            and (output / "static-analysis.json").is_file() for output in outputs))
        self.assertEqual(dumps_artifact(analyses[0]), dumps_artifact(analyses[1]))
        self.assertEqual((outputs[0] / "evidence.json").read_bytes(),
                         (outputs[1] / "evidence.json").read_bytes())

        analysis = analyses[0]
        self.assertEqual("1.1.0", analysis["schema_version"])
        calls = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        multiline_calls = [item for item in calls if item["line_start"] in {7, 10}]
        multiline_extends = [item for item in analysis["relations"]
                             if item["kind"] == "EXTENDS" and item["line_start"] == 10]
        self.assertEqual({7, 10}, {item["line_start"] for item in multiline_calls})
        self.assertEqual(1, len(multiline_extends))
        evidence = load_artifact(outputs[0] / "evidence.json")
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        for fact in multiline_calls + multiline_extends:
            self.assertTrue(fact["evidence_ids"])
            self.assertTrue(all(evidence_by_id[identifier]["level"] == "E1"
                                for identifier in fact["evidence_ids"]))

    def test_v11_git_tree_cli_keeps_committed_snapshot_when_worktree_changes(self):
        index, coverage, inputs = self._prepare(
            {"src/service.py": b"def target(): pass\ndef handler():\n    target()\n"},
            snapshot="git-tree",
        )
        (self.root / "src" / "service.py").write_text(
            "def changed_target(): pass\ndef handler():\n    changed_target()\n", encoding="utf-8",
        )
        out = self.work / "out-v11-tree"
        result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out,
            analysis_version="1.1.0",
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis = load_artifact(out / "static-analysis.json")
        targets = [item for item in analysis["relations"] if item["kind"] == "CALL_CANDIDATE"]
        self.assertEqual(1, len(targets))
        target_names = {item["qualified_name"] for item in analysis["symbols"] if item["id"] == targets[0]["target_id"]}
        self.assertEqual({"src.service.target"}, target_names)

    def test_v11_cli_redacts_dynamic_route_argument_identifiers_and_values(self):
        source = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get(SECRET_PATH_PROVIDER('CLI_ROUTE_ARGUMENT_VALUE'))\n"
            "def handler(): pass\n"
        ).encode("utf-8")
        index, coverage, inputs = self._prepare({"src/secret_routes.py": source})
        out = self.work / "out-redacted-route"
        result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out,
            analysis_version="1.1.0",
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis_bytes = (out / "static-analysis.json").read_text(encoding="utf-8")
        evidence_bytes = (out / "evidence.json").read_text(encoding="utf-8")
        combined = result.stdout + result.stderr + analysis_bytes + evidence_bytes
        self.assertNotIn("SECRET_PATH_PROVIDER", combined)
        self.assertNotIn("CLI_ROUTE_ARGUMENT_VALUE", combined)
        analysis = load_artifact(out / "static-analysis.json")
        self.assertTrue(any(
            item["kind"] == "CALL_CANDIDATE"
            and item["unresolved_target"] == "redacted-route-argument-call"
            for item in analysis["relations"]
        ))

    def test_same_line_duplicate_import_statements_publish_with_stable_distinct_ids(self):
        index, coverage, inputs = self._prepare({
            "src/service.py": b"import os; import os\n",
        })
        outputs = (self.work / "same-line-one", self.work / "same-line-two")

        results = [
            _run_python(
                self.root, index, coverage, inputs / "stack-profile.json",
                inputs / "evidence.json", output,
            )
            for output in outputs
        ]

        self.assertTrue(all(result.returncode == 0 for result in results), "\n".join(
            result.stdout + result.stderr for result in results
        ))
        artifacts = [load_artifact(output / "static-analysis.json") for output in outputs]
        import_id_sets = [
            [item["id"] for item in artifact["relations"] if item["kind"] == "IMPORTS"]
            for artifact in artifacts
        ]
        self.assertEqual(2, len(import_id_sets[0]))
        self.assertEqual(2, len(set(import_id_sets[0])))
        self.assertEqual(import_id_sets[0], import_id_sets[1])

    def test_git_tree_cli_reads_committed_source_not_changed_worktree(self):
        index, coverage, inputs = self._prepare(
            {"src/service.py": b"def committed_name():\n    pass\n"},
            snapshot="git-tree",
        )
        (self.root / "src" / "service.py").write_text(
            "def worktree_only_name():\n    pass\n", encoding="utf-8",
        )
        out = self.work / "out"

        result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        static_analysis = load_artifact(out / "static-analysis.json")
        names = {item["qualified_name"] for item in static_analysis["symbols"]}
        self.assertTrue(any(name.endswith("committed_name") for name in names))
        self.assertFalse(any(name.endswith("worktree_only_name") for name in names))
        self.assertEqual("git-tree", static_analysis["source_metadata"]["snapshot_kind"])

    def test_legacy_phase2_v10_pair_is_supported_through_3a_v11_provenance(self):
        index, coverage, inputs = self._prepare(
            {"src/service.py": b"class LegacyCompatible:\n    pass\n"},
            legacy=True,
        )
        self.assertEqual("1.0.0", load_artifact(index)["schema_version"])
        self.assertEqual("1.0.0", load_artifact(coverage)["schema_version"])

        result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json",
            self.work / "out",
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        evidence = load_artifact(self.work / "out" / "evidence.json")
        static_analysis = load_artifact(self.work / "out" / "static-analysis.json")
        self.assertEqual("1.1.0", profile["schema_version"])
        self.assertEqual("1.1.0", evidence["schema_version"])
        self.assertEqual("1.0.0", static_analysis["schema_version"])
        self.assertEqual(profile["source_metadata"], static_analysis["source_metadata"])

        extended_out = self.work / "out-v11"
        extended = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json",
            extended_out, analysis_version="1.1.0",
        )
        self.assertEqual(0, extended.returncode, extended.stdout + extended.stderr)
        self.assertEqual("1.1.0", load_artifact(extended_out / "static-analysis.json")["schema_version"])

    def test_all_skipped_python_sources_publish_partial_reasons_without_parser_text(self):
        index, coverage, inputs = self._prepare({
            "src/broken.py": b"def broken(: return 'PRIVATE_PARSE_CANARY'\n",
        })
        out = self.work / "out"

        result = _run_python(
            self.root, index, coverage, inputs / "stack-profile.json", inputs / "evidence.json", out,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_PARSE_CANARY", result.stdout + result.stderr)
        static_analysis = load_artifact(out / "static-analysis.json")
        self.assertEqual("PARTIAL", static_analysis["analysis_status"])
        self.assertEqual([], static_analysis["symbols"])
        self.assertEqual("SKIPPED", static_analysis["languages"][0]["files"][0]["status"])
        self.assertEqual("SYNTAX_ERROR", static_analysis["languages"][0]["files"][0]["limitations"][0]["code"])

    def test_forged_source_metadata_and_worktree_snapshot_mismatch_fail_closed(self):
        index, coverage, inputs = self._prepare({"src/service.py": b"def stable(): pass\n"})
        profile_path = inputs / "stack-profile.json"
        forged = load_artifact(profile_path)
        forged["source_metadata"]["project_index_sha256"] = "0" * 64
        profile_path.write_text(dumps_artifact(forged), encoding="utf-8")
        out = self.work / "out"
        out.mkdir()
        old = {
            "stack-profile.json": b"old stack profile",
            "evidence.json": b"old evidence",
            "static-analysis.json": b"old static analysis",
        }
        for name, payload in old.items():
            (out / name).write_bytes(payload)

        forged_result = _run_python(
            self.root, index, coverage, profile_path, inputs / "evidence.json", out,
        )
        self.assertEqual(1, forged_result.returncode, forged_result.stdout + forged_result.stderr)
        self.assertTrue(all((out / name).read_bytes() == payload for name, payload in old.items()))

        restored = load_artifact(profile_path)
        restored["source_metadata"]["project_index_sha256"] = load_artifact(inputs / "evidence.json")["source_metadata"]["project_index_sha256"]
        # Restore the profile to the digest computed from the unchanged Phase 2 inputs.
        from stack_detection import _source_metadata
        phase2_index, phase2_coverage = load_artifact(index), load_artifact(coverage)
        restored["source_metadata"] = _source_metadata(phase2_index, phase2_coverage, "PASS", 0)
        profile_path.write_text(dumps_artifact(restored), encoding="utf-8")
        (self.root / "src" / "service.py").write_text("def changed_after_scan(): pass\n", encoding="utf-8")

        changed_result = _run_python(
            self.root, index, coverage, profile_path, inputs / "evidence.json", out,
        )
        self.assertNotEqual(0, changed_result.returncode)
        self.assertTrue(all((out / name).read_bytes() == payload for name, payload in old.items()))

    def test_worktree_junction_escape_fails_g01_before_source_read_or_publish(self):
        _index, _coverage, inputs = self._prepare({"linked/service.py": b"def private(): pass\n"})
        outside = self.work / "outside"
        outside.mkdir()
        (outside / "service.py").write_text(
            "def PRIVATE_JUNCTION_CANARY(): pass\n", encoding="utf-8",
        )
        shutil.rmtree(self.root / "linked")
        create_junction_or_skip(self, self.root / "linked", outside)
        out = self.work / "out"

        result = _run_python(
            self.root, self.work / "project-index.json", self.work / "coverage.json",
            inputs / "stack-profile.json", inputs / "evidence.json", out,
        )

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_JUNCTION_CANARY", result.stdout + result.stderr)
        self.assertFalse((out / "static-analysis.json").exists())

    def test_second_g01_audit_failure_stops_before_publication(self):
        index, coverage, inputs = self._prepare({"src/service.py": b"def stable(): pass\n"})
        module = _cli_module(self)
        real_audit = module.audit_coverage
        first = real_audit(load_artifact(index), load_artifact(coverage), self.root)
        failed = replace(first, status="FAIL", violations=(
            AuditViolation("SNAPSHOT_CHANGED", "src/service.py", "snapshot changed before publication"),
        ))
        calls = 0

        def audit_twice(*args, **kwargs):
            nonlocal calls
            calls += 1
            return first if calls == 1 else failed

        out = self.work / "out"
        with patch.object(module, "audit_coverage", side_effect=audit_twice):
            result = module.main([
                "--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(inputs / "stack-profile.json"),
                "--evidence", str(inputs / "evidence.json"), "--out", str(out),
                "--analysis-version", "1.1.0",
            ])

        self.assertEqual(1, result)
        self.assertEqual(2, calls)
        self.assertFalse((out / "static-analysis.json").exists())

    def test_failure_on_third_replace_restores_all_three_previous_outputs(self):
        index, coverage, inputs = self._prepare({"src/service.py": b"def stable(): pass\n"})
        module = _cli_module(self)
        out = self.work / "out"
        out.mkdir()
        previous = {
            "stack-profile.json": b"previous stack profile",
            "evidence.json": b"previous evidence",
            "static-analysis.json": b"previous static analysis",
        }
        for name, payload in previous.items():
            (out / name).write_bytes(payload)

        real_replace = os.replace
        replacements = 0

        def fail_third_replace(source, target):
            nonlocal replacements
            source_path, target_path = Path(source), Path(target)
            if source_path.suffix == ".tmp" and target_path.name in previous:
                replacements += 1
                if replacements == 3:
                    raise OSError("injected third output replacement failure")
            return real_replace(source, target)

        with patch.object(scan_repository_cli.os, "replace", side_effect=fail_third_replace):
            result = module.main([
                "--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(inputs / "stack-profile.json"),
                "--evidence", str(inputs / "evidence.json"), "--out", str(out),
                "--analysis-version", "1.1.0",
            ])

        self.assertEqual(2, result)
        self.assertEqual(3, replacements)
        self.assertTrue(all((out / name).read_bytes() == payload for name, payload in previous.items()))


if __name__ == "__main__":
    unittest.main()
