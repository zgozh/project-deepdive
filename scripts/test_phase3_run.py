#!/usr/bin/env python3
"""Tests for portable Phase 3 input assembly and disk validation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from phase3_bundle_audit import _build_snapshot_inputs  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from repository_documentation_evidence import build_repository_documentation_evidence  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402
from test_phase3_bundle_audit import FIXED_TIME, _fixture  # noqa: E402
from phase3_run import (  # noqa: E402
    DOCUMENTATION_MEMBER_PATH,
    MEMBER_PATHS,
    Phase3RunError,
    assemble_phase3_run,
    validate_phase3_run,
)
import phase3_run as phase3_run_module  # noqa: E402


def _write_inputs(testcase, fixture, overrides=None):
    documents = {
        "project_index": fixture["project_index"],
        "coverage": fixture["coverage"],
        "stack_profile": fixture["stack_profile"],
        "phase3a_evidence": fixture["phase3a_evidence"],
        "python_analysis": fixture["python_analysis"],
        "python_evidence": fixture["python_evidence"],
    }
    documents.update(overrides or {})
    inputs_dir = fixture["work"] / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    paths = {}
    contents = {}
    for name, document in documents.items():
        if document is None:
            paths[name] = None
            continue
        path = inputs_dir / f"{name}.json"
        data = dumps_artifact(document).encode("utf-8")
        path.write_bytes(data)
        paths[name] = path
        contents[name] = data
    return paths, contents


def _git_tree_run(testcase, source_files):
    fixture = _fixture(testcase, source_files)
    scan = scan_repository(ScanOptions(
        root=fixture["root"],
        snapshot_kind="git-tree",
        generated_at=FIXED_TIME,
    ))
    source_reader, regular_paths = _build_snapshot_inputs(fixture["root"], scan.project_index)
    profile, phase3a_evidence = build_stack_artifacts(
        scan.project_index,
        scan.coverage,
        source_reader,
        generated_at=FIXED_TIME,
        regular_source_paths=regular_paths,
        g01_status=audit_coverage(scan.project_index, scan.coverage, fixture["root"]).status,
        unknown_files=scan.coverage["unknown_count"],
    )
    fixture.update({
        "project_index": scan.project_index,
        "coverage": scan.coverage,
        "stack_profile": profile,
        "phase3a_evidence": phase3a_evidence,
        "python_analysis": None,
        "python_evidence": None,
    })
    inputs, _ = _write_inputs(testcase, fixture)
    run_dir = fixture["work"] / "git-tree-run"
    manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=run_dir)
    return fixture, run_dir, manifest


class Phase3RunAssemblyTests(unittest.TestCase):
    def test_optional_documentation_member_uses_additive_run_v11_contract(self):
        self.assertNotIn("documentation_evidence", MEMBER_PATHS)
        fixture = _fixture(self, {"README.md": b"# Project\nA declared purpose.\n"})
        documentation = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )
        inputs, _ = _write_inputs(self, fixture, {"documentation_evidence": documentation})
        output = fixture["work"] / "documentation-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("1.1.0", manifest["schema_version"])
        self.assertEqual("1.3.0", manifest["members"][-1]["schema_version"])
        self.assertIn(DOCUMENTATION_MEMBER_PATH, {item["path"] for item in manifest["members"]})
        self.assertEqual("1.1.0", validate_phase3_run(output, root=fixture["root"])["schema_version"])

    def test_recomputed_documentation_member_digest_does_not_hide_tampered_source_hash(self):
        fixture = _fixture(self, {"README.md": b"# Project\nA declared purpose.\n"})
        documentation = build_repository_documentation_evidence(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            selections=[("README.md", 2, 2)],
            generated_at=FIXED_TIME,
        )
        inputs, _ = _write_inputs(self, fixture, {"documentation_evidence": documentation})
        output = fixture["work"] / "tampered-documentation-run"
        assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        documentation_path = output / DOCUMENTATION_MEMBER_PATH
        tampered = json.loads(documentation_path.read_text(encoding="utf-8"))
        tampered["items"][0]["source_bytes"]["span_sha256"] = "0" * 64
        documentation_bytes = dumps_artifact(tampered).encode("utf-8")
        documentation_path.write_bytes(documentation_bytes)
        manifest_path = output / "phase3-run.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(item for item in manifest["members"] if item["path"] == DOCUMENTATION_MEMBER_PATH)
        record["sha256"] = hashlib.sha256(documentation_bytes).hexdigest()
        manifest_path.write_text(dumps_artifact(manifest), encoding="utf-8")

        with self.assertRaises(Phase3RunError):
            validate_phase3_run(output, root=fixture["root"])

    def test_pass_package_preserves_input_bytes_and_revalidates_from_disk(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, bytes_by_name = _write_inputs(self, fixture)
        output = fixture["work"] / "phase3-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("PASS", manifest["status"])
        self.assertEqual("PASS", validate_phase3_run(output, root=fixture["root"])["status"])
        self.assertEqual(bytes_by_name["project_index"], (output / MEMBER_PATHS["project_index"]).read_bytes())
        self.assertEqual(bytes_by_name["phase3a_evidence"], (output / MEMBER_PATHS["phase3a_evidence"]).read_bytes())
        manifest_text = (output / "phase3-run.json").read_text(encoding="utf-8")
        self.assertNotIn(str(fixture["root"]), manifest_text)
        self.assertTrue(all(str(path) not in manifest_text for path in inputs.values() if path is not None))

    def test_partial_g01_remains_partial_in_the_run_manifest(self):
        fixture = _fixture(self, {"unclassified.oddity": b"Unknown file.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "partial-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("PARTIAL", manifest["status"])
        self.assertEqual({"before": "PARTIAL", "after": "PARTIAL"}, manifest["e1_audit"]["g01"])
        self.assertEqual("PARTIAL", validate_phase3_run(output, root=fixture["root"])["status"])

    def test_not_run_adapters_without_applicable_files_do_not_make_run_partial(self):
        fixture = _fixture(self, {"README.md": b"No analyzed language files.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "no-adapters-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("PASS", manifest["status"])
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in manifest["e1_audit"]["adapters"].values()))

    def test_missing_adapter_for_an_applicable_source_family_is_partial(self):
        fixture = _fixture(self, {"src/Service.java": b"class Service {}\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "missing-adapter-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("PARTIAL", manifest["status"])
        self.assertEqual("NOT_RUN", manifest["e1_audit"]["adapters"]["java"]["status"])
        self.assertIn("JAVA_NOT_ANALYZED", manifest["e1_audit"]["limitations"])

    def test_java_v12_and_v13_members_remain_separate_versions(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("JDK compiler is not installed")
        fixture = _fixture(self, {"src/Api.java": b"class Api { String value() { return \"ok\"; } }\n"})
        g01 = audit_coverage(fixture["project_index"], fixture["coverage"], fixture["root"])
        source_reader, regular_paths = _build_snapshot_inputs(fixture["root"], fixture["project_index"])
        java = __import__("java_static_analysis")
        framework = __import__("java_framework_static_analysis")
        v12_analysis, v12_evidence = java.analyze_java_artifacts(
            fixture["project_index"], fixture["coverage"], fixture["stack_profile"],
            fixture["phase3a_evidence"], source_reader, regular_paths,
            g01_status=g01.status, unknown_files=g01.measurements["unknown_files"],
        )
        v13_analysis, v13_evidence = framework.analyze_java_framework_candidates(
            fixture["project_index"], fixture["coverage"], fixture["stack_profile"],
            fixture["phase3a_evidence"], v12_analysis, v12_evidence,
            source_reader, regular_paths, g01_status=g01.status,
            unknown_files=g01.measurements["unknown_files"],
        )
        inputs, _ = _write_inputs(self, fixture, {
            "java_analysis_v12": v12_analysis,
            "java_evidence_v12": v12_evidence,
            "java_analysis_v13": v13_analysis,
            "java_evidence_v13": v13_evidence,
        })
        output = fixture["work"] / "java-version-run"

        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        members = {item["path"]: item for item in manifest["members"]}
        self.assertEqual("1.2.0", members[MEMBER_PATHS["java_analysis_v12"]]["schema_version"])
        self.assertEqual("1.3.0", members[MEMBER_PATHS["java_analysis_v13"]]["schema_version"])
        self.assertNotEqual(MEMBER_PATHS["java_analysis_v12"], MEMBER_PATHS["java_analysis_v13"])
        self.assertEqual("PASS", manifest["status"])

    def test_stale_phase3a_revision_is_rejected_without_publication(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        stale = copy.deepcopy(fixture["phase3a_evidence"])
        stale["repository_revision"] = "0" * 40
        inputs, _ = _write_inputs(self, fixture, {"phase3a_evidence": stale})
        output = fixture["work"] / "mixed-snapshot-run"

        with self.assertRaises(Phase3RunError):
            assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertFalse(output.exists())

    def test_member_byte_change_is_detected_even_when_json_still_parses(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "tamper-run"
        assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)
        index_path = output / MEMBER_PATHS["project_index"]
        index_path.write_bytes(index_path.read_bytes() + b" ")

        with self.assertRaises(Phase3RunError):
            validate_phase3_run(output, root=fixture["root"])

    def test_freshness_rejects_manifest_and_member_mutation_after_full_validation(self):
        fixture = _fixture(self, {"README.md": b"Pinned source.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "freshness-inputs"
        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)
        validate_phase3_run(output, root=fixture["root"])
        manifest_path = output / "phase3-run.json"
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        member = output / MEMBER_PATHS["project_index"]
        member_bytes = member.read_bytes()
        member.write_bytes(member_bytes + b" ")
        with self.assertRaises(Phase3RunError):
            phase3_run_module.verify_phase3_run_freshness(
                output, root=fixture["root"], expected_manifest=manifest,
                expected_manifest_sha256=manifest_sha,
            )
        member.write_bytes(member_bytes)

        phase3_run_module.verify_phase3_run_freshness(
            output, root=fixture["root"], expected_manifest=manifest,
            expected_manifest_sha256=manifest_sha,
        )
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
        with self.assertRaises(Phase3RunError):
            phase3_run_module.verify_phase3_run_freshness(
                output, root=fixture["root"], expected_manifest=manifest,
                expected_manifest_sha256=manifest_sha,
            )

    def test_worktree_freshness_rejects_an_edit_and_accepts_restored_bytes(self):
        fixture = _fixture(self, {"README.md": b"Pinned source.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "worktree-freshness"
        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)
        validate_phase3_run(output, root=fixture["root"])
        manifest_sha = hashlib.sha256((output / "phase3-run.json").read_bytes()).hexdigest()
        source = fixture["root"] / "README.md"
        original = source.read_bytes()
        source.write_bytes(b"Edited after validation.\n")

        with self.assertRaises(Phase3RunError):
            phase3_run_module.verify_phase3_run_freshness(
                output, root=fixture["root"], expected_manifest=manifest,
                expected_manifest_sha256=manifest_sha,
            )

        source.write_bytes(original)
        phase3_run_module.verify_phase3_run_freshness(
            output, root=fixture["root"], expected_manifest=manifest,
            expected_manifest_sha256=manifest_sha,
        )

    def test_git_tree_freshness_uses_pinned_commit_despite_worktree_edits(self):
        fixture, output, manifest = _git_tree_run(self, {"README.md": b"Committed source.\n"})
        validate_phase3_run(output, root=fixture["root"])
        manifest_sha = hashlib.sha256((output / "phase3-run.json").read_bytes()).hexdigest()
        (fixture["root"] / "README.md").write_bytes(b"Uncommitted worktree edit.\n")

        phase3_run_module.verify_phase3_run_freshness(
            output, root=fixture["root"], expected_manifest=manifest,
            expected_manifest_sha256=manifest_sha,
        )

    def test_freshness_detects_g01_drift_and_member_change_after_g01(self):
        fixture = _fixture(self, {"README.md": b"Pinned source.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "g01-drift"
        manifest = assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)
        validate_phase3_run(output, root=fixture["root"])
        manifest_sha = hashlib.sha256((output / "phase3-run.json").read_bytes()).hexdigest()
        source = fixture["root"] / "README.md"
        real_audit = phase3_run_module.audit_coverage
        calls = 0

        def drift_between_g01_samples(*args, **kwargs):
            nonlocal calls
            result = real_audit(*args, **kwargs)
            calls += 1
            if calls == 1:
                source.write_bytes(b"Changed during final freshness validation.\n")
            return result

        with patch.object(phase3_run_module, "audit_coverage", side_effect=drift_between_g01_samples):
            with self.assertRaises(Phase3RunError):
                phase3_run_module.verify_phase3_run_freshness(
                    output, root=fixture["root"], expected_manifest=manifest,
                    expected_manifest_sha256=manifest_sha,
                )
        self.assertEqual(2, calls)

        fixture2 = _fixture(self, {"README.md": b"Pinned source.\n"})
        inputs2, _ = _write_inputs(self, fixture2)
        output2 = fixture2["work"] / "member-drift-during-g01"
        manifest2 = assemble_phase3_run(root=fixture2["root"], input_paths=inputs2, out=output2)
        validate_phase3_run(output2, root=fixture2["root"])
        manifest_sha2 = hashlib.sha256((output2 / "phase3-run.json").read_bytes()).hexdigest()
        member = output2 / MEMBER_PATHS["project_index"]
        original_member = member.read_bytes()
        calls = 0

        def change_member_after_g01(*args, **kwargs):
            nonlocal calls
            result = real_audit(*args, **kwargs)
            calls += 1
            if calls == 2:
                member.write_bytes(original_member + b" ")
            return result

        with patch.object(phase3_run_module, "audit_coverage", side_effect=change_member_after_g01):
            with self.assertRaises(Phase3RunError):
                phase3_run_module.verify_phase3_run_freshness(
                    output2, root=fixture2["root"], expected_manifest=manifest2,
                    expected_manifest_sha256=manifest_sha2,
                )
        self.assertEqual(2, calls)

    def test_path_escape_in_manifest_is_rejected(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "path-run"
        assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)
        manifest_path = output / "phase3-run.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["members"][0]["path"] = "../outside.json"
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(Phase3RunError):
            validate_phase3_run(output, root=fixture["root"])

    def test_existing_output_is_never_overwritten(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "already-there"
        output.mkdir()
        sentinel = output / "user-data.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaises(Phase3RunError):
            assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_output_inside_audited_project_is_rejected(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["root"] / "phase3-run"

        with self.assertRaises(Phase3RunError):
            assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertFalse(output.exists())

    def test_package_symlink_is_rejected(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "linked-run"
        target = fixture["work"] / "target"
        assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=target)
        try:
            os.symlink(target, output, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks are unavailable: {type(exc).__name__}")

        with self.assertRaises(Phase3RunError):
            validate_phase3_run(output, root=fixture["root"])

        self.assertTrue(output.is_symlink())

    def test_windows_reparse_point_metadata_is_treated_as_a_link(self):
        class ReparsePath:
            def lstat(self):
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)

        self.assertTrue(phase3_run_module._is_link_or_junction(ReparsePath()))

    def test_assembler_cli_uses_audit_phase3_bundle_flags_plus_out(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "cli-assembled-run"
        argv = [sys.executable, str(SCRIPTS / "assemble_phase3_run.py"), "--root", str(fixture["root"])]
        for key, path in inputs.items():
            if path is not None:
                argv.extend(["--" + key.replace("_", "-"), str(path)])
        argv.extend(["--out", str(output)])

        result = subprocess.run(
            argv, cwd=SCRIPTS, text=True, encoding="utf-8", capture_output=True, check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PASS", result.stdout)
        self.assertTrue((output / "phase3-run.json").is_file())

    def test_failed_publication_cleans_staging_and_leaves_no_manifest(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "publish-failure"

        with patch("phase3_run._publish_directory", side_effect=OSError("publish failed")):
            with self.assertRaises(Phase3RunError):
                assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        self.assertFalse(output.exists())
        self.assertEqual([], list(fixture["work"].glob(".publish-failure.phase3-staging-*")))

    def test_validator_cli_reloads_manifest_and_audits_the_supplied_root(self):
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        inputs, _ = _write_inputs(self, fixture)
        output = fixture["work"] / "cli-run"
        assemble_phase3_run(root=fixture["root"], input_paths=inputs, out=output)

        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "validate_phase3_run.py"), str(output), "--root", str(fixture["root"])],
            cwd=SCRIPTS, text=True, encoding="utf-8", capture_output=True, check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PASS", result.stdout)
        self.assertNotIn(str(fixture["root"]), result.stdout)


if __name__ == "__main__":
    unittest.main()
