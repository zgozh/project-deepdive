"""Deterministic rendering of frozen source excerpts into reader Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import io
import os
from pathlib import PureWindowsPath
import re
import tokenize
import unicodedata
from typing import Callable, Mapping, Sequence


RENDERER_VERSION = "reader-source-blocks-v1"
_SLOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{0,31}\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_JS_LANGS = {"js", "jsx", "javascript", "ts", "tsx", "typescript"}


class SourceBlockError(ValueError):
    """Raised when a source block or slot cannot be rendered faithfully."""


@dataclass(frozen=True)
class SourceBlockPlan:
    slot_id: str
    path: str
    start_line: int
    end_line: int
    language: str
    annotations: Mapping[int, str] = field(default_factory=dict)
    expected_source_sha256: str | None = None
    expected_excerpt_sha256: str | None = None
    revision: str | None = None


@dataclass(frozen=True)
class RequiredSourceSymbol:
    path: str
    name: str
    start_line: int
    end_line: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes, label: str) -> str:
    if not isinstance(data, bytes):
        raise SourceBlockError(f"{label} must be bytes")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SourceBlockError(f"{label} is not valid UTF-8") from exc
    for index, char in enumerate(text):
        if char == "\r" and (index + 1 == len(text) or text[index + 1] != "\n"):
            raise SourceBlockError(f"{label} contains an isolated CR")
        if unicodedata.category(char) == "Cc" and char not in "\r\n\t":
            raise SourceBlockError(f"{label} contains an unsupported control character")
    return text


def _physical_lines(text: str) -> list[str]:
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if not text.endswith("\n"):
        lines.append(parts[-1])
    return lines


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"


def _relative_path(value: str | os.PathLike[str]) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise SourceBlockError("source path must be a relative path") from exc
    if not isinstance(raw, str) or not raw:
        raise SourceBlockError("source path must be a non-empty string")
    if any(unicodedata.category(ch) == "Cc" for ch in raw):
        raise SourceBlockError("source path contains a control character")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(raw).is_absolute() or re.match(r"^[A-Za-z]:", normalized):
        raise SourceBlockError("source path must be relative")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SourceBlockError("source path has an empty, dot, or parent component")
    return "/".join(parts)


def _language(value: str) -> str:
    if not isinstance(value, str) or not _LANGUAGE.fullmatch(value):
        raise SourceBlockError("language must be a simple Markdown fence identifier")
    return value.lower()


def _hash_expectation(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SourceBlockError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _annotations(value: Mapping[int, str] | None, start: int, end: int) -> dict[int, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SourceBlockError("annotations must map absolute source line numbers to text")
    result: dict[int, str] = {}
    for line, note in value.items():
        if type(line) is not int or not start <= line <= end:
            raise SourceBlockError("annotation line must be an in-range 1-based integer")
        if not isinstance(note, str) or not note.strip() or "\r" in note or "\n" in note:
            raise SourceBlockError("annotation must be non-empty single-line text")
        if any(unicodedata.category(ch) == "Cc" for ch in note):
            raise SourceBlockError("annotation contains a control character")
        result[line] = note
    return result


def _validate_plan(plan: SourceBlockPlan) -> dict[str, object]:
    if not isinstance(plan, SourceBlockPlan):
        raise SourceBlockError("plans must contain SourceBlockPlan values")
    if not isinstance(plan.slot_id, str) or not _SLOT_ID.fullmatch(plan.slot_id):
        raise SourceBlockError("source slot id is invalid")
    if type(plan.start_line) is not int or type(plan.end_line) is not int or not (1 <= plan.start_line <= plan.end_line):
        raise SourceBlockError("source line range must be ordered and 1-based")
    path = _relative_path(plan.path)
    language = _language(plan.language)
    annotations = _annotations(plan.annotations, plan.start_line, plan.end_line)
    if annotations and language not in {"java", "python", "py", *_JS_LANGS}:
        raise SourceBlockError(f"inline or external annotations are unsupported for {language}")
    revision = plan.revision
    if revision is not None and (not isinstance(revision, str) or any(unicodedata.category(ch) == "Cc" for ch in revision)):
        raise SourceBlockError("revision must be single-line caller-provided text")
    return {
        "slot_id": plan.slot_id,
        "path": path,
        "start_line": plan.start_line,
        "end_line": plan.end_line,
        "language": language,
        "annotations": annotations,
        "expected_source_sha256": _hash_expectation(plan.expected_source_sha256, "expected_source_sha256"),
        "expected_excerpt_sha256": _hash_expectation(plan.expected_excerpt_sha256, "expected_excerpt_sha256"),
        "revision": revision,
    }


def _require_symbol_coverage(
    required_symbols: Sequence[RequiredSourceSymbol],
    validated_plans: Mapping[str, dict[str, object]],
) -> None:
    try:
        symbols = tuple(required_symbols)
    except TypeError as exc:
        raise SourceBlockError("required_symbols must be a sequence of RequiredSourceSymbol values") from exc

    plan_ranges: dict[str, list[tuple[int, int]]] = {}
    for plan in validated_plans.values():
        path = str(plan["path"])
        plan_ranges.setdefault(path, []).append((int(plan["start_line"]), int(plan["end_line"])))

    for symbol in symbols:
        if not isinstance(symbol, RequiredSourceSymbol):
            raise SourceBlockError("required_symbols must contain RequiredSourceSymbol values")
        path = _relative_path(symbol.path)
        name = symbol.name
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(unicodedata.category(char) == "Cc" for char in name)
        ):
            raise SourceBlockError("required source symbol name must be non-empty single-line text")
        start = symbol.start_line
        end = symbol.end_line
        if type(start) is not int or type(end) is not int or not (1 <= start <= end):
            raise SourceBlockError("required source symbol range must be ordered and 1-based")

        next_line = start
        for range_start, range_end in sorted(plan_ranges.get(path, ())):
            if range_end < next_line:
                continue
            if range_start > next_line:
                break
            next_line = max(next_line, range_end + 1)
            if next_line > end:
                break
        if next_line <= end:
            raise SourceBlockError(
                f"required source symbol {name!r} in {path} (lines {start}-{end}) "
                f"is not fully covered by source plans; first missing line {next_line}"
            )


def _python_annotation_safe(source_text: str, line_number: int) -> bool:
    lines = _physical_lines(source_text)
    if line_number > 1:
        previous = lines[line_number - 2].rstrip("\r\n")
        if (len(previous) - len(previous.rstrip("\\"))) % 2:
            return False
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source_text).readline)
        fstring_starts: list[int] = []
        for token in tokens:
            if token.type == tokenize.STRING and token.start[0] < line_number <= token.end[0]:
                return False
            if token.type == getattr(tokenize, "FSTRING_START", -1):
                fstring_starts.append(token.start[0])
            elif token.type == getattr(tokenize, "FSTRING_END", -1) and fstring_starts:
                first = fstring_starts.pop()
                if first < line_number <= token.end[0]:
                    return False
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False
    return True


def _java_annotation_safe(source_text: str, line_number: int) -> bool:
    prefix = "".join(_physical_lines(source_text)[: line_number - 1])
    # Conservatively skip ambiguous regions rather than introduce a Java lexer.
    return (
        not re.search(r"\\u+[0-9a-fA-F]{4}|\"\"\"", prefix)
        and prefix.rfind("/*") <= prefix.rfind("*/")
    )


def _path_code_span(path: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", path)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if path.startswith("`") or path.endswith("`") or path.startswith(" ") or path.endswith(" ") else ""
    return f"{fence}{pad}{path}{pad}{fence}"


def _markdown_note(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escaped)


def _fence(code_lines: Sequence[str], language: str) -> str:
    longest = 2
    for line in code_lines:
        raw = line.rstrip("\r\n")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= 3:
            run = re.match(r"~+", raw[indent:])
            if run:
                longest = max(longest, len(run.group(0)))
    return "~" * (longest + 1) + language


def render_source_block(
    source_bytes: bytes,
    *,
    path: str | os.PathLike[str],
    start_line: int,
    end_line: int,
    language: str,
    annotations: Mapping[int, str] | None = None,
    expected_source_sha256: str | None = None,
    expected_excerpt_sha256: str | None = None,
    revision: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Render a real source range and return Markdown plus body-free provenance."""
    descriptor = _validate_plan(
        SourceBlockPlan(
            "single", path, start_line, end_line, language, annotations,
            expected_source_sha256, expected_excerpt_sha256, revision,
        )
    )
    source_text = _decode(source_bytes, "source")
    source_lines = _physical_lines(source_text)
    start = int(descriptor["start_line"])
    end = int(descriptor["end_line"])
    if end > len(source_lines):
        raise SourceBlockError(f"source range {start}-{end} exceeds {len(source_lines)} physical lines")
    excerpt_bytes = "".join(source_lines[start - 1 : end]).encode("utf-8")
    source_hash = _sha256(source_bytes)
    excerpt_hash = _sha256(excerpt_bytes)
    expected_source = descriptor["expected_source_sha256"]
    expected_excerpt = descriptor["expected_excerpt_sha256"]
    if expected_source is not None and source_hash != expected_source:
        raise SourceBlockError("source SHA-256 does not match the expected digest")
    if expected_excerpt is not None and excerpt_hash != expected_excerpt:
        raise SourceBlockError("excerpt SHA-256 does not match the expected digest")

    normalized_path = str(descriptor["path"])
    lang = str(descriptor["language"])
    notes = dict(descriptor["annotations"])
    code_lines = source_lines[start - 1 : end]
    code_prefix = {"python": "#", "py": "#", "java": "//"}.get(lang)
    if lang == "java" and any(re.search(r"\\u+[0-9a-fA-F]{4}", note) for note in notes.values()):
        raise SourceBlockError("Java annotations may not contain Unicode escapes")
    if notes and code_prefix is not None:
        for line_number in notes:
            safe = (
                _python_annotation_safe(source_text, line_number)
                if lang in {"python", "py"}
                else _java_annotation_safe(source_text, line_number)
            )
            if not safe:
                raise SourceBlockError(f"refusing unsafe {lang} annotation at source line {line_number}")
    elif notes and lang not in _JS_LANGS:
        raise SourceBlockError(f"annotations are unsupported for {lang}")

    inline_records: list[dict[str, object]] = []
    external_records: list[dict[str, object]] = []
    inline_by_line: dict[int, tuple[str, str]] = {}
    external_text: list[str] = []
    for line_number, note in sorted(notes.items()):
        overlay_id = hashlib.sha256(
            f"{source_hash}\0{normalized_path}\0{line_number}\0{note}".encode("utf-8")
        ).hexdigest()
        if code_prefix is not None:
            source_line = source_lines[line_number - 1]
            indent = source_line[: len(source_line) - len(source_line.lstrip(" \t"))]
            overlay = f"{indent}{code_prefix} 教学：{note}"
            ending = _line_ending(source_line)
            inline_by_line[line_number] = (overlay, ending)
            inline_records.append(
                {"overlay_id": overlay_id, "source_line": line_number, "line_text": overlay, "line_ending": ending}
            )
        else:
            note_line = f"> 教学说明（源码第 {line_number} 行）：{_markdown_note(note)}\n"
            external_text.append(note_line)
            external_records.append({"overlay_id": overlay_id, "source_line": line_number, "lines": [note_line]})

    rendered_lines: list[str] = []
    for line_number, source_line in enumerate(code_lines, start=start):
        overlay = inline_by_line.get(line_number)
        if overlay:
            rendered_lines.append(overlay[0] + overlay[1])
            record = next(item for item in inline_records if item["source_line"] == line_number)
            record["body_line_index"] = len(rendered_lines)
        rendered_lines.append(source_line)
    framing_newline = not "".join(rendered_lines).endswith("\n")
    if framing_newline:
        rendered_lines.append("\n")
    fence = _fence(rendered_lines, lang)
    caption = f"> **源码位置**：{_path_code_span(normalized_path)}，第 {start}–{end} 行"
    markdown = caption + "\n" + "".join(external_text) + fence + "\n" + "".join(rendered_lines) + fence[:-len(lang)] + "\n"
    markdown_bytes = markdown.encode("utf-8")
    provenance: dict[str, object] = {
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "source_path": normalized_path,
        "revision": descriptor["revision"],
        "revision_trust": "caller-provided; not authenticated by this helper",
        "language": lang,
        "line_range": {"start": start, "end": end},
        "source_sha256": source_hash,
        "expected_source_sha256": expected_source,
        "source_sha256_match": expected_source is not None,
        "excerpt_sha256": excerpt_hash,
        "expected_excerpt_sha256": expected_excerpt,
        "excerpt_sha256_match": expected_excerpt is not None,
        "excerpt_byte_length": len(excerpt_bytes),
        "source_ends_with_newline": excerpt_bytes.endswith(b"\n"),
        "framing_newline": framing_newline,
        "caption": caption,
        "external_overlay_lines": external_text,
        "inline_overlays": inline_records,
        "external_overlays": external_records,
        "opening_fence": fence,
        "closing_fence": fence[:-len(lang)],
        "rendered_markdown_sha256": _sha256(markdown_bytes),
        "review_state_effect": "UNCHANGED",
        "semantic_review": "NOT_PERFORMED",
        "source_authentication": "NOT_PERFORMED_BY_HELPER",
    }
    return markdown, provenance


