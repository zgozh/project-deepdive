#!/usr/bin/env python3
"""Same-snapshot integration tests for the Phase 3C1 Java CLI."""

from __future__ import annotations

import copy
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402
from test_repository_scan import init_git_repo  # noqa: E402
import scan_repository as scan_repository_cli  # noqa: E402


FIXED_TIME = "2026-09-23T00:00:00Z"
CLI = SKILL_ROOT / "scripts" / "analyze_java.py"


def _prepare(work: Path, root: Path, sources: dict[str, bytes], *, snapshot="worktree", legacy=False):
    init_git_repo(root, sources)
    scanned = scan_repository(ScanOptions(
        root=root,
        snapshot_kind=snapshot,
        generated_at=FIXED_TIME,
    ))
    index = copy.deepcopy(scanned.project_index)
    coverage = copy.deepcopy(scanned.coverage)
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
    inputs = work / "inputs"
    inputs.mkdir()
    index_path = inputs / "project-index.json"
    coverage_path = inputs / "coverage.json"
    index_path.write_text(dumps_artifact(index), encoding="utf-8")
    coverage_path.write_text(dumps_artifact(coverage), encoding="utf-8")
    unknown_files = coverage["unknown_count"]
    status = "PARTIAL" if unknown_files else "PASS"
    profile, evidence = build_stack_artifacts(
        index,
        coverage,
        lambda path, _entry: (root / path).read_bytes(),
        generated_at=FIXED_TIME,
        regular_source_paths=frozenset(sources),
        g01_status=status,
        unknown_files=unknown_files,
    )
    profile_path = inputs / "stack-profile.json"
    evidence_path = inputs / "evidence.json"
    profile_path.write_text(dumps_artifact(profile), encoding="utf-8")
    evidence_path.write_text(dumps_artifact(evidence), encoding="utf-8")
    return index_path, coverage_path, profile_path, evidence_path


def _run(root, index, coverage, profile, evidence, out):
    return subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), "--index", str(index),
         "--coverage", str(coverage), "--stack-profile", str(profile),
         "--evidence", str(evidence), "--out", str(out)],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


@unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
class AnalyzeJavaCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "repo"

    def test_worktree_cli_publishes_v12_deterministically_and_preserves_phase3a_inputs(self):
        source = (
            "package cli.fixture;\n"
            "import java.util.List;\n"
            "public class Entry implements Runnable {\n"
            "  public Entry() {}\n"
            "  public Entry(int size) {}\n"
            "  public void run() { String privateBody = \"PRIVATE_JAVA_CLI_CANARY\"; }\n"
            "}\n"
        ).encode("utf-8")
        index, coverage, profile, evidence = _prepare(
            self.work, self.root, {"src/Entry.java": source},
        )
        original_profile_bytes = profile.read_bytes()
        original_evidence = load_artifact(evidence)
        out_one, out_two = self.work / "out-one", self.work / "out-two"

        first = _run(self.root, index, coverage, profile, evidence, out_one)
        second = _run(self.root, index, coverage, profile, evidence, out_two)

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(original_profile_bytes, profile.read_bytes())
        for name in ("stack-profile.json", "evidence.json", "static-analysis.json"):
            self.assertEqual((out_one / name).read_bytes(), (out_two / name).read_bytes())
        self.assertEqual(original_profile_bytes, (out_one / "stack-profile.json").read_bytes())
        profile_doc = load_artifact(out_one / "stack-profile.json")
        merged = load_artifact(out_one / "evidence.json")
        analysis = load_artifact(out_one / "static-analysis.json")
        self.assertEqual("1.2.0", analysis["schema_version"])
        self.assertEqual(profile_doc["repository_revision"], analysis["repository_revision"])
        self.assertEqual(profile_doc["generated_at"], analysis["generated_at"])
        self.assertEqual(profile_doc["source_metadata"], analysis["source_metadata"])
        self.assertEqual(profile_doc["source_metadata"], merged["source_metadata"])
        merged_by_id = {item["id"]: item for item in merged["items"]}
        self.assertEqual(
            {item["id"]: item for item in original_evidence["items"]},
            {item["id"]: merged_by_id[item["id"]] for item in original_evidence["items"]},
        )
        combined = "".join(path.read_text(encoding="utf-8") for path in out_one.glob("*.json"))
        self.assertNotIn("PRIVATE_JAVA_CLI_CANARY", combined)
        self.assertNotIn("PRIVATE_JAVA_CLI_CANARY", first.stdout + first.stderr)

    def test_git_tree_cli_reads_committed_source_after_worktree_changes(self):
        index, coverage, profile, evidence = _prepare(
            self.work, self.root, {"src/Entry.java": b"class CommittedJavaName {}\n"},
            snapshot="git-tree",
        )
        (self.root / "src" / "Entry.java").write_text(
            "class WorktreeOnlyJavaName {}\n", encoding="utf-8",
        )
        result = _run(self.root, index, coverage, profile, evidence, self.work / "out")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis = load_artifact(self.work / "out" / "static-analysis.json")
        names = {item["name"] for item in analysis["symbols"]}
        self.assertIn("CommittedJavaName", names)
        self.assertNotIn("WorktreeOnlyJavaName", names)
        self.assertEqual("git-tree", analysis["source_metadata"]["snapshot_kind"])

    def test_legacy_phase2_v10_inputs_remain_supported(self):
        index, coverage, profile, evidence = _prepare(
            self.work, self.root, {"src/Legacy.java": b"class LegacyJava {}\n"}, legacy=True,
        )
        self.assertEqual("1.0.0", load_artifact(index)["schema_version"])
        self.assertEqual("1.0.0", load_artifact(coverage)["schema_version"])

        result = _run(self.root, index, coverage, profile, evidence, self.work / "out")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("1.2.0", load_artifact(self.work / "out" / "static-analysis.json")["schema_version"])

    def test_changed_worktree_snapshot_fails_before_touching_existing_outputs(self):
        index, coverage, profile, evidence = _prepare(
            self.work, self.root, {"src/Entry.java": b"class Original {}\n"},
        )
        (self.root / "src" / "Entry.java").write_text("class Changed {}\n", encoding="utf-8")
        out = self.work / "out"
        out.mkdir()
        old = {name: ("old " + name).encode() for name in (
            "stack-profile.json", "evidence.json", "static-analysis.json",
        )}
        for name, payload in old.items():
            (out / name).write_bytes(payload)

        result = _run(self.root, index, coverage, profile, evidence, out)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(old, {name: (out / name).read_bytes() for name in old})

    def test_output_cannot_overwrite_a_tracked_source_path(self):
        index, coverage, profile, evidence = _prepare(
            self.work, self.root,
            {"src/Entry.java": b"class Entry {}\n", "stack-profile.json": b"tracked file\n"},
        )

        result = _run(self.root, index, coverage, profile, evidence, self.root)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("overwrite a tracked repository file", result.stderr)
        self.assertFalse((self.root / "static-analysis.json").exists())

    def test_publication_failure_rolls_back_existing_outputs(self):
        index, coverage, profile, evidence = _prepare(
            self.work, self.root, {"src/Entry.java": b"class Entry {}\n"},
        )
        out = self.work / "out"
        out.mkdir()
        old = {name: ("previous " + name).encode() for name in (
            "stack-profile.json", "evidence.json", "static-analysis.json",
        )}
        for name, payload in old.items():
            (out / name).write_bytes(payload)
        module = importlib.import_module("analyze_java")
        arguments = [
            "--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
            "--stack-profile", str(profile), "--evidence", str(evidence), "--out", str(out),
        ]
        original_replace = os.replace
        replacements = 0

        def fail_second_replace(source, target):
            nonlocal replacements
            replacements += 1
            if replacements == 2:
                raise OSError("injected replacement failure")
            return original_replace(source, target)

        standard_out, standard_error = io.StringIO(), io.StringIO()
        with patch.object(scan_repository_cli.os, "replace", side_effect=fail_second_replace):
            with redirect_stdout(standard_out), redirect_stderr(standard_error):
                result = module.main(arguments)

        self.assertEqual(2, result, standard_out.getvalue() + standard_error.getvalue())
        self.assertEqual(old, {name: (out / name).read_bytes() for name in old})

    def test_bridge_output_limit_stops_stdout_stderr_and_compile_output_before_publish(self):
        java_analyzer = importlib.import_module("java_static_analysis")
        analyze_module = importlib.import_module("analyze_java")
        original_popen = subprocess.Popen
        writer = (
            "import os, sys\n"
            "fd = int(sys.argv[2])\n"
            "chunk = b'x' * 4096\n"
            "for _ in range(2048):\n"
            "    os.write(fd, chunk)\n"
            "with open(sys.argv[1], 'w', encoding='utf-8') as marker:\n"
            "    marker.write('writer completed')\n"
        )

        for phase, channel in (("parse", 1), ("parse", 2), ("compile", 2)):
            with self.subTest(phase=phase, channel=channel):
                case_work = self.work / f"case-{phase}-{channel}"
                case_work.mkdir()
                case_root = case_work / "repo"
                self.root = case_root
                index, coverage, profile, evidence = _prepare(
                    case_work, self.root, {"src/Entry.java": b"class Entry {}\n"},
                )
                marker = case_work / f"{phase}-{channel}-completed.marker"
                out = case_work / f"out-{phase}-{channel}"
                arguments = [
                    "--root", str(self.root), "--index", str(index),
                    "--coverage", str(coverage), "--stack-profile", str(profile),
                    "--evidence", str(evidence), "--out", str(out),
                ]

                def replace_target_process(command, *args, **kwargs):
                    parts = [str(item) for item in command]
                    is_compile = any(part.endswith("DeepDiveJavaParser.java") for part in parts)
                    is_parse = "DeepDiveJavaParser" in parts
                    should_replace = (phase == "compile" and is_compile) or (phase == "parse" and is_parse)
                    if should_replace:
                        command = [
                            sys.executable, "-c", writer, str(marker), str(channel),
                        ]
                    return original_popen(command, *args, **kwargs)

                standard_out, standard_error = io.StringIO(), io.StringIO()
                with (
                    patch.object(java_analyzer, "MAX_PROTOCOL_BYTES", 256),
                    patch.object(java_analyzer, "MAX_BRIDGE_COMPILE_OUTPUT_BYTES", 256, create=True),
                    patch.object(subprocess, "Popen", side_effect=replace_target_process),
                    redirect_stdout(standard_out),
                    redirect_stderr(standard_error),
                ):
                    result = analyze_module.main(arguments)

                self.assertEqual(1, result, standard_out.getvalue() + standard_error.getvalue())
                self.assertFalse(marker.exists(), "the writer must be stopped before its completion side effect")
                self.assertFalse(out.exists(), "an output-limit failure must not publish a partial bundle")
