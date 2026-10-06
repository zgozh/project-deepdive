#!/usr/bin/env python3
"""Contract and CLI tests for the read-only Phase 3 cross-artifact audit."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from python_static_analysis import analyze_python_artifacts  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402
from test_repository_scan import init_git_repo  # noqa: E402


FIXED_TIME = "2026-09-25T00:00:00Z"
CLI = SCRIPTS / "audit_phase3.py"
SOURCE_CANARY = "AUDIT_SOURCE_BODY_SECRET_CANARY"
EVIDENCE_CANARY = "AUDIT_EVIDENCE_SUMMARY_SECRET_CANARY"


def _api(testcase):
    spec = importlib.util.find_spec("phase3_bundle_audit")
    testcase.assertIsNotNone(spec, "Phase 3 cross-artifact audit API is missing")
    return importlib.import_module("phase3_bundle_audit")


def _fixture(testcase, source_files: dict[str, bytes]):
    temporary = tempfile.TemporaryDirectory()
    testcase.addCleanup(temporary.cleanup)
    work = Path(temporary.name)
    root = work / "repo"
    init_git_repo(root, source_files)
    scan = scan_repository(ScanOptions(
        root=root,
        snapshot_kind="worktree",
        generated_at=FIXED_TIME,
    ))
    g01 = audit_coverage(scan.project_index, scan.coverage, root)
    testcase.assertIn(g01.status, {"PASS", "PARTIAL"})
    sources = dict(source_files)

    def read_source(path, _entry):
        return sources[path]

    profile, phase3a_evidence = build_stack_artifacts(
        scan.project_index,
        scan.coverage,
        read_source,
        generated_at=FIXED_TIME,
        regular_source_paths=frozenset(sources),
        g01_status=g01.status,
        unknown_files=g01.measurements["unknown_files"],
    )
    python_analysis = None
    python_evidence = None
    if any(Path(path).suffix.lower() == ".py" for path in sources):
        python_paths = frozenset(
            path for path in sources if Path(path).suffix.lower() == ".py"
        )
        python_analysis, python_evidence = analyze_python_artifacts(
            scan.project_index,
            scan.coverage,
            profile,
            phase3a_evidence,
            read_source,
            python_paths,
            g01_status=g01.status,
            unknown_files=g01.measurements["unknown_files"],
        )
    return {
        "work": work,
        "root": root,
        "sources": sources,
        "project_index": scan.project_index,
        "coverage": scan.coverage,
        "stack_profile": profile,
        "phase3a_evidence": phase3a_evidence,
        "python_analysis": python_analysis,
        "python_evidence": python_evidence,
    }


class Phase3BundleAuditTests(unittest.TestCase):
    def test_schema_invalid_phase2_mapping_returns_input_invalid_report(self):
        api = _api(self)
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        invalid_index = copy.deepcopy(fixture["project_index"])
        invalid_index["file_count"] = "not-an-integer"
        self.assertEqual("project-index", invalid_index["artifact_kind"])
        self.assertEqual("1.1.0", invalid_index["schema_version"])

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=invalid_index,
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
        )

        self.assertEqual("FAIL", report["status"])
        self.assertIn("INPUT_INVALID", report["limitations"])

    def test_invalid_phase2_shapes_return_a_fail_report(self):
        api = _api(self)
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})

        try:
            report = api.audit_phase3_bundles(
                root=fixture["root"],
                project_index=None,
                coverage=None,
                stack_profile={},
                phase3a_evidence={},
            )
        except Exception as exc:
            self.fail(f"invalid Phase 2 inputs must return a fail report: {type(exc).__name__}")

        self.assertEqual("FAIL", report["status"])
        self.assertEqual({"before": "FAIL", "after": "FAIL"}, report["g01"])
        self.assertIn("INPUT_INVALID", report["limitations"])

    def test_present_families_report_missing_adapters_separately_from_skips(self):
        api = _api(self)
        fixture = _fixture(self, {
            "README.md": b"A small fixture.\n",
            "src/service.py": f"def value(): return '{SOURCE_CANARY}'\n".encode(),
            "src/Service.java": b"class Service {}\n",
            "frontend/src/page.tsx": b"export const Page = () => null;\n",
        })

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=fixture["python_analysis"],
            python_evidence=fixture["python_evidence"],
        )

        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual({"before": "PASS", "after": "PASS"}, report["g01"])
        self.assertEqual("PASS", report["adapters"]["python"]["status"])
        self.assertEqual("NOT_RUN", report["adapters"]["java"]["status"])
        self.assertEqual("NOT_RUN", report["adapters"]["frontend"]["status"])
        self.assertEqual(1, report["counts"]["analyzed_files"])
        self.assertEqual(2, report["counts"]["unreported_source_files"])
        self.assertIn("OUTSIDE_ANALYZER_SCOPE", report["limitations"])

    def test_no_applicable_source_family_does_not_force_partial(self):
        api = _api(self)
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
        )

        self.assertEqual("PASS", report["status"])
        self.assertEqual(0, report["counts"]["unreported_source_files"])
        self.assertEqual(1, report["counts"]["outside_analyzer_files"])
        self.assertTrue(all(
            report["adapters"][name]["status"] == "NOT_RUN"
            for name in ("python", "java", "frontend")
        ))

    def test_partial_g01_without_applicable_families_makes_overall_partial(self):
        api = _api(self)
        fixture = _fixture(self, {"unclassified.oddity": b"Unclassified file.\n"})

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
        )

        self.assertEqual({"before": "PARTIAL", "after": "PARTIAL"}, report["g01"])
        self.assertEqual("PARTIAL", report["status"])
        self.assertIn("G01_PARTIAL", report["limitations"])
        self.assertTrue(all(
            report["adapters"][name]["status"] == "NOT_RUN"
            for name in ("python", "java", "frontend")
        ))

    def test_frontend_family_detection_includes_secondary_surface(self):
        api = _api(self)

        families, tracked, covered, outside = api._family_paths(
            {"files": [{"path": "shared/module.ts"}]},
            {"entries": [{
                "path": "shared/module.ts",
                "surface": "backend",
                "secondary_surfaces": ["frontend"],
                "classification": "CLASSIFIED",
            }]},
        )

        self.assertEqual({"shared/module.ts"}, families["frontend"])
        self.assertEqual((1, 1, 0), (tracked, covered, outside))

    def test_frontend_v15_pair_is_accepted_without_widening_other_adapters(self):
        api = _api(self)
        frontend_builder = importlib.import_module("frontend_snapshot_analysis")
        source = b"export function Page() { return <main />; }\nfetch(dynamicUrl());\n"
        fixture = _fixture(self, {"frontend/src/app/page.tsx": source})
        g01 = audit_coverage(
            fixture["project_index"], fixture["coverage"], fixture["root"],
        )
        analysis, frontend_evidence = frontend_builder.analyze_frontend_artifacts(
            fixture["project_index"],
            fixture["coverage"],
            fixture["stack_profile"],
            fixture["phase3a_evidence"],
            lambda path, _entry: fixture["sources"][path],
            frozenset(fixture["sources"]),
            g01_status=g01.status,
            unknown_files=g01.measurements["unknown_files"],
            analysis_version="1.5.0",
        )

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            frontend_analysis=analysis,
            frontend_evidence=frontend_evidence,
        )

        self.assertEqual("PASS", report["status"])
        self.assertEqual("PASS", report["adapters"]["frontend"]["status"])
        self.assertEqual("NOT_RUN", report["adapters"]["python"]["status"])
        self.assertEqual("NOT_RUN", report["adapters"]["java"]["status"])

    def test_supported_python_versions_dispatch_and_unknown_version_fails_closed(self):
        api = _api(self)
        fixture = _fixture(self, {"src/service.py": b"def value(): return 7\n"})
        result_v10 = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=fixture["python_analysis"],
            python_evidence=fixture["python_evidence"],
        )
        self.assertEqual("PASS", result_v10["adapters"]["python"]["status"])

        from python_static_analysis import analyze_python_artifacts_v11

        def read_source(path, _entry):
            return fixture["sources"][path]

        analysis_v11, evidence_v11 = analyze_python_artifacts_v11(
            fixture["project_index"],
            fixture["coverage"],
            fixture["stack_profile"],
            fixture["phase3a_evidence"],
            read_source,
            frozenset(fixture["sources"]),
            g01_status="PASS",
            unknown_files=0,
        )
        result_v11 = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=analysis_v11,
            python_evidence=evidence_v11,
        )
        self.assertEqual("PASS", result_v11["adapters"]["python"]["status"])

        unsupported = copy.deepcopy(fixture["python_analysis"])
        unsupported["schema_version"] = "1.2.0"
        rejected = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=unsupported,
            python_evidence=fixture["python_evidence"],
        )
        self.assertEqual("FAIL", rejected["status"])
        self.assertIn("UNSUPPORTED_VERSION", rejected["limitations"])

    def test_stale_revision_and_conflicting_evidence_are_failures(self):
        api = _api(self)
        fixture = _fixture(self, {"src/service.py": b"def value(): return 7\n"})
        stale = copy.deepcopy(fixture["python_analysis"])
        stale["repository_revision"] = "0" * 40
        stale_report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=stale,
            python_evidence=fixture["python_evidence"],
        )
        self.assertEqual("FAIL", stale_report["status"])
        self.assertTrue({"REVISION_MISMATCH", "ADAPTER_INVALID"} & set(stale_report["limitations"]))

        conflicting_evidence = copy.deepcopy(fixture["python_evidence"])
        phase3a_ids = {item["id"] for item in fixture["phase3a_evidence"]["items"]}
        shared_record = next(
            item for item in conflicting_evidence["items"] if item["id"] in phase3a_ids
        )
        shared_record["summary"] = EVIDENCE_CANARY
        collision_report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            python_analysis=fixture["python_analysis"],
            python_evidence=conflicting_evidence,
        )
        self.assertEqual("FAIL", collision_report["status"])
        self.assertIn("EVIDENCE_ID_COLLISION", collision_report["limitations"])

    def test_second_g01_detects_worktree_drift_during_semantic_validation(self):
        api = _api(self)
        fixture = _fixture(self, {"src/service.py": b"def value(): return 7\n"})
        original_validator = api.validate_analysis_bundle

        def mutate_after_validation(*args, **kwargs):
            original_validator(*args, **kwargs)
            (fixture["root"] / "src/service.py").write_bytes(b"def value(): return 8\n")

        with patch.object(api, "validate_analysis_bundle", side_effect=mutate_after_validation):
            report = api.audit_phase3_bundles(
                root=fixture["root"],
                project_index=fixture["project_index"],
                coverage=fixture["coverage"],
                stack_profile=fixture["stack_profile"],
                phase3a_evidence=fixture["phase3a_evidence"],
                python_analysis=fixture["python_analysis"],
                python_evidence=fixture["python_evidence"],
            )

        self.assertEqual("FAIL", report["status"])
        self.assertEqual("PASS", report["g01"]["before"])
        self.assertEqual("FAIL", report["g01"]["after"])
        self.assertIn("SNAPSHOT_DRIFT", report["limitations"])

    def test_java_v13_requires_the_explicit_c1_v12_pair(self):
        api = _api(self)
        fixture = _fixture(self, {"README.md": b"Documentation only.\n"})
        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            java_analysis_v13={},
            java_evidence_v13={},
        )
        self.assertEqual("FAIL", report["status"])
        self.assertIn("PAIR_INCOMPLETE", report["limitations"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK compiler is not installed")
    def test_java_v12_and_v13_bundles_dispatch_through_both_semantic_validators(self):
        api = _api(self)
        source = (
            b"package demo;\n"
            b"import org.springframework.web.bind.annotation.GetMapping;\n"
            b"import org.springframework.web.bind.annotation.RestController;\n"
            b"@RestController class Api { @GetMapping(\"/items\") String list() { return \"ok\"; } }\n"
        )
        fixture = _fixture(self, {"src/Api.java": source})
        inputs = fixture["work"] / "inputs"
        inputs.mkdir()
        paths = {}
        for name, document in (
            ("project-index", fixture["project_index"]),
            ("coverage", fixture["coverage"]),
            ("stack-profile", fixture["stack_profile"]),
            ("phase3a-evidence", fixture["phase3a_evidence"]),
        ):
            paths[name] = inputs / f"{name}.json"
            paths[name].write_text(dumps_artifact(document), encoding="utf-8")
        c1_out = fixture["work"] / "java-v12"
        c1 = subprocess.run(
            [
                sys.executable, str(SCRIPTS / "analyze_java.py"),
                "--root", str(fixture["root"]),
                "--index", str(paths["project-index"]),
                "--coverage", str(paths["coverage"]),
                "--stack-profile", str(paths["stack-profile"]),
                "--evidence", str(paths["phase3a-evidence"]),
                "--out", str(c1_out),
            ],
            cwd=SCRIPTS,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, c1.returncode, c1.stdout + c1.stderr)

        c2_out = fixture["work"] / "java-v13"
        c2 = subprocess.run(
            [
                sys.executable, str(SCRIPTS / "analyze_java_frameworks.py"),
                "--root", str(fixture["root"]),
                "--index", str(paths["project-index"]),
                "--coverage", str(paths["coverage"]),
                "--stack-profile", str(paths["stack-profile"]),
                "--phase3a-evidence", str(paths["phase3a-evidence"]),
                "--java-analysis-v1.2", str(c1_out / "static-analysis.json"),
                "--java-evidence-v1.2", str(c1_out / "evidence.json"),
                "--out", str(c2_out),
            ],
            cwd=SCRIPTS,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, c2.returncode, c2.stdout + c2.stderr)

        report = api.audit_phase3_bundles(
            root=fixture["root"],
            project_index=fixture["project_index"],
            coverage=fixture["coverage"],
            stack_profile=fixture["stack_profile"],
            phase3a_evidence=fixture["phase3a_evidence"],
            java_analysis_v12=load_artifact(c1_out / "static-analysis.json"),
            java_evidence_v12=load_artifact(c1_out / "evidence.json"),
            java_analysis_v13=load_artifact(c2_out / "static-analysis.json"),
            java_evidence_v13=load_artifact(c2_out / "evidence.json"),
        )

        self.assertEqual("PASS", report["status"], report)
        self.assertEqual("PASS", report["adapters"]["java"]["status"])
        self.assertEqual("1.3.0", report["adapters"]["java"]["version"])
        self.assertEqual(1, report["adapters"]["java"]["analyzed_files"])

    def test_cli_emits_only_the_summary_and_leaves_inputs_and_target_unchanged(self):
        _api(self)
        fixture = _fixture(self, {
            "src/service.py": f"def value(): return '{SOURCE_CANARY}'\n".encode(),
            "src/Service.java": b"class Service {}\n",
        })
        artifact_dir = fixture["work"] / "artifacts"
        artifact_dir.mkdir()
        artifact_paths = {}
        for name, value in (
            ("project-index", fixture["project_index"]),
            ("coverage", fixture["coverage"]),
            ("stack-profile", fixture["stack_profile"]),
            ("phase3a-evidence", fixture["phase3a_evidence"]),
            ("python-analysis", fixture["python_analysis"]),
            ("python-evidence", fixture["python_evidence"]),
        ):
            path = artifact_dir / f"{name}.json"
            path.write_text(dumps_artifact(value), encoding="utf-8")
            artifact_paths[name] = path
        before = {
            path: path.read_bytes()
            for path in [*artifact_paths.values(), *(fixture["root"] / name for name in fixture["sources"])]
        }
        command = [
            sys.executable, str(CLI),
            "--root", str(fixture["root"]),
            "--project-index", str(artifact_paths["project-index"]),
            "--coverage", str(artifact_paths["coverage"]),
            "--stack-profile", str(artifact_paths["stack-profile"]),
            "--phase3a-evidence", str(artifact_paths["phase3a-evidence"]),
            "--python-analysis", str(artifact_paths["python-analysis"]),
            "--python-evidence", str(artifact_paths["python-evidence"]),
        ]
        result = subprocess.run(
            command,
            cwd=SCRIPTS,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("status=PARTIAL", result.stdout)
        self.assertNotIn(SOURCE_CANARY, result.stdout + result.stderr)
        self.assertNotIn(EVIDENCE_CANARY, result.stdout + result.stderr)
        for path, payload in before.items():
            self.assertEqual(payload, path.read_bytes())

        conflicting = copy.deepcopy(fixture["python_evidence"])
        phase3a_ids = {item["id"] for item in fixture["phase3a_evidence"]["items"]}
        shared_record = next(item for item in conflicting["items"] if item["id"] in phase3a_ids)
        shared_record["summary"] = EVIDENCE_CANARY
        artifact_paths["python-evidence"].write_text(
            dumps_artifact(conflicting), encoding="utf-8",
        )
        forged_input_bytes = artifact_paths["python-evidence"].read_bytes()
        rejected = subprocess.run(
            command,
            cwd=SCRIPTS,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(1, rejected.returncode)
        self.assertIn("EVIDENCE_ID_COLLISION", rejected.stdout)
        self.assertNotIn(SOURCE_CANARY, rejected.stdout + rejected.stderr)
        self.assertNotIn(EVIDENCE_CANARY, rejected.stdout + rejected.stderr)
        self.assertEqual(forged_input_bytes, artifact_paths["python-evidence"].read_bytes())


if __name__ == "__main__":
    unittest.main()
