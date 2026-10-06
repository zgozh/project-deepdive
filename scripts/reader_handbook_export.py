#!/usr/bin/env python3
"""Render supported general teaching Markdown into a Chinese reader draft.

This is a deterministic presentation step. It does not write teaching content,
validate evidence, or change the review status of its input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RENDERER_VERSION = "1.0.0"

LESSON_HEADINGS = {
    "intuition": "直觉",
    "new concepts": "新概念",
    "prerequisite ideas": "前置概念",
    "worked example": "示例推演",
    "common misconception": "常见误解",
    "check yourself": "自我检查",
}
ANSWER_HEADINGS = {
    "question": "问题",
    "answer": "解答",
    "hint": "提示",
    "hints": "提示",
    "rubric": "评分要点",
}
LABELS = {
    "question": "问题",
    "answer": "解答",
    "hint": "提示",
    "hints": "提示",
    "rubric": "评分要点",
}

ATX_HEADING_RE = re.compile(r"^(?P<indent> {0,3})(?P<marks>#{1,6})[ \t]+(?P<body>.*?)(?P<newline>\r?\n)?$")
FENCE_OPEN_RE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)(?P<newline>\r?\n)?$")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?:\r?\n)?$")
SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*(?:\r?\n)?$")
INLINE_PREFIX_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:[-*+] |[0-9]+[.)] )?)(?P<body>.*?)(?P<newline>\r?\n)?$"
)
MACHINE_REF_PATTERNS = (
    re.compile(r"\b(?:CLAIM|EVID|EVIDENCE)[-_][A-Za-z0-9][A-Za-z0-9._:/-]*\b"),
    re.compile(r"\b(?:CLAIM|EVID|EVIDENCE)[-_](?=[\s.,;:)\]}]|$)"),
    re.compile(r"\b(?:CLAIM|EVID|EVIDENCE)(?:ID)?\s*[:=#]\s*[A-Za-z0-9][A-Za-z0-9._:/-]*\b"),
    re.compile(r"\b(?:CLAIM|EVID|EVIDENCE)(?:ID)?\s*[:=#](?=[\s,.;]|$)"),
    re.compile(r"\[\^[^\]\r\n]*\]"),
    re.compile(r"\[\^[^\]\r\n]*(?=$|\s)"),
    re.compile(r"^ {0,3}\[\^[^\]\r\n]*\]:", re.MULTILINE),
)
KNOWN_DRAFT_BANNER = "draft teaching material - not independently reviewed or verified."


class ExportError(ValueError):
    """Raised when the input is outside the renderer's deliberately small scope."""


@dataclass(frozen=True)
class RenderResult:
    text: str
    heading_mappings: list[dict[str, object]]
    label_mappings: list[dict[str, object]]
    original_title: str
    input_review_state: str
    source_metadata: list[dict[str, object]]


def _validate_controls(text: str, source: str) -> None:
    for index, char in enumerate(text):
        if char == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                continue
            raise ExportError(f"{source} contains an isolated CR control character")
        if char in ("\n", "\t"):
            continue
        if unicodedata.category(char) == "Cc":
            raise ExportError(f"{source} contains an unsupported control character U+{ord(char):04X}")


def _normalized_heading(body: str) -> str:
    value = body.strip()
    value = re.sub(r"[ \t]+#+[ \t]*$", "", value).strip()
    return re.sub(r"\s+", " ", value).casefold()


def _has_machine_reference(line: str) -> bool:
    return any(pattern.search(line) for pattern in MACHINE_REF_PATTERNS)


def _translate_inline_label(line: str, line_number: int) -> tuple[str, str, str] | None:
    match = INLINE_PREFIX_RE.match(line)
    if match is None:
        return None
    prefix = match.group("prefix")
    body = match.group("body")
    newline = match.group("newline") or ""
    marker = body[:2] if body[:2] in {"**", "__"} else ""
    label_body = body[len(marker):] if marker else body

    for source_label in sorted(LABELS, key=len, reverse=True):
        if label_body[:len(source_label)].casefold() != source_label:
            continue
        suffix = label_body[len(source_label):]
        if suffix.startswith((":", "：")):
            colon = suffix[0]
            rest = suffix[1:]
            if marker:
                if rest.startswith(marker):
                    rendered = f"{prefix}{marker}{LABELS[source_label]}{colon}{marker}{rest[2:]}{newline}"
                else:
                    raise ExportError(f"malformed emphasis around teaching label at line {line_number}")
            else:
                rendered = f"{prefix}{LABELS[source_label]}{colon}{rest}{newline}"
            return rendered, source_label, LABELS[source_label]
        if marker and suffix.startswith(marker):
            rest = suffix[len(marker):]
            if rest.startswith((":", "：")):
                return f"{prefix}{marker}{LABELS[source_label]}{marker}{rest}{newline}", source_label, LABELS[source_label]
            if len(rest) > 0 and rest[0] in {"*", "_"} and re.match(r"[:：]", rest[1:]):
                raise ExportError(f"malformed emphasis around teaching label at line {line_number}")
    return None


