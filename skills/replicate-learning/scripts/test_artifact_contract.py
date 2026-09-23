#!/usr/bin/env python3
"""Tests for Project DeepDive's versioned intermediate artifact contract."""

import copy
import json
import hashlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
SCHEMA_ROOT = SKILL_ROOT / "schemas" / "v1"
FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "artifacts" / "v1"
CLI = SKILL_ROOT / "scripts" / "validate_artifact.py"
EXPECTED_KINDS = {
    "project-index",
    "stack-profile",
    "evidence",
    "coverage",
    "knowledge-graph",
    "curriculum",
    "quality-report",
}
# Phase 2 owns the repository scanner.  Only the two artifact kinds that the
# scanner emits gain a compatible v1 minor version; every other kind stays at
# exactly 1.0.0 so a new minor cannot be enabled globally by accident.
SCANNER_KINDS = {"project-index", "coverage"}
SUPPORTED_V1_VERSIONS = ["1.0.0", "1.1.0"]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_contract import (  # noqa: E402
    ARTIFACT_KINDS,
    ArtifactValidationError,
    dumps_artifact,
    load_artifact,
    schema_path,
    validate_artifact,
    validate_schema_document,
)


def minimal_artifacts():
    common = {
        "schema_version": "1.0.0",
        "repository_revision": "1463a06437fc903edec24722ecbb686a46d8f9da",
        "generated_at": "2026-09-22T00:00:00Z",
    }
    return {
        "project-index": {
            **common,
            "artifact_kind": "project-index",
            "project": {"name": "replicate-learning", "root": ".", "vcs": "git", "dirty": False, "snapshot_kind": "worktree"},
            "file_count": 1,
            "files": [{"path": "README.md", "bytes": 28229, "sha256": "0" * 64, "tracked": True}],
        },
        "stack-profile": {**common, "artifact_kind": "stack-profile", "detections": []},
        "evidence": {**common, "artifact_kind": "evidence", "items": []},
        "coverage": {
            **common,
            "artifact_kind": "coverage",
            "tracked_file_count": 1,
            "unknown_count": 0,
            "entries": [{"path": "README.md", "surface": "documentation", "classification": "COVERED", "teaching_status": "LOCATED"}],
        },
        "knowledge-graph": {**common, "artifact_kind": "knowledge-graph", "nodes": [], "edges": []},
        "curriculum": {**common, "artifact_kind": "curriculum", "learner_profile": "BEGINNER", "units": []},
        "quality-report": {
            **common,
            "artifact_kind": "quality-report",
            "mode": "FAST",
            "status": "NOT_RUN",
            "gates": [],
            "critical_gaps": ["Quality gates have not run."],
            "warnings": [],
        },
    }


class SchemaDocumentTests(unittest.TestCase):
    def test_v1_contains_exactly_the_seven_phase_one_schemas(self):
        actual = {path.name.removesuffix(".schema.json") for path in SCHEMA_ROOT.glob("*.schema.json")}

        self.assertEqual(EXPECTED_KINDS, actual)

    def test_every_schema_is_parseable_and_requires_common_identity_fields(self):
        for kind in sorted(EXPECTED_KINDS):
            with self.subTest(kind=kind):
                path = SCHEMA_ROOT / f"{kind}.schema.json"
                schema = json.loads(path.read_text(encoding="utf-8"))

                self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
                self.assertEqual(f"https://project-deepdive.dev/schemas/v1/{kind}.schema.json", schema["$id"])
                self.assertEqual("object", schema["type"])
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(
                    {"artifact_kind", "schema_version", "repository_revision", "generated_at"},
                    {"artifact_kind", "schema_version", "repository_revision", "generated_at"}
                    & set(schema["required"]),
                )
                self.assertEqual({"const": kind}, schema["properties"]["artifact_kind"])
                if kind in SCANNER_KINDS:
                    self.assertEqual(
                        {"enum": SUPPORTED_V1_VERSIONS},
                        schema["properties"]["schema_version"],
                    )
                else:
                    self.assertEqual({"const": "1.0.0"}, schema["properties"]["schema_version"])


