from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

import reader_manuscript_authoring as authoring  # noqa: E402


SOURCE = b"def normalize(value):\n    return value.strip()\n"
SYMBOL_SOURCE = (
    b"# fixture\n\n"
    b"def lesson_step(value):\n"
    b"    normalized = value.strip()\n"
    b"    return normalized\n\n"
    b"def answer_step(value):\n"
    b"    result = value.upper()\n"
    b"    return result\n"
)
REVISION = "caller-revision-for-local-render"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def block(
    slot_id: str,
    *,
    manuscript: str,
    start: int,
    end: int,
    source: bytes = SOURCE,
) -> dict[str, object]:
    excerpt = b"".join(source.splitlines(keepends=True)[start - 1 : end])
    return {
        "slot_id": slot_id,
        "path": "cache/shared.py",
        "start_line": start,
        "end_line": end,
        "language": "python",
        "annotations": {},
        "expected_source_sha256": sha(source),
        "expected_excerpt_sha256": sha(excerpt),
        "manuscript": manuscript,
        "supports": [f"解释 {slot_id} 的输入和结果"],
    }


def manifest(*blocks: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_kind": "reader-theme-sources",
        "version": "1.0",
        "source_revision": REVISION,
        "source_blocks": list(blocks),
        "unknowns": [],
        "general_references": [],
    }


LESSON = "# 构建基础\n先看输入和输出。\n@@source:lesson-entry@@\n".encode("utf-8")
ANSWERS = (
    "## 练习与完整答案\n"
    "- 答案标记：这句话与答案正文在同一行，不依赖固定结尾标签。\n"
    "@@source:answer-entry@@\n"
).encode("utf-8")