def _is_draft_banner(line: str) -> bool:
    candidate = line.strip()
    if candidate.startswith("> "):
        candidate = candidate[2:].strip()
    return candidate.casefold() == KNOWN_DRAFT_BANNER


def _translate_heading(kind: str, level: int, body: str) -> str | None:
    key = _normalized_heading(body)
    heading_map = LESSON_HEADINGS if kind == "general-lesson" else ANSWER_HEADINGS
    if key in heading_map:
        return heading_map[key]
    if kind == "general-lesson" and level == 3:
        match = re.fullmatch(r"(new concept|prerequisite idea) ([1-9][0-9]*)", key)
        if match:
            label = "新概念" if match.group(1) == "new concept" else "前置概念"
            return f"{label} {match.group(2)}"
    return None


def render_markdown(kind: str, title: str, source_text: str) -> RenderResult:
    """Render a supported general lesson or answer while preserving body text."""
    if kind == "canonical-v2":
        raise ExportError("canonical-v2 rendering is not implemented")
    if kind not in {"general-lesson", "general-answer"}:
        raise ExportError(f"unsupported kind: {kind}")

    title = title.strip()
    if not title:
        raise ExportError("title must not be empty")
    _validate_controls(title, "title")
    if "\n" in title or "\r" in title:
        raise ExportError("title must be a single line")

    _validate_controls(source_text, "input Markdown")
    # Preserve an optional UTF-8 BOM while letting the heading parser see '#'.
    bom = "\ufeff" if source_text.startswith("\ufeff") else ""
    if bom:
        source_text = source_text[1:]

    lines = source_text.splitlines(keepends=True)
    output: list[str] = []
    heading_mappings: list[dict[str, object]] = []
    label_mappings: list[dict[str, object]] = []
    source_metadata: list[dict[str, object]] = []
    h1_count = 0
    source_title = ""
    input_review_state = "unknown"
    in_fence: tuple[str, int] | None = None
    previous_plain_line = ""

    for line_number, line in enumerate(lines, start=1):
        if in_fence is not None:
            fence_char, fence_length = in_fence
            close = FENCE_CLOSE_RE.match(line)
            if close and close.group("fence")[0] == fence_char and len(close.group("fence")) >= fence_length:
                in_fence = None
            output.append(line)
            previous_plain_line = ""
            continue

        opener = FENCE_OPEN_RE.match(line)
        fence_like = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opener:
            fence_token = opener.group("fence")
            info = opener.group("info")
            if fence_token[0] == "`" and "`" in info:
                raise ExportError(f"malformed code fence at line {line_number}")
            in_fence = (fence_token[0], len(fence_token))
            output.append(line)
            previous_plain_line = ""
            continue
        if fence_like:
            raise ExportError(f"malformed code fence at line {line_number}")

        if _is_draft_banner(line):
            source_metadata.append({
                "kind": "source-draft-banner",
                "line": line_number,
                "original_text": line.rstrip("\r\n"),
            })
            input_review_state = "NOT_REVIEWED: detected source draft banner"
            continue

        if _has_machine_reference(line):
            raise ExportError(f"unsupported machine reference outside a code fence at line {line_number}")

        heading = ATX_HEADING_RE.match(line)
        if heading:
            level = len(heading.group("marks"))
            raw_heading = heading.group("body").strip()
            newline = heading.group("newline") or ""
            if level == 1:
                h1_count += 1
                if h1_count != 1:
                    raise ExportError("input must have exactly one level-one title heading")
                if any(part.strip() for part in output):
                    raise ExportError("the level-one title must be the first non-empty line")
                source_title = raw_heading
                escaped_title = _escape_heading_text(title)
                output.append(f"{heading.group('indent')}# {escaped_title}{newline}")
                heading_mappings.append({"line": line_number, "from": raw_heading, "to": title, "role": "title"})
            else:
                translated = _translate_heading(kind, level, heading.group("body"))
                if translated is None:
                    raise ExportError(f"unsupported {kind} heading at line {line_number}: {raw_heading}")
                output.append(f"{heading.group('indent')}{heading.group('marks')} {translated}{newline}")
                heading_mappings.append({"line": line_number, "from": raw_heading, "to": translated, "role": "section"})
            previous_plain_line = ""
            continue

        if SETEXT_RE.match(line):
            if previous_plain_line.strip():
                raise ExportError(f"setext headings are unsupported at line {line_number}")

        label = _translate_inline_label(line, line_number)
        if label:
            rendered_line, source_label, translated = label
            output.append(rendered_line)
            label_mappings.append({"line": line_number, "from": source_label, "to": translated})
            previous_plain_line = ""
            continue

        output.append(line)
        previous_plain_line = line

    if in_fence is not None:
        raise ExportError("input ends inside an unclosed code fence")
    if h1_count != 1:
        raise ExportError("input must have exactly one level-one title heading")

    return RenderResult(
        text=bom + "".join(output),
        heading_mappings=heading_mappings,
        label_mappings=label_mappings,
        original_title=source_title,
        input_review_state=input_review_state,
        source_metadata=source_metadata,
    )


