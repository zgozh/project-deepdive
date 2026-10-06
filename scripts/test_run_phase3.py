#!/usr/bin/env python3
"""End-to-end tests for the Phase 3E2B integrated static runner."""

from __future__ import annotations

import copy
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
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402


FIXED_TIME = "2026-09-25T00:00:00Z"
RUNNER = SCRIPTS / "run_phase3.py"
FRONTEND_READY = bool(
    shutil.which("node")
    and (SCRIPTS / "frontend_parse_bridge" / "node_modules" / "typescript" / "package.json").is_file()
)
runner_module = importlib.import_module("run_phase3")


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"temporary fixture git {arguments[0]} failed")


def _fixture(testcase, sources: dict[str, bytes]):
    temporary = tempfile.TemporaryDirectory(prefix="pd-phase3e2b-")
    testcase.addCleanup(temporary.cleanup)
    work = Path(temporary.name)
    root = work / "repo"
    root.mkdir()
    for relative, payload in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Project DeepDive Test")
    _git(root, "config", "user.email", "deepdive@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")

    scan = scan_repository(ScanOptions(
        root=root,
        snapshot_kind="worktree",
        generated_at=FIXED_TIME,
    ))
    index_path = work / "project-index.json"
    coverage_path = work / "coverage.json"
    index_path.write_text(dumps_artifact(scan.project_index), encoding="utf-8")
    coverage_path.write_text(dumps_artifact(scan.coverage), encoding="utf-8")
    return {
        "work": work,
        "root": root,
        "project_index": scan.project_index,
        "coverage": scan.coverage,
        "index_path": index_path,
        "coverage_path": coverage_path,
    }


def _run(fixture, output: Path):
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--root", str(fixture["root"]),
            "--project-index", str(fixture["index_path"]),
            "--coverage", str(fixture["coverage_path"]),
            "--out", str(output),
        ],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _main_argv(fixture, output: Path) -> list[str]:
    return [
        "--root", str(fixture["root"]),
        "--project-index", str(fixture["index_path"]),
        "--coverage", str(fixture["coverage_path"]),
        "--out", str(output),
    ]


