"""Bind reader source blocks to a live-authenticated curriculum plan."""

from __future__ import annotations

from dataclasses import replace
import copy
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

import curriculum_run_workflow
import phase6_chapter as chapter
import reader_handbook_workflow
from reader_source_blocks import (
    SourceBlockError,
    SourceBlockPlan,
    materialize_reader_sources,
)


_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class ReaderThemeSourceError(ValueError):
    """A fixed, redacted failure while binding reader excerpts to trusted inputs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ReaderThemeSourceError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _copy_plans(plans: Sequence[SourceBlockPlan]) -> tuple[SourceBlockPlan, ...]:
    if isinstance(plans, (str, bytes)) or not isinstance(plans, Sequence):
        _fail("SOURCE_PLAN_INVALID")
    try:
        copied = []
        for plan in plans:
            if not isinstance(plan, SourceBlockPlan):
                _fail("SOURCE_PLAN_INVALID")
            annotations = dict(plan.annotations) if isinstance(plan.annotations, Mapping) else plan.annotations
            copied.append(replace(plan, annotations=annotations))
        return tuple(copied)
    except (TypeError, ValueError) as exc:
        raise ReaderThemeSourceError("SOURCE_PLAN_INVALID") from exc


def _read_snapshot_bytes(
    path: str,
    *,
    plan: Mapping[str, Any],
    inputs: chapter.ChapterInputPaths,
) -> bytes:
    if not chapter._safe_relative_path(path):
        _fail("SOURCE_PLAN_INVALID")
    try:
        if plan["snapshot_kind"] == "git-tree":
            return chapter._read_git_object_bounded(
                chapter._absolute(inputs.root), plan["repository_revision"], path,
            )
        source_path = chapter._absolute(inputs.root).joinpath(*PurePosixPath(path).parts)
        chapter._assert_no_link_components(source_path)
        if chapter._is_link(source_path) or not source_path.is_file():
            _fail("SOURCE_INVALID")
        with source_path.open("rb") as source_file:
            return source_file.read(chapter.MAX_EXCERPT_SOURCE_BYTES + 1)
    except ReaderThemeSourceError:
        raise
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderThemeSourceError("SOURCE_INVALID") from exc


def _plan_signature(plans: Sequence[SourceBlockPlan]) -> tuple[SourceBlockPlan, ...]:
    return _copy_plans(plans)


def materialize_reader_theme_sources(
    auth_context: curriculum_run_workflow.AuthenticatedCurriculumRunPlan,
    template_bytes: bytes,
    plans: Sequence[SourceBlockPlan],
    *,
    frozen_sources: Mapping[str, bytes] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Render source slots against the current authenticated project snapshot."""
    if not isinstance(auth_context, curriculum_run_workflow.AuthenticatedCurriculumRunPlan):
        _fail("INPUT_CONTEXT_INVALID")
    if type(template_bytes) is not bytes:
        _fail("TEMPLATE_INVALID")
    if isinstance(plans, (str, bytes)) or not isinstance(plans, Sequence):
        _fail("SOURCE_PLAN_INVALID")
    copied_plans = _copy_plans(plans)
    if frozen_sources is None:
        cache_snapshot: dict[str, bytes] = {}
    elif isinstance(frozen_sources, Mapping):
        try:
            cache_snapshot = dict(frozen_sources)
        except (TypeError, ValueError) as exc:
            raise ReaderThemeSourceError("SOURCE_CACHE_INVALID") from exc
    else:
        _fail("SOURCE_CACHE_INVALID")
    for path, raw in cache_snapshot.items():
        if not isinstance(path, str) or not chapter._safe_relative_path(path) or type(raw) is not bytes:
            _fail("SOURCE_CACHE_INVALID")

    reader_handbook_workflow._recheck_context(auth_context)
    plan = reader_handbook_workflow._parse_authenticated_raw(auth_context)
    revision = plan.get("repository_revision")
    snapshot_kind = plan.get("snapshot_kind")
    source_metadata = plan.get("source_metadata")
    if not isinstance(revision, str) or not revision or not isinstance(source_metadata, Mapping):
        _fail("PLAN_INVALID")
    expected_index_sha = source_metadata.get("project_index_sha256")
    if not isinstance(expected_index_sha, str) or not _SHA256.fullmatch(expected_index_sha):
        _fail("PLAN_INVALID")

    index_path = chapter._absolute(auth_context.inputs.run_dir) / "phase2" / "project-index.json"
    try:
        index_read = chapter._read_artifact(index_path)
    except chapter.Phase6ChapterError as exc:
        raise ReaderThemeSourceError(getattr(exc, "code", "PROJECT_INDEX_INVALID")) from exc
    if index_read.sha256 != expected_index_sha.lower():
        _fail("PROJECT_INDEX_CHANGED")
    records = index_read.artifact.get("files")
    if not isinstance(records, list):
        _fail("PROJECT_INDEX_INVALID")
    by_path: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if isinstance(record, Mapping) and isinstance(record.get("path"), str):
            if record["path"] in by_path:
                _fail("PROJECT_INDEX_INVALID")
            by_path[record["path"]] = record

    adapted_plans: list[SourceBlockPlan] = []
    source_records: dict[str, Mapping[str, Any]] = {}
    for source_plan in copied_plans:
        path = source_plan.path
        if not isinstance(path, str) or not chapter._safe_relative_path(path):
            _fail("SOURCE_PLAN_INVALID")
        record = by_path.get(path)
        if record is None:
            _fail("SOURCE_INVALID")
        source_sha = record.get("sha256")
        source_size = record.get("bytes")
        if (
            record.get("content_kind") != "text"
            or type(source_size) is not int
            or source_size < 0
            or source_size > chapter.MAX_EXCERPT_SOURCE_BYTES
            or not isinstance(source_sha, str)
            or not _SHA256.fullmatch(source_sha)
        ):
            _fail("SOURCE_INVALID")
        if source_plan.revision is not None and source_plan.revision != revision:
            _fail("SOURCE_PLAN_INVALID")
        if (
            source_plan.expected_source_sha256 is not None
            and (
                not isinstance(source_plan.expected_source_sha256, str)
                or not _SHA256.fullmatch(source_plan.expected_source_sha256)
                or source_plan.expected_source_sha256.lower() != source_sha.lower()
            )
        ):
            _fail("SOURCE_PLAN_INVALID")
        adapted_plans.append(replace(
            source_plan,
            expected_source_sha256=source_sha.lower(),
            revision=revision,
            annotations=dict(source_plan.annotations) if isinstance(source_plan.annotations, Mapping)
            else source_plan.annotations,
        ))
        source_records[path] = record

    def read_source(path: str) -> bytes:
        record = source_records.get(path)
        if record is None:
            _fail("SOURCE_INVALID")
        if path in cache_snapshot:
            raw = cache_snapshot[path]
        else:
            raw = _read_snapshot_bytes(path, plan=plan, inputs=auth_context.inputs)
        if len(raw) != record["bytes"] or _sha256(raw) != record["sha256"].lower():
            _fail("SOURCE_CACHE_INVALID" if path in cache_snapshot else "SOURCE_INVALID")
        return raw

    try:
        rendered, renderer_metadata = materialize_reader_sources(
            template_bytes, adapted_plans, read_source,
        )
    except SourceBlockError:
        raise
    try:
        if _plan_signature(tuple(plans)) != copied_plans:
            _fail("INPUT_CHANGED")
    except (TypeError, ValueError) as exc:
        raise ReaderThemeSourceError("INPUT_CHANGED") from exc
    if frozen_sources is not None:
        try:
            if dict(frozen_sources) != cache_snapshot:
                _fail("INPUT_CHANGED")
        except (TypeError, ValueError) as exc:
            raise ReaderThemeSourceError("INPUT_CHANGED") from exc
    reader_handbook_workflow._recheck_context(auth_context)
    metadata = copy.deepcopy(renderer_metadata)
    for source_block in metadata["source_blocks"]:
        source_block["revision_trust"] = "authenticated by curriculum-run-plan"
        source_block["source_authentication"] = "authenticated project-index bytes"
    plan_sha = _sha256(auth_context.raw)
    index_sha = index_read.sha256
    metadata.update({
        "status": "RENDERED",
        "review_status": "NOT_REVIEWED",
        "semantic_review": "NOT_PERFORMED",
        "source_authentication": "AUTHENTICATED_PROJECT_INDEX",
        "source_anchor": {
            "repository_revision": revision,
            "snapshot_kind": snapshot_kind,
            "source_metadata": copy.deepcopy(dict(source_metadata)),
            "source_run_manifest_sha256": plan.get("source_run_manifest_sha256"),
        },
        "plan_sha256": plan_sha,
        "project_index_sha256": index_sha,
    })
    return rendered, metadata
