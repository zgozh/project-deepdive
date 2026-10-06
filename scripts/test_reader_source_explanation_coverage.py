from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import reader_source_blocks
import reader_source_explanation_coverage as coverage


def source_block(
    slot: str,
    path: str,
    start: int,
    end: int,
    manuscript: str = "lesson.md",
) -> dict[str, object]:
    return {
        "slot_id": slot,
        "path": path,
        "start_line": start,
        "end_line": end,
        "language": "java",
        "annotations": {},
        "expected_source_sha256": "a" * 64,
        "expected_excerpt_sha256": "b" * 64,
        "manuscript": manuscript,
        "supports": ["teaching fixture"],
    }


def manifest(*blocks: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_kind": "reader-theme-sources",
        "version": "1.0",
        "source_revision": "fixture-revision",
        "source_blocks": list(blocks),
        "unknowns": [],
        "general_references": [],
    }


def rendered(block: dict[str, object]) -> tuple[str, str]:
    end = int(block["end_line"])
    source = "".join(f"line {i}\n" for i in range(1, end + 1)).encode("utf-8")
    markdown, proof = reader_source_blocks.render_source_block(
        source,
        path=str(block["path"]),
        start_line=int(block["start_line"]),
        end_line=end,
        language=str(block["language"]),
    )
    return markdown, str(proof["caption"])


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


