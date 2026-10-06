#!/usr/bin/env python3
"""End-to-end and failure-path tests for the Phase 3A CLI."""

import importlib
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_repository_scan import (  # noqa: E402
    commit_all,
    create_junction_or_skip,
    create_symlink_or_skip,
    init_git_repo,
    run_git_bytes,
)
import scan_repository as scan_repository_cli  # noqa: E402


FIXED_TIME = "2026-09-23T00:00:00Z"
CLI = SKILL_ROOT / "scripts" / "detect_stack.py"


def _scan(root, snapshot="worktree"):
    return scan_repository(ScanOptions(
        root=Path(root), snapshot_kind=snapshot, generated_at=FIXED_TIME,
    ))


def _write_inputs(work, artifacts, *, index_name="project-index.json"):
    index_path = work / index_name
    coverage_path = work / "coverage.json"
    index_path.write_text(dumps_artifact(artifacts.project_index), encoding="utf-8")
    coverage_path.write_text(dumps_artifact(artifacts.coverage), encoding="utf-8")
    return index_path, coverage_path


def _expected_source_metadata(artifacts, *, g01_status="PASS", unknown_files=0):
    return {
        "snapshot_kind": artifacts.project_index["project"]["snapshot_kind"],
        "g01_status": g01_status,
        "unknown_files": unknown_files,
        "project_index_sha256": hashlib.sha256(
            dumps_artifact(artifacts.project_index).encode("utf-8")
        ).hexdigest(),
        "coverage_sha256": hashlib.sha256(
            dumps_artifact(artifacts.coverage).encode("utf-8")
        ).hexdigest(),
    }


