"""Tests for redacted, snapshot-bound repository documentation evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from repository_documentation_evidence import (  # noqa: E402
    RepositoryDocumentationEvidenceError,
    build_repository_documentation_evidence,
    validate_repository_documentation_evidence,
)
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_phase3_bundle_audit import FIXED_TIME, _fixture  # noqa: E402


class RepositoryDocumentationEvidenceTests(unittest.TestCase):
    def test_emits_only_generic_summary_and_exact_original_byte_digests(self):
        source = (
            b"# Product\r\n"
            b"The private deployment URL is https://private.example.invalid\r\n"
            b"credential=DOCUMENTATION_SECRET_CANARY\r\n"
        )
        fixture = _fixture(self, {"README.md": source})

        evidence = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )

        item = evidence["items"][0]
        expected_span = source[source.index(b"The private"):source.index(b"credential=")]
        self.assertEqual("1.3.0", evidence["schema_version"])
        self.assertEqual("E2", item["level"])
        self.assertEqual("repository_documentation", item["kind"])
        self.assertEqual("The selected repository documentation span records a repository declaration.", item["summary"])
        self.assertEqual({"path": "README.md", "line_start": 2, "line_end": 2}, item["locator"])
        self.assertEqual(hashlib.sha256(source).hexdigest(), item["source_bytes"]["file_sha256"])
        self.assertEqual(hashlib.sha256(expected_span).hexdigest(), item["source_bytes"]["span_sha256"])
        self.assertEqual(len(expected_span), item["source_bytes"]["span_bytes"])
        serialized = dumps_artifact(evidence)
        self.assertNotIn("private.example.invalid", serialized)
        self.assertNotIn("DOCUMENTATION_SECRET_CANARY", serialized)
        self.assertNotIn("The private deployment URL", serialized)
        repeated = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )
        self.assertEqual(item["id"], repeated["items"][0]["id"])

    def test_git_tree_selection_reads_committed_bytes_after_worktree_edit(self):
        committed = b"# Original\nPinned declaration\n"
        fixture = _fixture(self, {"README.md": committed})
        (fixture["root"] / "README.md").write_bytes(b"# Changed\nUnpinned worktree text\n")
        scan = scan_repository(ScanOptions(
            root=fixture["root"], snapshot_kind="git-tree", generated_at=FIXED_TIME,
        ))

        evidence = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=scan.project_index,
            coverage=scan.coverage,
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )

        item = evidence["items"][0]
        self.assertEqual(hashlib.sha256(committed).hexdigest(), item["source_bytes"]["file_sha256"])
        self.assertEqual(hashlib.sha256(b"Pinned declaration\n").hexdigest(), item["source_bytes"]["span_sha256"])
        self.assertEqual("git-tree", evidence["snapshot_kind"])

    def test_invalid_paths_ranges_and_nonindexed_files_fail_closed(self):
        fixture = _fixture(self, {"README.md": b"one\ntwo\n"})
        base = {
            "root": fixture["root"],
            "project_index": fixture["project_index"],
            "coverage": fixture["coverage"],
            "generated_at": FIXED_TIME,
        }
        for selection in (("../README.md", 1, 1), ("README.md", 3, 3), ("missing.md", 1, 1)):
            with self.subTest(selection=selection):
                with self.assertRaises(RepositoryDocumentationEvidenceError):
                    build_repository_documentation_evidence(**base, selections=[selection])

    def test_validator_reconstructs_selected_records_instead_of_trusting_digests(self):
        source = b"# Readme\nA declaration\n"
        fixture = _fixture(self, {"README.md": source})
        evidence = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )
        audit = audit_coverage(fixture["project_index"], fixture["coverage"], fixture["root"])
        tampered = json.loads(json.dumps(evidence))
        tampered["items"][0]["source_bytes"]["span_sha256"] = "0" * 64

        with self.assertRaises(RepositoryDocumentationEvidenceError):
            validate_repository_documentation_evidence(
                tampered,
                root=fixture["root"],
                project_index=fixture["project_index"],
                coverage=fixture["coverage"],
                g01_status=audit.status,
                unknown_files=audit.measurements["unknown_files"],
            )

    def test_cli_publishes_a_redacted_artifact(self):
        source = b"Public heading\nCLI_SECRET_CANARY https://private.example.invalid\n"
        fixture = _fixture(self, {"README.md": source})
        index_path = fixture["work"] / "project-index.json"
        coverage_path = fixture["work"] / "coverage.json"
        index_path.write_text(dumps_artifact(fixture["project_index"]), encoding="utf-8")
        coverage_path.write_text(dumps_artifact(fixture["coverage"]), encoding="utf-8")
        output = fixture["work"] / "documentation-output"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "repository_documentation_evidence.py"),
                "--root", str(fixture["root"]),
                "--project-index", str(index_path),
                "--coverage", str(coverage_path),
                "--selection", "README.md:2:2",
                "--out", str(output),
                "--generated-at", FIXED_TIME,
            ],
            cwd=SKILL_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("CLI_SECRET_CANARY", result.stdout + result.stderr)
        artifact = load_artifact(output / "evidence.json")
        self.assertNotIn("CLI_SECRET_CANARY", dumps_artifact(artifact))
        self.assertNotIn("private.example.invalid", dumps_artifact(artifact))


if __name__ == "__main__":
    unittest.main()