class ReaderSourceExplanationCoverageTests(unittest.TestCase):
    def test_multiple_blocks_custom_columns_and_line_formats_ignore_caption_inside_example_fence(self) -> None:
        first = source_block("first", "src/First.java", 10, 12)
        second = source_block("second", "src/Second.java", 20, 21)
        first_md, fake_caption = rendered(first)
        second_md, _ = rendered(second)
        lesson = (
            "```text\n" + fake_caption + "\n```\n\n"
            + first_md + "\n### 行为说明\n\n"
            + table(["源码行", "输入/条件", "结果"], [["10", "quota", "分支检查"], ["L11–L12", "空行与闭合分支", "继续返回"]])
            + "\n\n" + second_md + "\n\n"
            + table(["源码行", "配置项", "有效值", "生效后果"], [["L20至L21", "mode", "strict", "执行校验"]])
        )
        report = coverage.analyze_source_explanation_coverage(lesson, manifest(first, second))
        self.assertEqual("COVERED", report["status"])
        self.assertEqual(2, report["covered_source_block_count"])
        self.assertEqual(5, report["covered_line_count"])
        self.assertEqual(0, report["unrecognized_caption_count"])
        self.assertEqual("NOT_PERFORMED", report["semantic_review"])
        self.assertEqual("NOT_PERFORMED", report["source_authentication"])

    def test_escaped_table_pipes_preserve_cells_and_backslash_parity(self) -> None:
        self.assertEqual(
            ["1", r"String \| null", "type"],
            coverage._split_table_row(r"| 1 | String \| null | type |"),
        )
        self.assertEqual(
            [r"left\\", "right"],
            coverage._split_table_row(r"| left\\| right |"),
        )
        self.assertEqual(
            [r"left\\\| still-left", "right"],
            coverage._split_table_row(r"| left\\\| still-left | right |"),
        )

        block = source_block("escaped-pipe", "src/Pipe.java", 3, 4)
        code, _ = rendered(block)
        lesson = code + "\n\n" + table(
            ["源码行", "语法", "说明"],
            [["3", r"String \| null", "pipe stays in this cell"], ["4", "plain", "ordinary cell"]],
        )
        report = coverage.analyze_source_explanation_coverage(lesson, manifest(block))
        self.assertEqual("COVERED", report["status"])
        self.assertEqual(2, report["covered_line_count"])

    def test_missing_source_line_is_reported(self) -> None:
        block = source_block("gap", "src/Gap.java", 40, 42)
        code, _ = rendered(block)
        lesson = code + "\n" + table(["源码行", "职责"], [["40", "条件"], ["42", "返回"]])
        report = coverage.analyze_source_explanation_coverage(lesson, manifest(block))
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertEqual(1, report["missing_line_count"])
        self.assertEqual([[41, 41]], report["blocks"][0]["missing_ranges"])

    def test_loaded_manifest_preserves_raw_annotation_keys_for_analysis(self) -> None:
        block = source_block("annotated", "src/Annotated.java", 1, 1)
        block["annotations"] = {"1": "important source detail"}
        code, _ = rendered(block)
        lesson = code + "\n" + table(["源码行", "职责"], [["1", "声明"]])
        with tempfile.TemporaryDirectory() as temp_dir:
            sources_path = Path(temp_dir) / "sources.json"
            sources_path.write_text(json.dumps(manifest(block)), encoding="utf-8")
            loaded = coverage.load_source_manifest(sources_path)
        self.assertEqual({"1": "important source detail"}, loaded["source_blocks"][0]["annotations"])
        report = coverage.analyze_source_explanation_coverage(lesson, loaded)
        self.assertEqual("COVERED", report["status"])

    def test_embedded_answer_source_requires_assembled_chapter_mode(self) -> None:
        lesson_block = source_block("lesson-source", "src/Lesson.java", 10, 10)
        answer_block = source_block("answer-source", "src/Answer.java", 20, 20, manuscript="answers.md")
        lesson_code, _ = rendered(lesson_block)
        answer_code, _ = rendered(answer_block)
        assembled = (
            lesson_code + "\n" + table(["源码行", "职责"], [["10", "正文说明"]])
            + "\n\n## 练习与答案\n\n" + answer_code
            + "\n" + table(["源码行", "推演"], [["20", "答案说明"]])
        )
        sources = manifest(lesson_block, answer_block)

        lesson_only = coverage.analyze_source_explanation_coverage(assembled, sources)
        self.assertEqual("INCOMPLETE", lesson_only["status"])
        self.assertEqual("lesson_only", lesson_only["coverage_scope"])
        self.assertEqual(1, lesson_only["excluded_answer_source_block_count"])
        self.assertEqual(1, lesson_only["covered_source_block_count"])

        assembled_report = coverage.analyze_source_explanation_coverage(
            assembled, sources, include_answers=True,
        )
        self.assertEqual("COVERED", assembled_report["status"])
        self.assertEqual("assembled_chapter", assembled_report["coverage_scope"])
        self.assertEqual(2, assembled_report["source_block_count"])
        self.assertEqual(0, assembled_report["excluded_answer_source_block_count"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lesson_path = root / "assembled-lesson.md"
            sources_path = root / "sources.json"
            lesson_path.write_text(assembled, encoding="utf-8")
            sources_path.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = coverage.main([
                    "--lesson", str(lesson_path), "--sources", str(sources_path), "--assembled-chapter",
                ])
        self.assertEqual(0, result)
        self.assertEqual("COVERED", json.loads(output.getvalue())["status"])

    def test_out_of_range_source_line_is_reported(self) -> None:
        block = source_block("over", "src/Over.java", 50, 51)
        code, _ = rendered(block)
        lesson = code + "\n" + table(["源码行", "作用"], [["50–52", "分支和方法结束"]])
        report = coverage.analyze_source_explanation_coverage(lesson, manifest(block))
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertEqual(1, report["out_of_range_line_count"])
        self.assertEqual([[52, 52]], report["blocks"][0]["out_of_range_ranges"])

    def test_duplicate_caption_and_wrong_first_table_are_not_accepted_as_coverage(self) -> None:
        block = source_block("duplicate", "src/Duplicate.java", 70, 70)
        code, _ = rendered(block)
        good_table = table(["源码行", "职责"], [["70", "返回"]])
        duplicate = coverage.analyze_source_explanation_coverage(
            code + "\n" + good_table + "\n\n" + code + "\n" + good_table,
            manifest(block),
        )
        self.assertEqual(1, duplicate["duplicate_caption_count"])
        self.assertEqual("INCOMPLETE", duplicate["status"])

        wrong_table = coverage.analyze_source_explanation_coverage(
            code + "\n" + table(["文件行", "职责"], [["70", "返回"]]),
            manifest(block),
        )
        self.assertEqual(1, wrong_table["unrecognized_table_count"])
        self.assertEqual("INCOMPLETE", wrong_table["status"])

    def test_cli_rejects_duplicate_json_keys_and_does_not_overwrite_output(self) -> None:
        block = source_block("cli", "src/Cli.java", 80, 80)
        code, _ = rendered(block)
        good_lesson = code + "\n" + table(["源码行", "说明"], [["80", "返回"]])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lesson_path = root / "lesson.md"
            sources_path = root / "sources.json"
            output_path = root / "report.json"
            lesson_path.write_text(good_lesson, encoding="utf-8")
            output_path.write_text("existing", encoding="utf-8")
            sources_path.write_text(json.dumps(manifest(block), ensure_ascii=False), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                result = coverage.main([
                    "--lesson", str(lesson_path), "--sources", str(sources_path), "--output", str(output_path),
                ])
            self.assertEqual(2, result)
            self.assertEqual("existing", output_path.read_text(encoding="utf-8"))

            sources_path.write_text('{"artifact_kind":"x","artifact_kind":"y"}', encoding="utf-8")
            with self.assertRaises(coverage.CoverageInputError):
                coverage.load_source_manifest(sources_path)


if __name__ == "__main__":
    unittest.main()
