#!/usr/bin/env python3
"""Count source-line explanation coverage in a reader lesson; never judge semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

import reader_handbook_review
import reader_source_blocks


_CAPTION_PREFIX = "> **源码位置**："
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LINE_RANGE = re.compile(r"^\s*(?:L\s*)?(\d+)(?:\s*(?:-|–|—|至)\s*(?:L\s*)?(\d+))?\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+\S")
_BOLD_HEADING = re.compile(r"^\s*(?:\*\*.+\*\*|__.+__)\s*$")


class CoverageInputError(ValueError):
    """Raised for malformed UTF-8, JSON, or reader-source manifest inputs."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CoverageInputError("duplicate JSON key")
        result[key] = value
    return result


def load_source_manifest(path: str | Path) -> dict[str, Any]:
    """Strictly parse and shape-check a v1.0 reader source manifest only."""
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(CoverageInputError("invalid JSON constant")),
        )
    except CoverageInputError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoverageInputError("source manifest is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CoverageInputError("source manifest root must be an object")
    revision = value.get("source_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise CoverageInputError("source manifest revision is missing")
    try:
        reader_handbook_review.validate_reader_theme_source_manifest(
            value, source_revision=revision,
        )
        # Keep the parsed JSON shape; the validator's normalized annotations
        # use integer keys and are not themselves a valid raw manifest shape.
        return value
    except (reader_handbook_review.ReaderHandbookReviewError, TypeError, ValueError) as exc:
        raise CoverageInputError("source manifest shape is invalid") from exc


def _caption(block: dict[str, Any]) -> str:
    path = reader_source_blocks._path_code_span(block["path"])
    start, end = block["start_line"], block["end_line"]
    return f"{_CAPTION_PREFIX}{path}，第 {start}–{end} 行"


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    cells: list[str] = []
    current: list[str] = []
    backslashes = 0
    for character in value:
        if character == "|" and backslashes % 2 == 0:
            cells.append("".join(current).strip())
            current = []
            backslashes = 0
            continue
        current.append(character)
        if character == "\\":
            backslashes += 1
        else:
            backslashes = 0
    cells.append("".join(current).strip())
    if value.startswith("|"):
        cells.pop(0)
    if value.endswith("|"):
        trailing_backslashes = 0
        for character in reversed(value[:-1]):
            if character != "\\":
                break
            trailing_backslashes += 1
        if trailing_backslashes % 2 == 0:
            cells.pop()
    return cells


def _is_table_candidate(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and "|" in lines[index + 1]
        and len(_split_table_row(lines[index])) >= 2
        and len(_split_table_row(lines[index + 1])) >= 2
    )


def _parse_table(lines: list[str], index: int) -> dict[str, Any]:
    header = _split_table_row(lines[index])
    separator = _split_table_row(lines[index + 1])
    separator_ok = len(header) == len(separator) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator
    )
    rows: list[tuple[int, list[str]]] = []
    cursor = index + 2
    while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
        rows.append((cursor, _split_table_row(lines[cursor])))
        cursor += 1
    return {
        "start": index,
        "end": cursor,
        "header": header,
        "separator_ok": separator_ok,
        "rows": rows,
    }


def _scan_markdown(lines: list[str]) -> tuple[list[dict[str, Any]], list[tuple[int, str]], list[dict[str, Any]]]:
    fences: list[dict[str, Any]] = []
    captions: list[tuple[int, str]] = []
    tables: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        opener = _FENCE_OPEN.match(lines[index])
        if opener:
            run = opener.group(1)
            marker, size = run[0], len(run)
            close_index: int | None = None
            cursor = index + 1
            while cursor < len(lines):
                candidate = re.match(r"^ {0,3}(`+|~+)[ \t]*$", lines[cursor])
                if candidate and candidate.group(1)[0] == marker and len(candidate.group(1)) >= size:
                    close_index = cursor
                    break
                cursor += 1
            fences.append({"start": index, "end": close_index})
            index = len(lines) if close_index is None else close_index + 1
            continue
        if lines[index].startswith(_CAPTION_PREFIX):
            captions.append((index, lines[index]))
        if _is_table_candidate(lines, index):
            table = _parse_table(lines, index)
            tables.append(table)
            index = max(index + 1, table["end"])
            continue
        index += 1
    return fences, captions, tables


def _line_ranges(table: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    intervals: list[tuple[int, int]] = []
    outside: list[tuple[int, int]] = []
    malformed = 0
    for _line, cells in table["rows"]:
        if len(cells) != len(table["header"]) or len(cells) < 2:
            malformed += 1
            continue
        match = _LINE_RANGE.fullmatch(cells[0])
        if not match:
            malformed += 1
            continue
        first = int(match.group(1))
        last = int(match.group(2) or match.group(1))
        if last < first:
            malformed += 1
            continue
        if first < start:
            outside.append((first, min(last, start - 1)))
        if last > end:
            outside.append((max(first, end + 1), last))
        low, high = max(first, start), min(last, end)
        if low <= high:
            intervals.append((low, high))

    intervals.sort()
    merged: list[tuple[int, int]] = []
    sum_covered = 0
    for low, high in intervals:
        sum_covered += high - low + 1
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    covered = sum(high - low + 1 for low, high in merged)
    missing: list[list[int]] = []
    cursor = start
    for low, high in merged:
        if cursor < low:
            missing.append([cursor, low - 1])
        cursor = max(cursor, high + 1)
    if cursor <= end:
        missing.append([cursor, end])

    outside.sort()
    merged_outside: list[tuple[int, int]] = []
    for low, high in outside:
        if low > high:
            continue
        if merged_outside and low <= merged_outside[-1][1] + 1:
            merged_outside[-1] = (merged_outside[-1][0], max(merged_outside[-1][1], high))
        else:
            merged_outside.append((low, high))
    return {
        "covered_lines": covered,
        "missing_ranges": missing,
        "out_of_range_ranges": [[low, high] for low, high in merged_outside],
        "duplicate_line_count": sum_covered - covered,
        "malformed_line_range_count": malformed,
    }


def analyze_source_explanation_coverage(
    lesson_text: str,
    source_manifest: dict[str, Any],
    *,
    include_answers: bool = False,
) -> dict[str, Any]:
    """Return numeric coverage; use include_answers for a lesson with answers embedded."""
    if not isinstance(lesson_text, str):
        raise CoverageInputError("lesson must be decoded UTF-8 text")
    revision = source_manifest.get("source_revision") if isinstance(source_manifest, dict) else None
    if not isinstance(revision, str) or not revision.strip():
        raise CoverageInputError("source manifest revision is missing")
    try:
        manifest = reader_handbook_review.validate_reader_theme_source_manifest(
            source_manifest, source_revision=revision,
        )
    except (reader_handbook_review.ReaderHandbookReviewError, TypeError, ValueError) as exc:
        raise CoverageInputError("source manifest shape is invalid") from exc

    manifest_blocks = manifest["source_blocks"]
    answer_blocks = [row for row in manifest_blocks if row["manuscript"] == "answers.md"]
    blocks = [row for row in manifest_blocks if row["manuscript"] == "lesson.md"]
    if include_answers:
        blocks += answer_blocks
    excluded_answer_count = 0 if include_answers else len(answer_blocks)
    lines = lesson_text.splitlines()
    fences, captions, tables = _scan_markdown(lines)
    expected: dict[str, list[int]] = {}
    for block_index, block in enumerate(blocks):
        expected.setdefault(_caption(block), []).append(block_index)
    occurrences: dict[int, list[int]] = {i: [] for i in range(len(blocks))}
    unrecognized_caption_count = 0
    for line_index, caption in captions:
        indexes = expected.get(caption)
        if indexes is None:
            unrecognized_caption_count += 1
            continue
        for block_index in indexes:
            occurrences[block_index].append(line_index)

    duplicate_caption_count = sum(max(0, len(rows) - 1) for rows in occurrences.values())
    collision_count = sum(max(0, len(indexes) - 1) for indexes in expected.values())
    duplicate_caption_count += collision_count
    next_caption_by_line: dict[int, int | None] = {}
    for pos, (line_index, _caption_text) in enumerate(captions):
        next_caption_by_line[line_index] = captions[pos + 1][0] if pos + 1 < len(captions) else None

    table_used: set[int] = set()
    block_reports: list[dict[str, Any]] = []
    totals = {
        "missing_caption_count": 0,
        "missing_source_fence_count": 0,
        "missing_table_count": 0,
        "unrecognized_table_count": 0,
        "malformed_line_range_count": 0,
        "duplicate_line_count": 0,
        "expected_line_count": 0,
        "covered_line_count": 0,
        "missing_line_count": 0,
        "out_of_range_line_count": 0,
    }

    for block_index, block in enumerate(blocks):
        start, end = block["start_line"], block["end_line"]
        expected_count = end - start + 1
        totals["expected_line_count"] += expected_count
        matched_captions = occurrences[block_index]
        if not matched_captions:
            totals["missing_caption_count"] += 1
        caption_line = matched_captions[0] if matched_captions else None
        boundary = next_caption_by_line.get(caption_line) if caption_line is not None else None
        source_fence = next((f for f in fences if caption_line is not None and f["start"] > caption_line and (boundary is None or f["start"] < boundary)), None)
        if source_fence is None or source_fence["end"] is None:
            totals["missing_source_fence_count"] += 1
            close_line = None
            table = None
        else:
            close_line = source_fence["end"]
            next_caption = next((line for line, _text in captions if line > close_line), None)
            table = next((t for t in tables if close_line < t["start"] and (next_caption is None or t["start"] < next_caption)), None)

        table_valid = False
        table_count = 0
        range_data = {
            "covered_lines": 0,
            "missing_ranges": [[start, end]],
            "out_of_range_ranges": [],
            "duplicate_line_count": 0,
            "malformed_line_range_count": 0,
        }
        if table is None:
            totals["missing_table_count"] += 1
        else:
            table_count = 1
            table_used.add(table["start"])
            before_table = lines[close_line + 1:table["start"]] if close_line is not None else []
            allowed_gap = all(not line.strip() or _HEADING.match(line) or _BOLD_HEADING.match(line) for line in before_table)
            first_header = table["header"][0].strip(" `*_ ") if table["header"] else ""
            table_valid = allowed_gap and table["separator_ok"] and first_header == "源码行" and len(table["header"]) >= 2
            if not table_valid:
                totals["unrecognized_table_count"] += 1
            else:
                range_data = _line_ranges(table, start, end)
                totals["malformed_line_range_count"] += range_data["malformed_line_range_count"]
                totals["duplicate_line_count"] += range_data["duplicate_line_count"]

        missing_ranges = range_data["missing_ranges"]
        out_ranges = range_data["out_of_range_ranges"]
        covered = range_data["covered_lines"]
        missing_count = expected_count - covered
        out_count = sum(high - low + 1 for low, high in out_ranges)
        totals["covered_line_count"] += covered
        totals["missing_line_count"] += missing_count
        totals["out_of_range_line_count"] += out_count

        ambiguous = len(expected[_caption(block)]) > 1
        block_ok = (
            len(matched_captions) == 1
            and not ambiguous
            and source_fence is not None
            and source_fence["end"] is not None
            and table is not None
            and table_valid
            and not missing_ranges
            and not out_ranges
            and range_data["duplicate_line_count"] == 0
            and range_data["malformed_line_range_count"] == 0
        )
        block_reports.append({
            "block_index": block_index + 1,
            "status": "COVERED" if block_ok else "INCOMPLETE",
            "caption_occurrences": len(matched_captions),
            "table_count": table_count,
            "expected_lines": expected_count,
            "covered_lines": covered,
            "missing_ranges": missing_ranges,
            "out_of_range_ranges": out_ranges,
            "duplicate_line_count": range_data["duplicate_line_count"],
            "malformed_line_range_count": range_data["malformed_line_range_count"],
        })

    unmatched_table_count = sum(
        1 for table in tables
        if table["start"] not in table_used
        and table["header"]
        and table["header"][0].strip(" `*_ ") == "源码行"
    )
    covered_blocks = sum(1 for row in block_reports if row["status"] == "COVERED")
    incomplete_blocks = len(blocks) - covered_blocks
    status = "COVERED" if (
        bool(blocks)
        and excluded_answer_count == 0
        and incomplete_blocks == 0
        and unrecognized_caption_count == 0
        and unmatched_table_count == 0
    ) else "INCOMPLETE"
    return {
        "schema_version": "reader-source-explanation-coverage/1.1",
        "status": status,
        "coverage_scope": "assembled_chapter" if include_answers else "lesson_only",
        "semantic_review": "NOT_PERFORMED",
        "source_authentication": "NOT_PERFORMED",
        "manifest_source_block_count": len(manifest_blocks),
        "source_block_count": len(blocks),
        "excluded_answer_source_block_count": excluded_answer_count,
        "covered_source_block_count": covered_blocks,
        "incomplete_source_block_count": incomplete_blocks,
        "unrecognized_caption_count": unrecognized_caption_count,
        "duplicate_caption_count": duplicate_caption_count,
        "unmatched_source_table_count": unmatched_table_count,
        **totals,
        "blocks": block_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lesson", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument(
        "--assembled-chapter",
        action="store_true",
        help="include answers.md source blocks; --lesson must contain the complete chapter with answers embedded",
    )
    parser.add_argument("--output", default="-", help="report path (must not exist) or - for stdout")
    args = parser.parse_args(argv)
    try:
        lesson = args.lesson.read_bytes().decode("utf-8", errors="strict")
        sources = load_source_manifest(args.sources)
        report = analyze_source_explanation_coverage(
            lesson, sources, include_answers=args.assembled_chapter,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output == "-":
            sys.stdout.write(rendered)
        else:
            with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
    except (OSError, UnicodeDecodeError, CoverageInputError) as exc:
        print(f"SOURCE_EXPLANATION_COVERAGE_ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
