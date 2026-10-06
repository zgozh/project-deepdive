#!/usr/bin/env python3
"""Freeze source-bound reader review inputs without approving or publishing them."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from artifact_contract import _json_domain_errors, _strict_json_loads
import curriculum_run_workflow as curriculum_workflow
import phase6_chapter as chapter
import reader_handbook_workflow as reader_workflow
import reader_source_blocks


_FILES = ("lesson.md", "answers.md", "sources.json", "teaching-blueprint.md")
_PREPARED_FILES = (
    "reader-input-manifest.json",
    "writer-packet.md",
    "reader-handbook-writer.md",
    "teaching-blueprint.md",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = {
    "artifact_kind", "version", "status", "review_status", "writer_session_id",
    "run_plan_id", "run_plan_path", "plan_sha256", "blueprint_path", "blueprint_sha256",
    "writer_prompt_path", "writer_prompt_sha256", "source_anchor", "source_status",
    "unknown_files", "selected_units", "known_input_digests", "outputs",
}
_MANIFEST_KEYS_WITH_BOOK_PLAN = _MANIFEST_KEYS | {"book_plan"}
_OUTPUT_KEYS = {"writer_packet", "teaching_blueprint", "writer_prompt"}
_OUTPUT_KEYS_WITH_BOOK_PLAN = _OUTPUT_KEYS | {"reader_book_plan"}
_AUTHORING_REFERENCE_KEYS = {row[0] for row in reader_workflow._AUTHORING_REFERENCES}
_OUTPUT_KEYS_V12 = _OUTPUT_KEYS | _AUTHORING_REFERENCE_KEYS
_OUTPUT_KEYS_V12_WITH_BOOK_PLAN = _OUTPUT_KEYS_V12 | {"reader_book_plan"}
_SOURCE_KEYS = {
    "artifact_kind", "version", "source_revision", "source_blocks",
    "unknowns", "general_references",
}
_BLOCK_KEYS = {
    "slot_id", "path", "start_line", "end_line", "language", "annotations",
    "expected_source_sha256", "expected_excerpt_sha256", "manuscript", "supports",
}


class ReaderHandbookReviewError(ValueError):
    """A fixed, redacted reader-review preparation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ReaderHandbookReviewError(code)


def _read_v12_authoring_reference(path: str | Path, code: str) -> tuple[Path, bytes, str]:
    try:
        return reader_workflow._read_utf8(path, code)
    except reader_workflow.ReaderHandbookWorkflowError as exc:
        raise ReaderHandbookReviewError(exc.code) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(text: str, code: str) -> Any:
    try:
        value = _strict_json_loads(text)
        errors: list[str] = []
        _json_domain_errors(value, "$", errors)
        if errors:
            _fail(code)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8", errors="strict")
        return value
    except ReaderHandbookReviewError:
        raise
    except (UnicodeError, ValueError, TypeError, OverflowError) as exc:
        raise ReaderHandbookReviewError(code) from exc


