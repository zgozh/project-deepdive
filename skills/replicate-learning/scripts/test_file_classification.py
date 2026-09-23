#!/usr/bin/env python3
"""Tests for deterministic Project DeepDive file typing and surface coverage rules."""

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_classification import (  # noqa: E402
    CLASSIFICATION_POLICY_VERSION,
    CoverageDecision,
    CoverageOverride,
    FileFacts,
    SURFACES,
    classify_file,
    detect_media_type,
    extension_of,
    normalize_artifact_path,
)

PNG_PREFIX = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def facts(path, *, content_kind="text", sample=b"", media_type=None, extension=None):
    """Build facts the way the scanner would, so table cases stay honest."""
    if extension is None:
        extension = extension_of(path)
    if media_type is None:
        media_type, content_kind = detect_media_type(path, content_kind, sample)
    return FileFacts(
        path=path,
        content_kind=content_kind,
        media_type=media_type,
        extension=extension,
    )


class MediaTypeTests(unittest.TestCase):
    """Deterministic typing must not consult the host MIME registry."""

    def test_python_source_is_typed_by_extension(self):
        media_type, content_kind = detect_media_type("src/app.py", "file", b"import os\n")

        self.assertEqual(("text/x-python", "text"), (media_type, content_kind))
        self.assertEqual("py", extension_of("src/app.py"))

    def test_dockerfile_is_typed_by_exact_name_and_has_no_extension(self):
        media_type, content_kind = detect_media_type("Dockerfile", "file", b"FROM python:3.12\n")

        self.assertEqual(("text/x-dockerfile", "text"), (media_type, content_kind))
        self.assertEqual("", extension_of("Dockerfile"))

    def test_png_is_typed_by_extension_and_is_binary(self):
        media_type, content_kind = detect_media_type("public/logo.png", "file", PNG_PREFIX)

        self.assertEqual(("image/png", "binary"), (media_type, content_kind))

    def test_unknown_extension_with_utf8_text_falls_back_to_text_plain(self):
        media_type, content_kind = detect_media_type("notes.unknown", "file", "héllo\n".encode("utf-8"))

        self.assertEqual(("text/plain", "text"), (media_type, content_kind))

    def test_unknown_extension_containing_nul_is_octet_stream(self):
        media_type, content_kind = detect_media_type("notes.unknown", "file", b"abc\x00def")

        self.assertEqual(("application/octet-stream", "binary"), (media_type, content_kind))

    def test_gitlink_fact_is_reported_as_gitlink_payload(self):
        media_type, content_kind = detect_media_type("vendor/lib", "gitlink", b"")

        self.assertEqual(("application/x-gitlink", "gitlink"), (media_type, content_kind))

    def test_typing_is_stable_for_uppercase_extensions_and_dotted_names(self):
        self.assertEqual(("image/png", "binary"), detect_media_type("A/B/Logo.PNG", "file", b""))
        self.assertEqual("gz", extension_of("archive.tar.gz"))
        self.assertEqual("", extension_of(".gitignore"))

    def test_policy_version_is_declared(self):
        self.assertEqual("1.0.0", CLASSIFICATION_POLICY_VERSION)