def _run_main(fixture, output: Path) -> tuple[int, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = runner_module.main(_main_argv(fixture, output))
    return result, stdout.getvalue() + stderr.getvalue()


class RunPhase3Tests(unittest.TestCase):
    def test_python_only_publishes_aligned_v11_without_running_or_echoing_source(self):
        fixture = _fixture(self, {"README.md": b"temporary fixture seed\n"})
        marker = fixture["work"] / "target-executed.txt"
        secret = "PHASE3E2B_PRIVATE_SOURCE_CANARY"
        source = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('target executed')\n"
            f"SECRET = {secret!r}\n"
            "def value():\n    return SECRET\n"
        )
        source_path = fixture["root"] / "src" / "service.py"
        source_path.parent.mkdir(parents=True)
        source_path.write_text(source, encoding="utf-8")
        _git(fixture["root"], "add", "--all")
        _git(fixture["root"], "commit", "--quiet", "-m", "add source")
        scan = scan_repository(ScanOptions(
            root=fixture["root"], snapshot_kind="worktree", generated_at=FIXED_TIME,
        ))
        fixture["project_index"] = scan.project_index
        fixture["coverage"] = scan.coverage
        fixture["index_path"].write_text(dumps_artifact(scan.project_index), encoding="utf-8")
        fixture["coverage_path"].write_text(dumps_artifact(scan.coverage), encoding="utf-8")
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(0, result.returncode, "integrated runner failed")
        manifest = load_artifact(output / "phase3-run.json")
        members = {row["path"]: row for row in manifest["members"]}
        self.assertEqual("PASS", manifest["status"])
        self.assertEqual("1.1.0", members["python/static-analysis.json"]["schema_version"])
        profile = load_artifact(output / "phase3a/stack-profile.json")
        analysis = load_artifact(output / "python/static-analysis.json")
        evidence = load_artifact(output / "python/evidence.json")
        self.assertEqual(FIXED_TIME, profile["generated_at"])
        self.assertEqual(FIXED_TIME, analysis["generated_at"])
        self.assertEqual(FIXED_TIME, evidence["generated_at"])
        self.assertFalse(marker.exists())
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(
            fixture["index_path"].read_bytes(),
            (output / "phase2/project-index.json").read_bytes(),
        )

    def test_no_applicable_source_families_remain_not_run(self):
        fixture = _fixture(self, {"README.md": b"documentation only\n"})
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(0, result.returncode, "integrated runner failed")
        self.assertIn("python:NOT_RUN,java:NOT_RUN,frontend:NOT_RUN", result.stdout)
        self.assertEqual("PASS", load_artifact(output / "phase3-run.json")["status"])

    def test_g01_partial_continues_and_remains_partial(self):
        fixture = _fixture(self, {
            "src/service.py": b"def value(): return 1\n",
            "mystery.oddity": b"unclassified fixture\n",
        })
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(0, result.returncode, "integrated runner failed")
        manifest = load_artifact(output / "phase3-run.json")
        self.assertEqual("PARTIAL", manifest["status"])
        self.assertEqual({"before": "PARTIAL", "after": "PARTIAL"}, manifest["e1_audit"]["g01"])
        self.assertIn("G01_PARTIAL", manifest["e1_audit"]["limitations"])

    def test_g01_revision_mismatch_stops_before_publication(self):
        fixture = _fixture(self, {"src/service.py": b"def value(): return 1\n"})
        bad_coverage = copy.deepcopy(fixture["coverage"])
        bad_coverage["repository_revision"] = "0" * 40
        fixture["coverage_path"].write_text(dumps_artifact(bad_coverage), encoding="utf-8")
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(1, result.returncode)
        self.assertIn("G01_FAILED", result.stdout)
        self.assertFalse(output.exists())

    def test_phase2_pair_bytes_are_pinned_for_all_adapter_calls(self):
        fixture = _fixture(self, {"src/service.py": b"def value(): return 1\n"})
        original_index = fixture["index_path"].read_bytes()
        original_coverage = fixture["coverage_path"].read_bytes()
        output = fixture["work"] / "phase3-run"
        replacement_index = copy.deepcopy(fixture["project_index"])
        replacement_coverage = copy.deepcopy(fixture["coverage"])
        replacement_time = "2026-09-25T01:00:00Z"
        replacement_index["generated_at"] = replacement_time
        replacement_coverage["generated_at"] = replacement_time
        real_run = subprocess.run
        replaced = False

        def replace_pair_before_phase3a(command, *args, **kwargs):
            nonlocal replaced
            if Path(command[1]).name == "detect_stack.py" and not replaced:
                fixture["index_path"].write_text(dumps_artifact(replacement_index), encoding="utf-8")
                fixture["coverage_path"].write_text(dumps_artifact(replacement_coverage), encoding="utf-8")
                replaced = True
            return real_run(command, *args, **kwargs)

        with patch.object(runner_module.subprocess, "run", side_effect=replace_pair_before_phase3a):
            status, _output_text = _run_main(fixture, output)

        self.assertEqual(0, status)
        self.assertNotEqual(original_index, fixture["index_path"].read_bytes())
        self.assertNotEqual(original_coverage, fixture["coverage_path"].read_bytes())
        self.assertEqual(original_index, (output / "phase2/project-index.json").read_bytes())
        self.assertEqual(original_coverage, (output / "phase2/coverage.json").read_bytes())

    def test_worktree_drift_during_phase3a_stops_without_echo_or_output(self):
        fixture = _fixture(self, {"src/service.py": b"def value(): return 1\n"})
        source_path = fixture["root"] / "src" / "service.py"
        output = fixture["work"] / "phase3-run"
        real_run = subprocess.run
        changed = False

        def mutate_before_phase3a(command, *args, **kwargs):
            nonlocal changed
            if Path(command[1]).name == "detect_stack.py" and not changed:
                source_path.write_text("def changed(): return 2\n", encoding="utf-8")
                changed = True
            return real_run(command, *args, **kwargs)

        with patch.object(runner_module.subprocess, "run", side_effect=mutate_before_phase3a):
            status, output_text = _run_main(fixture, output)

        self.assertEqual(1, status)
        self.assertIn("ADAPTER_FAILED_PHASE3A", output_text)
        self.assertFalse(output.exists())
        self.assertEqual([], list(fixture["work"].glob(".phase3-integrated-staging-*")))

    def test_applicable_adapter_failure_is_redacted_and_cleans_staging(self):
        fixture = _fixture(self, {"src/service.py": b"def value(): return 1\n"})
        output = fixture["work"] / "phase3-run"
        real_run = subprocess.run
        secret = "PRIVATE_ADAPTER_DIAGNOSTIC_CANARY"

        def fail_python(command, *args, **kwargs):
            if Path(command[1]).name == "analyze_python.py":
                return subprocess.CompletedProcess(command, 2, stdout=secret, stderr=secret)
            return real_run(command, *args, **kwargs)

        with patch.object(runner_module.subprocess, "run", side_effect=fail_python):
            status, output_text = _run_main(fixture, output)

        self.assertEqual(1, status)
        self.assertIn("ADAPTER_FAILED_PYTHON", output_text)
        self.assertNotIn(secret, output_text)
        self.assertFalse(output.exists())
        self.assertEqual([], list(fixture["work"].glob(".phase3-integrated-staging-*")))

    def test_existing_output_is_preserved(self):
        fixture = _fixture(self, {"src/service.py": b"def value(): return 1\n"})
        output = fixture["work"] / "phase3-run"
        output.mkdir()
        sentinel = output / "caller-data.txt"
        sentinel.write_text("keep", encoding="utf-8")

        status, _output_text = _run_main(fixture, output)

        self.assertEqual(1, status)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([], list(fixture["work"].glob(".phase3-integrated-staging-*")))

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_java_only_keeps_c1_and_c2_versions_separate(self):
        fixture = _fixture(self, {
            "src/Service.java": b"class Service { String value() { return \"ok\"; } }\n",
        })
        output = fixture["work"] / "phase3-run"
        real_assemble = runner_module.phase3_run.assemble_phase3_run

        def verify_pair_then_assemble(**kwargs):
            inputs = kwargs["input_paths"]
            v12_analysis = Path(inputs["java_analysis_v12"]).read_bytes()
            v12_evidence = Path(inputs["java_evidence_v12"]).read_bytes()
            c2_dir = Path(inputs["java_analysis_v13"]).parent
            self.assertEqual(v12_analysis, (c2_dir / "static-analysis-v1.2.json").read_bytes())
            self.assertEqual(v12_evidence, (c2_dir / "evidence-v1.2.json").read_bytes())
            return real_assemble(**kwargs)

        with patch.object(runner_module.phase3_run, "assemble_phase3_run", side_effect=verify_pair_then_assemble):
            status, _output_text = _run_main(fixture, output)

        self.assertEqual(0, status)
        members = {row["path"]: row for row in load_artifact(output / "phase3-run.json")["members"]}
        self.assertEqual("1.2.0", members["java-v12/static-analysis.json"]["schema_version"])
        self.assertEqual("1.3.0", members["java-v13/static-analysis.json"]["schema_version"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_java_c2_retained_pair_mismatch_stops_before_assembly(self):
        fixture = _fixture(self, {"src/Service.java": b"class Service {}\n"})
        output = fixture["work"] / "phase3-run"
        real_run = subprocess.run

        def corrupt_retained_pair(command, *args, **kwargs):
            result = real_run(command, *args, **kwargs)
            if Path(command[1]).name == "analyze_java_frameworks.py" and result.returncode == 0:
                c2_dir = Path(command[command.index("--out") + 1])
                retained = c2_dir / "static-analysis-v1.2.json"
                retained.write_bytes(retained.read_bytes() + b" ")
            return result

        with patch.object(runner_module.subprocess, "run", side_effect=corrupt_retained_pair):
            status, output_text = _run_main(fixture, output)

        self.assertEqual(1, status)
        self.assertIn("JAVA_C1_C2_PAIR_MISMATCH", output_text)
        self.assertFalse(output.exists())

    @unittest.skipUnless(FRONTEND_READY, "Node.js or tool-local TypeScript parser is unavailable")
    def test_frontend_only_packages_v15(self):
        fixture = _fixture(self, {
            "frontend/src/Page.tsx": b"export function Page() { return null; }\n",
        })
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(0, result.returncode, "integrated runner failed")
        analysis = load_artifact(output / "frontend/static-analysis.json")
        members = {row["path"]: row for row in load_artifact(output / "phase3-run.json")["members"]}
        self.assertEqual("1.5.0", analysis["schema_version"])
        self.assertEqual("1.5.0", members["frontend/static-analysis.json"]["schema_version"])

    @unittest.skipUnless(
        shutil.which("java") and shutil.which("javac") and FRONTEND_READY,
        "JDK or tool-local TypeScript parser is unavailable",
    )
    def test_mixed_families_publish_from_one_phase2_pair(self):
        fixture = _fixture(self, {
            "src/service.py": b"def value(): return 1\n",
            "src/Service.java": b"class Service {}\n",
            "frontend/src/Page.tsx": b"export function Page() { return null; }\n",
        })
        output = fixture["work"] / "phase3-run"

        result = _run(fixture, output)

        self.assertEqual(0, result.returncode, "integrated runner failed")
        packaged_index = output / "phase2/project-index.json"
        self.assertEqual(fixture["index_path"].read_bytes(), packaged_index.read_bytes())
        self.assertEqual("1.1.0", load_artifact(output / "python/static-analysis.json")["schema_version"])
        self.assertEqual("1.2.0", load_artifact(output / "java-v12/static-analysis.json")["schema_version"])
        self.assertEqual("1.3.0", load_artifact(output / "java-v13/static-analysis.json")["schema_version"])
        self.assertEqual("1.5.0", load_artifact(output / "frontend/static-analysis.json")["schema_version"])


if __name__ == "__main__":
    unittest.main()