class ReaderManuscriptAuthoringTests(unittest.TestCase):
    def test_required_symbol_range_is_checked_against_its_manuscript_only(self):
        required = authoring.reader_source_blocks.RequiredSourceSymbol(
            path="cache/shared.py", name="answer_step", start_line=7, end_line=9,
        )
        lesson_template = b"# Chapter\n@@source:lesson-entry@@\n" + ANSWERS
        truncated_manifest = manifest(
            block("lesson-entry", manuscript="lesson.md", start=7, end=8, source=SYMBOL_SOURCE),
            block("answer-entry", manuscript="answers.md", start=9, end=9, source=SYMBOL_SOURCE),
        )

        with self.assertRaises(authoring.ReaderManuscriptAuthoringError) as raised:
            authoring.compose_reader_manuscript(
                lesson_template,
                ANSWERS,
                truncated_manifest,
                lambda _path: SYMBOL_SOURCE,
                answer_mode="embedded",
                required_symbols={"lesson.md": (required,)},
            )

        self.assertEqual(raised.exception.code, "SOURCE_RENDER_FAILED")
        self.assertIsInstance(
            raised.exception.__cause__, authoring.reader_source_blocks.SourceBlockError,
        )

    def test_required_symbol_ranges_are_independent_for_embedded_manuscripts(self):
        required_symbols = {
            "lesson.md": (
                authoring.reader_source_blocks.RequiredSourceSymbol(
                    path="cache/shared.py", name="lesson_step", start_line=3, end_line=5,
                ),
            ),
            "answers.md": (
                authoring.reader_source_blocks.RequiredSourceSymbol(
                    path="cache/shared.py", name="answer_step", start_line=7, end_line=9,
                ),
            ),
        }
        lesson_template = b"# Chapter\n@@source:lesson-entry@@\n" + ANSWERS
        reads: list[str] = []

        result = authoring.compose_reader_manuscript(
            lesson_template,
            ANSWERS,
            manifest(
                block("lesson-entry", manuscript="lesson.md", start=3, end=5, source=SYMBOL_SOURCE),
                block("answer-entry", manuscript="answers.md", start=7, end=9, source=SYMBOL_SOURCE),
            ),
            lambda path: reads.append(path) or SYMBOL_SOURCE,
            answer_mode="embedded",
            required_symbols=required_symbols,
        )

        self.assertEqual(reads, ["cache/shared.py"])
        self.assertEqual(
            [row["manuscript"] for row in result.provenance["source_blocks"]],
            ["lesson.md", "answers.md"],
        )
        self.assertTrue(result.chapter_bytes.endswith(result.answers_bytes))

    def test_append_preserves_utf8_answer_bytes_and_reads_shared_path_once(self):
        reads: list[str] = []
        result = authoring.compose_reader_manuscript(
            LESSON,
            ANSWERS,
            manifest(
                block("lesson-entry", manuscript="lesson.md", start=1, end=2),
                block("answer-entry", manuscript="answers.md", start=2, end=2),
            ),
            lambda path: reads.append(path) or SOURCE,
            answer_mode="append",
        )

        self.assertEqual(reads, ["cache/shared.py"])
        self.assertEqual(result.chapter_bytes.count(result.answers_bytes), 1)
        self.assertTrue(result.chapter_bytes.endswith(result.answers_bytes))
        self.assertIn("答案标记：这句话与答案正文在同一行".encode("utf-8"), result.chapter_bytes)
        self.assertEqual(result.provenance["status"], "LOCAL_RENDERED")
        self.assertEqual(result.provenance["source_authentication"], "NOT_AUTHENTICATED")
        self.assertEqual(result.provenance["semantic_review"], "NOT_REVIEWED")
        proofs = result.provenance["source_blocks"]
        self.assertEqual([row["manuscript"] for row in proofs], ["lesson.md", "answers.md"])
        self.assertTrue(all(row["rendered_markdown_sha256"] for row in proofs))
        self.assertNotIn("def normalize", json.dumps(result.provenance, ensure_ascii=False))

    def test_embedded_answers_with_source_block_are_replaced_once_and_preserved(self):
        lesson_template = b"# Chapter\nNarration comes first.\n" + ANSWERS
        result = authoring.compose_reader_manuscript(
            lesson_template,
            ANSWERS,
            manifest(block("answer-entry", manuscript="answers.md", start=2, end=2)),
            lambda _path: SOURCE,
            answer_mode="embedded",
        )

        self.assertEqual(result.chapter_bytes.count(result.answers_bytes), 1)
        self.assertTrue(result.chapter_bytes.endswith(result.answers_bytes))
        self.assertEqual(len(result.provenance["source_blocks"]), 1)
        self.assertEqual(result.provenance["source_blocks"][0]["manuscript"], "answers.md")

    def test_bad_hash_and_escaping_manifest_do_not_create_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            (cache / "shared.py").write_bytes(SOURCE)
            lesson_file = root / "lesson-template.md"
            answers_file = root / "answers-template.md"
            sources_file = root / "sources.json"
            lesson_file.write_bytes(LESSON)
            answers_file.write_bytes(b"## Answers\nPlain answer.\n")
            output_dir = root / "out"

            wrong_hash = manifest(block("lesson-entry", manuscript="lesson.md", start=1, end=2))
            wrong_hash["source_blocks"][0]["expected_source_sha256"] = "0" * 64
            sources_file.write_text(json.dumps(wrong_hash), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = authoring.main([
                    "--lesson-template", str(lesson_file),
                    "--answers-template", str(answers_file),
                    "--sources", str(sources_file),
                    "--cache-root", str(root),
                    "--output-dir", str(output_dir),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(output_dir.exists())

            escaping = manifest(block("lesson-entry", manuscript="lesson.md", start=1, end=2))
            escaping["source_blocks"][0]["path"] = "../shared.py"
            sources_file.write_text(json.dumps(escaping), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = authoring.main([
                    "--lesson-template", str(lesson_file),
                    "--answers-template", str(answers_file),
                    "--sources", str(sources_file),
                    "--cache-root", str(root),
                    "--output-dir", str(output_dir),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(output_dir.exists())

    def test_cli_reuses_identical_outputs_and_refuses_different_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            (cache / "shared.py").write_bytes(SOURCE)
            lesson_file = root / "lesson-template.md"
            answers_file = root / "answers-template.md"
            sources_file = root / "sources.json"
            output_dir = root / "out"
            lesson_file.write_bytes(LESSON)
            answers_file.write_bytes(b"## Answers\nPlain answer.\n")
            sources_file.write_text(
                json.dumps(manifest(block("lesson-entry", manuscript="lesson.md", start=1, end=2))),
                encoding="utf-8",
            )
            args = [
                "--lesson-template", str(lesson_file),
                "--answers-template", str(answers_file),
                "--sources", str(sources_file),
                "--cache-root", str(root),
                "--output-dir", str(output_dir),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(authoring.main(args), 0)
                self.assertEqual(authoring.main(args), 0)
            original = (output_dir / "lesson.md").read_bytes()

            lesson_file.write_bytes(LESSON + b"\nA changed explanation.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(authoring.main(args), 2)
            self.assertEqual((output_dir / "lesson.md").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
