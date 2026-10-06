"""Check that renderer-prepared source blocks remain byte-identical in a chapter."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import sys

from reader_source_blocks import SourceBlockError, strip_source_overlays


class SourceBlockVerificationError(ValueError):
    """Raised when a prepared source block is missing, duplicated as proof, or changed."""


def _caption_positions(chapter: bytes, caption: bytes) -> list[int]:
    positions: list[int] = []
    line_start = 0
    while line_start <= len(chapter):
        line_end = chapter.find(b"\n", line_start)
        if line_end < 0:
            line = chapter[line_start:]
        else:
            line = chapter[line_start:line_end]
        if line.endswith(b"\r"):
            line = line[:-1]
        if line == caption:
            positions.append(line_start)
        if line_end < 0:
            break
        line_start = line_end + 1
    return positions


def _next_line_suffix(chapter: bytes, start: int, suffix: bytes) -> int:
    position = chapter.find(suffix, start)
    while position >= 0:
        if position == 0 or chapter[position - 1 : position] == b"\n":
            return position
        position = chapter.find(suffix, position + 1)
    return -1


def _framing(provenance: Mapping[str, object], index: int) -> tuple[bytes, bytes, bytes]:
    caption = provenance.get("caption")
    opening = provenance.get("opening_fence")
    closing = provenance.get("closing_fence")
    external = provenance.get("external_overlay_lines")
    if (
        not isinstance(caption, str)
        or not isinstance(opening, str)
        or not isinstance(closing, str)
        or not isinstance(external, list)
        or not all(isinstance(line, str) for line in external)
    ):
        raise SourceBlockVerificationError(f"provenance item {index} has malformed block framing")
    try:
        caption_bytes = caption.encode("utf-8", errors="strict")
        prefix = (caption + "\n" + "".join(external) + opening + "\n").encode("utf-8", errors="strict")
        suffix = (closing + "\n").encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SourceBlockVerificationError(f"provenance item {index} is not valid UTF-8 text") from exc
    if not caption_bytes or b"\n" in caption_bytes or b"\r" in caption_bytes:
        raise SourceBlockVerificationError(f"provenance item {index} has an invalid caption")
    return caption_bytes, prefix, suffix


def verify_reader_source_blocks(
    chapter_bytes: bytes,
    provenance_items: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Verify every final-chapter occurrence for each supplied renderer provenance."""
    if not isinstance(chapter_bytes, bytes):
        raise SourceBlockVerificationError("chapter content must be read as bytes")
    if isinstance(provenance_items, (str, bytes)) or not isinstance(provenance_items, Sequence):
        raise SourceBlockVerificationError("provenance must be an array of renderer provenance objects")

    seen_captions: set[bytes] = set()
    verified_occurrences = 0
    for index, provenance in enumerate(provenance_items, start=1):
        if not isinstance(provenance, Mapping):
            raise SourceBlockVerificationError(f"provenance item {index} must be an object")
        caption, prefix, suffix = _framing(provenance, index)
        if caption in seen_captions:
            raise SourceBlockVerificationError(f"provenance repeats caption for item {index}")
        seen_captions.add(caption)

        positions = _caption_positions(chapter_bytes, caption)
        if not positions:
            raise SourceBlockVerificationError(f"source block {index} caption was not found in the final chapter")
        for occurrence, start in enumerate(positions, start=1):
            if not chapter_bytes.startswith(prefix, start):
                raise SourceBlockVerificationError(
                    f"source block {index} occurrence {occurrence} framing after its caption was changed"
                )
            body_start = start + len(prefix)
            closing_start = _next_line_suffix(chapter_bytes, body_start, suffix)
            if closing_start < 0:
                raise SourceBlockVerificationError(
                    f"source block {index} occurrence {occurrence} closing fence was not found"
                )
            block = chapter_bytes[start : closing_start + len(suffix)]
            try:
                strip_source_overlays(block, provenance)
            except SourceBlockError as exc:
                raise SourceBlockVerificationError(
                    f"source block {index} occurrence {occurrence} does not match its provenance: {exc}"
                ) from exc
            verified_occurrences += 1

    return {
        "supplied_provenance_count": len(provenance_items),
        "verified_block_occurrence_count": verified_occurrences,
        "scope": "Every final-chapter occurrence for each supplied renderer provenance was byte-checked.",
        "unchecked_scope": "Unprovenanced manual excerpts and all chapter content outside those blocks were not checked.",
        "source_authentication": "NOT_PERFORMED",
        "teaching_review": "NOT_PERFORMED",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check renderer-provenance source blocks in one final Markdown chapter."
    )
    parser.add_argument("--chapter", required=True, type=Path, help="local final Markdown chapter")
    parser.add_argument("--provenance", required=True, type=Path, help="local JSON array of renderer provenance")
    args = parser.parse_args(argv)

    try:
        chapter_bytes = args.chapter.read_bytes()
        provenance_value = json.loads(args.provenance.read_text(encoding="utf-8"))
        if not isinstance(provenance_value, list):
            raise SourceBlockVerificationError("provenance JSON must be an array")
        report = verify_reader_source_blocks(chapter_bytes, provenance_value)
    except (OSError, UnicodeError, json.JSONDecodeError, SourceBlockVerificationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
