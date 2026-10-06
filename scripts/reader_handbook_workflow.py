#!/usr/bin/env python3
"""Prepare a reader-handbook writer packet from an authenticated curriculum plan."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from artifact_contract import _strict_json_loads
import curriculum_run_workflow as curriculum_workflow
import phase6_chapter as chapter


_SKILL_ROOT = Path(__file__).resolve().parents[1]
_WRITER_PROMPT = _SKILL_ROOT / "prompts" / "phase6-reader-handbook-writer.md"
_AUTHORING_REFERENCES = (
    ("source_topic_writing_card", "references/phase6-source-topic-writing-card.md", "source-topic-writing-card.md"),
    ("reader_contract", "references/phase6-reader-handbook-contract.md", "reader-handbook-contract.md"),
    ("learner_ai_collaboration_card", "references/learner-ai-collaboration-card.md", "learner-ai-collaboration-card.md"),
)
_OUTPUT_FILES = (
    "writer-packet.md",
    "teaching-blueprint.md",
    "reader-handbook-writer.md",
    "source-topic-writing-card.md",
    "reader-handbook-contract.md",
    "learner-ai-collaboration-card.md",
    "reader-input-manifest.json",
)
_AUTHENTICATED_INPUT_ROLE_PATHS = {
    "phase4_base_graph": ("phase4", "knowledge-graph"),
    "phase4_base_evidence": ("phase4", "evidence"),
    "phase4_semantic_proposals": ("phase4", "semantic-proposals"),
    "phase4_claim_candidates": ("phase4", "claim_candidates"),
    "phase4_claim_evidence": ("phase4", "claim_evidence"),
    "phase4_claim_evidence_graph": ("phase4", "claim_evidence_graph"),
    "phase5_prerequisite_candidates": ("phase5", "prerequisite_candidates"),
    "phase5_prerequisite_graph": ("phase5", "prerequisite_graph"),
    "phase5_curriculum_candidates": ("phase5", "curriculum_candidates"),
    "phase5_curriculum": ("phase5", "curriculum"),
}


class ReaderHandbookWorkflowError(ValueError):
    """A fixed, redacted reader-packet preparation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ReaderHandbookWorkflowError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_utf8(path_value: str | Path, code: str, *, allow_empty: bool = False) -> tuple[Path, bytes, str]:
    path = chapter._absolute(path_value)
    try:
        chapter._assert_no_link_components(path)
        if path.is_symlink() or not path.is_file():
            _fail(code)
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except ReaderHandbookWorkflowError:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise ReaderHandbookWorkflowError(code) from exc
    if not allow_empty and (not raw or not text.strip()):
        _fail(code)
    return path.resolve(strict=True), raw, text


def _read_authoring_references() -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for manifest_key, relative_path, output_path in _AUTHORING_REFERENCES:
        source_path, raw, text = _read_utf8(_SKILL_ROOT / relative_path, "AUTHORING_REFERENCE_INVALID")
        references.append({
            "manifest_key": manifest_key,
            "relative_path": relative_path,
            "output_path": output_path,
            "source_path": source_path,
            "raw": raw,
            "text": text,
            "sha256": _sha256(raw),
        })
    return references


def _recheck_context(context: curriculum_workflow.AuthenticatedCurriculumRunPlan) -> None:
    try:
        context.recheck()
    except (chapter.Phase6ChapterError, curriculum_workflow.Phase6CurriculumRunError) as exc:
        raise ReaderHandbookWorkflowError(getattr(exc, "code", "INPUT_CHANGED")) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookWorkflowError("INPUT_CHANGED") from exc