def strip_source_overlays(markdown: str | bytes, provenance: Mapping[str, object]) -> bytes:
    """Remove only recorded tool overlays and restore the exact excerpt bytes."""
    if isinstance(markdown, bytes):
        rendered = _decode(markdown, "rendered Markdown")
    elif isinstance(markdown, str):
        rendered = markdown
        _decode(markdown.encode("utf-8", errors="strict"), "rendered Markdown")
    else:
        raise SourceBlockError("rendered Markdown must be text or UTF-8 bytes")
    if not isinstance(provenance, Mapping) or provenance.get("schema_version") != 1:
        raise SourceBlockError("unsupported source-block provenance")
    if _sha256(rendered.encode("utf-8")) != provenance.get("rendered_markdown_sha256"):
        raise SourceBlockError("rendered Markdown does not match its provenance digest")
    caption = provenance.get("caption")
    opening = provenance.get("opening_fence")
    closing = provenance.get("closing_fence")
    external = provenance.get("external_overlay_lines")
    overlays = provenance.get("inline_overlays")
    if not isinstance(caption, str) or not isinstance(opening, str) or not isinstance(closing, str):
        raise SourceBlockError("provenance is missing the block framing")
    if not isinstance(external, list) or not all(isinstance(line, str) for line in external):
        raise SourceBlockError("provenance external overlays are malformed")
    if not isinstance(overlays, list):
        raise SourceBlockError("provenance inline overlays are malformed")
    prefix = caption + "\n" + "".join(external) + opening + "\n"
    suffix = closing + "\n"
    if not rendered.startswith(prefix) or not rendered.endswith(suffix):
        raise SourceBlockError("rendered source-block framing was changed")
    body = rendered[len(prefix) : -len(suffix)]
    body_lines = _physical_lines(body)
    positions: list[int] = []
    for record in overlays:
        if (
            not isinstance(record, Mapping)
            or not isinstance(record.get("line_text"), str)
            or not isinstance(record.get("line_ending"), str)
            or type(record.get("body_line_index")) is not int
        ):
            raise SourceBlockError("provenance inline overlay record is malformed")
        positions.append(record["body_line_index"])
    if len(positions) != len(set(positions)):
        raise SourceBlockError("provenance inline overlay positions are duplicated")
    for record in sorted(overlays, key=lambda item: item["body_line_index"], reverse=True):
        position = record["body_line_index"]
        if not 1 <= position <= len(body_lines):
            raise SourceBlockError("provenance inline overlay position is out of range")
        target = record["line_text"] + record["line_ending"]
        if body_lines[position - 1] != target:
            raise SourceBlockError("recorded tool overlay does not match its body position")
        del body_lines[position - 1]
    body = "".join(body_lines)
    if provenance.get("framing_newline"):
        if not body.endswith("\n"):
            raise SourceBlockError("framing newline is missing")
        body = body[:-1]
    excerpt = body.encode("utf-8")
    if _sha256(excerpt) != provenance.get("excerpt_sha256"):
        raise SourceBlockError("stripped source bytes do not match the excerpt digest")
    return excerpt


