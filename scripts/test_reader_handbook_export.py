from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reader_handbook_export import ExportError, export_file, render_markdown


class ReaderHandbookExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def _export(self, kind: str, source: str, title: str = "读者标题") -> tuple[Path, Path, Path]:
        input_path = self._write("input.md", source)
        output_path = self.root / "staging" / "reader.md"
        audit_path = self.root / "staging" / "reader.audit.json"
        export_file(
            kind=kind,
            input_arg=str(input_path),
            output_arg=str(output_path),
            audit_arg=str(audit_path),
            title=title,
            revision="rev-123",
        )
        return input_path, output_path, audit_path

    def test_general_lesson_translates_only_known_headings_and_preserves_fenced_code(self) -> None:
        code = "```python\nclass ClaimService:\n    def answer(self, question: str) -> str:\n        return \"Question: CLAIM-01\"\n```\n"
        typescript_code = "~~~typescript\nexport const answerFor = (question: Question): Answer => lookup(question);\n~~~\n"
        source = (
            "# English lesson title\n\n"
            "> Draft teaching material - not independently reviewed or verified.\n\n"
            "## Intuition\n\n"
            "The word Answer remains in this ordinary prose.\n\n"
            "## Worked example\n\n"
            + code
            + "\n"
            + typescript_code
            + "\n## Common misconception\n\nKeep this explanation intact.\n\n## Check yourself\n\nOne question.\n"
        )
        input_path, output_path, audit_path = self._export("general-lesson", source)
        output = output_path.read_text(encoding="utf-8")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertIn("# 读者标题\n", output)
        self.assertIn("## 直觉\n", output)
        self.assertIn("## 示例推演\n", output)
        self.assertIn("## 常见误解\n", output)
        self.assertIn("## 自我检查\n", output)
        self.assertIn("The word Answer remains in this ordinary prose.", output)
        self.assertIn(code, output)
        self.assertIn(typescript_code, output)
        self.assertEqual(input_path.read_text(encoding="utf-8"), source)
        self.assertEqual(audit["input_sha256"], hashlib.sha256(source.encode("utf-8")).hexdigest())
        self.assertEqual(audit["output_sha256"], hashlib.sha256(output.encode("utf-8")).hexdigest())
        self.assertEqual(audit["revision"], "rev-123")
        self.assertEqual(audit["input_review_state"], "NOT_REVIEWED: detected source draft banner")
        self.assertEqual(audit["output_teaching_depth"], "NOT_REVIEWED")
        self.assertFalse(audit["review_status_raised"])

    def test_general_answer_translates_sections_and_standalone_labels(self) -> None:
        source = (
            "# Answer Book: English title\n\n"
            "## Question\n\nWhat should the learner explain?\n\n"
            "## Answer\n\nExplain it step by step.\n\n"
            "## Hints\n\n- Hint: Start with the user's goal.\n\n"
            "## Rubric\n\n- **Answer:** mention the boundary.\n"
        )
        _, output_path, audit_path = self._export("general-answer", source, "问题与答案")
        output = output_path.read_text(encoding="utf-8")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertIn("# 问题与答案\n", output)
        self.assertIn("## 问题\n", output)
        self.assertIn("## 解答\n", output)
        self.assertIn("## 提示\n", output)
        self.assertIn("## 评分要点\n", output)
        self.assertIn("- 提示: Start with the user's goal.", output)
        self.assertIn("- **解答:** mention the boundary.", output)
        self.assertEqual(len(audit["heading_mappings"]), 5)
        self.assertEqual(len(audit["label_mappings"]), 2)

    def test_new_concept_headings_and_draft_banner_metadata_ignore_fenced_code(self) -> None:
        banner = "> Draft teaching material - not independently reviewed or verified."
        code = f"```text\n{banner}\n```\n"
        source = (
            "# Neutral lesson\n\n"
            + banner
            + "\n\n## New concepts\n\n### New concept 1\n\nExplain the idea.\n\n"
            "## Prerequisite ideas\n\n### Prerequisite idea 1\n\nExplain the prerequisite.\n\n"
            "## Worked example\n\n"
            + code
        )
        input_path, output_path, audit_path = self._export("general-lesson", source)
        output = output_path.read_text(encoding="utf-8")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertIn("## 新概念\n", output)
        self.assertIn("### 新概念 1\n", output)
        self.assertIn("## 前置概念\n", output)
        self.assertIn("### 前置概念 1\n", output)
        self.assertEqual(output.count(banner), 1)
        self.assertIn(code, output)
        self.assertEqual(audit["input_review_state"], "NOT_REVIEWED: detected source draft banner")
        self.assertEqual(audit["source_metadata"], [{
            "kind": "source-draft-banner",
            "line": 3,
            "original_text": banner,
        }])
        self.assertEqual(input_path.read_text(encoding="utf-8"), source)

        code_only = f"# Code sample\n\n## Intuition\n\n```text\n{banner}\n```\n"
        result = render_markdown("general-lesson", "代码示例", code_only)
        self.assertEqual(result.input_review_state, "unknown")
        self.assertEqual(result.source_metadata, [])
        self.assertIn(banner, result.text)

    def test_rejects_unsupported_kind_heading_reference_and_unclosed_fence_before_writing(self) -> None:
        cases = (
            ("canonical-v2", "# Title\n", "canonical-v2"),
            ("general-lesson", "# Title\n\n## Unknown section\n", "unsupported"),
            ("general-answer", "# Title\n\nEvidence: CLAIM-12\n", "machine reference"),
            ("general-answer", "# Title\n\nBroken marker: EVID- \n", "machine reference"),
            ("general-lesson", "# Title\n\n```ts\nconst x = 1;\n", "unclosed code fence"),
        )
        for kind, source, message in cases:
            with self.subTest(kind=kind, message=message):
                input_path = self._write(f"{kind}-{message}.md", source)
                output_path = self.root / f"{kind}-{message}.out.md"
                audit_path = self.root / f"{kind}-{message}.json"
                with self.assertRaisesRegex(ExportError, message):
                    export_file(
                        kind=kind,
                        input_arg=str(input_path),
                        output_arg=str(output_path),
                        audit_arg=str(audit_path),
                        title="标题",
                    )
                self.assertFalse(output_path.exists())
                self.assertFalse(audit_path.exists())

    def test_rejects_path_aliases_existing_destinations_and_isolated_cr(self) -> None:
        source = "# Title\r\n\r\n## Intuition\r\n\r\ntext\r\n"
        input_path = self._write("input.md", source)
        with self.assertRaisesRegex(ExportError, "different paths"):
            export_file(
                kind="general-lesson",
                input_arg=str(input_path),
                output_arg=str(input_path.parent / "." / input_path.name),
                audit_arg=str(self.root / "audit.json"),
                title="标题",
            )

        output_path = self.root / "output.md"
        audit_path = self.root / "audit.json"
        output_path.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "already exists"):
            export_file(
                kind="general-lesson",
                input_arg=str(input_path),
                output_arg=str(output_path),
                audit_arg=str(audit_path),
                title="标题",
            )
        self.assertEqual(output_path.read_text(encoding="utf-8"), "keep")

        bad = "# Title\n\n## Intuition\n\nPath: D:\ragent\n"
        with self.assertRaisesRegex(ExportError, "isolated CR"):
            render_markdown("general-lesson", "标题", bad)
        self.assertFalse(audit_path.exists())

    def test_overwrite_is_explicit_and_source_remains_unchanged(self) -> None:
        source = "# Title\n\n## Intuition\n\nText.\n"
        input_path = self._write("input.md", source)
        output_path = self.root / "output.md"
        audit_path = self.root / "audit.json"
        output_path.write_text("old output", encoding="utf-8")
        audit_path.write_text("old audit", encoding="utf-8")

        export_file(
            kind="general-lesson",
            input_arg=str(input_path),
            output_arg=str(output_path),
            audit_arg=str(audit_path),
            title="标题",
            overwrite=True,
        )
        self.assertIn("## 直觉", output_path.read_text(encoding="utf-8"))
        self.assertEqual(input_path.read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