class ArtifactValidationTests(unittest.TestCase):
    def test_registry_matches_the_public_v1_schema_set(self):
        self.assertEqual(EXPECTED_KINDS, set(ARTIFACT_KINDS))
        for kind in EXPECTED_KINDS:
            self.assertTrue(schema_path(kind, "1.0.0").is_file())

    def test_project_index_accepts_legacy_1_0_and_scanner_1_1(self):
        legacy = minimal_artifacts()["project-index"]
        self.assertIs(legacy, validate_artifact(legacy))

        current = copy.deepcopy(legacy)
        current["schema_version"] = "1.1.0"
        current["files"][0].update({
            "content_kind": "text",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })
        self.assertIs(current, validate_artifact(current))

    def test_coverage_accepts_legacy_1_0_and_scanner_1_1(self):
        legacy = minimal_artifacts()["coverage"]
        self.assertIs(legacy, validate_artifact(legacy))

        current = copy.deepcopy(legacy)
        current["schema_version"] = "1.1.0"
        current["classification_policy_version"] = "1.0.0"
        current["entries"][0].update({
            "secondary_surfaces": [],
            "rule_id": "path:test",
        })
        self.assertIs(current, validate_artifact(current))

    def test_unlisted_v1_minor_versions_fail_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.2.0"
        with self.assertRaisesRegex(ArtifactValidationError, "schema_version"):
            validate_artifact(artifact)

    def test_scanner_minor_is_not_enabled_for_other_artifact_kinds(self):
        artifact = minimal_artifacts()["evidence"]
        artifact["schema_version"] = "1.1.0"

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
            validate_artifact(artifact)

    def test_project_index_v1_1_rejects_unknown_typing_vocabulary(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.1.0"
        artifact["files"][0].update({
            "content_kind": "hologram",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.files\[0\]\.content_kind"):
            validate_artifact(artifact)

    def test_scanner_entries_keep_every_legacy_required_field(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.1.0"
        artifact["files"][0].update({
            "content_kind": "text",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })
        del artifact["files"][0]["tracked"]

        with self.assertRaisesRegex(ArtifactValidationError, r"tracked: required property is missing"):
            validate_artifact(artifact)

    def test_coverage_secondary_surfaces_reuse_the_primary_surface_vocabulary(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["schema_version"] = "1.1.0"
        artifact["entries"][0].update({
            "secondary_surfaces": ["imaginary-surface"],
            "rule_id": "path:test",
        })

        with self.assertRaisesRegex(ArtifactValidationError, r"secondary_surfaces\[0\]"):
            validate_artifact(artifact)

    def test_minimal_artifact_for_every_kind_is_valid(self):
        for kind, artifact in minimal_artifacts().items():
            with self.subTest(kind=kind):
                self.assertIs(artifact, validate_artifact(artifact))

    def test_missing_required_field_reports_its_json_path(self):
        artifact = minimal_artifacts()["project-index"]
        del artifact["generated_at"]

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.generated_at: required property is missing"):
            validate_artifact(artifact)

    def test_nested_type_error_reports_the_nested_json_path(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["project"]["dirty"] = 1

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.project\.dirty: expected boolean"):
            validate_artifact(artifact)

    def test_unknown_artifact_kind_fails_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["artifact_kind"] = "imaginary-report"

        with self.assertRaisesRegex(ArtifactValidationError, "unsupported artifact_kind"):
            validate_artifact(artifact)

    def test_unsupported_major_version_fails_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "2.0.0"

        with self.assertRaisesRegex(ArtifactValidationError, "unsupported schema major version: 2"):
            validate_artifact(artifact)

    def test_unexpected_property_is_rejected(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["surprise"] = True

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.surprise: unexpected property"):
            validate_artifact(artifact)

    def test_partial_coverage_can_record_unknown_until_the_final_gate_resolves_it(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["unknown_count"] = 1
        artifact["entries"][0]["classification"] = "UNKNOWN"

        self.assertIs(artifact, validate_artifact(artifact))

    def test_canonical_serialization_round_trips_stably(self):
        artifact = minimal_artifacts()["evidence"]

        first = dumps_artifact(artifact)
        second = dumps_artifact(json.loads(first))

        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n"))
        self.assertLess(first.index('"artifact_kind"'), first.index('"schema_version"'))

    def test_canonical_serialization_refuses_non_finite_numbers(self):
        artifact = minimal_artifacts()["stack-profile"]
        artifact["detections"] = [{
            "category": "language",
            "name": "Python",
            "status": "LIKELY",
            "confidence": math.nan,
            "evidence_ids": [],
        }]

        with self.assertRaises(ValueError):
            dumps_artifact(artifact)

    def test_open_objects_still_reject_non_json_values_and_keys(self):
        artifact = minimal_artifacts()["knowledge-graph"]
        artifact["nodes"] = [{
            "id": "node:one",
            "type": "Project",
            "label": "One",
            "evidence_ids": [],
            "properties": {"bad_value": {1, 2}},
        }]
        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.nodes\[0\]\.properties\.bad_value: expected JSON value"):
            validate_artifact(artifact)

        artifact["nodes"][0]["properties"] = {1: "integer key"}
        with self.assertRaisesRegex(ArtifactValidationError, r"object keys must be strings"):
            validate_artifact(artifact)

    def test_open_objects_reject_non_finite_floats(self):
        artifact = minimal_artifacts()["quality-report"]
        artifact["gates"] = [{
            "id": "G01",
            "result": "PASS",
            "measurements": {"ratio": math.nan},
            "failures": [],
            "warnings": [],
            "evidence_ids": [],
            "recommended_repair": "",
        }]

        with self.assertRaisesRegex(ArtifactValidationError, r"measurements\.ratio: expected finite number"):
            validate_artifact(artifact)

    def test_arbitrarily_large_json_integer_does_not_overflow(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["file_count"] = 10**400

        self.assertIs(artifact, validate_artifact(artifact))

    def test_schema_definition_rejects_unsupported_assertion_keyword(self):
        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.not: unsupported schema keyword"):
            validate_schema_document({"type": "string", "not": {"const": "bad"}})

    def test_strict_loader_rejects_non_standard_nan_and_duplicate_keys(self):
        artifact = dumps_artifact(minimal_artifacts()["quality-report"])
        nan_text = artifact.replace('"gates": []', '"gates": [], "extra_number": NaN')
        duplicate_text = artifact.replace(
            '"artifact_kind": "quality-report"',
            '"artifact_kind": "quality-report", "artifact_kind": "quality-report"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            nan_path = Path(tmp) / "nan.json"
            duplicate_path = Path(tmp) / "duplicate.json"
            nan_path.write_text(nan_text, encoding="utf-8")
            duplicate_path.write_text(duplicate_text, encoding="utf-8")

            with self.assertRaisesRegex(ArtifactValidationError, "invalid JSON constant: NaN"):
                load_artifact(nan_path)
            with self.assertRaisesRegex(ArtifactValidationError, "duplicate object key: artifact_kind"):
                load_artifact(duplicate_path)


class ArtifactCliTests(unittest.TestCase):
    def run_cli(self, *paths):
        return subprocess.run(
            [sys.executable, str(CLI), *(str(path) for path in paths)],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_cli_validates_all_real_v1_fixtures(self):
        paths = sorted(FIXTURE_ROOT.glob("*.json"))
        self.assertEqual(7, len(paths))

        result = self.run_cli(*paths)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(7, result.stdout.count("PASS "))
        self.assertIn("project-index v1.0.0", result.stdout)
        self.assertIn("quality-report v1.0.0", result.stdout)

    def test_cli_returns_one_and_reports_an_actionable_path_for_invalid_input(self):
        artifact = minimal_artifacts()["project-index"]
        del artifact["generated_at"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")

            result = self.run_cli(path)

        self.assertEqual(1, result.returncode)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("$.generated_at: required property is missing", result.stdout)

    def test_cli_checks_every_input_even_when_one_fails(self):
        valid = FIXTURE_ROOT / "evidence.json"
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")

            result = self.run_cli(invalid, valid)

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, result.stdout.count("FAIL "))
        self.assertEqual(1, result.stdout.count("PASS "))

    def test_cli_reports_malformed_utf8_and_continues_with_later_inputs(self):
        valid = FIXTURE_ROOT / "evidence.json"
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid-utf8.json"
            invalid.write_bytes(b"{\"artifact_kind\":\xff}")

            result = self.run_cli(invalid, valid)

        self.assertEqual(1, result.returncode)
        self.assertIn("cannot read JSON", result.stdout)
        self.assertEqual(1, result.stdout.count("FAIL "))
        self.assertEqual(1, result.stdout.count("PASS "))


class FixtureTruthTests(unittest.TestCase):
    def load_fixtures(self):
        return {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in FIXTURE_ROOT.glob("*.json")
        }

    def test_fixture_revision_is_one_real_git_commit(self):
        fixtures = self.load_fixtures()
        revisions = {artifact["repository_revision"] for artifact in fixtures.values()}
        self.assertEqual(1, len(revisions))

        revision = revisions.pop()
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, f"fixture revision is not a commit: {revision}")

    def test_project_index_hashes_and_sizes_match_real_fixture_files(self):
        project_index = self.load_fixtures()["project-index"]
        target_root = SKILL_ROOT / project_index["project"]["root"]
        self.assertEqual(project_index["file_count"], len(project_index["files"]))
        for entry in project_index["files"]:
            with self.subTest(path=entry["path"]):
                path = target_root / entry["path"]
                if project_index["project"]["snapshot_kind"] == "git-tree":
                    git_path = path.relative_to(REPOSITORY_ROOT).as_posix()
                    content = subprocess.check_output(
                        ["git", "show", f"{project_index['repository_revision']}:{git_path}"],
                        cwd=REPOSITORY_ROOT,
                    )
                else:
                    self.assertTrue(path.is_file(), path)
                    content = path.read_bytes()
                self.assertEqual(entry["bytes"], len(content))
                self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())

    def test_evidence_paths_share_the_project_index_root_and_exist_in_the_pinned_tree(self):
        fixtures = self.load_fixtures()
        project_index = fixtures["project-index"]
        target_root = SKILL_ROOT / project_index["project"]["root"]
        for item in fixtures["evidence"]["items"]:
            relative = item["locator"].get("path")
            if relative is None:
                continue
            with self.subTest(evidence_id=item["id"], path=relative):
                path = (target_root / relative).resolve()
                self.assertTrue(path.is_relative_to(target_root.resolve()))
                git_path = path.relative_to(REPOSITORY_ROOT).as_posix()
                result = subprocess.run(
                    ["git", "cat-file", "-e", f"{project_index['repository_revision']}:{git_path}"],
                    cwd=REPOSITORY_ROOT,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, f"dangling evidence path: {relative}")

    def test_fixture_cross_references_resolve(self):
        fixtures = self.load_fixtures()
        evidence_ids = {item["id"] for item in fixtures["evidence"]["items"]}
        nodes = {item["id"] for item in fixtures["knowledge-graph"]["nodes"]}
        units = {item["id"] for item in fixtures["curriculum"]["units"]}

        for detection in fixtures["stack-profile"]["detections"]:
            self.assertLessEqual(set(detection["evidence_ids"]), evidence_ids)
        for node in fixtures["knowledge-graph"]["nodes"]:
            self.assertLessEqual(set(node["evidence_ids"]), evidence_ids)
        for edge in fixtures["knowledge-graph"]["edges"]:
            self.assertIn(edge["from"], nodes)
            self.assertIn(edge["to"], nodes)
            self.assertLessEqual(set(edge["evidence_ids"]), evidence_ids)
        for unit in fixtures["curriculum"]["units"]:
            self.assertLessEqual(set(unit["graph_node_ids"]), nodes)
            self.assertLessEqual(set(unit["evidence_ids"]), evidence_ids)
            self.assertLessEqual(set(unit["prerequisite_ids"]), units)
        for gate in fixtures["quality-report"]["gates"]:
            self.assertLessEqual(set(gate["evidence_ids"]), evidence_ids)


if __name__ == "__main__":
    unittest.main()