def _parse_authenticated_raw(context: curriculum_workflow.AuthenticatedCurriculumRunPlan) -> dict[str, Any]:
    raw = context.raw
    if not isinstance(raw, bytes):
        _fail("PLAN_INVALID")
    try:
        plan = _strict_json_loads(raw.decode("utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("plan must be an object")
        chapter.validate_artifact(plan)
        if plan.get("artifact_kind") != "curriculum-run-plan":
            raise ValueError("wrong artifact kind")
        if chapter.dumps_artifact(plan).encode("utf-8") != raw:
            raise ValueError("non-canonical plan")
    except (chapter.ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise ReaderHandbookWorkflowError("PLAN_INVALID") from exc
    return plan


def _selected_units(plan: Mapping[str, Any], selected_unit_ids: Sequence[str]) -> list[dict[str, Any]]:
    if isinstance(selected_unit_ids, (str, bytes)) or not isinstance(selected_unit_ids, (list, tuple)) or not selected_unit_ids:
        _fail("UNIT_SELECTION_INVALID")
    if any(not isinstance(value, str) or not value for value in selected_unit_ids):
        _fail("UNIT_SELECTION_INVALID")
    if len(set(selected_unit_ids)) != len(selected_unit_ids):
        _fail("UNIT_SELECTION_DUPLICATE")
    rows = plan.get("units")
    if not isinstance(rows, list):
        _fail("PLAN_INVALID")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = row.get("unit_identity") if isinstance(row, Mapping) else None
        unit_id = identity.get("id") if isinstance(identity, Mapping) else None
        if not isinstance(unit_id, str) or unit_id in by_id:
            _fail("PLAN_INVALID")
        by_id[unit_id] = row
    unknown = [unit_id for unit_id in selected_unit_ids if unit_id not in by_id]
    if unknown:
        _fail("UNIT_UNKNOWN")
    source_status = plan.get("source_status")
    unknown_files = plan.get("unknown_files")
    selected = []
    for unit_id in selected_unit_ids:
        row = json.loads(json.dumps(by_id[unit_id], ensure_ascii=False))
        row["source_status"] = source_status
        row["unknown_files"] = json.loads(json.dumps(unknown_files, ensure_ascii=False))
        selected.append(row)
    return selected


def _verified_input_records(
    plan: Mapping[str, Any],
    context: curriculum_workflow.AuthenticatedCurriculumRunPlan,
) -> list[dict[str, str]]:
    authenticated = context.authenticated
    audit = getattr(authenticated, "_audit", None)
    phase4_paths = getattr(audit, "input_paths", None)
    if not isinstance(phase4_paths, Mapping):
        _fail("INPUT_CONTEXT_INVALID")
    phase4_paths = dict(phase4_paths)
    phase4_paths.update({
        "claim_candidates": context.inputs.claim_candidates_path,
        "claim_evidence": getattr(authenticated, "_report_path", None),
        "claim_evidence_graph": getattr(authenticated, "_overlay_path", None),
    })
    phase5_paths = {key: value.path for key, value in context.reads.items()}
    records = plan.get("input_digests")
    if (
        not isinstance(records, list)
        or len(records) != len(_AUTHENTICATED_INPUT_ROLE_PATHS)
    ):
        _fail("PLAN_INVALID")
    roles = [record.get("role") for record in records if isinstance(record, Mapping)]
    if (
        len(roles) != len(records)
        or any(not isinstance(role, str) for role in roles)
        or set(roles) != set(_AUTHENTICATED_INPUT_ROLE_PATHS)
    ):
        _fail("PLAN_INVALID")
    output: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("role") not in _AUTHENTICATED_INPUT_ROLE_PATHS:
            _fail("PLAN_INVALID")
        group, lookup_key = _AUTHENTICATED_INPUT_ROLE_PATHS[record["role"]]
        path_value = (phase4_paths if group == "phase4" else phase5_paths).get(lookup_key)
        if path_value is None:
            _fail("INPUT_CONTEXT_INVALID")
        try:
            path = chapter._absolute(path_value).resolve(strict=True)
            chapter._assert_no_link_components(path)
            if path.is_symlink() or not path.is_file():
                _fail("INPUT_CHANGED")
            if _sha256(path.read_bytes()) != record.get("sha256"):
                _fail("INPUT_CHANGED")
        except ReaderHandbookWorkflowError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ReaderHandbookWorkflowError("INPUT_CHANGED") from exc
        output.append({
            "role": record["role"],
            "artifact_kind": record["artifact_kind"],
            "schema_version": record["schema_version"],
            "path": str(path),
            "sha256": record["sha256"],
        })
    return output


def _fenced(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}{'' if text.endswith(chr(10)) else chr(10)}{fence}"


def _writer_packet(
    plan: Mapping[str, Any],
    selected: list[dict[str, Any]],
    input_records: list[dict[str, str]],
    blueprint_text: str,
    prompt_text: str,
    *,
    blueprint_path: Path,
    prompt_path: Path,
    plan_path: Path,
    plan_sha256: str,
    blueprint_sha256: str,
    prompt_sha256: str,
    writer_session_id: str,
    book_plan_text: str | None = None,
    book_plan_path: Path | None = None,
    book_plan_sha256: str | None = None,
    authoring_references: Sequence[Mapping[str, Any]] | None = None,
) -> bytes:
    book_plan_section: list[str] = []
    if book_plan_text is not None:
        if book_plan_path is None or book_plan_sha256 is None:
            raise ValueError("book plan binding is incomplete")
        book_plan_section = [
            "## 人工读者全书路线（控制阅读顺序与范围）",
            "",
            f"路线文件：`{book_plan_path}`；SHA-256：`{book_plan_sha256}`。以下为原文：",
            "",
            _fenced(book_plan_text),
            "",
            "本人工路线控制读者手册的阅读顺序与覆盖范围；教学蓝图规定学习结果；已认证课程单元只是证据单元，不是读者目录。",
            "",
        ]
    planning_guidance = [
        f"- 结合“{row['unit_identity']['title']}”规划主题位置和讲解所需的项目锚点；这只是写作提示，不是学习结果。"
        for row in selected
    ]
    source_anchor = {
        key: plan[key]
        for key in ("repository_revision", "snapshot_kind", "source_metadata", "source_status",
                    "source_run_manifest_sha256", "unknown_files")
    }
    unit_payload = json.dumps(selected, ensure_ascii=False, indent=2)
    digest_payload = json.dumps(input_records, ensure_ascii=False, indent=2)
    anchor_payload = json.dumps(source_anchor, ensure_ascii=False, indent=2)
    authoring_loading_section: list[str] = []
    authoring_reference_sections: list[str] = []
    if authoring_references is not None:
        required_keys = {row[0] for row in _AUTHORING_REFERENCES}
        if {item.get("manifest_key") for item in authoring_references} != required_keys:
            raise ValueError("authoring references are incomplete")
        authoring_loading_section = [
            "## 完整阅读随包核心规范",
            "",
            "开始写正文前，请完整阅读本目录中的三份规范并按主题适用：`source-topic-writing-card.md`、`reader-handbook-contract.md`、`learner-ai-collaboration-card.md`。",
            "本输入包也附有规范原文；若 packet 或工具显示被截断，请分段读取对应随包文件直到末尾。部分显示不代表全文已读。只需加载这三份规范，不必重复读取整个项目或全库。",
            "",
        ]
        authoring_reference_sections = [
            "## 必需的 Skill 核心参考全文",
            "",
            "以下是本包随 writer packet 冻结的规范原文。写作时直接使用这些副本，不依赖会话历史、外部 Skill 目录或网页；SHA-256 仅标识原文，不表示教学验收通过。",
            "",
        ]
        for item in authoring_references:
            authoring_reference_sections.extend([
                f"### {item['relative_path']}",
                "",
                f"随包文件：`{item['output_path']}`；SHA-256：`{item['sha256']}`。以下为原文：",
                "",
                _fenced(str(item["text"])),
                "",
            ])
    lines = [
        "# 读者手册主题写作输入",
        "",
        *authoring_loading_section,
        "## 写作规划提示（非学习结果）",
        "",
        "以下条目只用于组织所选单元的写作计划，不定义、改写或补充学习结果：",
        *planning_guidance,
        "",
        *book_plan_section,
        "本次唯一权威学习结果来自下方随附的完整教学蓝图。保留蓝图明确列出的结果；不得根据单元标题、路由或其他元数据推导或发明学习结果。围绕蓝图组织课程，但已接受证据账本不是教学范围上限。不要从空泛的 canonical 章节骨架开始，也不要把未知补成事实。",
        "",
        "## 教学蓝图全文",
        "",
        f"蓝图文件：`{blueprint_path}`；SHA-256：`{blueprint_sha256}`。以下为原文：",
        "",
        _fenced(blueprint_text),
        "",
        "## 已认证课程单元",
        "",
        "保留每个单元的完整身份、来源、路线、状态、原因码与先修阻断信息；可在同一主题中组合多个所选单元。",
        "",
        _fenced(unit_payload),
        "",
        "## 项目来源锚点与完整性",
        "",
        f"项目来源状态：`{plan['source_status']}`；未分类文件数：`{plan['unknown_files']}`。PARTIAL 状态必须在教学内容中保留为部分观察，不得写成项目全貌。",
        "",
        _fenced(anchor_payload),
        "",
        "## 已验证输入路径与摘要",
        "",
        f"认证课程计划：`{plan_path}`；SHA-256：`{plan_sha256}`。以下路径与摘要来自同一已认证输入捕获：",
        "",
        _fenced(digest_payload),
        "",
        "## 项目定位与作者约束",
        "",
        "每个所选单元的完整身份对象保留 `project_refs` 中的图节点、边、证据和文件路径锚点。区分静态源码事实、通用原理、教学假设和未知运行时效果；只补必要前置，围绕蓝图设计业务流程、跨层连接、具体输入推演、失败路径、验证和有边界的练习。不要声称覆盖未选择的项目模块。",
        "",
        *authoring_reference_sections,
        "## 当前作者提示全文",
        "",
        f"作者提示文件：`{prompt_path}`；SHA-256：`{prompt_sha256}`。以下为本次快照：",
        "",
        _fenced(prompt_text),
        "",
        f"写作会话：`{writer_session_id}`。本输入包最高状态为 `PREPARED` / `NOT_REVIEWED`，不证明正文已写成、已审查或可发布。",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def prepare_reader_theme(
    auth_context: curriculum_workflow.AuthenticatedCurriculumRunPlan,
    selected_unit_ids: Sequence[str],
    blueprint_path: str | Path,
    out_dir: str | Path,
    writer_session_id: str,
    *,
    book_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write a bound, provider-free input packet for one or more curriculum units."""
    if not isinstance(auth_context, curriculum_workflow.AuthenticatedCurriculumRunPlan):
        _fail("INPUT_CONTEXT_INVALID")
    if not isinstance(writer_session_id, str) or not writer_session_id.strip() or not writer_session_id.isprintable():
        _fail("WRITER_SESSION_INVALID")
    _recheck_context(auth_context)
    plan = _parse_authenticated_raw(auth_context)
    selected = _selected_units(plan, selected_unit_ids)
    blueprint, blueprint_raw, blueprint_text = _read_utf8(blueprint_path, "BLUEPRINT_INVALID")
    book_plan: Path | None = None
    book_plan_raw: bytes | None = None
    book_plan_text: str | None = None
    if book_plan_path is not None:
        book_plan, book_plan_raw, book_plan_text = _read_utf8(book_plan_path, "BOOK_PLAN_INVALID")
    prompt, prompt_raw, prompt_text = _read_utf8(_WRITER_PROMPT, "WRITER_PROMPT_INVALID")
    authoring_references = _read_authoring_references()
    try:
        plan_path = chapter._absolute(auth_context.path).resolve(strict=True)
        output = chapter._check_external_output(out_dir, auth_context.inputs, must_exist=False)
    except chapter.Phase6ChapterError as exc:
        raise ReaderHandbookWorkflowError(exc.code) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookWorkflowError("OUTPUT_INVALID") from exc
    if chapter._inside(output, _SKILL_ROOT.resolve(strict=True)):
        _fail("OUTPUT_INVALID")
    input_records = _verified_input_records(plan, auth_context)
    plan_sha = _sha256(auth_context.raw)
    blueprint_sha = _sha256(blueprint_raw)
    book_plan_sha = _sha256(book_plan_raw) if book_plan_raw is not None else None
    prompt_sha = _sha256(prompt_raw)
    packet = _writer_packet(
        plan, selected, input_records, blueprint_text, prompt_text,
        blueprint_path=blueprint, prompt_path=prompt, plan_path=plan_path,
        plan_sha256=plan_sha, blueprint_sha256=blueprint_sha,
        prompt_sha256=prompt_sha, writer_session_id=writer_session_id,
        book_plan_text=book_plan_text, book_plan_path=book_plan,
        book_plan_sha256=book_plan_sha,
        authoring_references=authoring_references,
    )
    source_anchor = {
        key: plan[key]
        for key in ("repository_revision", "snapshot_kind", "source_metadata", "source_status",
                    "source_run_manifest_sha256", "unknown_files")
    }
    manifest = {
        "artifact_kind": "reader-handbook-writer-input",
        "version": "1.2",
        "status": "PREPARED",
        "review_status": "NOT_REVIEWED",
        "writer_session_id": writer_session_id,
        "run_plan_id": plan["run_plan_id"],
        "run_plan_path": str(plan_path),
        "plan_sha256": plan_sha,
        "blueprint_path": str(blueprint),
        "blueprint_sha256": blueprint_sha,
        **({"book_plan": {"path": str(book_plan), "sha256": book_plan_sha}} if book_plan is not None else {}),
        "writer_prompt_path": str(prompt),
        "writer_prompt_sha256": prompt_sha,
        "source_anchor": source_anchor,
        "source_status": plan["source_status"],
        "unknown_files": plan["unknown_files"],
        "selected_units": selected,
        "known_input_digests": input_records,
        "outputs": {
            "writer_packet": {"path": "writer-packet.md", "sha256": _sha256(packet)},
            "teaching_blueprint": {"path": "teaching-blueprint.md", "sha256": blueprint_sha},
            "writer_prompt": {"path": "reader-handbook-writer.md", "sha256": prompt_sha},
        },
    }
    for item in authoring_references:
        manifest["outputs"][item["manifest_key"]] = {
            "path": item["output_path"],
            "sha256": item["sha256"],
        }
    if book_plan is not None and book_plan_raw is not None:
        manifest["outputs"]["reader_book_plan"] = {
            "path": "reader-book-plan.md",
            "sha256": book_plan_sha,
        }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        _recheck_context(auth_context)
        if _read_utf8(blueprint, "INPUT_CHANGED")[1] != blueprint_raw:
            _fail("INPUT_CHANGED")
        if book_plan is not None and book_plan_raw is not None:
            if _read_utf8(book_plan, "INPUT_CHANGED")[1] != book_plan_raw:
                _fail("INPUT_CHANGED")
        if _read_utf8(prompt, "INPUT_CHANGED")[1] != prompt_raw:
            _fail("INPUT_CHANGED")
        for item in authoring_references:
            if _read_utf8(item["source_path"], "INPUT_CHANGED")[1] != item["raw"]:
                _fail("INPUT_CHANGED")
        output = chapter._check_external_output(out_dir, auth_context.inputs, must_exist=False)
        if chapter._inside(output, _SKILL_ROOT.resolve(strict=True)):
            _fail("OUTPUT_INVALID")
        files_to_publish = {
            "writer-packet.md": packet,
            "teaching-blueprint.md": blueprint_raw,
            "reader-handbook-writer.md": prompt_raw,
            "reader-input-manifest.json": manifest_raw,
        }
        for item in authoring_references:
            files_to_publish[item["output_path"]] = item["raw"]
        if book_plan_raw is not None:
            files_to_publish["reader-book-plan.md"] = book_plan_raw
        chapter._publish_prepare_files(output, files_to_publish)
    except ReaderHandbookWorkflowError:
        raise
    except chapter.Phase6ChapterError as exc:
        raise ReaderHandbookWorkflowError(exc.code) from exc
    return manifest
