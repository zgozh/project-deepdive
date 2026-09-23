#!/usr/bin/env python3
"""Tests for the thin Phase 2 scan and audit command-line interfaces."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_repository_scan import (  # noqa: E402
    build_fixture_repository,
    commit_all,
    init_git_repo,
)

SCAN_CLI = SKILL_ROOT / "scripts" / "scan_repository.py"
AUDIT_CLI = SKILL_ROOT / "scripts" / "validate_coverage.py"
FIXED_TIME = "2026-09-22T00:00:00Z"
REPOSITORY_FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "repository_scanner"
FRONTEND_OVERRIDES = REPOSITORY_FIXTURE_ROOT / "overrides" / "frontend.json"
CLEAN_FILES = {
    "src/app.py": b"print('hi')\n",
    "docs/overview.md": b"# Overview\n",
    "dist/assets/app.js": b"console.log(1)\n",
    "vendor/lib/source.c": b"int main(void) { return 0; }\n",
}
UNKNOWN_FILES = {**CLEAN_FILES, "mystery.odd": b"unknown payload\n"}


def run_cli(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class CliFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.out = self.work / "artifacts"

    def make_repo(self, files=None, name="repo"):
        repo = self.work / name
        init_git_repo(repo, dict(CLEAN_FILES if files is None else files))
        return repo

    def scan(self, repo, *extra):
        return run_cli(SCAN_CLI, "--root", repo, "--out", self.out,
                       "--generated-at", FIXED_TIME, *extra)

    def write_overrides(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "1.0.0", "entries": entries}), encoding="utf-8")
        return path


class ScanCliTests(CliFixture):
    def test_complete_scan_writes_two_artifacts_and_reports_pass(self):
        repo = self.make_repo()

        result = self.scan(repo)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertIn(f"indexed_files={len(CLEAN_FILES)}", result.stdout)
        self.assertIn("unknown_files=0", result.stdout)

        project_index = load_artifact(self.out / "project-index.json")
        coverage = load_artifact(self.out / "coverage.json")
        self.assertEqual("1.1.0", project_index["schema_version"])
        self.assertEqual("1.1.0", coverage["schema_version"])
        self.assertEqual("PASS", audit_coverage(project_index, coverage, repo).status)

    def test_honest_unknown_reports_partial_and_exits_zero(self):
        repo = self.make_repo(UNKNOWN_FILES)

        result = self.scan(repo)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PARTIAL", result.stdout)
        self.assertIn("unknown_files=1", result.stdout)

    def test_strict_completeness_fails_and_preserves_existing_outputs(self):
        repo = self.make_repo(UNKNOWN_FILES)

        relaxed = self.scan(repo)
        self.assertEqual(0, relaxed.returncode, relaxed.stdout + relaxed.stderr)
        before = {
            name: (self.out / name).read_bytes()
            for name in ("project-index.json", "coverage.json")
        }

        strict = self.scan(repo, "--require-complete")

        self.assertEqual(1, strict.returncode, strict.stdout + strict.stderr)
        self.assertIn("FAIL", strict.stdout)
        self.assertIn("UNKNOWN_REMAINS", strict.stdout)
        self.assertNotIn("Traceback", strict.stderr)
        for name, payload in before.items():
            self.assertEqual(payload, (self.out / name).read_bytes(), name)

    def test_non_git_root_exits_two_without_traceback_or_outputs(self):
        plain = self.work / "not-a-repo"
        plain.mkdir()
        (plain / "file.txt").write_bytes(b"x\n")

        result = self.scan(plain)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("Git", result.stderr)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_malformed_override_exits_two_with_the_path_and_reason(self):
        repo = self.make_repo(UNKNOWN_FILES)
        overrides = self.write_overrides(self.work / "overrides.json", {
            "not/tracked.txt": {
                "surface": "tooling",
                "classification": "CLASSIFIED",
                "reason": "This path is not tracked.",
            },
        })

        result = self.scan(repo, "--overrides", overrides)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("overrides.json", result.stderr)
        self.assertIn("not/tracked.txt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_output_must_not_overwrite_the_override_input(self):
        repo = self.make_repo(UNKNOWN_FILES)
        overrides = self.write_overrides(self.out / "coverage.json", {
            "mystery.odd": {
                "surface": "tooling",
                "classification": "CLASSIFIED",
                "reason": "Repository-owned generator input documented by the project.",
            },
        })

        result = self.scan(repo, "--overrides", overrides)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("override", result.stderr)

    def test_existing_output_is_replaced_but_unrelated_files_survive(self):
        repo = self.make_repo()
        self.out.mkdir(parents=True)
        (self.out / "keep-me.txt").write_bytes(b"unrelated\n")
        (self.out / "project-index.json").write_bytes(b"stale\n")

        result = self.scan(repo)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(b"unrelated\n", (self.out / "keep-me.txt").read_bytes())
        self.assertNotEqual(b"stale\n", (self.out / "project-index.json").read_bytes())

    def test_audit_failure_before_publication_preserves_previous_artifacts(self):
        repo = self.make_repo(UNKNOWN_FILES)
        first = self.scan(repo)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        before = (self.out / "project-index.json").read_bytes()

        overrides = self.write_overrides(self.work / "overrides.json", {
            "mystery.odd": {
                "surface": "tooling",
                "classification": "COVERED",
                "reason": "Trying to bypass the audit.",
            },
        })
        failed = self.scan(repo, "--overrides", overrides)

        self.assertEqual(2, failed.returncode, failed.stdout + failed.stderr)
        self.assertEqual(before, (self.out / "project-index.json").read_bytes())

    def test_git_tree_snapshot_is_selectable(self):
        repo = self.make_repo()

        result = self.scan(repo, "--snapshot", "git-tree")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        coverage = load_artifact(self.out / "coverage.json")
        self.assertEqual("git-tree", load_artifact(self.out / "project-index.json")["project"]["snapshot_kind"])
        self.assertTrue(coverage["entries"])

    def test_argparse_misuse_exits_two(self):
        missing = run_cli(SCAN_CLI, "--root", self.work)
        self.assertEqual(2, missing.returncode)

        repo = self.make_repo()
        bad_snapshot = self.scan(repo, "--snapshot", "magic")
        self.assertEqual(2, bad_snapshot.returncode)
        self.assertIn("invalid choice", bad_snapshot.stderr)

        bad_time = run_cli(SCAN_CLI, "--root", repo, "--out", self.out, "--generated-at", "yesterday")
        self.assertEqual(2, bad_time.returncode)
        self.assertIn("generated-at", bad_time.stderr)

    def test_scan_output_is_byte_stable_for_a_fixed_timestamp(self):
        repo = self.make_repo()

        first = self.scan(repo)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        before = (self.out / "coverage.json").read_bytes()
        second = self.scan(repo)

        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(before, (self.out / "coverage.json").read_bytes())


class AuditCliTests(CliFixture):
    def build_artifacts(self, repo, name="coverage.json"):
        artifacts = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=FIXED_TIME))
        index_path = self.work / "project-index.json"
        coverage_path = self.work / name
        index_path.write_text(dumps_artifact(artifacts.project_index), encoding="utf-8")
        coverage_path.write_text(dumps_artifact(artifacts.coverage), encoding="utf-8")
        return index_path, coverage_path

    def audit(self, index_path, coverage_path, repo, *extra):
        return run_cli(AUDIT_CLI, "--project-index", index_path, "--coverage", coverage_path,
                       "--root", repo, *extra)

    def test_passing_audit_exits_zero(self):
        repo = self.make_repo()
        index_path, coverage_path = self.build_artifacts(repo)

        result = self.audit(index_path, coverage_path, repo)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertIn(f"indexed_files={len(CLEAN_FILES)}", result.stdout)

    def test_partial_audit_exits_zero_with_counts(self):
        repo = self.make_repo(UNKNOWN_FILES)
        index_path, coverage_path = self.build_artifacts(repo)

        result = self.audit(index_path, coverage_path, repo)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PARTIAL", result.stdout)
        self.assertIn("unknown_files=1", result.stdout)

    def test_require_complete_on_partial_artifacts_exits_one(self):
        repo = self.make_repo(UNKNOWN_FILES)
        index_path, coverage_path = self.build_artifacts(repo)

        result = self.audit(index_path, coverage_path, repo, "--require-complete")

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("UNKNOWN_REMAINS", result.stdout)

    def test_violation_is_reported_with_its_stable_code_and_exit_one(self):
        repo = self.make_repo()
        index_path, coverage_path = self.build_artifacts(repo)
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        coverage["unknown_count"] = 4
        coverage_path.write_text(dumps_artifact(coverage), encoding="utf-8")

        result = self.audit(index_path, coverage_path, repo)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("UNKNOWN_COUNT_MISMATCH", result.stdout)

    def test_malformed_artifact_exits_one_with_a_stable_code(self):
        repo = self.make_repo()
        index_path, coverage_path = self.build_artifacts(repo)
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        del coverage["generated_at"]
        coverage_path.write_text(json.dumps(coverage), encoding="utf-8")

        result = self.audit(index_path, coverage_path, repo)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("SCHEMA_INVALID", result.stdout)

    def test_argparse_misuse_exits_two(self):
        result = run_cli(AUDIT_CLI, "--project-index", "missing.json")

        self.assertEqual(2, result.returncode)
        self.assertIn("required", result.stderr)


class FixtureCliTests(CliFixture):
    """One real end-to-end CLI scan of the committed frontend fixture."""

    def test_frontend_fixture_scan_then_strict_audit(self):
        repo = self.work / "frontend"
        build_fixture_repository("frontend", repo)

        strict_scan = run_cli(SCAN_CLI, "--root", repo, "--out", self.out,
                              "--generated-at", FIXED_TIME,
                              "--overrides", FRONTEND_OVERRIDES, "--require-complete")

        self.assertEqual(0, strict_scan.returncode, strict_scan.stdout + strict_scan.stderr)
        self.assertIn("PASS", strict_scan.stdout)
        self.assertIn("unknown_files=0", strict_scan.stdout)
        self.assertTrue((self.out / "project-index.json").is_file())
        self.assertTrue((self.out / "coverage.json").is_file())

        audit = run_cli(AUDIT_CLI,
                        "--project-index", self.out / "project-index.json",
                        "--coverage", self.out / "coverage.json",
                        "--root", repo, "--require-complete")

        self.assertEqual(0, audit.returncode, audit.stdout + audit.stderr)
        self.assertIn("PASS", audit.stdout)

    def test_frontend_fixture_without_override_reports_partial_then_fails_strict(self):
        repo = self.work / "frontend"
        build_fixture_repository("frontend", repo)

        relaxed = run_cli(SCAN_CLI, "--root", repo, "--out", self.out,
                          "--generated-at", FIXED_TIME)

        self.assertEqual(0, relaxed.returncode, relaxed.stdout + relaxed.stderr)
        self.assertIn("PARTIAL", relaxed.stdout)
        self.assertIn("unknown_files=1", relaxed.stdout)

        strict = run_cli(SCAN_CLI, "--root", repo, "--out", self.out,
                         "--generated-at", FIXED_TIME, "--require-complete")

        self.assertEqual(1, strict.returncode, strict.stdout + strict.stderr)
        self.assertIn("UNKNOWN_REMAINS", strict.stdout)


if __name__ == "__main__":
    unittest.main()