def _escape_heading_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#+.!|])", r"\\\1", value)


def _resolved_path(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve(strict=False)


def _validate_distinct_paths(input_path: Path, output_path: Path, audit_path: Path) -> None:
    resolved = [os.path.normcase(str(path)) for path in (input_path, output_path, audit_path)]
    if len(set(resolved)) != len(resolved):
        raise ExportError("input, output, and audit paths must resolve to three different paths")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_file(
    *,
    kind: str,
    input_arg: str,
    output_arg: str,
    audit_arg: str,
    title: str,
    revision: str = "unknown",
    overwrite: bool = False,
) -> dict[str, object]:
    input_path = _resolved_path(input_arg)
    output_path = _resolved_path(output_arg)
    audit_path = _resolved_path(audit_arg)
    _validate_distinct_paths(input_path, output_path, audit_path)

    if kind == "canonical-v2":
        raise ExportError("canonical-v2 rendering is not implemented")
    if kind not in {"general-lesson", "general-answer"}:
        raise ExportError(f"unsupported kind: {kind}")
    if not input_path.exists() or not stat.S_ISREG(input_path.stat().st_mode):
        raise ExportError(f"input is not a regular file: {input_path}")
    for destination in (output_path, audit_path):
        if destination.exists():
            if not overwrite:
                raise ExportError(f"destination already exists (pass --overwrite to replace it): {destination}")
            if not stat.S_ISREG(destination.stat().st_mode):
                raise ExportError(f"destination is not a regular file: {destination}")

    input_bytes = input_path.read_bytes()
    try:
        input_text = input_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError("input must be UTF-8 Markdown") from exc
    rendered = render_markdown(kind, title, input_text)
    output_bytes = rendered.text.encode("utf-8")
    audit: dict[str, object] = {
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "kind": kind,
        "title": title.strip(),
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_sha256": _sha256(input_bytes),
        "output_sha256": _sha256(output_bytes),
        "revision": revision.strip() or "unknown",
        "input_review_state": rendered.input_review_state,
        "source_metadata": rendered.source_metadata,
        "output_teaching_depth": "NOT_REVIEWED",
        "review_status_raised": False,
        "source_title": rendered.original_title,
        "heading_mappings": rendered.heading_mappings,
        "label_mappings": rendered.label_mappings,
    }
    audit_bytes = (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # All path, format, text, and collision checks finish before either artifact is written.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)
    audit_path.write_bytes(audit_bytes)
    return audit


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("canonical-v2", "general-lesson", "general-answer"))
    parser.add_argument("--input", required=True, dest="input_path", help="source Markdown file")
    parser.add_argument("--output", required=True, help="reader-facing staging Markdown file")
    parser.add_argument("--audit", required=True, help="separate provenance JSON path")
    parser.add_argument("--title", required=True, help="Chinese reader-facing title")
    parser.add_argument("--revision", default="unknown", help="input revision identifier, or unknown")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output and audit files")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        export_file(
            kind=args.kind,
            input_arg=args.input_path,
            output_arg=args.output,
            audit_arg=args.audit,
            title=args.title,
            revision=args.revision,
            overwrite=args.overwrite,
        )
    except (ExportError, OSError) as exc:
        print(f"reader handbook export: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
