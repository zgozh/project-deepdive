#!/usr/bin/env python3
"""Same-snapshot CLI tests for Phase 3C2 Java framework candidates."""

from __future__ import annotations

import copy
import hashlib
import importlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from test_analyze_java_cli import _prepare, _run as _run_c1  # noqa: E402


CLI = SKILL_ROOT / "scripts" / "analyze_java_frameworks.py"


def _run_c2(root, index, coverage, profile, phase3a_evidence,
            java_analysis, java_evidence, out):
    return subprocess.run(
        [sys.executable, str(CLI), "--root", str(root),
         "--index", str(index), "--coverage", str(coverage),
         "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
         "--java-analysis-v1.2", str(java_analysis),
         "--java-evidence-v1.2", str(java_evidence), "--out", str(out)],
        cwd=SKILL_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def _framework_cli(testcase):
    try:
        return importlib.import_module("analyze_java_frameworks")
    except ImportError as exc:
        testcase.fail(f"Phase 3C2 CLI is not implemented: {exc}")


@unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
class AnalyzeJavaFrameworkCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pd-c2-cli-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "repo"

    def _prepare_complete_c1(self, source):
        index, coverage, profile, phase3a_evidence = _prepare(
            self.work, self.root, {"src/Api.java": source},
        )
        c1_out = self.work / "c1-out"
        result = _run_c1(self.root, index, coverage, profile, phase3a_evidence, c1_out)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return index, coverage, profile, phase3a_evidence, c1_out

    def _args(self, index, coverage, profile, phase3a_evidence, c1_out, out):
        return (self.root, index, coverage, profile, phase3a_evidence,
                c1_out / "static-analysis.json", c1_out / "evidence.json", out)

    def test_cli_publishes_five_matching_artifacts_from_worktree_snapshot(self):
        marker = self.work / "target-was-executed.txt"
        marker_literal = marker.as_posix().replace("\\", "\\\\").replace('"', '\\"')
        source = f'''package sample;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import jakarta.persistence.Entity;
import org.junit.jupiter.api.Test;
@RestController class Api {{ static {{ try {{ java.nio.file.Files.createFile(java.nio.file.Path.of("{marker_literal}")); }} catch (java.io.IOException ignored) {{}} }} @GetMapping(value = SECRET_PATH_PROVIDER()) String list() {{ return "SECRET_VALUE"; }} }}
@Entity class Model {{}}
class ApiTest {{ @Test void lists() {{}} }}
'''.encode("utf-8")
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        first_out, second_out = self.work / "c2-one", self.work / "c2-two"
        first = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, first_out))
        second = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, second_out))

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        names = {
            "stack-profile.json", "static-analysis-v1.2.json", "evidence-v1.2.json",
            "static-analysis.json", "evidence.json",
        }
        self.assertEqual(names, {path.name for path in first_out.iterdir()})
        for name in names:
            self.assertEqual((first_out / name).read_bytes(), (second_out / name).read_bytes())
        self.assertEqual(profile.read_bytes(), (first_out / "stack-profile.json").read_bytes())
        self.assertEqual((c1_out / "static-analysis.json").read_bytes(),
                         (first_out / "static-analysis-v1.2.json").read_bytes())
        self.assertEqual((c1_out / "evidence.json").read_bytes(),
                         (first_out / "evidence-v1.2.json").read_bytes())
        analysis = load_artifact(first_out / "static-analysis.json")
        evidence = load_artifact(first_out / "evidence.json")
        c1_analysis = load_artifact(c1_out / "static-analysis.json")
        c1_evidence = load_artifact(c1_out / "evidence.json")
        self.assertEqual("1.3.0", analysis["schema_version"])
        validate_artifact(analysis)
        validate_artifact(evidence)
        self.assertEqual(c1_analysis["symbols"], analysis["symbols"])
        self.assertEqual(c1_analysis["relations"], analysis["relations"][:len(c1_analysis["relations"])])
        final_evidence_by_id = {item["id"]: item for item in evidence["items"]}
        for item in c1_evidence["items"]:
            self.assertEqual(item, final_evidence_by_id[item["id"]])
        self.assertFalse(marker.exists())
        self.assertEqual(
            hashlib.sha256(dumps_artifact(load_artifact(profile)).encode("utf-8")).hexdigest(),
            analysis["stack_profile_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(dumps_artifact(load_artifact(phase3a_evidence)).encode("utf-8")).hexdigest(),
            analysis["phase3a_evidence_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(dumps_artifact(load_artifact(first_out / "static-analysis-v1.2.json")).encode("utf-8")).hexdigest(),
            analysis["java_analysis_v12_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(dumps_artifact(load_artifact(first_out / "evidence-v1.2.json")).encode("utf-8")).hexdigest(),
            analysis["java_evidence_v12_sha256"],
        )
        combined = b"".join((first_out / name).read_bytes() for name in names) + first.stdout.encode() + first.stderr.encode()
        for canary in (b"SECRET_PATH_PROVIDER", b"SECRET_VALUE"):
            self.assertNotIn(canary, combined)

    def test_cli_rejects_forged_v12_pair_before_publish(self):
        source = b"package sample; class Api extends RealBase {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        analysis_path = c1_out / "static-analysis.json"
        evidence_path = c1_out / "evidence.json"
        analysis = load_artifact(analysis_path)
        evidence = load_artifact(evidence_path)
        relation = next(row for row in analysis["relations"] if row["kind"] == "EXTENDS")
        relation["unresolved_target"] = "java-type:ForgedPriorBase"
        citation = next(row for row in evidence["items"] if row["id"] == relation["evidence_ids"][0])
        citation["locator"]["symbol"] = relation["unresolved_target"]
        analysis_path.write_text(dumps_artifact(analysis), encoding="utf-8")
        evidence_path.write_text(dumps_artifact(evidence), encoding="utf-8")
        output = self.work / "forged-output"

        result = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, output))

        self.assertNotEqual(0, result.returncode)
        self.assertIn("C1_REEXTRACTION_MISMATCH", result.stdout + result.stderr)
        self.assertFalse(output.exists())

    def test_cli_refuses_output_alias_with_c1_input(self):
        source = b"import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        before = {path.name: path.read_bytes() for path in c1_out.iterdir()}

        result = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, c1_out))

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, {path.name: path.read_bytes() for path in c1_out.iterdir()})

    def test_cli_uses_committed_git_tree_after_worktree_edit(self):
        committed_source = b"import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence = _prepare(
            self.work, self.root, {"src/Api.java": committed_source}, snapshot="git-tree",
        )
        c1_out = self.work / "c1-git-tree"
        (self.root / "src/Api.java").write_bytes(
            b"class WorktreeOnly { String marker = \"SECRET_WORKTREE\"; }\n",
        )
        c1_result = _run_c1(self.root, index, coverage, profile, phase3a_evidence, c1_out)
        self.assertEqual(0, c1_result.returncode, c1_result.stdout + c1_result.stderr)
        output = self.work / "c2-git-tree"

        result = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, output))

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        final_analysis = load_artifact(output / "static-analysis.json")
        self.assertEqual("git-tree", final_analysis["source_metadata"]["snapshot_kind"])
        self.assertEqual(["data_model_candidate"], [row["kind"] for row in final_analysis["roles"]])
        for name in ("stack-profile.json", "static-analysis-v1.2.json", "evidence-v1.2.json",
                     "static-analysis.json", "evidence.json"):
            self.assertNotIn(b"SECRET_WORKTREE", (output / name).read_bytes())

    def test_cli_preserves_existing_five_file_bundle_when_publication_fails(self):
        import os
        import scan_repository

        source = b"import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        output = self.work / "existing-output"
        output.mkdir()
        names = {
            "stack-profile.json", "static-analysis-v1.2.json", "evidence-v1.2.json",
            "static-analysis.json", "evidence.json",
        }
        original = {name: f"sentinel:{name}".encode("ascii") for name in names}
        for name, payload in original.items():
            (output / name).write_bytes(payload)
        framework_cli = _framework_cli(self)
        args = ["--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
                "--java-analysis-v1.2", str(c1_out / "static-analysis.json"),
                "--java-evidence-v1.2", str(c1_out / "evidence.json"), "--out", str(output)]
        real_replace = os.replace
        failed = {"done": False}

        def fail_second_replace(source_path, destination_path):
            if Path(destination_path).name == "static-analysis-v1.2.json" and not failed["done"]:
                failed["done"] = True
                raise OSError("injected replacement failure")
            return real_replace(source_path, destination_path)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("scan_repository.os.replace", side_effect=fail_second_replace), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = framework_cli.main(args)

        self.assertNotEqual(0, exit_code)
        self.assertTrue(failed["done"])
        self.assertEqual(names, {path.name for path in output.iterdir()})
        self.assertEqual(original, {name: (output / name).read_bytes() for name in names})

    def test_cli_snapshot_drift_and_phase3a_digest_mismatch_do_not_publish(self):
        import analyze_java_frameworks as framework_cli_module

        source = b"import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        drift_out = self.work / "drift-output"
        args = ["--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
                "--java-analysis-v1.2", str(c1_out / "static-analysis.json"),
                "--java-evidence-v1.2", str(c1_out / "evidence.json"), "--out", str(drift_out)]
        real_audit = framework_cli_module.audit_coverage
        audit_calls = {"count": 0}

        def drift_before_final_audit(project_index, coverage_doc, root):
            audit_calls["count"] += 1
            if audit_calls["count"] == 2:
                (Path(root) / "src/Api.java").write_bytes(b"class ChangedAfterAudit {}\n")
            return real_audit(project_index, coverage_doc, root)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(framework_cli_module, "audit_coverage", side_effect=drift_before_final_audit), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            drift_exit = framework_cli_module.main(args)
        self.assertEqual(2, audit_calls["count"])
        self.assertNotEqual(0, drift_exit)
        self.assertFalse(drift_out.exists())

        # Recreate a fresh locked fixture for the Phase 3A digest mismatch case.
        second_work = self.work / "second"
        second_work.mkdir()
        self.work = second_work
        self.root = second_work / "repo"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        profile_doc = load_artifact(profile)
        profile_doc["source_metadata"]["coverage_sha256"] = "0" * 64
        profile.write_text(dumps_artifact(profile_doc), encoding="utf-8")
        metadata_out = self.work / "metadata-output"
        metadata = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, metadata_out))
        self.assertNotEqual(0, metadata.returncode)
        self.assertFalse(metadata_out.exists())

    def test_cli_preserves_partial_skip_reason_and_analyzes_other_eligible_files(self):
        sources = {
            "src/Api.java": b"import jakarta.persistence.Entity; @Entity class Api {}\n",
            "src/Broken.java": b"class Broken { void bad( { String SECRET_SKIP = \"secret\"; }\n",
        }
        index, coverage, profile, phase3a_evidence = _prepare(self.work, self.root, sources)
        c1_out = self.work / "c1-partial"
        c1 = _run_c1(self.root, index, coverage, profile, phase3a_evidence, c1_out)
        self.assertEqual(0, c1.returncode, c1.stdout + c1.stderr)
        output = self.work / "c2-partial"

        result = _run_c2(*self._args(index, coverage, profile, phase3a_evidence, c1_out, output))

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis = load_artifact(output / "static-analysis.json")
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        records = {row["path"]: row for row in analysis["languages"][0]["files"]}
        self.assertEqual("SKIPPED", records["src/Broken.java"]["status"])
        self.assertEqual("UNSUPPORTED_SYNTAX", records["src/Broken.java"]["limitations"][0]["code"])
        self.assertEqual(["data_model_candidate"], [row["kind"] for row in analysis["roles"]])
        for name in ("static-analysis.json", "evidence.json"):
            self.assertNotIn(b"SECRET_SKIP", (output / name).read_bytes())

    def test_pdj2_output_cap_stops_child_during_drain_and_publishes_nothing(self):
        import java_framework_static_analysis as framework
        import os

        source = b"import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        output = self.work / "protocol-overflow"
        framework_cli = _framework_cli(self)
        args = ["--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
                "--java-analysis-v1.2", str(c1_out / "static-analysis.json"),
                "--java-evidence-v1.2", str(c1_out / "evidence.json"), "--out", str(output)]
        real_drain = framework._run_bounded_process
        marker = self.work / "child-after-output.txt"
        child = (
            "import pathlib,sys,time; sys.stdout.buffer.write(b'x'*8192); sys.stdout.flush(); "
            f"time.sleep(2); pathlib.Path({str(marker)!r}).write_text('finished')"
        )
        capped_operations = []

        def capped_drain(command, **kwargs):
            if kwargs.get("operation") == "JDK Java framework parser":
                capped_operations.append(kwargs["operation"])
                command = [sys.executable, "-c", child]
                kwargs["max_output_bytes"] = 512
            return real_drain(command, **kwargs)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(framework, "_run_bounded_process", side_effect=capped_drain), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = framework_cli.main(args)

        self.assertEqual(["JDK Java framework parser"], capped_operations)
        self.assertNotEqual(0, exit_code)
        self.assertIn("output limit", stdout.getvalue() + stderr.getvalue())
        self.assertFalse(output.exists())
        self.assertFalse(marker.exists())

    def test_missing_jdk_rejects_supplied_complete_c1_without_publication(self):
        source = b"package sample; class Api extends RealBase {}\n"
        index, coverage, profile, phase3a_evidence, c1_out = self._prepare_complete_c1(source)
        framework_cli = _framework_cli(self)
        output = self.work / "no-jdk-complete"
        args = ["--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
                "--java-analysis-v1.2", str(c1_out / "static-analysis.json"),
                "--java-evidence-v1.2", str(c1_out / "evidence.json"), "--out", str(output)]
        stdout, stderr = io.StringIO(), io.StringIO()

        with patch("shutil.which", return_value=None), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = framework_cli.main(args)

        self.assertNotEqual(0, exit_code)
        self.assertIn("C1_REEXTRACTION_MISMATCH", stdout.getvalue() + stderr.getvalue())
        self.assertFalse(output.exists())

    def test_missing_jdk_publishes_matching_partial_c1_without_candidates(self):
        source = b"package sample; import jakarta.persistence.Entity; @Entity class Api {}\n"
        index, coverage, profile, phase3a_evidence = _prepare(
            self.work, self.root, {"src/Api.java": source},
        )
        java_analyzer = importlib.import_module("java_static_analysis")
        index_doc = load_artifact(index)
        coverage_doc = load_artifact(coverage)
        profile_doc = load_artifact(profile)
        phase3a_doc = load_artifact(phase3a_evidence)
        from detect_stack import _snapshot_reader
        from repository_scan import discover_git_context, enumerate_git_snapshot_entries
        read_source, regular_paths = _snapshot_reader(
            self.root, discover_git_context(self.root), "worktree", index_doc,
        )
        regular_paths = frozenset(
            item.path for item in enumerate_git_snapshot_entries(discover_git_context(self.root), "worktree")
            if item.mode in {"100644", "100755"}
        )
        c1_out = self.work / "c1-partial"
        c1_out.mkdir()

        with patch("shutil.which", return_value=None):
            c1_analysis, c1_evidence = java_analyzer.analyze_java_artifacts(
                index_doc, coverage_doc, profile_doc, phase3a_doc, read_source, regular_paths,
                g01_status="PASS", unknown_files=0,
            )

        c1_analysis_path = c1_out / "static-analysis.json"
        c1_evidence_path = c1_out / "evidence.json"
        c1_analysis_path.write_text(dumps_artifact(c1_analysis), encoding="utf-8")
        c1_evidence_path.write_text(dumps_artifact(c1_evidence), encoding="utf-8")
        framework_cli = _framework_cli(self)
        output = self.work / "no-jdk-partial"
        args = ["--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--stack-profile", str(profile), "--phase3a-evidence", str(phase3a_evidence),
                "--java-analysis-v1.2", str(c1_analysis_path),
                "--java-evidence-v1.2", str(c1_evidence_path), "--out", str(output)]
        stdout, stderr = io.StringIO(), io.StringIO()

        with patch("shutil.which", return_value=None), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = framework_cli.main(args)

        self.assertEqual(0, exit_code, stdout.getvalue() + stderr.getvalue())
        final_analysis = load_artifact(output / "static-analysis.json")
        self.assertEqual("PARTIAL", final_analysis["analysis_status"])
        self.assertEqual([], final_analysis["roles"])
        self.assertEqual([], [row for row in final_analysis["relations"] if row["kind"] == "ROUTE_TO"])
