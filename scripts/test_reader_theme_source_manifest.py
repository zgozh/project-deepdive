#!/usr/bin/env python3
"""Pure shape checks for reader-theme source manifests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import reader_handbook_review as review  # noqa: E402


REVISION = "fixture-revision"
DIGEST = "a" * 64


def _block(slot_id: str, path: str, language: str, *, supports=None, manuscript="lesson.md"):
    return {
        "slot_id": slot_id,
        "path": path,
        "start_line": 1,
        "end_line": 2,
        "language": language,
        "annotations": {"1": "入口"} if language == "java" else {},
        "expected_source_sha256": DIGEST,
        "expected_excerpt_sha256": DIGEST,
        "manuscript": manuscript,
        "supports": supports if supports is not None else ["说明这个处理步骤"],
    }


def _manifest():
    return {
        "artifact_kind": "reader-theme-sources",
        "version": "1.0",
        "source_revision": REVISION,
        "source_blocks": [
            _block("java-entry", "src/main/java/demo/Controller.java", "java"),
            _block("python-check", "src/service.py", "python", manuscript="answers.md"),
        ],
        "unknowns": [{
            "learning_outcome": "运行时部署效果",
            "reason": "本次源码材料不覆盖真实部署环境。",
            "critical": True,
        }],
        "general_references": [{
            "title": "Framework guide",
            "url": "https://example.test/guide",
            "supports": ["解释该框架概念"],
        }],
    }


class ReaderThemeSourceManifestTests(unittest.TestCase):
    def test_valid_v1_manifest_preserves_java_python_and_critical_unknown_shape(self):
        result = review.validate_reader_theme_source_manifest(_manifest(), source_revision=REVISION)

        self.assertEqual(result["version"], "1.0")
        self.assertEqual([row["language"] for row in result["source_blocks"]], ["java", "python"])
        self.assertEqual(result["source_blocks"][0]["annotations"], {1: "入口"})
        self.assertIs(result["unknowns"][0]["critical"], True)
        self.assertEqual(result["general_references"][0]["supports"], ["解释该框架概念"])

    def test_rejects_the_mismatched_first_chapter_manifest_shape(self):
        wrong = {
            "kind": "reader-theme-sources",
            "version": "1.0",
            "source_revision": REVISION,
            "source_blocks": [_block("wrong-shape", "src/App.java", "java", supports="supports text",
                                      manuscript="learner-chapter.md")],
            "unknowns": "none",
        }
        with self.assertRaisesRegex(review.ReaderHandbookReviewError, "SOURCES_INVALID"):
            review.validate_reader_theme_source_manifest(wrong, source_revision=REVISION)

    def test_rejects_manifest_aliases_types_and_duplicate_or_unknown_fields(self):
        cases = []
        wrong = _manifest()
        wrong["kind"] = wrong.pop("artifact_kind")
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["unknowns"] = "not a list"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["general_references"] = "not a list"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["unexpected"] = True
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["source_revision"] = "different-revision"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["version"] = "2.0"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["unknowns"][0]["critical"] = "true"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["unknowns"] = [False]
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["general_references"] = [False]
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"] = ["not an object"]
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["supports"] = "not a list"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["manuscript"] = "learner-chapter.md"
        cases.append((wrong, "SOURCES_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][1]["slot_id"] = wrong["source_blocks"][0]["slot_id"]
        cases.append((wrong, "SOURCES_INVALID"))

        for index, (manifest, code) in enumerate(cases):
            with self.subTest(case=index, code=code):
                with self.assertRaisesRegex(review.ReaderHandbookReviewError, code):
                    review.validate_reader_theme_source_manifest(manifest, source_revision=REVISION)

    def test_rejects_source_block_path_hash_line_and_annotation_shapes(self):
        cases = []
        wrong = _manifest()
        wrong["source_blocks"][0]["path"] = "../Controller.java"
        cases.append((wrong, "SOURCE_BLOCK_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["expected_source_sha256"] = "A" * 64
        cases.append((wrong, "SOURCE_BLOCK_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["start_line"] = 0
        cases.append((wrong, "SOURCE_BLOCK_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["end_line"] = 0
        cases.append((wrong, "SOURCE_BLOCK_INVALID"))

        wrong = _manifest()
        wrong["source_blocks"][0]["annotations"] = {"3": "line is outside the 1-2 range"}
        cases.append((wrong, "SOURCE_BLOCK_INVALID"))

        for index, (manifest, code) in enumerate(cases):
            with self.subTest(case=index, code=code):
                with self.assertRaisesRegex(review.ReaderHandbookReviewError, code):
                    review.validate_reader_theme_source_manifest(manifest, source_revision=REVISION)


if __name__ == "__main__":
    unittest.main()
