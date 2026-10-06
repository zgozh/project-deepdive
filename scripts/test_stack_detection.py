#!/usr/bin/env python3
"""Regression tests for deterministic, declaration-only stack detection."""

import importlib
import hashlib
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = "2026-09-23T00:00:00Z"
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from coverage_audit import audit_coverage  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from test_repository_scan import init_git_repo  # noqa: E402


def _detector(testcase):
    try:
        return importlib.import_module("stack_detection")
    except ImportError as exc:
        testcase.fail(f"stack_detection implementation is missing: {exc}")


def _scan(root: Path, snapshot: str = "worktree"):
    return scan_repository(ScanOptions(
        root=root,
        snapshot_kind=snapshot,
        generated_at=FIXED_TIME,
    ))


def _audit_kwargs(root: Path, project_index, coverage):
    audit = audit_coverage(project_index, coverage, root)
    if audit.status == "FAIL":
        raise AssertionError(f"fixture Phase 2 audit failed: {audit.violations}")
    return {
        "g01_status": audit.status,
        "unknown_files": audit.measurements["unknown_files"],
    }


def _expected_source_metadata(project_index, coverage, *, g01_status="PASS", unknown_files=0):
    return {
        "snapshot_kind": project_index["project"]["snapshot_kind"],
        "g01_status": g01_status,
        "unknown_files": unknown_files,
        "project_index_sha256": hashlib.sha256(dumps_artifact(project_index).encode("utf-8")).hexdigest(),
        "coverage_sha256": hashlib.sha256(dumps_artifact(coverage).encode("utf-8")).hexdigest(),
    }


def _build(root: Path, files: dict[str, bytes], *, snapshot: str = "worktree"):
    init_git_repo(root, files)
    artifacts = _scan(root, snapshot)
    content = dict(files)

    def read_manifest(path, _entry):
        return content[path]

    detector = _detector(unittest.TestCase())
    return detector.build_stack_artifacts(
        artifacts.project_index,
        artifacts.coverage,
        read_manifest,
        generated_at=FIXED_TIME,
        **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
    )