class SurfaceRuleTests(unittest.TestCase):
    """Rule precedence must be fixed, ordered and evidence-bearing."""

    CASES = {
        ".github/workflows/ci.yml": ("ci", "CLASSIFIED"),
        "Dockerfile": ("infrastructure", "CLASSIFIED"),
        "src/test/java/acme/AppTest.java": ("test", "CLASSIFIED"),
        "src/main/resources/db/migration/V1__init.sql": ("migration", "CLASSIFIED"),
        "frontend/src/pages/Home.tsx": ("frontend", "CLASSIFIED"),
        "backend/routes/users.py": ("backend", "CLASSIFIED"),
        "api/openapi.yml": ("api", "CLASSIFIED"),
        "domain/order.py": ("domain", "CLASSIFIED"),
        "agent/tools/search.py": ("agent", "CLASSIFIED"),
        "db/schema.sql": ("database", "CLASSIFIED"),
        "tests/fixtures/user.json": ("fixture", "CLASSIFIED"),
        "config/settings.yml": ("configuration", "CLASSIFIED"),
        "scripts/release.py": ("script", "CLASSIFIED"),
        "cli/main.py": ("cli", "CLASSIFIED"),
        "mcp/server.py": ("mcp", "CLASSIFIED"),
        "docs/overview.md": ("documentation", "CLASSIFIED"),
        "assets/logo.svg": ("asset", "CLASSIFIED"),
        ".pre-commit-config.yaml": ("tooling", "CLASSIFIED"),
        "vendor/lib/source.c": ("vendor", "VENDOR"),
        "dist/assets/app.js": ("generated", "GENERATED"),
        ".env": ("environment", "IGNORED_WITH_REASON"),
        ".env.example": ("environment", "CLASSIFIED"),
        "mystery.odd": ("other", "UNKNOWN"),
    }

    def test_ordered_surface_table(self):
        for path, (surface, classification) in self.CASES.items():
            with self.subTest(path=path):
                decision = classify_file(facts(path))

                self.assertEqual(surface, decision.surface)
                self.assertEqual(classification, decision.classification)

    def test_every_non_unknown_decision_carries_a_reason_and_a_known_rule_id(self):
        for path in self.CASES:
            with self.subTest(path=path):
                decision = classify_file(facts(path))

                self.assertTrue(decision.reason.strip(), path)
                self.assertNotIn(" ", decision.rule_id, path)
                if decision.classification != "UNKNOWN":
                    self.assertNotEqual("fallback:unknown", decision.rule_id)

    def test_generated_vendor_and_ignored_are_not_applicable_to_teaching(self):
        for path in ("vendor/lib/source.c", "dist/assets/app.js", ".env"):
            with self.subTest(path=path):
                self.assertEqual("NOT_APPLICABLE", classify_file(facts(path)).teaching_status)

    def test_owned_and_unknown_files_are_located(self):
        for path in ("src/test/java/acme/AppTest.java", "mystery.odd"):
            with self.subTest(path=path):
                self.assertEqual("LOCATED", classify_file(facts(path)).teaching_status)

    def test_no_builtin_rule_ever_emits_covered(self):
        for path in self.CASES:
            with self.subTest(path=path):
                self.assertNotEqual("COVERED", classify_file(facts(path)).classification)

    def test_surfaces_are_drawn_from_the_published_vocabulary(self):
        for path in self.CASES:
            with self.subTest(path=path):
                decision = classify_file(facts(path))

                self.assertIn(decision.surface, SURFACES)
                self.assertTrue(set(decision.secondary_surfaces) <= set(SURFACES))

    def test_secondary_surfaces_are_sorted_unique_and_exclude_the_primary(self):
        for path in self.CASES:
            with self.subTest(path=path):
                decision = classify_file(facts(path))
                secondary = list(decision.secondary_surfaces)

                self.assertEqual(sorted(set(secondary)), secondary)
                self.assertNotIn(decision.surface, secondary)

    def test_migration_precedes_generic_database_and_configuration(self):
        decision = classify_file(facts("src/main/resources/db/migration/V1__init.sql"))

        self.assertEqual("migration", decision.surface)
        self.assertEqual("path:migration", decision.rule_id)
        self.assertIn("database", decision.secondary_surfaces)

    def test_fixture_directory_is_more_specific_than_a_test_directory(self):
        decision = classify_file(facts("tests/fixtures/user.json"))

        self.assertEqual("fixture", decision.surface)
        self.assertEqual("path:fixture", decision.rule_id)
        self.assertIn("test", decision.secondary_surfaces)

    def test_generated_output_beats_a_static_asset_directory(self):
        decision = classify_file(facts("dist/assets/app.js"))

        self.assertEqual("generated", decision.surface)
        self.assertEqual("path:generated", decision.rule_id)

    def test_safety_rule_beats_every_path_convention(self):
        decision = classify_file(facts("config/.env"))

        self.assertEqual("environment", decision.surface)
        self.assertEqual("safety:tracked-env", decision.rule_id)
        self.assertEqual("IGNORED_WITH_REASON", decision.classification)
        self.assertNotIn("configuration", decision.secondary_surfaces)

    def test_known_extension_without_architectural_context_stays_honest(self):
        decision = classify_file(facts("src/sample/service.py", sample=b"import os\n"))

        self.assertEqual(("other", "CLASSIFIED"), (decision.surface, decision.classification))
        self.assertEqual("extension:known", decision.rule_id)
        self.assertIn("Phase 3", decision.reason)

    def test_decision_is_immutable(self):
        decision = classify_file(facts("docs/overview.md"))

        self.assertIsInstance(decision, CoverageDecision)
        with self.assertRaises(Exception):
            decision.surface = "backend"