def _same_json(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _object(value: Any, keys: set[str], code: str, *, optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    optional = optional or set()
    if not keys.issubset(value) or set(value) - keys - optional:
        _fail(code)
    return value


def _nonempty_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        _fail(code)
    return value


def _nonempty_string_list(value: Any, code: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(code)
    output = [_nonempty_text(item, code) for item in value]
    if len(set(output)) != len(output):
        _fail(code)
    return output


def _load_json_file(path: Path, code: str) -> tuple[bytes, str, Any]:
    _resolved, raw, text = reader_workflow._read_utf8(path, code)
    return raw, text, _strict_json(text, code)


def _source_anchor(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: plan[key]
        for key in (
            "repository_revision", "snapshot_kind", "source_metadata", "source_status",
            "source_run_manifest_sha256", "unknown_files",
        )
    }


def _parse_annotations(value: Any) -> dict[int, str]:
    if not isinstance(value, dict):
        _fail("SOURCES_INVALID")
    output: dict[int, str] = {}
    for key, note in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key):
            _fail("SOURCES_INVALID")
        line = int(key)
        if line in output:
            _fail("SOURCES_INVALID")
        output[line] = _nonempty_text(note, "SOURCES_INVALID")
    return output


def validate_reader_theme_source_manifest(
    value: Any,
    *,
    source_revision: str,
) -> dict[str, Any]:
    """Validate only the reader source-manifest shape, without reading source files."""
    submission = _object(value, _SOURCE_KEYS, "SOURCES_INVALID", optional={"unknowns", "general_references"})
    if (
        submission.get("artifact_kind") != "reader-theme-sources"
        or submission.get("version") != "1.0"
        or submission.get("source_revision") != source_revision
    ):
        _fail("SOURCES_INVALID")

    unknowns_value = submission.get("unknowns", [])
    if not isinstance(unknowns_value, list):
        _fail("SOURCES_INVALID")
    unknowns: list[dict[str, Any]] = []
    for row in unknowns_value:
        row = _object(row, {"learning_outcome", "reason", "critical"}, "SOURCES_INVALID")
        if type(row["critical"]) is not bool:
            _fail("SOURCES_INVALID")
        unknowns.append({
            "learning_outcome": _nonempty_text(row["learning_outcome"], "SOURCES_INVALID"),
            "reason": _nonempty_text(row["reason"], "SOURCES_INVALID"),
            "critical": row["critical"],
        })

    references_value = submission.get("general_references", [])
    if not isinstance(references_value, list):
        _fail("SOURCES_INVALID")
    references: list[dict[str, Any]] = []
    for row in references_value:
        row = _object(row, {"title", "url", "supports"}, "SOURCES_INVALID")
        references.append({
            "title": _nonempty_text(row["title"], "SOURCES_INVALID"),
            "url": _nonempty_text(row["url"], "SOURCES_INVALID"),
            "supports": _nonempty_string_list(row["supports"], "SOURCES_INVALID"),
        })

    blocks_value = submission.get("source_blocks")
    if not isinstance(blocks_value, list):
        _fail("SOURCES_INVALID")
    seen_slots: set[str] = set()
    blocks: list[dict[str, Any]] = []
    for raw_block in blocks_value:
        block = _object(raw_block, _BLOCK_KEYS, "SOURCES_INVALID")
        slot_id = _nonempty_text(block["slot_id"], "SOURCES_INVALID")
        manuscript = block["manuscript"]
        if slot_id in seen_slots or not isinstance(manuscript, str) or manuscript not in {"lesson.md", "answers.md"}:
            _fail("SOURCES_INVALID")
        seen_slots.add(slot_id)
        supports = _nonempty_string_list(block["supports"], "SOURCES_INVALID")
        path_value = _nonempty_text(block["path"], "SOURCES_INVALID")
        try:
            normalized_path = reader_source_blocks._relative_path(path_value)
        except (reader_source_blocks.SourceBlockError, TypeError, ValueError) as exc:
            raise ReaderHandbookReviewError("SOURCE_BLOCK_INVALID") from exc
        if normalized_path != path_value:
            _fail("SOURCE_BLOCK_INVALID")

        start = block["start_line"]
        end = block["end_line"]
        language = block["language"]
        expected_source = block["expected_source_sha256"]
        expected_excerpt = block["expected_excerpt_sha256"]
        if (
            not isinstance(expected_source, str) or not _SHA256.fullmatch(expected_source)
            or not isinstance(expected_excerpt, str) or not _SHA256.fullmatch(expected_excerpt)
        ):
            _fail("SOURCE_BLOCK_INVALID")
        annotations = _parse_annotations(block["annotations"])
        try:
            reader_source_blocks._validate_plan(reader_source_blocks.SourceBlockPlan(
                slot_id=slot_id,
                path=normalized_path,
                start_line=start,
                end_line=end,
                language=language,
                annotations=annotations,
                expected_source_sha256=expected_source,
                expected_excerpt_sha256=expected_excerpt,
                revision=source_revision,
            ))
        except (reader_source_blocks.SourceBlockError, TypeError, ValueError) as exc:
            raise ReaderHandbookReviewError("SOURCE_BLOCK_INVALID") from exc
        blocks.append({
            "slot_id": slot_id,
            "path": normalized_path,
            "start_line": start,
            "end_line": end,
            "language": language,
            "annotations": annotations,
            "expected_source_sha256": expected_source,
            "expected_excerpt_sha256": expected_excerpt,
            "manuscript": manuscript,
            "supports": supports,
        })

    return {
        "artifact_kind": "reader-theme-sources",
        "version": "1.0",
        "source_revision": source_revision,
        "source_blocks": blocks,
        "unknowns": unknowns,
        "general_references": references,
    }


def _parse_sources(
    value: Any,
    *,
    source_revision: str,
    lesson_text: str,
    answers_text: str,
    auth_context: curriculum_workflow.AuthenticatedCurriculumRunPlan,
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, bytes]]:
    submission = validate_reader_theme_source_manifest(value, source_revision=source_revision)
    unknowns = submission["unknowns"]
    references = submission["general_references"]
    source_bytes_by_path: dict[str, bytes] = {}
    proofs: list[dict[str, Any]] = []
    manuscripts = {"lesson.md": lesson_text, "answers.md": answers_text}
    for block in submission["source_blocks"]:
        slot_id = block["slot_id"]
        manuscript = block["manuscript"]
        supports = block["supports"]
        normalized_path = block["path"]
        start = block["start_line"]
        end = block["end_line"]
        language = block["language"]
        expected_source = block["expected_source_sha256"]
        expected_excerpt = block["expected_excerpt_sha256"]
        annotations = block["annotations"]
        if normalized_path not in source_bytes_by_path:
            try:
                source_bytes_by_path[normalized_path] = chapter._read_snapshot_source(
                    normalized_path,
                    facts=plan,
                    run_dir=auth_context.inputs.run_dir,
                    root=auth_context.inputs.root,
                )
            except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
                raise ReaderHandbookReviewError("SOURCE_BLOCK_INVALID") from exc
        source_raw = source_bytes_by_path[normalized_path]
        try:
            rendered, renderer_proof = reader_source_blocks.render_source_block(
                source_raw,
                path=normalized_path,
                start_line=start,
                end_line=end,
                language=language,
                annotations=annotations,
                expected_source_sha256=expected_source,
                expected_excerpt_sha256=expected_excerpt,
                revision=source_revision,
            )
        except (reader_source_blocks.SourceBlockError, UnicodeError, TypeError, ValueError) as exc:
            raise ReaderHandbookReviewError("SOURCE_BLOCK_INVALID") from exc
        rendered_raw = rendered.encode("utf-8")
        manuscript_raw = manuscripts[manuscript].encode("utf-8")
        if manuscript_raw.count(rendered_raw) != 1:
            _fail("SOURCE_BLOCK_RENDER_MISMATCH")
        source_text = source_raw.decode("utf-8", errors="strict")
        lines = source_text.split("\n")
        physical_lines = [line + "\n" for line in lines[:-1]]
        if not source_text.endswith("\n"):
            physical_lines.append(lines[-1])
        excerpt_raw = "".join(physical_lines[start - 1:end]).encode("utf-8")
        proofs.append({
            "slot_id": slot_id,
            "manuscript": manuscript,
            "supports": supports,
            "path": normalized_path,
            "revision": source_revision,
            "line_range": {"start": start, "end": end},
            "language": renderer_proof["language"],
            "annotations": {str(key): value for key, value in sorted(annotations.items())},
            "source_sha256": renderer_proof["source_sha256"],
            "excerpt_sha256": renderer_proof["excerpt_sha256"],
            "excerpt_bytes_base64": base64.b64encode(excerpt_raw).decode("ascii"),
            "rendered_markdown_sha256": renderer_proof["rendered_markdown_sha256"],
            "rendered_occurrences": 1,
            "source_snapshot_path": normalized_path,
        })

    snapshots = [
        {
            "path": path,
            "sha256": _sha256(raw),
            "byte_length": len(raw),
            "raw_bytes_base64": base64.b64encode(raw).decode("ascii"),
        }
        for path, raw in sorted(source_bytes_by_path.items())
    ]
    return proofs, snapshots, source_bytes_by_path


@dataclass(frozen=True)
class AuthenticatedReaderReviewContext:
    """Read-only snapshot of writer content bound to one live authenticated plan."""

    _auth_context: curriculum_workflow.AuthenticatedCurriculumRunPlan = field(repr=False, compare=False)
    attempt_dir: Path
    _bound_files: tuple[tuple[str, Path, bytes], ...] = field(repr=False, compare=False)
    _review_input_json: str = field(repr=False, compare=False)
    _source_bytes: tuple[tuple[str, bytes], ...] = field(repr=False, compare=False)
    status: str = "REVIEW_INPUT_READY"
    review_status: str = "NOT_REVIEWED"

    @property
    def review_input(self) -> dict[str, Any]:
        """Return an independent JSON-serializable review snapshot."""
        return json.loads(self._review_input_json)

    @property
    def file_digests(self) -> dict[str, str]:
        return dict(self.review_input["file_digests"])

    def read_source(self, path: str) -> bytes:
        """Return the fixed source bytes included for independent review."""
        for source_path, raw in self._source_bytes:
            if source_path == path:
                return raw
        raise KeyError(path)

    def recheck(self) -> None:
        reader_workflow._recheck_context(self._auth_context)
        for _name, path, expected in self._bound_files:
            _resolved, raw, _text = reader_workflow._read_utf8(path, "INPUT_CHANGED")
            if raw != expected:
                _fail("INPUT_CHANGED")


def prepare_reader_review(
    auth_context: curriculum_workflow.AuthenticatedCurriculumRunPlan,
    attempt_dir: str | Path,
) -> AuthenticatedReaderReviewContext:
    """Freeze the current authored bytes against a real, live-authenticated plan."""
    if not isinstance(auth_context, curriculum_workflow.AuthenticatedCurriculumRunPlan):
        _fail("INPUT_CONTEXT_INVALID")
    reader_workflow._recheck_context(auth_context)
    plan = reader_workflow._parse_authenticated_raw(auth_context)
    try:
        attempt = chapter._absolute(attempt_dir)
        chapter._assert_no_link_components(attempt)
        if attempt.is_symlink() or not attempt.is_dir():
            _fail("ATTEMPT_INVALID")
        attempt = attempt.resolve(strict=True)
    except ReaderHandbookReviewError:
        raise
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookReviewError("ATTEMPT_INVALID") from exc

    bound: dict[str, tuple[Path, bytes, str]] = {}
    for name in _PREPARED_FILES:
        path, raw, text = reader_workflow._read_utf8(attempt / name, "PREPARED_INPUT_INVALID")
        bound[name] = (path, raw, text)
    for name in ("lesson.md", "answers.md", "sources.json"):
        path, raw, text = reader_workflow._read_utf8(attempt / name, "WRITER_INPUT_INVALID")
        bound[name] = (path, raw, text)

    manifest_raw, _manifest_text, manifest_value = _load_json_file(
        bound["reader-input-manifest.json"][0], "PREPARED_MANIFEST_INVALID",
    )
    if not isinstance(manifest_value, dict):
        _fail("PREPARED_MANIFEST_INVALID")
    manifest_version = manifest_value.get("version")
    if manifest_version == "1.0":
        manifest = _object(manifest_value, _MANIFEST_KEYS, "PREPARED_MANIFEST_INVALID")
        prepared_file_names = _PREPARED_FILES
        book_plan_path_value: str | None = None
        book_plan_raw: bytes | None = None
        book_plan_text: str | None = None
        book_plan_sha256: str | None = None
    elif manifest_version == "1.1":
        manifest = _object(manifest_value, _MANIFEST_KEYS_WITH_BOOK_PLAN, "PREPARED_MANIFEST_INVALID")
        book_plan = _object(manifest.get("book_plan"), {"path", "sha256"}, "PREPARED_MANIFEST_INVALID")
        book_plan_path_value = _nonempty_text(book_plan.get("path"), "PREPARED_MANIFEST_INVALID")
        book_plan_sha256 = book_plan.get("sha256")
        if (
            not Path(book_plan_path_value).is_absolute()
            or not isinstance(book_plan_sha256, str)
            or not _SHA256.fullmatch(book_plan_sha256)
        ):
            _fail("PREPARED_MANIFEST_INVALID")
        book_plan_path, book_plan_raw, book_plan_text = reader_workflow._read_utf8(
            attempt / "reader-book-plan.md", "PREPARED_INPUT_INVALID",
        )
        bound["reader-book-plan.md"] = (book_plan_path, book_plan_raw, book_plan_text)
        if _sha256(book_plan_raw) != book_plan_sha256:
            _fail("PREPARED_INPUT_STALE")
        prepared_file_names = (*_PREPARED_FILES, "reader-book-plan.md")
    elif manifest_version == "1.2":
        has_book_plan = "book_plan" in manifest_value
        manifest = _object(
            manifest_value,
            _MANIFEST_KEYS_WITH_BOOK_PLAN if has_book_plan else _MANIFEST_KEYS,
            "PREPARED_MANIFEST_INVALID",
        )
        book_plan_path_value = None
        book_plan_raw = None
        book_plan_text = None
        book_plan_sha256 = None
        prepared_file_names = _PREPARED_FILES
        if has_book_plan:
            book_plan = _object(manifest.get("book_plan"), {"path", "sha256"}, "PREPARED_MANIFEST_INVALID")
            book_plan_path_value = _nonempty_text(book_plan.get("path"), "PREPARED_MANIFEST_INVALID")
            book_plan_sha256 = book_plan.get("sha256")
            if (
                not Path(book_plan_path_value).is_absolute()
                or not isinstance(book_plan_sha256, str)
                or not _SHA256.fullmatch(book_plan_sha256)
            ):
                _fail("PREPARED_MANIFEST_INVALID")
            book_plan_path, book_plan_raw, book_plan_text = reader_workflow._read_utf8(
                attempt / "reader-book-plan.md", "PREPARED_INPUT_INVALID",
            )
            bound["reader-book-plan.md"] = (book_plan_path, book_plan_raw, book_plan_text)
            if _sha256(book_plan_raw) != book_plan_sha256:
                _fail("PREPARED_INPUT_STALE")
            prepared_file_names = (*prepared_file_names, "reader-book-plan.md")

        for _manifest_key, _relative_path, output_path in reader_workflow._AUTHORING_REFERENCES:
            path, raw, text = _read_v12_authoring_reference(
                attempt / output_path, "PREPARED_INPUT_INVALID",
            )
            bound[output_path] = (path, raw, text)
        prepared_file_names = (
            *prepared_file_names,
            *(row[2] for row in reader_workflow._AUTHORING_REFERENCES),
        )
    else:
        _fail("PREPARED_MANIFEST_INVALID")
    if (
        manifest.get("artifact_kind") != "reader-handbook-writer-input"
        or manifest.get("status") != "PREPARED"
        or manifest.get("review_status") != "NOT_REVIEWED"
    ):
        _fail("PREPARED_MANIFEST_INVALID")
    writer_session_id = _nonempty_text(manifest.get("writer_session_id"), "PREPARED_MANIFEST_INVALID")
    if any(ord(char) < 32 for char in writer_session_id):
        _fail("PREPARED_MANIFEST_INVALID")

    plan_path = chapter._absolute(auth_context.path).resolve(strict=True)
    expected_anchor = _source_anchor(plan)
    selected_ids: list[str] = []
    selected_value = manifest.get("selected_units")
    if not isinstance(selected_value, list):
        _fail("PREPARED_MANIFEST_INVALID")
    try:
        selected_ids = [row["unit_identity"]["id"] for row in selected_value]
        selected = reader_workflow._selected_units(plan, selected_ids)
        input_records = reader_workflow._verified_input_records(plan, auth_context)
    except (KeyError, TypeError, ReaderHandbookReviewError) as exc:
        raise ReaderHandbookReviewError("PREPARED_MANIFEST_INVALID") from exc
    if (
        manifest.get("run_plan_id") != plan.get("run_plan_id")
        or manifest.get("run_plan_path") != str(plan_path)
        or manifest.get("plan_sha256") != _sha256(auth_context.raw)
        or not _same_json(manifest.get("source_anchor"), expected_anchor)
        or manifest.get("source_status") != plan.get("source_status")
        or not _same_json(manifest.get("unknown_files"), plan.get("unknown_files"))
        or not _same_json(selected_value, selected)
        or not _same_json(manifest.get("known_input_digests"), input_records)
    ):
        _fail("PREPARED_MANIFEST_STALE")

    blueprint_path_value = manifest.get("blueprint_path")
    if not isinstance(blueprint_path_value, str) or not Path(blueprint_path_value).is_absolute() or not blueprint_path_value.isprintable():
        _fail("PREPARED_MANIFEST_INVALID")
    blueprint_raw = bound["teaching-blueprint.md"][1]
    prompt_path, prompt_raw, prompt_text = reader_workflow._read_utf8(
        reader_workflow._WRITER_PROMPT, "WRITER_PROMPT_INVALID",
    )
    if (
        manifest.get("blueprint_sha256") != _sha256(blueprint_raw)
        or manifest.get("writer_prompt_path") != str(prompt_path)
        or manifest.get("writer_prompt_sha256") != _sha256(prompt_raw)
        or bound["reader-handbook-writer.md"][1] != prompt_raw
    ):
        _fail("PREPARED_INPUT_STALE")

    authoring_references: list[dict[str, Any]] | None = None
    if manifest_version == "1.2":
        authoring_references = []
        for manifest_key, relative_path, output_path in reader_workflow._AUTHORING_REFERENCES:
            copy_raw = bound[output_path][1]
            source_path, source_raw, source_text = _read_v12_authoring_reference(
                reader_workflow._SKILL_ROOT / relative_path,
                "AUTHORING_REFERENCE_INVALID",
            )
            if copy_raw != source_raw:
                _fail("PREPARED_INPUT_STALE")
            authoring_references.append({
                "manifest_key": manifest_key,
                "relative_path": relative_path,
                "output_path": output_path,
                "source_path": source_path,
                "raw": copy_raw,
                "text": source_text,
                "sha256": _sha256(copy_raw),
            })
    packet_raw = reader_workflow._writer_packet(
        plan,
        selected,
        input_records,
        bound["teaching-blueprint.md"][2],
        prompt_text,
        blueprint_path=Path(blueprint_path_value),
        prompt_path=prompt_path,
        plan_path=plan_path,
        plan_sha256=_sha256(auth_context.raw),
        blueprint_sha256=_sha256(blueprint_raw),
        prompt_sha256=_sha256(prompt_raw),
        writer_session_id=writer_session_id,
        book_plan_text=book_plan_text,
        book_plan_path=Path(book_plan_path_value) if book_plan_path_value is not None else None,
        book_plan_sha256=book_plan_sha256,
        authoring_references=authoring_references,
    )
    outputs = manifest.get("outputs")
    if manifest_version == "1.2":
        expected_output_keys = _OUTPUT_KEYS_V12_WITH_BOOK_PLAN if book_plan_raw is not None else _OUTPUT_KEYS_V12
    else:
        expected_output_keys = _OUTPUT_KEYS_WITH_BOOK_PLAN if manifest_version == "1.1" else _OUTPUT_KEYS
    if not isinstance(outputs, dict) or set(outputs) != expected_output_keys:
        _fail("PREPARED_MANIFEST_INVALID")
    expected_outputs = {
        "writer_packet": {"path": "writer-packet.md", "sha256": _sha256(packet_raw)},
        "teaching_blueprint": {"path": "teaching-blueprint.md", "sha256": _sha256(blueprint_raw)},
        "writer_prompt": {"path": "reader-handbook-writer.md", "sha256": _sha256(prompt_raw)},
    }
    if book_plan_raw is not None:
        expected_outputs["reader_book_plan"] = {
            "path": "reader-book-plan.md",
            "sha256": _sha256(book_plan_raw),
        }
    if authoring_references is not None:
        for item in authoring_references:
            expected_outputs[item["manifest_key"]] = {
                "path": item["output_path"],
                "sha256": item["sha256"],
            }
    if not _same_json(outputs, expected_outputs) or bound["writer-packet.md"][1] != packet_raw:
        _fail("PREPARED_INPUT_STALE")

    _sources_raw, _sources_text, sources_value = _load_json_file(
        bound["sources.json"][0], "SOURCES_INVALID",
    )
    source_revision = plan.get("repository_revision")
    if not isinstance(source_revision, str):
        _fail("PLAN_INVALID")
    source_proofs, source_snapshots, source_bytes = _parse_sources(
        sources_value,
        source_revision=source_revision,
        lesson_text=bound["lesson.md"][2],
        answers_text=bound["answers.md"][2],
        auth_context=auth_context,
        plan=plan,
    )

    file_digests = {name: _sha256(bound[name][1]) for name in _FILES}
    prepared_digests = {name: _sha256(bound[name][1]) for name in prepared_file_names}
    packet = {
        "artifact_kind": "reader-handbook-review-input",
        "version": manifest_version,
        "status": "REVIEW_INPUT_READY",
        "review_status": "NOT_REVIEWED",
        "writer_session_id": writer_session_id,
        "selected_units": selected,
        "source_anchor": expected_anchor,
        "source_status": plan["source_status"],
        "unknown_files": plan["unknown_files"],
        "known_input_digests": input_records,
        "prepared_inputs": prepared_digests,
        "prepared_input_sha256": prepared_digests["reader-input-manifest.json"],
        "file_digests": file_digests,
        "files": {name: bound[name][2] for name in _FILES},
        "source_proofs": source_proofs,
        "source_snapshots": source_snapshots,
        "unknowns": sources_value.get("unknowns", []),
        "general_references": sources_value.get("general_references", []),
        "review_tasks": {
            "source_evidence": {
                "session_id": None,
                "instructions": (
                    "In an independent reviewer and execution session, inspect every material project fact "
                    "and unmarked factual statement in the complete lesson and answers; verify each source "
                    "binding and supported scope, and identify unsupported or omitted facts. Source blocks "
                    "and declared supports do not prove full factual coverage."
                ),
            },
            "principal_teaching": {
                "session_id": None,
                "instructions": (
                    "In a session independent from the writer and source reviewer, review the complete lesson "
                    "and answers for a true beginner. Trace the assumed input through the business context, "
                    "prerequisites, architecture, source, mechanism, failure, verification, extension, and "
                    "worked answer reasoning; record concrete locations and reasons. Do not use word counts "
                    "or structural counts as a teaching-quality conclusion."
                ),
            },
            "identity_boundary": (
                "Writer, source reviewer, and Principal teaching reviewer must be different people and "
                "execution contexts/session IDs. These declared IDs are not cryptographic identity proof."
            ),
        },
    }
    if authoring_references is not None:
        packet["authoring_references"] = [
            {
                "path": item["relative_path"],
                "sha256": item["sha256"],
                "text": item["text"],
            }
            for item in authoring_references
        ]
        packet["review_tasks"]["principal_teaching"]["instructions"] += (
            " Apply the exact frozen source-topic writing card, reader-handbook contract, and learner AI collaboration "
            "card included in authoring_references. Check the applicable mapped teaching duties in the actual complete "
            "chapter, including explanation before/within/after each source block and the required learner collaboration. "
            "Adapt duties to the authorized topic scope; do not require fixed headings or use counts as a quality verdict."
        )
    if book_plan_text is not None:
        packet["book_plan"] = {
            "source_path": book_plan_path_value,
            "sha256": book_plan_sha256,
            "text": book_plan_text,
        }
        packet["review_tasks"]["principal_teaching"]["instructions"] += (
            " Compare the lesson's and answers' actual order and scope with the frozen reader-book plan, "
            "and compare their stated learning outcomes with the teaching blueprint. Do not use titles or "
            "hashes as a judgment of teaching depth."
        )
    review_json = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    reader_workflow._recheck_context(auth_context)
    captured = tuple((name, path, raw) for name, (path, raw, _text) in bound.items())
    context = AuthenticatedReaderReviewContext(
        _auth_context=auth_context,
        attempt_dir=attempt,
        _bound_files=captured,
        _review_input_json=review_json,
        _source_bytes=tuple(sorted(source_bytes.items())),
    )
    context.recheck()
    return context