class StackDetectionTests(unittest.TestCase):
    def detect(self, files):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            return _build(root, files)

    @staticmethod
    def by_name(profile):
        return {item["name"]: item for item in profile["detections"]}

    def test_python_manifest_emits_verified_python_and_langgraph_evidence(self):
        profile, evidence = self.detect({
            "pyproject.toml": (
                b"[project]\nname = 'agent-demo'\nrequires-python = '>=3.12'\n"
                b"dependencies = ['langgraph>=0.2']\n"
            ),
            "src/agent.py": b"def run():\n    return 'not executed'\n",
        })

        detections = self.by_name(profile)
        self.assertEqual("VERIFIED", detections["Python"]["status"])
        self.assertEqual("VERIFIED", detections["LangGraph"]["status"])
        self.assertEqual(0.95, detections["LangGraph"]["confidence"])
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        citation = evidence_by_id[detections["LangGraph"]["evidence_ids"][0]]
        self.assertEqual("E2", citation["level"])
        self.assertEqual("dependency", citation["kind"])
        self.assertEqual("pyproject.toml", citation["locator"]["path"])
        self.assertEqual("project.dependencies[0]", citation["locator"]["symbol"])
        self.assertIn("declares", citation["summary"].lower())
        self.assertEqual(profile["repository_revision"], evidence["repository_revision"])
        self.assertEqual(profile["generated_at"], evidence["generated_at"])
        self.assertEqual("1.1.0", profile["schema_version"])
        self.assertEqual("1.1.0", evidence["schema_version"])
        metadata = profile["source_metadata"]
        self.assertEqual(metadata, evidence["source_metadata"])
        self.assertEqual("worktree", metadata["snapshot_kind"])
        self.assertEqual("PASS", metadata["g01_status"])
        self.assertEqual(0, metadata["unknown_files"])
        self.assertTrue(all(
            len(metadata[key]) == 64 and set(metadata[key]) <= set("0123456789abcdef")
            for key in ("project_index_sha256", "coverage_sha256")
        ))

    def test_python_optional_and_poetry_group_dependencies_have_exact_locators(self):
        profile, evidence = self.detect({
            "pyproject.toml": (
                b"[project]\nname='agent-demo'\n"
                b"[project.optional-dependencies]\n'qa.extra'=['fastapi>=0.1']\n"
                b"[tool.poetry.group.'dev.extra'.dependencies]\nlanggraph='^0.2'\n"
                b"[tool.poetry.dependencies]\n'openai.agents'='^1.0'\n"
            ),
        })

        detections = self.by_name(profile)
        citations = {
            item["id"]: item["locator"]["symbol"] for item in evidence["items"]
        }
        hash_key = lambda key: '[key-sha256:' + hashlib.sha256(key.encode("utf-8")).hexdigest() + ']'
        self.assertEqual(
            f"project.optional-dependencies{hash_key('qa.extra')}[0]",
            citations[detections["FastAPI"]["evidence_ids"][0]],
        )
        self.assertEqual(
            f"tool.poetry.group{hash_key('dev.extra')}.dependencies{hash_key('langgraph')}",
            citations[detections["LangGraph"]["evidence_ids"][0]],
        )
        self.assertEqual(
            f"tool.poetry.dependencies{hash_key('openai.agents')}",
            citations[detections["OpenAI Agents SDK"]["evidence_ids"][0]],
        )

    def test_dynamic_toml_key_sentinel_is_not_serialized(self):
        sentinel = "PRIVATE_GROUP_KEY_CANARY"
        profile, evidence = self.detect({
            "pyproject.toml": (
                "[project]\nname='agent-demo'\n"
                "[project.optional-dependencies]\n"
                f"'{sentinel}'=['fastapi>=0.1']\n"
            ).encode("utf-8"),
        })

        serialized = dumps_artifact(profile) + dumps_artifact(evidence)
        self.assertFalse(sentinel in serialized, "dynamic TOML group key leaked into artifacts")
        citation_ids = self.by_name(profile)["FastAPI"]["evidence_ids"]
        citations = [item for item in evidence["items"] if item["id"] in citation_ids]
        expected = '[key-sha256:' + hashlib.sha256(sentinel.encode("utf-8")).hexdigest() + ']'
        self.assertTrue(any(expected in item["locator"]["symbol"] for item in citations))

    def test_requirements_dependency_evidence_uses_source_line(self):
        profile, evidence = self.detect({
            "requirements-dev.txt": b"# toolchain\nfastapi>=0.1\n",
        })

        fastapi = self.by_name(profile)["FastAPI"]
        citation = next(
            item for item in evidence["items"] if item["id"] == fastapi["evidence_ids"][0]
        )
        self.assertEqual("requirements-dev.txt:line:2", citation["locator"]["symbol"])

    def test_java_maven_and_spring_declarations_are_source_located(self):
        pom = b"""<?xml version='1.0'?>
<project xmlns='http://maven.apache.org/POM/4.0.0'>
  <modelVersion>4.0.0</modelVersion>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
  <dependencies><dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
  </dependency></dependencies>
</project>
"""
        profile, evidence = self.detect({
            "pom.xml": pom,
            "src/main/java/App.java": b"class App {}\n",
        })

        detections = self.by_name(profile)
        self.assertEqual("VERIFIED", detections["Java"]["status"])
        self.assertEqual("VERIFIED", detections["Maven"]["status"])
        self.assertEqual("VERIFIED", detections["Spring Boot"]["status"])
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        citation = evidence_by_id[detections["Spring Boot"]["evidence_ids"][0]]
        self.assertEqual("E2", citation["level"])
        self.assertEqual("pom.xml", citation["locator"]["path"])
        self.assertEqual(
            "project.dependencies.dependency[0]",
            citation["locator"]["symbol"],
        )

    def test_maven_project_requires_supported_model_version_and_matching_namespace(self):
        detector = _detector(self)
        malformed = (
            b"<project/>\n",
            b"<project><modelVersion>not-a-version</modelVersion></project>\n",
            b"<project xmlns='http://maven.apache.org/POM/4.0.0'>"
            b"<modelVersion>4.1.0</modelVersion></project>\n",
        )
        for payload in malformed:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "repo"
                init_git_repo(root, {"pom.xml": payload})
                artifacts = _scan(root)

                with self.assertRaisesRegex(detector.StackDetectionError, "Maven|modelVersion|malformed"):
                    detector.build_stack_artifacts(
                        artifacts.project_index,
                        artifacts.coverage,
                        lambda _path, _entry: payload,
                        generated_at=FIXED_TIME,
                        **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
                    )

    def test_foreign_namespace_model_version_cannot_create_maven_or_spring_evidence(self):
        payload = b"""<project xmlns='http://maven.apache.org/POM/4.0.0'
 xmlns:x='urn:untrusted'>
  <x:modelVersion>4.0.0</x:modelVersion>
  <x:dependencies><x:dependency>
    <x:groupId>org.springframework.boot</x:groupId>
    <x:artifactId>spring-boot-starter-web</x:artifactId>
  </x:dependency></x:dependencies>
</project>"""
        detector = _detector(self)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            init_git_repo(root, {"pom.xml": payload})
            artifacts = _scan(root)

            with self.assertRaisesRegex(detector.StackDetectionError, "Maven|modelVersion|malformed"):
                detector.build_stack_artifacts(
                    artifacts.project_index,
                    artifacts.coverage,
                    lambda _path, _entry: payload,
                    generated_at=FIXED_TIME,
                    **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
                )

    def test_foreign_namespace_dependencies_and_properties_are_not_maven_claims(self):
        payload = b"""<project xmlns='http://maven.apache.org/POM/4.0.0'
 xmlns:x='urn:untrusted'>
  <modelVersion>4.0.0</modelVersion>
  <x:properties><x:maven.compiler.release>17</x:maven.compiler.release></x:properties>
  <x:dependencies><x:dependency>
    <x:groupId>org.springframework.boot</x:groupId>
    <x:artifactId>spring-boot-starter-web</x:artifactId>
  </x:dependency></x:dependencies>
</project>"""
        profile, _evidence = self.detect({"pom.xml": payload})
        names = set(self.by_name(profile))

        self.assertIn("Maven", names)
        self.assertNotIn("Java", names)
        self.assertNotIn("Spring Boot", names)

    def test_java_compiler_source_evidence_points_to_source_property(self):
        profile, evidence = self.detect({
            "pom.xml": (
                b"<project><modelVersion>4.0.0</modelVersion>"
                b"<properties><maven.compiler.source>11</maven.compiler.source>"
                b"</properties></project>"
            ),
        })

        java = self.by_name(profile)["Java"]
        citation = next(
            item for item in evidence["items"] if item["id"] == java["evidence_ids"][0]
        )
        self.assertEqual("properties.maven.compiler.source", citation["locator"]["symbol"])

    def test_node_package_manifest_detects_node_react_vite_without_exposing_scripts(self):
        package = b'''{
  "name": "web",
  "packageManager": "npm@10.0.0",
  "scripts": {"build": "echo PRIVATE_BUILD_CANARY"},
  "dependencies": {"react": "18.3.1"},
  "devDependencies": {"vite": "5.4.0"}
}
'''
        profile, evidence = self.detect({
            "package.json": package,
            "package-lock.json": b'{"name":"web","lockfileVersion":3,"packages":{}}\n',
            "src/main.tsx": b"export const View = () => null;\n",
        })

        detections = self.by_name(profile)
        self.assertEqual("VERIFIED", detections["Node.js"]["status"])
        self.assertEqual("VERIFIED", detections["React"]["status"])
        self.assertEqual("VERIFIED", detections["Vite"]["status"])
        self.assertEqual("VERIFIED", detections["npm"]["status"])
        evidence_by_id = {item["id"]: item for item in evidence["items"]}
        react_citation = evidence_by_id[detections["React"]["evidence_ids"][0]]
        vite_citation = evidence_by_id[detections["Vite"]["evidence_ids"][0]]
        self.assertEqual("package.json.dependencies.react", react_citation["locator"]["symbol"])
        self.assertEqual("package.json.devDependencies.vite", vite_citation["locator"]["symbol"])
        serialized = dumps_artifact(profile) + dumps_artifact(evidence)
        self.assertNotIn("PRIVATE_BUILD_CANARY", serialized)
        self.assertNotIn("scripts.build", serialized)
        self.assertTrue(all(item["level"] in ("E1", "E2") for item in evidence["items"]))
        self.assertTrue(all(item["kind"] != "runtime" for item in evidence["items"]))
        self.assertTrue(all(
            "declares" in item["summary"].lower()
            for item in evidence["items"] if item["level"] == "E2"
        ))

    def test_source_suffix_only_is_likely_and_uses_e1_evidence(self):
        profile, evidence = self.detect({"src/service.py": b"pass\n"})

        python = self.by_name(profile)["Python"]
        self.assertEqual("LIKELY", python["status"])
        self.assertEqual(0.65, python["confidence"])
        citation = evidence["items"][0]
        self.assertEqual("E1", citation["level"])
        self.assertEqual("source", citation["kind"])
        self.assertEqual("src/service.py", citation["locator"]["path"])

    def test_builder_requires_explicit_g01_audit_result(self):
        detector = _detector(self)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            files = {"README.md": b"fixture\n"}
            init_git_repo(root, files)
            artifacts = _scan(root)

            with self.assertRaisesRegex(TypeError, "g01_status|unknown_files"):
                detector.build_stack_artifacts(
                    artifacts.project_index,
                    artifacts.coverage,
                    lambda path, _entry: files[path],
                    generated_at=FIXED_TIME,
                )

    def test_missing_evidence_reference_is_rejected(self):
        profile, evidence = self.detect({
            "pyproject.toml": b"[project]\nname='x'\nrequires-python='>=3.12'\n",
        })
        profile["detections"][0]["evidence_ids"] = ["EVID-does-not-exist"]

        detector = _detector(self)
        with self.assertRaisesRegex(detector.StackDetectionError, "evidence"):
            detector.validate_evidence_references(profile, evidence)

    def test_v11_source_metadata_is_required_and_must_match_across_artifacts(self):
        profile, evidence = self.detect({
            "pyproject.toml": b"[project]\nname='x'\nrequires-python='>=3.12'\n",
        })
        metadata = {
            "snapshot_kind": "worktree",
            "g01_status": "PASS",
            "unknown_files": 0,
            "project_index_sha256": "a" * 64,
            "coverage_sha256": "b" * 64,
        }
        profile["schema_version"] = evidence["schema_version"] = "1.1.0"
        profile["source_metadata"] = dict(metadata)
        evidence["source_metadata"] = dict(metadata)
        detector = _detector(self)

        with self.assertRaisesRegex(detector.StackDetectionError, "source metadata|metadata"):
            profile.pop("source_metadata")
            detector.validate_evidence_references(profile, evidence)

        profile["source_metadata"] = dict(metadata)
        evidence["source_metadata"]["unknown_files"] = 1
        with self.assertRaisesRegex(detector.StackDetectionError, "source metadata|metadata"):
            detector.validate_evidence_references(profile, evidence)

    def test_v11_input_digest_tampering_fails_against_phase2_pair(self):
        detector = _detector(self)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            files = {"pyproject.toml": b"[project]\nname='digest-check'\n"}
            init_git_repo(root, files)
            artifacts = _scan(root)
            expected = _expected_source_metadata(artifacts.project_index, artifacts.coverage)
            profile, evidence = detector.build_stack_artifacts(
                artifacts.project_index,
                artifacts.coverage,
                lambda path, _entry: files[path],
                generated_at=FIXED_TIME,
                **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
            )
            profile["source_metadata"]["project_index_sha256"] = "f" * 64
            evidence["source_metadata"]["project_index_sha256"] = "f" * 64

            with self.assertRaisesRegex(detector.StackDetectionError, "digest|source metadata|Phase 2"):
                detector.validate_evidence_references(
                    profile, evidence, expected_source_metadata=expected,
                )

    def test_v10_stack_and_evidence_fixtures_remain_loadable(self):
        fixtures = SKILL_ROOT / "tests" / "fixtures" / "artifacts" / "v1"
        profile = load_artifact(fixtures / "stack-profile.json")
        evidence = load_artifact(fixtures / "evidence.json")

        self.assertEqual("1.0.0", profile["schema_version"])
        self.assertEqual("1.0.0", evidence["schema_version"])
        self.assertNotIn("source_metadata", profile)
        self.assertNotIn("source_metadata", evidence)

    def test_validation_rejects_runtime_evidence_and_runtime_claims(self):
        profile, evidence = self.detect({
            "pyproject.toml": b"[project]\nname='x'\nrequires-python='>=3.12'\n",
        })
        detector = _detector(self)
        evidence["items"][0]["level"] = "E2"
        evidence["items"][0]["kind"] = "config"
        evidence["items"][0]["summary"] = "Python is running in production."

        with self.assertRaisesRegex(detector.StackDetectionError, "runtime|declaration"):
            detector.validate_evidence_references(profile, evidence)

    def test_invalid_toml_duplicate_json_and_xml_entities_fail_without_echoing_body(self):
        cases = (
            ("pyproject.toml", b"[project\napi_key='PRIVATE_CANARY'\n"),
            ("package.json", b'{"dependencies":{},"dependencies":{"react":"1"}}'),
            ("package.json", b'{"dependencies":{"react":{}}}'),
            ("package-lock.json", b'{"lockfileVersion":3}'),
            ("pom.xml", b"<!DOCTYPE project [<!ENTITY x 'PRIVATE_CANARY'>]><project>&x;</project>"),
        )
        detector = _detector(self)
        for path, payload in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "repo"
                init_git_repo(root, {path: payload})
                artifacts = _scan(root)
                with self.assertRaises(detector.StackDetectionError) as raised:
                    detector.build_stack_artifacts(
                        artifacts.project_index,
                        artifacts.coverage,
                        lambda _path, _entry: payload,
                        generated_at=FIXED_TIME,
                        **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
                    )
                self.assertNotIn("PRIVATE_CANARY", str(raised.exception))

    def test_oversized_manifest_is_rejected_before_parsing(self):
        detector = _detector(self)
        payload = b"x" * (detector.MAX_MANIFEST_BYTES + 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            init_git_repo(root, {"package.json": payload})
            artifacts = _scan(root)

            with self.assertRaisesRegex(detector.StackDetectionError, "size|limit|large"):
                detector.build_stack_artifacts(
                    artifacts.project_index,
                    artifacts.coverage,
                    lambda _path, _entry: payload,
                    generated_at=FIXED_TIME,
                    **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
                )

    def test_outputs_are_byte_stable_for_identical_phase2_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            files = {
                "package.json": b'{"dependencies":{"react":"1","vite":"2"}}',
                "src/a.js": b"",
                "src/b.py": b"",
            }
            init_git_repo(root, files)
            artifacts = _scan(root)
            detector = _detector(self)
            reader = lambda path, _entry: files[path]
            profile, evidence = detector.build_stack_artifacts(
                artifacts.project_index,
                artifacts.coverage,
                reader,
                generated_at=FIXED_TIME,
                **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
            )
            reversed_files = dict(reversed(list(files.items())))
            again_profile, again_evidence = detector.build_stack_artifacts(
                artifacts.project_index,
                artifacts.coverage,
                lambda path, _entry: reversed_files[path],
                generated_at=FIXED_TIME,
                **_audit_kwargs(root, artifacts.project_index, artifacts.coverage),
            )

        self.assertEqual(dumps_artifact(profile), dumps_artifact(again_profile))
        self.assertEqual(dumps_artifact(evidence), dumps_artifact(again_evidence))

    def test_contradictory_input_revisions_are_rejected(self):
        profile, evidence = self.detect({"pyproject.toml": b"[project]\nname='x'\n"})
        detector = _detector(self)
        inputs = self.detect({"pyproject.toml": b"[project]\nname='y'\n"})
        other_revision = inputs[0]["repository_revision"]
        self.assertNotEqual(profile["repository_revision"], other_revision)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            init_git_repo(root, {"pyproject.toml": b"[project]\nname='x'\n"})
            artifacts = _scan(root)
            coverage = dict(artifacts.coverage)
            coverage["repository_revision"] = other_revision
            with self.assertRaisesRegex(detector.StackDetectionError, "revision|audit"):
                detector.build_stack_artifacts(
                    artifacts.project_index,
                    coverage,
                    lambda _path, _entry: b"[project]\nname='x'\n",
                    generated_at=FIXED_TIME,
                    g01_status="PASS",
                    unknown_files=0,
                )

    def test_contradictory_input_timestamps_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            files = {"pyproject.toml": b"[project]\nname='x'\n"}
            init_git_repo(root, files)
            artifacts = _scan(root)
            coverage = dict(artifacts.coverage)
            coverage["generated_at"] = "2026-09-22T00:00:00Z"

            detector = _detector(self)
            with self.assertRaisesRegex(detector.StackDetectionError, "timestamp"):
                detector.build_stack_artifacts(
                    artifacts.project_index,
                    coverage,
                    lambda path, _entry: files[path],
                    generated_at=FIXED_TIME,
                    g01_status="PASS",
                    unknown_files=0,
                )

    def test_transitive_package_lock_dependency_is_not_claimed_as_direct(self):
        profile, _evidence = self.detect({
            "package.json": b'{"name":"app","dependencies":{}}',
            "package-lock.json": (
                b'{"name":"app","lockfileVersion":3,"packages":'
                b'{"":{"name":"app"},"node_modules/react":{"version":"18"}}}'
            ),
        })

        self.assertNotIn("React", self.by_name(profile))


if __name__ == "__main__":
    unittest.main()