def _run(root, index, coverage, out, *extra):
    return subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), "--index", str(index),
         "--coverage", str(coverage), "--out", str(out), *map(str, extra)],
        cwd=SKILL_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class DetectStackCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "repo"

    def test_git_repository_invocation_publishes_two_schema_valid_artifacts(self):
        init_git_repo(self.root, {
            "package.json": b'{"scripts":{"build":"PRIVATE_BUILD_CANARY"},"dependencies":{"react":"18.0.0"}}',
            "src/app.js": b"module.exports = {};\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)
        out = self.work / "out"

        result = _run(self.root, index, coverage, out, "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(out / "stack-profile.json")
        evidence = load_artifact(out / "evidence.json")
        self.assertEqual(artifacts.project_index["repository_revision"], profile["repository_revision"])
        self.assertEqual(profile["repository_revision"], evidence["repository_revision"])
        self.assertEqual(FIXED_TIME, profile["generated_at"])
        self.assertEqual(profile["generated_at"], evidence["generated_at"])
        self.assertEqual("1.1.0", profile["schema_version"])
        self.assertEqual("1.1.0", evidence["schema_version"])
        self.assertEqual(
            _expected_source_metadata(artifacts),
            profile["source_metadata"],
        )
        self.assertEqual(profile["source_metadata"], evidence["source_metadata"])
        self.assertIn("React", {item["name"] for item in profile["detections"]})
        self.assertNotIn("runtime", result.stdout.lower())
        self.assertNotIn("PRIVATE_BUILD_CANARY", result.stdout + result.stderr)

    def test_partial_phase2_audit_is_accepted_and_reported(self):
        init_git_repo(self.root, {
            "src/app.py": b"pass\n",
            "mystery.odd": b"unclassified fixture\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PARTIAL", result.stdout)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        evidence = load_artifact(self.work / "out" / "evidence.json")
        metadata = _expected_source_metadata(artifacts, g01_status="PARTIAL", unknown_files=1)
        self.assertEqual(metadata, profile["source_metadata"])
        self.assertEqual(profile["source_metadata"], evidence["source_metadata"])

    def test_python_declaration_cli_end_to_end(self):
        init_git_repo(self.root, {
            "pyproject.toml": b"[project]\nname='agent'\ndependencies=['langgraph>=0.2']\n",
            "src/agent.py": b"def run(): pass\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        evidence = load_artifact(self.work / "out" / "evidence.json")
        names = {item["name"] for item in profile["detections"]}
        self.assertTrue({"Python", "LangGraph"} <= names)
        self.assertTrue(all(
            evidence_id in {item["id"] for item in evidence["items"]}
            for detection in profile["detections"]
            for evidence_id in detection["evidence_ids"]
        ))

    def test_java_declaration_cli_end_to_end(self):
        init_git_repo(self.root, {
            "pom.xml": (
                b"<project xmlns='http://maven.apache.org/POM/4.0.0'>"
                b"<modelVersion>4.0.0</modelVersion><properties>"
                b"<maven.compiler.release>17</maven.compiler.release>"
                b"</properties><dependencies><dependency>"
                b"<groupId>org.springframework.boot</groupId>"
                b"<artifactId>spring-boot-starter-web</artifactId>"
                b"</dependency></dependencies></project>"
            ),
            "src/main/java/App.java": b"class App {}\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        names = {item["name"] for item in profile["detections"]}
        self.assertTrue({"Java", "Maven", "Spring Boot"} <= names, names)

    def test_node_declaration_cli_end_to_end(self):
        init_git_repo(self.root, {
            "package.json": b'{"dependencies":{"react":"18.3.1"},"devDependencies":{"vite":"5.4.0"}}',
            "src/app.jsx": b"export default function App() {}\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        names = {item["name"] for item in profile["detections"]}
        self.assertTrue({"Node.js", "React", "Vite"} <= names)

    def test_malformed_manifest_error_does_not_echo_manifest_body(self):
        payload = b'{"dependencies": {"react": "1"}, "secret": "PRIVATE_CANARY", "dependencies": {}}'
        init_git_repo(self.root, {"package.json": payload})
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_CANARY", result.stdout + result.stderr)
        self.assertFalse((self.work / "out" / "stack-profile.json").exists())

    def test_toml_dynamic_group_key_is_redacted_from_cli_artifacts_and_diagnostics(self):
        sentinel = "PRIVATE_TOML_GROUP_CANARY"
        init_git_repo(self.root, {
            "pyproject.toml": (
                "[project]\nname='agent'\n[project.optional-dependencies]\n"
                f"'{sentinel}'=['fastapi>=0.1']\n"
            ).encode("utf-8"),
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        outputs = (self.work / "out" / "stack-profile.json", self.work / "out" / "evidence.json")
        self.assertNotIn(sentinel, result.stdout + result.stderr, "TOML sentinel leaked to CLI diagnostics")
        self.assertTrue(all(sentinel not in path.read_text(encoding="utf-8") for path in outputs),
                        "TOML sentinel leaked into CLI artifacts")

    def test_unreadable_existing_phase2_input_uses_operational_exit_code(self):
        detector_cli = importlib.import_module("detect_stack")
        init_git_repo(self.root, {"README.md": b"fixture\n"})
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        def unreadable(_path):
            try:
                raise PermissionError("injected read failure")
            except OSError as exc:
                raise ArtifactValidationError("cannot read Phase 2 input") from exc

        with patch.object(detector_cli, "load_artifact", side_effect=unreadable):
            result = detector_cli.main([
                "--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--out", str(self.work / "out"), "--generated-at", FIXED_TIME,
            ])

        self.assertEqual(2, result)

    def test_stale_revision_and_double_omission_fail_without_replacing_outputs(self):
        init_git_repo(self.root, {
            "pyproject.toml": b"[project]\nname='x'\nrequires-python='>=3.12'\n",
            "src/a.py": b"pass\n",
        })
        original = _scan(self.root)
        index, coverage = _write_inputs(self.work, original)
        out = self.work / "out"
        out.mkdir()
        (out / "stack-profile.json").write_bytes(b"old stack")
        (out / "evidence.json").write_bytes(b"old evidence")

        stale_index = dict(original.project_index)
        stale_coverage = dict(original.coverage)
        stale_index["repository_revision"] = "0" * 40
        stale_coverage["repository_revision"] = "0" * 40
        index.write_text(dumps_artifact(stale_index), encoding="utf-8")
        coverage.write_text(dumps_artifact(stale_coverage), encoding="utf-8")
        stale = _run(self.root, index, coverage, out, "--generated-at", FIXED_TIME)
        self.assertEqual(1, stale.returncode, stale.stdout + stale.stderr)
        self.assertEqual(b"old stack", (out / "stack-profile.json").read_bytes())
        self.assertEqual(b"old evidence", (out / "evidence.json").read_bytes())

        omitted_index = dict(original.project_index)
        omitted_coverage = dict(original.coverage)
        omitted_index["files"] = [item for item in omitted_index["files"] if item["path"] != "pyproject.toml"]
        omitted_index["file_count"] = len(omitted_index["files"])
        omitted_coverage["entries"] = [item for item in omitted_coverage["entries"] if item["path"] != "pyproject.toml"]
        omitted_coverage["tracked_file_count"] = len(omitted_coverage["entries"])
        index.write_text(dumps_artifact(omitted_index), encoding="utf-8")
        coverage.write_text(dumps_artifact(omitted_coverage), encoding="utf-8")
        double_omitted = _run(self.root, index, coverage, out, "--generated-at", FIXED_TIME)
        self.assertEqual(1, double_omitted.returncode, double_omitted.stdout + double_omitted.stderr)
        self.assertIn("PATH_SET_MISMATCH", double_omitted.stdout)
        self.assertEqual(b"old stack", (out / "stack-profile.json").read_bytes())
        self.assertEqual(b"old evidence", (out / "evidence.json").read_bytes())

    def test_git_tree_reads_committed_manifest_not_changed_worktree_bytes(self):
        init_git_repo(self.root, {
            "package.json": b'{"dependencies":{"react":"18.0.0"}}',
        })
        (self.root / "package.json").write_text(
            '{"dependencies":{"@angular/core":"18.0.0"}}', encoding="utf-8",
        )
        artifacts = _scan(self.root, snapshot="git-tree")
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        names = {item["name"] for item in load_artifact(self.work / "out" / "stack-profile.json")["detections"]}
        self.assertIn("React", names)
        self.assertNotIn("Angular", names)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        evidence = load_artifact(self.work / "out" / "evidence.json")
        self.assertEqual("git-tree", profile["source_metadata"]["snapshot_kind"])
        self.assertEqual(profile["source_metadata"], evidence["source_metadata"])

    def test_v10_git_tree_symlink_with_source_suffix_is_not_a_language_detection(self):
        init_git_repo(self.root, {"README.md": b"fixture\n"})
        blob = run_git_bytes(self.root, "hash-object", "-w", "--stdin", input_bytes=b"target.py")
        self.assertEqual(0, blob.returncode, blob.stderr.decode("utf-8", "replace"))
        object_id = blob.stdout.decode("ascii").strip()
        added = run_git_bytes(
            self.root, "update-index", "--add", "--cacheinfo", f"120000,{object_id},src/linked.py",
        )
        self.assertEqual(0, added.returncode, added.stderr.decode("utf-8", "replace"))
        committed = run_git_bytes(self.root, "commit", "--quiet", "--no-verify", "-m", "add tracked symlink")
        self.assertEqual(0, committed.returncode, committed.stderr.decode("utf-8", "replace"))

        artifacts = _scan(self.root, snapshot="git-tree")
        index = dict(artifacts.project_index)
        index["schema_version"] = "1.0.0"
        index["files"] = [
            {key: value for key, value in item.items()
             if key not in {"content_kind", "media_type", "extension", "vcs_object_id"}}
            for item in index["files"]
        ]
        coverage = dict(artifacts.coverage)
        coverage["schema_version"] = "1.0.0"
        coverage.pop("classification_policy_version", None)
        coverage["entries"] = [
            {key: value for key, value in item.items() if key not in {"secondary_surfaces", "rule_id"}}
            for item in coverage["entries"]
        ]
        legacy_artifacts = type("LegacyArtifacts", (), {"project_index": index, "coverage": coverage})()
        index_path, coverage_path = _write_inputs(self.work, legacy_artifacts)

        result = _run(
            self.root, index_path, coverage_path, self.work / "out", "--generated-at", FIXED_TIME,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        profile = load_artifact(self.work / "out" / "stack-profile.json")
        evidence = load_artifact(self.work / "out" / "evidence.json")
        self.assertNotIn("Python", {item["name"] for item in profile["detections"]})
        self.assertEqual("1.1.0", profile["schema_version"])
        self.assertEqual("1.1.0", evidence["schema_version"])

    def test_worktree_manifest_symlink_escape_is_rejected_without_reading_target(self):
        init_git_repo(self.root, {"README.md": b"fixture\n"})
        outside = self.work / "outside.json"
        outside.write_text('{"dependencies":{"react":"18"},"private":"PRIVATE_CANARY"}', encoding="utf-8")
        link = self.root / "package.json"
        create_symlink_or_skip(self, link, outside)
        commit_all(self.root, "add manifest symlink")
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("PRIVATE_CANARY", result.stdout + result.stderr)
        self.assertFalse((self.work / "out" / "stack-profile.json").exists())

    def test_worktree_ancestor_junction_escape_fails_g01_before_reading_target(self):
        init_git_repo(self.root, {
            "linked/package.json": b'{"dependencies":{"react":"18"}}',
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)
        outside = self.work / "outside"
        outside.mkdir()
        (outside / "package.json").write_text(
            '{"dependencies":{"react":"18"},"private":"PRIVATE_CANARY"}', encoding="utf-8",
        )
        shutil.rmtree(self.root / "linked")
        create_junction_or_skip(self, self.root / "linked", outside)

        result = _run(self.root, index, coverage, self.work / "out", "--generated-at", FIXED_TIME)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("SNAPSHOT_PATH_UNSAFE", result.stdout)
        self.assertNotIn("PRIVATE_CANARY", result.stdout + result.stderr)
        self.assertFalse((self.work / "out" / "stack-profile.json").exists())

    def test_publication_failure_restores_both_previous_outputs(self):
        if not CLI.is_file():
            self.fail("detect_stack.py CLI implementation is missing")
        detector_cli = importlib.import_module("detect_stack")
        init_git_repo(self.root, {
            "pyproject.toml": b"[project]\nname='x'\nrequires-python='>=3.12'\n",
        })
        artifacts = _scan(self.root)
        index, coverage = _write_inputs(self.work, artifacts)
        out = self.work / "out"
        out.mkdir()
        (out / "stack-profile.json").write_bytes(b"old stack")
        (out / "evidence.json").write_bytes(b"old evidence")
        real_replace = os.replace
        replacements = 0

        def fail_second_replace(source, target):
            nonlocal replacements
            source_path, target_path = Path(source), Path(target)
            if source_path.suffix == ".tmp" and target_path.name in {"stack-profile.json", "evidence.json"}:
                replacements += 1
                if replacements == 2:
                    raise OSError("injected second output replacement failure")
            return real_replace(source, target)

        with patch.object(scan_repository_cli.os, "replace", side_effect=fail_second_replace):
            result = detector_cli.main([
                "--root", str(self.root), "--index", str(index), "--coverage", str(coverage),
                "--out", str(out), "--generated-at", FIXED_TIME,
            ])

        self.assertEqual(2, result)
        self.assertEqual(b"old stack", (out / "stack-profile.json").read_bytes())
        self.assertEqual(b"old evidence", (out / "evidence.json").read_bytes())

    def test_output_cannot_alias_an_input_artifact(self):
        init_git_repo(self.root, {"pyproject.toml": b"[project]\nname='x'\n"})
        artifacts = _scan(self.root)
        out = self.work / "out"
        out.mkdir()
        index, coverage = _write_inputs(out, artifacts, index_name="stack-profile.json")

        result = _run(self.root, index, coverage, out, "--generated-at", FIXED_TIME)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual(dumps_artifact(artifacts.project_index), index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
