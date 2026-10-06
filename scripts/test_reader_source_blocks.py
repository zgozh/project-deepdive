import hashlib
import json
import unittest

from reader_source_blocks import (
    RequiredSourceSymbol,
    SourceBlockError,
    SourceBlockPlan,
    materialize_reader_sources,
    render_source_block,
    strip_source_overlays,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReaderSourceBlocksTests(unittest.TestCase):
    def test_java_overlay_has_chinese_caption_and_round_trips_exact_excerpt(self):
        source = (
            'class Demo {\n'
            '  String endpoint = "https://example.invalid/a";\n'
            '  // existing teaching note\n'
            '  void send() {}\n'
            '}\n'
        ).encode("utf-8")
        excerpt = b'  String endpoint = "https://example.invalid/a";\n  // existing teaching note\n'
        rendered, meta = render_source_block(
            source,
            path="src/main/java/Demo.java",
            start_line=2,
            end_line=3,
            language="java",
            annotations={2: "这里的 URL 是字符串内容，调用会在下一步使用它。"},
            expected_source_sha256=sha(source),
            expected_excerpt_sha256=sha(excerpt),
            revision="caller-revision",
        )
        self.assertIn("> **源码位置**：`src/main/java/Demo.java`，第 2–3 行", rendered)
        self.assertIn("这里的 URL 是字符串内容", rendered)
        self.assertIn('  String endpoint = "https://example.invalid/a";\n', rendered)
        self.assertIn("  // existing teaching note\n", rendered)
        self.assertIn("  // 教学：这里的 URL 是字符串内容", rendered)
        self.assertNotIn("PDD-READER-OVERLAY-", rendered)
        self.assertEqual(meta["inline_overlays"][0]["source_line"], 2)
        self.assertEqual(meta["inline_overlays"][0]["body_line_index"], 1)
        self.assertEqual(strip_source_overlays(rendered, meta), excerpt)
        self.assertTrue(meta["source_sha256_match"])
        self.assertTrue(meta["excerpt_sha256_match"])
        self.assertEqual(meta["revision_trust"], "caller-provided; not authenticated by this helper")
        self.assertEqual(meta["semantic_review"], "NOT_PERFORMED")
        self.assertNotIn("class Demo", json.dumps(meta, ensure_ascii=False))
        with self.assertRaises(SourceBlockError):
            render_source_block(
                source,
                path="src/main/java/Demo.java",
                start_line=2,
                end_line=2,
                language="java",
                annotations={2: r"\u000a would change Java comment tokenization"},
            )

    def test_strip_removes_only_positioned_overlay_when_source_has_same_teaching_comment(self):
        original_comment = "// 教学：这行原本就在源码里\n"
        source = (original_comment + "int count = 1;\nreturn count;\n").encode("utf-8")
        rendered, meta = render_source_block(
            source,
            path="src/main/java/Counter.java",
            start_line=1,
            end_line=3,
            language="java",
            annotations={3: "这行原本就在源码里"},
        )
        self.assertEqual(rendered.count(original_comment), 2)
        self.assertEqual(strip_source_overlays(rendered, meta), source)

    def test_typescript_template_literals_remain_raw_with_external_annotations(self):
        source = b"const payload = `path/${resourceId}`;\nconst next = `value-${value}`;\n"
        rendered, meta = render_source_block(
            source,
            path="web/request.ts",
            start_line=1,
            end_line=2,
            language="typescript",
            annotations={1: "模板字符串中的变量会在运行时插入。"},
            expected_source_sha256=sha(source),
        )
        self.assertIn("模板字符串中的变量", rendered)
        self.assertIn("const payload = `path/${resourceId}`;\n", rendered)
        self.assertIn("const next = `value-${value}`;\n", rendered)
        self.assertNotIn("\\x60", rendered)
        self.assertNotIn("PDD-READER-OVERLAY-", rendered)
        self.assertEqual(strip_source_overlays(rendered, meta), source)

    def test_range_keeps_blank_lines_and_missing_final_lf(self):
        source = b"first\n\nlast"
        excerpt = b"\nlast"
        rendered, meta = render_source_block(
            source,
            path="src/sample.txt",
            start_line=2,
            end_line=3,
            language="text",
            expected_source_sha256=sha(source),
            expected_excerpt_sha256=sha(excerpt),
        )
        self.assertIn("第 2–3 行", rendered)
        self.assertTrue(meta["framing_newline"])
        self.assertEqual(strip_source_overlays(rendered, meta), excerpt)

    def test_source_tilde_line_does_not_close_rendered_fence(self):
        source = b"before\n~~~\nafter\n"
        rendered, meta = render_source_block(source, path="src/sample.txt", start_line=1, end_line=3, language="text")
        self.assertEqual(meta["opening_fence"], "~~~~text")
        self.assertIn("~~~~text\nbefore\n~~~\nafter\n~~~~\n", rendered)
        self.assertEqual(strip_source_overlays(rendered, meta), source)

    def test_python_annotations_allow_normal_line_and_reject_string_or_continuation(self):
        normal = b"total = 2\nprint(total)\n"
        rendered, meta = render_source_block(
            normal,
            path="src/calc.py",
            start_line=1,
            end_line=2,
            language="python",
            annotations={2: "第二行读取前一行算出的值。"},
        )
        self.assertIn("# 教学：", rendered)
        self.assertNotIn("PDD-READER-OVERLAY-", rendered)
        self.assertEqual(strip_source_overlays(rendered, meta), normal)
        for unsafe in (
            b'value = """start\nmiddle\nend"""\n',
            b"result = 1 + \\\n    2\n",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(SourceBlockError):
                    render_source_block(
                        unsafe,
                        path="src/calc.py",
                        start_line=2,
                        end_line=2,
                        language="python",
                        annotations={2: "不应插入到跨行结构中。"},
                    )

    def test_unknown_language_is_raw_only_and_controls_fail_closed(self):
        source = b'{"id": "x"}\n'
        rendered, meta = render_source_block(source, path="data/item.json", start_line=1, end_line=1, language="json")
        self.assertIn(source.decode(), rendered)
        self.assertEqual(strip_source_overlays(rendered, meta), source)
        with self.assertRaises(SourceBlockError):
            render_source_block(source, path="data/item.json", start_line=1, end_line=1, language="json", annotations={1: "说明"})
        for bad in (b"x\r", b"x\x00\n", b"\xff"):
            with self.subTest(bad=bad):
                with self.assertRaises(SourceBlockError):
                    render_source_block(bad, path="src/x.txt", start_line=1, end_line=1, language="text")

    def test_materializer_reads_same_source_once_and_preserves_slot_order(self):
        source = b"one\ntwo\n"
        calls = []

        def read(path):
            calls.append(path)
            return source

        plans = [
            SourceBlockPlan("first", "src/shared.txt", 1, 1, "text", expected_source_sha256=sha(source)),
            SourceBlockPlan("second", "src/shared.txt", 2, 2, "text", expected_source_sha256=sha(source)),
        ]
        output, audit = materialize_reader_sources(
            "课程\n@@source:first@@\n中间\n@@source:second@@\n".encode("utf-8"), plans, read
        )
        result = output.decode("utf-8")
        self.assertEqual(calls, ["src/shared.txt"])
        self.assertIn("> **源码位置**：`src/shared.txt`，第 1–1 行", result)
        self.assertLess(result.index("第 1–1 行"), result.index("中间"))
        self.assertLess(result.index("中间"), result.index("第 2–2 行"))
        self.assertEqual(len(audit["source_blocks"]), 2)
        self.assertEqual([item["slot_id"] for item in audit["source_blocks"]], ["first", "second"])
        self.assertEqual(audit["review_state_effect"], "UNCHANGED")
        self.assertEqual(audit["semantic_review"], "NOT_PERFORMED")

    def test_required_symbol_preflight_rejects_truncated_python_function(self):
        source = (
            "from typing import Any\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def request_path(request: Any) -> str:\n"
            '    \"\"\"Return the projected request path.\"\"\"\n'
            "    return request.scope[\"path\"]\n"
        ).encode("utf-8")
        required = RequiredSourceSymbol("backend/app/gateway/request_path.py", "request_path", 7, 9)
        calls = []

        def read(path):
            calls.append(path)
            return source

        template = b"@@source:helper@@\n"
        partial = SourceBlockPlan("helper", required.path, 1, 8, "python")
        with self.assertRaisesRegex(SourceBlockError, "request_path.*first missing line 9"):
            materialize_reader_sources(template, [partial], read, required_symbols=(required,))
        self.assertEqual(calls, [])

        complete = SourceBlockPlan("helper", required.path, 1, 9, "python")
        output, audit = materialize_reader_sources(
            template, [complete], read, required_symbols=(required,)
        )
        self.assertIn(b'    return request.scope["path"]\n', output)
        self.assertIn("第 1–9 行", output.decode("utf-8"))
        self.assertEqual(len(audit["source_blocks"]), 1)

        # Without a completeness declaration, existing partial evidence remains valid.
        partial_output, _ = materialize_reader_sources(template, [partial], read)
        self.assertNotIn(b"return request.scope", partial_output)

    def test_required_symbol_coverage_can_be_split_across_contiguous_java_ranges(self):
        source = (
            b"class Box {\n"
            b"  int total() {\n"
            b"    return 1;\n"
            b"  }\n"
            b"}\n"
        )
        symbol = RequiredSourceSymbol("src/Box.java", "Box.total", 2, 4)
        plans = (
            SourceBlockPlan("head", symbol.path, 1, 2, "java"),
            SourceBlockPlan("tail", symbol.path, 3, 5, "java"),
        )
        output, audit = materialize_reader_sources(
            b"@@source:head@@\n@@source:tail@@\n",
            plans,
            lambda _path: source,
            required_symbols=(symbol,),
        )
        self.assertIn(b"  int total() {\n", output)
        self.assertIn(b"    return 1;\n", output)
        self.assertEqual(len(audit["source_blocks"]), 2)

    def test_required_symbol_reports_first_gap_across_planned_ranges(self):
        source = b"one\ntwo\nthree\nfour\nfive\n"
        symbol = RequiredSourceSymbol("src/source.txt", "operation", 2, 4)
        plans = (
            SourceBlockPlan("first", symbol.path, 1, 2, "text"),
            SourceBlockPlan("last", symbol.path, 4, 5, "text"),
        )
        calls = []
        with self.assertRaisesRegex(SourceBlockError, "operation.*first missing line 3"):
            materialize_reader_sources(
                b"@@source:first@@\n@@source:last@@\n",
                plans,
                lambda path: calls.append(path) or source,
                required_symbols=(symbol,),
            )
        self.assertEqual(calls, [])

    def test_materializer_rejects_source_slots_inside_tilde_and_backtick_fences(self):
        source = b"source line\n"
        calls = []

        def read(path):
            calls.append(path)
            return source

        plan = SourceBlockPlan("slot", "src/source.txt", 1, 1, "text")
        templates = (
            b"~~~source\n@@source:slot@@\n~~~\n",
            b"```text\n@@source:slot@@\n```\n",
        )
        for template in templates:
            with self.subTest(template=template):
                with self.assertRaisesRegex(SourceBlockError, "outside a fenced code block"):
                    materialize_reader_sources(template, [plan], read)
        self.assertEqual(calls, [])

    def test_short_or_different_fences_do_not_close_source_slot_context(self):
        source = b"source line\n"
        plan = SourceBlockPlan("slot", "src/source.txt", 1, 1, "text")
        templates = (
            b"````text\n```\n@@source:slot@@\n````\n",
            b"~~~~source\n```\n@@source:slot@@\n~~~~\n",
            b"~~~~source\n~~~\n@@source:slot@@\n~~~~\n",
        )
        for template in templates:
            with self.subTest(template=template):
                with self.assertRaisesRegex(SourceBlockError, "outside a fenced code block"):
                    materialize_reader_sources(template, [plan], lambda _path: source)

    def test_bare_slot_after_closed_fence_keeps_existing_materialization_bytes(self):
        source = b"source line\n"
        plan = SourceBlockPlan("slot", "src/source.txt", 1, 1, "text")
        block, _ = render_source_block(source, path=plan.path, start_line=1, end_line=1, language="text")
        prefix = b"~~~text\nordinary code\n~~~   \n"
        output, _ = materialize_reader_sources(prefix + b"@@source:slot@@\n", [plan], lambda _path: source)
        self.assertEqual(output, prefix + block.encode("utf-8"))

        invalid_backtick_opener = b"```info`with-backtick\n@@source:slot@@\n"
        output, _ = materialize_reader_sources(invalid_backtick_opener, [plan], lambda _path: source)
        self.assertEqual(output, invalid_backtick_opener.splitlines(keepends=True)[0] + block.encode("utf-8"))

    def test_slot_path_range_and_digest_errors_never_return_output(self):
        source = b"only one line\n"
        calls = []

        def read(path):
            calls.append(path)
            return source

        invalid_cases = (
            (b"@@source:slot@@\n", [SourceBlockPlan("missing", "src/a", 1, 1, "text")]),
            (b"@@source:slot@@\n@@source:slot@@\n", [SourceBlockPlan("slot", "src/a", 1, 1, "text")]),
            (b"@@source:bad id@@\n", [SourceBlockPlan("bad id", "src/a", 1, 1, "text")]),
            (b"@@source:slot@@\n", [SourceBlockPlan("slot", "../secret", 1, 1, "text")]),
        )
        for template, plans in invalid_cases:
            with self.subTest(template=template):
                with self.assertRaises(SourceBlockError):
                    materialize_reader_sources(template, plans, read)
        self.assertEqual(calls, [])

        for plan in (
            SourceBlockPlan("slot", "src/a", 1, 2, "text"),
            SourceBlockPlan("slot", "src/a", 1, 1, "text", expected_source_sha256="0" * 64),
            SourceBlockPlan("slot", "src/a", 1, 1, "text", expected_excerpt_sha256="0" * 64),
        ):
            with self.subTest(plan=plan):
                with self.assertRaises(SourceBlockError):
                    materialize_reader_sources(b"@@source:slot@@\n", [plan], read)


if __name__ == "__main__":
    unittest.main()