def materialize_reader_sources(
    template_bytes: bytes,
    plans: Sequence[SourceBlockPlan],
    source_reader: Callable[[str], bytes],
    *,
    required_symbols: Sequence[RequiredSourceSymbol] = (),
) -> tuple[bytes, dict[str, object]]:
    """Replace source slots, optionally requiring complete declared symbol spans."""
    template = _decode(template_bytes, "reader template")
    if not callable(source_reader):
        raise SourceBlockError("source_reader must be callable")
    plan_list = tuple(plans)
    validated: dict[str, dict[str, object]] = {}
    for plan in plan_list:
        values = _validate_plan(plan)
        slot_id = str(values["slot_id"])
        if slot_id in validated:
            raise SourceBlockError(f"duplicate source plan id: {slot_id}")
        validated[slot_id] = values
    _require_symbol_coverage(required_symbols, validated)

    template_lines = _physical_lines(template)
    slot_lines: list[tuple[str, str]] = []
    fence_character: str | None = None
    fence_length = 0
    for line in template_lines:
        body = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
        if "@@source:" in body:
            match = re.fullmatch(r"@@source:([A-Za-z0-9][A-Za-z0-9._-]{0,63})@@", body)
            if match is None:
                raise SourceBlockError("source slot must occupy a whole line and use a valid id")
            if fence_character is not None:
                raise SourceBlockError("source slot must be outside a fenced code block")
            slot_lines.append((match.group(1), line))
            continue

        if fence_character is not None:
            closing = re.fullmatch(r" {0,3}(`+|~+)[ \t]*", body)
            if (
                closing is not None
                and closing.group(1)[0] == fence_character
                and len(closing.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
            continue

        opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", body)
        if opening is not None:
            fence = opening.group(1)
            info = opening.group(2)
            if fence[0] == "`" and "`" in info:
                continue
            fence_character = fence[0]
            fence_length = len(fence)
    slot_ids = [slot_id for slot_id, _ in slot_lines]
    if len(slot_ids) != len(set(slot_ids)):
        raise SourceBlockError("duplicate source slot in template")
    if set(slot_ids) != set(validated):
        raise SourceBlockError("template slots and source plans do not match")

    source_cache: dict[str, bytes] = {}
    rendered_by_id: dict[str, tuple[bytes, dict[str, object]]] = {}
    for slot_id, _line in slot_lines:
        plan_values = validated[slot_id]
        path = str(plan_values["path"])
        if path not in source_cache:
            source_cache[path] = source_reader(path)
        block, provenance = render_source_block(
            source_cache[path],
            path=path,
            start_line=int(plan_values["start_line"]),
            end_line=int(plan_values["end_line"]),
            language=str(plan_values["language"]),
            annotations=plan_values["annotations"],
            expected_source_sha256=plan_values["expected_source_sha256"],
            expected_excerpt_sha256=plan_values["expected_excerpt_sha256"],
            revision=plan_values["revision"],
        )
        provenance["slot_id"] = slot_id
        rendered_by_id[slot_id] = (block.encode("utf-8"), provenance)

    output: list[bytes] = []
    source_index = 0
    for line in template_lines:
        body = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
        if "@@source:" not in body:
            output.append(line.encode("utf-8"))
            continue
        slot_id = slot_ids[source_index]
        source_index += 1
        output.append(rendered_by_id[slot_id][0])
    result = b"".join(output)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "template_sha256": _sha256(template_bytes),
        "output_sha256": _sha256(result),
        "source_blocks": [rendered_by_id[slot_id][1] for slot_id in slot_ids],
        "review_state_effect": "UNCHANGED",
        "semantic_review": "NOT_PERFORMED",
    }
    return result, metadata
