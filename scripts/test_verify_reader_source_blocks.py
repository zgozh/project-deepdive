import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reader_source_blocks import render_source_block, strip_source_overlays
from verify_reader_source_blocks import main, verify_reader_source_blocks


class VerifyReaderSourceBlocksTests(unittest.TestCase):
    def test_preserves_python_indentation_blank_lines_unicode_crlf_and_missing_eof_newline(self):
        source = 'def project():\r\n    label = "雪"\r\n\r\n    return label'.encode("utf-8")
        block, provenance = render_source_block(
            source,
            path="backend/学.py",
            start_line=1,
            end_line=4,
            language="python",
        )
        chapter = b"# Lesson\n\n" + block.encode("utf-8") + b"\nExplanation.\n"

        result = verify_reader_source_blocks(chapter, [provenance])

        self.assertEqual(result["supplied_provenance_count"], 1)
        self.assertEqual(result["verified_block_occurrence_count"], 1)
        self.assertEqual(strip_source_overlays(block.encode("utf-8"), provenance), source)

    def test_external_overlay_is_verified_and_strips_back_to_unicode_typescript(self):
        source = 'const label = `值-${id}`;\n'.encode("utf-8")
        block, provenance = render_source_block(
            source,
            path="web/request.ts",
            start_line=1,
            end_line=1,
            language="typescript",
            annotations={1: "模板值在请求组装时插入。"},
        )
        self.assertTrue(provenance["external_overlay_lines"])

        result = verify_reader_source_blocks(block.encode("utf-8"), [provenance])

        self.assertEqual(result["verified_block_occurrence_count"], 1)
        self.assertEqual(strip_source_overlays(block.encode("utf-8"), provenance), source)

    def test_java_inline_overlay_is_verified_without_reformatting(self):
        source = 'class Demo {\n  String name = "雪";\n}\n'.encode("utf-8")
        block, provenance = render_source_block(
            source,
            path="src/main/java/Demo.java",
            start_line=1,
            end_line=3,
            language="java",
            annotations={2: "字段保存显示名称。"},
        )

        result = verify_reader_source_blocks(block.encode("utf-8"), [provenance])

        self.assertEqual(result["verified_block_occurrence_count"], 1)
        self.assertEqual(strip_source_overlays(block.encode("utf-8"), provenance), source)

    def test_dedented_prepared_block_fails_against_its_renderer_provenance(self):
        source = b"def project():\n    return 1\n"
        block, provenance = render_source_block(
            source,
            path="backend/project.py",
            start_line=1,
            end_line=2,
            language="python",
        )
        changed = block.replace("    return 1\n", "return 1\n")

        with self.assertRaisesRegex(ValueError, "does not match its provenance digest"):
            verify_reader_source_blocks(changed.encode("utf-8"), [provenance])

    def test_missing_or_changed_caption_fails_explicitly(self):
        block, provenance = render_source_block(
            b"value = 1\n",
            path="src/value.py",
            start_line=1,
            end_line=1,
            language="python",
        )

        with self.assertRaisesRegex(ValueError, "caption was not found"):
            verify_reader_source_blocks(b"# No source block\n", [provenance])
        changed_caption = block.replace("源码位置", "改写位置")
        with self.assertRaisesRegex(ValueError, "caption was not found"):
            verify_reader_source_blocks(changed_caption.encode("utf-8"), [provenance])

    def test_identical_block_can_be_reused_and_counts_each_occurrence(self):
        block, provenance = render_source_block(
            b"def project():\n    return 1\n",
            path="backend/project.py",
            start_line=1,
            end_line=2,
            language="python",
        )
        chapter = (block + "\nExercise answer:\n\n" + block).encode("utf-8")

        result = verify_reader_source_blocks(chapter, [provenance])

        self.assertEqual(result["supplied_provenance_count"], 1)
        self.assertEqual(result["verified_block_occurrence_count"], 2)

    def test_corrupted_second_reuse_fails_after_first_copy_matches(self):
        block, provenance = render_source_block(
            b"def project():\n    return 1\n",
            path="backend/project.py",
            start_line=1,
            end_line=2,
            language="python",
        )
        damaged = block.replace("    return 1\n", "return 1\n")
        chapter = (block + "\nExercise answer:\n\n" + damaged).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "occurrence 2 does not match its provenance"):
            verify_reader_source_blocks(chapter, [provenance])

    def test_duplicate_provenance_for_same_caption_is_rejected(self):
        block, provenance = render_source_block(
            b"value = 1\n",
            path="src/value.py",
            start_line=1,
            end_line=1,
            language="python",
        )

        with self.assertRaisesRegex(ValueError, "provenance repeats caption"):
            verify_reader_source_blocks(block.encode("utf-8"), [provenance, dict(provenance)])

    def test_cli_reads_only_explicit_chapter_and_provenance_and_reports_scope(self):
        block, provenance = render_source_block(
            '{"name": "雪"}\n'.encode("utf-8"),
            path="config/example.json",
            start_line=1,
            end_line=1,
            language="json",
        )
        with tempfile.TemporaryDirectory() as temporary:
            chapter_path = Path(temporary) / "chapter.md"
            provenance_path = Path(temporary) / "provenance.json"
            chapter_path.write_bytes(("# Example\n" + block).encode("utf-8"))
            provenance_path.write_text(json.dumps([provenance], ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(["--chapter", str(chapter_path), "--provenance", str(provenance_path)])

        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["supplied_provenance_count"], 1)
        self.assertEqual(report["verified_block_occurrence_count"], 1)
        self.assertIn("Every final-chapter occurrence", report["scope"])
        self.assertIn("not checked", report["unchecked_scope"])
        self.assertEqual(report["source_authentication"], "NOT_PERFORMED")
        self.assertEqual(report["teaching_review"], "NOT_PERFORMED")


if __name__ == "__main__":
    unittest.main()