class OverrideTests(unittest.TestCase):
    """An override is the only way to remove a deliberate UNKNOWN."""

    def test_override_resolves_an_unknown_with_its_evidence(self):
        override = CoverageOverride(
            surface="tooling",
            classification="CLASSIFIED",
            reason="Repository-owned generator input documented by the project.",
        )

        decision = classify_file(facts("mystery.odd"), override)

        self.assertEqual(("tooling", "CLASSIFIED", "LOCATED"), (
            decision.surface, decision.classification, decision.teaching_status))
        self.assertEqual("override:exact-path", decision.rule_id)
        self.assertEqual("Repository-owned generator input documented by the project.", decision.reason)
        self.assertEqual((), decision.secondary_surfaces)
        self.assertNotEqual("COVERED", decision.classification)

    def test_override_with_blank_reason_is_rejected(self):
        for reason in ("", "   ", "\n"):
            with self.subTest(reason=repr(reason)):
                with self.assertRaisesRegex(ValueError, "reason"):
                    classify_file(
                        facts("mystery.odd"),
                        CoverageOverride(surface="tooling", classification="CLASSIFIED", reason=reason),
                    )

    def test_override_cannot_assert_unknown_or_covered(self):
        for classification in ("UNKNOWN", "COVERED"):
            with self.subTest(classification=classification):
                with self.assertRaisesRegex(ValueError, classification):
                    classify_file(
                        facts("mystery.odd"),
                        CoverageOverride(
                            surface="tooling",
                            classification=classification,
                            reason="Trying to bypass the audit.",
                        ),
                    )

    def test_override_rejects_an_unknown_surface(self):
        with self.assertRaisesRegex(ValueError, "surface"):
            classify_file(
                facts("mystery.odd"),
                CoverageOverride(
                    surface="imaginary-surface",
                    classification="CLASSIFIED",
                    reason="Bad surface.",
                ),
            )

    def test_override_can_mark_vendor_or_generated_with_not_applicable_status(self):
        for classification in ("VENDOR", "GENERATED", "IGNORED_WITH_REASON"):
            with self.subTest(classification=classification):
                decision = classify_file(
                    facts("mystery.odd"),
                    CoverageOverride(
                        surface="vendor",
                        classification=classification,
                        reason="Third-party drop documented by the project.",
                    ),
                )

                self.assertEqual("NOT_APPLICABLE", decision.teaching_status)


class PathNormalizationTests(unittest.TestCase):
    def test_relative_posix_paths_are_returned_unchanged(self):
        self.assertEqual("src/app.py", normalize_artifact_path("src/app.py"))
        self.assertEqual("src/app.py", normalize_artifact_path("./src/app.py"))

    def test_absolute_escaping_and_windows_paths_fail_closed(self):
        for raw in ("/etc/passwd", "C:/Windows/system32", "../outside.py", "src/../../outside.py",
                    "", ".", "src\\app.py", "src//app.py"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "path"):
                    normalize_artifact_path(raw)

    def test_scanner_facts_reject_escaping_paths(self):
        with self.assertRaisesRegex(ValueError, "path"):
            classify_file(FileFacts(path="../escape.py", content_kind="text",
                                    media_type="text/x-python", extension="py"))


if __name__ == "__main__":
    unittest.main()
