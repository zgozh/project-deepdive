#!/usr/bin/env python3
"""Tests for the reusable cross-artifact and snapshot coverage audit."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "artifacts" / "v1"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from coverage_audit import VIOLATION_CODES, audit_coverage  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_repository_scan import build_fixture_repository, init_git_repo, run_git_bytes  # noqa: E402

FIXED_TIME = "2026-09-22T00:00:00Z"
REPOSITORY_FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "repository_scanner"
FRONTEND_OVERRIDES = REPOSITORY_FIXTURE_ROOT / "overrides" / "frontend.json"
FIXTURE_FILES = {
    "src/app.py": b"print('hi')\n",
    "docs/overview.md": b"# Overview\n",
    "backend/tests/fixtures/user.json": b'{"id": 1}\n',
    "dist/assets/app.js": b"console.log(1)\n",
    "vendor/lib/source.c": b"int main(void) { return 0; }\n",
    ".env": b"SECRET_TOKEN=not-a-real-secret\n",
    "mystery.odd": b"unknown payload\n",
}
KNOWN_FREE_FILES = {path: data for path, data in FIXTURE_FILES.items() if path != "mystery.odd"}


def codes(result):
    return tuple(violation.code for violation in result.violations)


def code_set(result):
    return set(codes(result))


def entry_of(coverage, path):
    return next(item for item in coverage["entries"] if item["path"] == path)


def file_of(project_index, path):
    return next(item for item in project_index["files"] if item["path"] == path)


def add_gitlink(repo, path="vendor/lib", object_id="a" * 40):
    result = run_git_bytes(repo, "update-index", "--add", "--cacheinfo", f"160000,{object_id},{path}")
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return run_git_bytes(repo, "commit", "--quiet", "--no-verify", "-m", "add gitlink")


class AuditFixture(unittest.TestCase):
    def build(self, snapshot_kind="worktree", files=None, overrides_path=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name) / "repo"
        init_git_repo(repo, dict(FIXTURE_FILES if files is None else files))
        artifacts = scan_repository(ScanOptions(
            root=repo,
            snapshot_kind=snapshot_kind,
            generated_at=FIXED_TIME,
            overrides_path=overrides_path,
        ))
        return repo, artifacts.project_index, artifacts.coverage

    def build_gitlink_repo(self, snapshot_kind="git-tree"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name) / "repo"
        init_git_repo(repo, {"one.txt": b"1\n"})
        add_gitlink(repo)
        return repo, scan_repository(ScanOptions(root=repo, snapshot_kind=snapshot_kind,
                                                 generated_at=FIXED_TIME))


class PositiveAuditTests(AuditFixture):
    def test_clean_worktree_scan_passes_with_measurements(self):
        repo, project_index, coverage = self.build(files=KNOWN_FREE_FILES)

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual("PASS", result.status, result.violations)
        self.assertEqual((), result.violations)
        self.assertEqual(0, result.measurements["unknown_files"])
        self.assertEqual(len(project_index["files"]), result.measurements["indexed_files"])
        self.assertEqual(len(coverage["entries"]), result.measurements["coverage_entries"])

    def test_clean_git_tree_scan_passes(self):
        repo, project_index, coverage = self.build("git-tree", files=KNOWN_FREE_FILES)

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual("PASS", result.status, result.violations)

    def test_resolved_override_reaches_zero_unknown_and_passes(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        overrides = Path(temporary.name) / "overrides.json"
        overrides.write_text(json.dumps({
            "schema_version": "1.0.0",
            "entries": {
                "mystery.odd": {
                    "surface": "tooling",
                    "classification": "CLASSIFIED",
                    "reason": "Repository-owned generator input documented by the project.",
                },
            },
        }), encoding="utf-8")
        repo, project_index, coverage = self.build(overrides_path=overrides)

        result = audit_coverage(project_index, coverage, repo, require_complete=True)

        self.assertEqual("PASS", result.status, result.violations)
        self.assertEqual(0, result.measurements["unknown_files"])

    def test_honest_unknown_is_partial_without_strict_completeness(self):
        repo, project_index, coverage = self.build()

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual("PARTIAL", result.status)
        self.assertEqual((), result.violations)
        self.assertEqual(1, result.measurements["unknown_files"])

    def test_strict_completeness_rejects_the_same_honest_unknown(self):
        repo, project_index, coverage = self.build()

        result = audit_coverage(project_index, coverage, repo, require_complete=True)

        self.assertEqual("FAIL", result.status)
        self.assertEqual(("UNKNOWN_REMAINS",), codes(result))

    def test_gitlink_payload_semantics_are_accepted_without_traversal(self):
        repo, artifacts = self.build_gitlink_repo()

        result = audit_coverage(artifacts.project_index, artifacts.coverage, repo)

        self.assertEqual("PASS", result.status, result.violations)

    def test_legacy_v1_0_artifacts_are_still_auditable(self):
        project_index = json.loads((FIXTURE_ROOT / "project-index.json").read_text(encoding="utf-8"))
        coverage = json.loads((FIXTURE_ROOT / "coverage.json").read_text(encoding="utf-8"))
        root = SKILL_ROOT / project_index["project"]["root"]

        result = audit_coverage(project_index, coverage, root)

        self.assertEqual("PASS", result.status, result.violations)

    def test_violation_codes_are_the_published_vocabulary(self):
        self.assertEqual(
            {
                "SCHEMA_INVALID", "REVISION_MISMATCH", "TIMESTAMP_MISMATCH", "COUNT_MISMATCH",
                "DUPLICATE_PATH", "PATH_SET_MISMATCH", "UNSAFE_PATH", "V11_FIELD_MISSING",
                "SURFACE_INVALID", "REASON_MISSING", "UNKNOWN_COUNT_MISMATCH", "UNKNOWN_REMAINS",
                "SNAPSHOT_FILE_MISSING", "SNAPSHOT_SIZE_MISMATCH", "SNAPSHOT_HASH_MISMATCH",
                "GIT_OBJECT_MISSING",
            },
            set(VIOLATION_CODES),
        )


class NegativeAuditTests(AuditFixture):
    def test_schema_invalid_artifact_short_circuits_deeper_access(self):
        repo, project_index, coverage = self.build()
        project_index["schema_version"] = "1.2.0"

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual("FAIL", result.status)
        self.assertEqual(("SCHEMA_INVALID",), codes(result))
        self.assertEqual(len(project_index["files"]), result.measurements["indexed_files"])
        self.assertEqual(1, result.measurements["violations"])

    def test_missing_coverage_entry_is_a_path_set_mismatch(self):
        repo, project_index, coverage = self.build()
        coverage["entries"] = [item for item in coverage["entries"] if item["path"] != "docs/overview.md"]
        coverage["tracked_file_count"] = len(coverage["entries"])

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("PATH_SET_MISMATCH",), codes(result))

    def test_duplicate_path_is_reported(self):
        repo, project_index, coverage = self.build()
        coverage["entries"].append(dict(coverage["entries"][0]))
        coverage["tracked_file_count"] = len(coverage["entries"])

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("DUPLICATE_PATH",), codes(result))

    def test_count_mismatch_is_reported(self):
        repo, project_index, coverage = self.build()
        project_index["file_count"] = 99

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("COUNT_MISMATCH",), codes(result))

    def test_unknown_count_mismatch_is_reported(self):
        repo, project_index, coverage = self.build()
        coverage["unknown_count"] = 5

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("UNKNOWN_COUNT_MISMATCH",), codes(result))

    def test_revision_mismatch_is_reported(self):
        repo, project_index, coverage = self.build()
        project_index["repository_revision"] = "b" * 40

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("REVISION_MISMATCH",), codes(result))

    def test_timestamp_mismatch_is_reported(self):
        repo, project_index, coverage = self.build()
        coverage["generated_at"] = "2026-09-23T00:00:00Z"

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("TIMESTAMP_MISMATCH",), codes(result))

    def test_reason_is_required_for_generated_vendor_and_ignored(self):
        for path in ("dist/assets/app.js", "vendor/lib/source.c", ".env"):
            with self.subTest(path=path):
                repo, project_index, coverage = self.build()
                del entry_of(coverage, path)["reason"]

                result = audit_coverage(project_index, coverage, repo)

                self.assertEqual(("REASON_MISSING",), codes(result))

    def test_v1_1_fields_are_required_on_scanner_entries(self):
        repo, project_index, coverage = self.build()
        del entry_of(coverage, "src/app.py")["rule_id"]

        result = audit_coverage(project_index, coverage, repo)
        self.assertEqual(("V11_FIELD_MISSING",), codes(result))

        repo, project_index, coverage = self.build()
        del file_of(project_index, "src/app.py")["media_type"]

        result = audit_coverage(project_index, coverage, repo)
        self.assertEqual(("V11_FIELD_MISSING",), codes(result))

    def test_unknown_rule_id_is_rejected(self):
        repo, project_index, coverage = self.build()
        entry_of(coverage, "src/app.py")["rule_id"] = "totally:made-up"

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("V11_FIELD_MISSING",), codes(result))

    def test_secondary_surfaces_must_be_sorted_unique_and_not_the_primary(self):
        path = "backend/tests/fixtures/user.json"
        repo, project_index, coverage = self.build()
        self.assertEqual(["backend", "test"], entry_of(coverage, path)["secondary_surfaces"])

        for mutated in (["test", "backend"], ["backend", "backend"], ["fixture"]):
            with self.subTest(secondary_surfaces=mutated):
                repo, project_index, coverage = self.build()
                entry_of(coverage, path)["secondary_surfaces"] = list(mutated)

                result = audit_coverage(project_index, coverage, repo)

                self.assertEqual(("SURFACE_INVALID",), codes(result))

    def test_unsafe_artifact_path_is_rejected(self):
        repo, project_index, coverage = self.build()
        for item in project_index["files"]:
            if item["path"] == "src/app.py":
                item["path"] = "../escape.py"
        for item in coverage["entries"]:
            if item["path"] == "src/app.py":
                item["path"] = "../escape.py"

        result = audit_coverage(project_index, coverage, repo)

        self.assertIn("UNSAFE_PATH", code_set(result))

    def test_missing_worktree_file_is_rejected(self):
        repo, project_index, coverage = self.build()
        (repo / "docs" / "overview.md").unlink()

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("SNAPSHOT_FILE_MISSING",), codes(result))

    def test_modified_worktree_bytes_are_rejected(self):
        repo, project_index, coverage = self.build()
        (repo / "docs" / "overview.md").write_bytes(b"# OVERVIEW\n")

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("SNAPSHOT_HASH_MISMATCH",), codes(result))

    def test_resized_worktree_file_is_rejected(self):
        repo, project_index, coverage = self.build()
        (repo / "docs" / "overview.md").write_bytes(b"# Overview rewritten\n")

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("SNAPSHOT_SIZE_MISMATCH",), codes(result))

    def test_missing_git_object_at_the_declared_revision_is_rejected(self):
        repo, project_index, coverage = self.build("git-tree")
        project_index["repository_revision"] = "b" * 40
        coverage["repository_revision"] = "b" * 40

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual({"GIT_OBJECT_MISSING"}, code_set(result))

    def test_mutated_git_tree_hash_is_rejected(self):
        repo, project_index, coverage = self.build("git-tree")
        file_of(project_index, "src/app.py")["sha256"] = "0" * 64

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("SNAPSHOT_HASH_MISMATCH",), codes(result))

    def test_mutated_gitlink_payload_is_rejected(self):
        repo, artifacts = self.build_gitlink_repo()
        file_of(artifacts.project_index, "vendor/lib")["sha256"] = hashlib.sha256(b"wrong").hexdigest()

        result = audit_coverage(artifacts.project_index, artifacts.coverage, repo)

        self.assertEqual(("SNAPSHOT_HASH_MISMATCH",), codes(result))

    def test_independent_violations_are_accumulated_not_short_circuited(self):
        repo, project_index, coverage = self.build()
        project_index["file_count"] = 99
        project_index["repository_revision"] = "b" * 40

        result = audit_coverage(project_index, coverage, repo)

        self.assertEqual(("COUNT_MISMATCH", "REVISION_MISMATCH"), codes(result))
        self.assertEqual("FAIL", result.status)
        self.assertEqual(2, result.measurements["violations"])


class FixtureUnknownResolutionTests(unittest.TestCase):
    """The committed frontend fixture proves the honest-unknown resolution flow."""

    def build_frontend(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name) / "frontend"
        build_fixture_repository("frontend", repo)
        return repo

    def test_frontend_unknown_blocks_strict_audit_until_an_override_exists(self):
        repo = self.build_frontend()

        unresolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=FIXED_TIME))
        unresolved_entries = {item["path"]: item for item in unresolved.coverage["entries"]}
        deliberate = unresolved_entries["mystery.odd"]

        self.assertEqual(1, unresolved.coverage["unknown_count"])
        self.assertEqual(("other", "UNKNOWN", "LOCATED", "fallback:unknown"),
                         (deliberate["surface"], deliberate["classification"],
                          deliberate["teaching_status"], deliberate["rule_id"]))
        self.assertEqual("PARTIAL", audit_coverage(unresolved.project_index, unresolved.coverage,
                                                   repo).status)
        strict = audit_coverage(unresolved.project_index, unresolved.coverage, repo,
                                require_complete=True)
        self.assertEqual("FAIL", strict.status)
        self.assertEqual(("UNKNOWN_REMAINS",), codes(strict))

        resolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                              generated_at=FIXED_TIME,
                                              overrides_path=FRONTEND_OVERRIDES))
        resolved_entries = {item["path"]: item for item in resolved.coverage["entries"]}
        resolved_unknown = resolved_entries["mystery.odd"]

        self.assertEqual(0, resolved.coverage["unknown_count"])
        self.assertEqual(("tooling", "CLASSIFIED", "LOCATED", "override:exact-path"),
                         (resolved_unknown["surface"], resolved_unknown["classification"],
                          resolved_unknown["teaching_status"], resolved_unknown["rule_id"]))
        self.assertEqual("Repository-owned generator input documented by the project.",
                         resolved_unknown["reason"])
        passed = audit_coverage(resolved.project_index, resolved.coverage, repo, require_complete=True)
        self.assertEqual("PASS", passed.status, passed.violations)

    def test_the_override_changes_nothing_else_in_the_frontend_fixture(self):
        repo = self.build_frontend()
        unresolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=FIXED_TIME))
        resolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                              generated_at=FIXED_TIME,
                                              overrides_path=FRONTEND_OVERRIDES))

        self.assertEqual(unresolved.project_index, resolved.project_index)
        difference = [
            (before, after)
            for before, after in zip(unresolved.coverage["entries"], resolved.coverage["entries"])
            if before != after
        ]
        self.assertEqual(1, len(difference))
        self.assertEqual("mystery.odd", difference[0][0]["path"])


if __name__ == "__main__":
    unittest.main()
