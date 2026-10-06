#!/usr/bin/env python3
"""Static development-plan validation and literal, full-content rendering."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact

STAGES = ("BASELINE", "DISCOVER", "SPEC", "PLAN", "SLICE", "BUILD",
          "VERIFY", "REVIEW", "REPAIR", "FULL-CHAIN", "RETRO")
PROMPT_FIELDS = (("goal", "目标 GOAL"), ("context", "上下文 CONTEXT"),
                 ("constraints", "约束 CONSTRAINTS"), ("scope", "范围 SCOPE"),
                 ("evidence", "证据 EVIDENCE"), ("verification", "验证 VERIFICATION"),
                 ("done_when", "完成标准 DONE WHEN"),
                 ("output_contract", "输出合同 OUTPUT CONTRACT"))


def _fail(message: str) -> None:
    raise ArtifactValidationError(message)


def _unique(values: list[str], where: str) -> set[str]:
    if any(not value.strip() for value in values) or len(set(values)) != len(values):
        _fail(f"{where}: blank or duplicate identifier/path")
    return set(values)


def _path(value: str, where: str, *, root: bool = False) -> str:
    if root and value == ".":
        return value
    if (not value or value != value.strip() or "\\" in value or ":" in value
            or value.startswith("/") or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        _fail(f"{where}: expected safe POSIX relative path")
    return value


def _paths(values: list[str], where: str) -> set[str]:
    result = _unique(values, where)
    for value in values:
        _path(value, where)
    return result


def _refs(values: list[str], available: set[str], where: str) -> set[str]:
    refs = _unique(values, where)
    if not refs <= available:
        _fail(f"{where}: unknown reference: {sorted(refs - available)}")
    return refs


def validate_development_plan(plan: Mapping[str, Any],
                              project_index: Mapping[str, Any],
                              evidence: Mapping[str, Any]) -> None:
    """Prove only static input consistency; never authenticate source or run it."""
    for data, kind in ((plan, "development-plan"), (project_index, "project-index"),
                       (evidence, "evidence")):
        validate_artifact(data)
        if data["artifact_kind"] != kind:
            _fail(f"expected artifact_kind {kind}")
    if len({data["repository_revision"] for data in (plan, project_index, evidence)}) != 1:
        _fail("repository_revision: plan/index/evidence mismatch")
    snapshot = project_index["project"]["snapshot_kind"]
    for candidate in (evidence.get("snapshot_kind"),
                      evidence.get("source_metadata", {}).get("snapshot_kind")):
        if candidate is not None and candidate != snapshot:
            _fail("snapshot_kind: project-index/evidence mismatch")
    indexed = _paths([row["path"] for row in project_index["files"]], "project-index.files")
    if project_index["file_count"] != len(indexed):
        _fail("project-index.file_count mismatch")
    items = evidence["items"]
    evidence_ids = _unique([row["id"] for row in items], "evidence.items")
    plan_refs = _refs(plan["evidence_refs"], evidence_ids, "evidence_refs")
    by_id = {row["id"]: row for row in items}
    for ref in plan_refs:
        row = by_id[ref]
        locator = row["locator"]
        # Official external references and explicit E6 inference need not name project files.
        if row["kind"] in ("source", "config", "test", "dependency") and "path" in locator:
            path = _path(locator["path"], f"evidence[{ref}].locator.path")
            if path not in indexed:
                _fail(f"evidence[{ref}]: project path absent from index")
    impact = plan["impact"]
    existing = _paths(impact["existing_files"], "impact.existing_files")
    planned = _paths(impact["planned_new_files"], "impact.planned_new_files")
    if not existing <= indexed:
        _fail("impact.existing_files: path absent from index")
    if planned & indexed:
        _fail("impact.planned_new_files: already exists in index")
    acceptance = _unique([row["id"] for row in plan["requirement"]["acceptance"]],
                         "requirement.acceptance")
    _unique([row["id"] for row in plan["decisions"]], "decisions")
    checks = _unique([row["id"] for row in plan["checks"]], "checks")
    for check in plan["checks"]:
        _path(check["cwd"], f"checks[{check['id']}].cwd", root=True)
        _refs(check["evidence_refs"], plan_refs, f"checks[{check['id']}].evidence_refs")
    if tuple(stage["name"] for stage in plan["stages"]) != STAGES:
        _fail("stages: require complete, unique canonical order")
    for stage in plan["stages"]:
        _refs(stage["checks"], checks, f"stages[{stage['name']}].checks")
    slices = plan["slices"]
    ids = _unique([row["id"] for row in slices], "slices")
    predecessors: set[str] = set()
    for row in slices:
        where = f"slices[{row['id']}]"
        dependencies = _refs(row["depends_on"], ids, where + ".depends_on")
        if not dependencies <= predecessors:
            _fail(where + ": dependencies must precede slice (cycle/self/forward reference)")
        allowed_existing = _paths(row["allowed_existing"], where + ".allowed_existing")
        allowed_new = _paths(row["allowed_new"], where + ".allowed_new")
        protected = _paths(row["protected"], where + ".protected")
        if not allowed_existing <= existing or not allowed_new <= planned:
            _fail(where + ": allowed paths outside impact")
        if not protected <= indexed or protected & (allowed_existing | allowed_new):
            _fail(where + ": protected paths absent from index or overlap allowed")
        if not allowed_existing and not allowed_new:
            _fail(where + ": slice has no affected file")
        _refs(row["tests"], checks, where + ".tests")
        _refs(row["acceptance_refs"], acceptance, where + ".acceptance_refs")
        _refs(row["evidence_refs"], plan_refs, where + ".evidence_refs")
        predecessors.add(row["id"])


def _section(title: str, content: str) -> str:
    # Content is not summarized, escaped, stripped, reformatted or executed.
    return "\n## " + title + "\n\n" + content + "\n"


def _json_block(value: Any) -> str:
    return _fence(json.dumps(value, ensure_ascii=False, indent=2), "json")


def _fence(content: str, language: str = "text") -> str:
    length = max([2] + [len(run) for run in re.findall(r"`+", content)]) + 1
    delimiter = "`" * length
    return delimiter + language + "\n" + content + "\n" + delimiter + "\n"


def _prompt(prompt: Mapping[str, str]) -> str:
    return _fence("\n\n".join(label + "\n" + prompt[key] for key, label in PROMPT_FIELDS))


def _header(plan: Mapping[str, Any]) -> str:
    return (f"# {plan['title']}\n\n"
            f"实验：{plan['lab_id']}；证据 repository_revision：{plan['repository_revision']}。\n\n"
            "实施 NOT_RUN；验证 NOT_RUN；独立语义/教学审查 PENDING。\n\n"
            "本产物为静态作者计划，生成计划≠实施/运行验证/人工验收通过。"
            "只核对提供的冻结输入，不重新扫描/认证源码，不承诺当前工作树等于该snapshot。"
            "作者示例/模拟输出不能充当真实命令结果。development-plan.json是唯一权威需求/决定/计划；"
            "Markdown为只读投影视图，修订应回到权威计划后重新编译到新目录。\n")


def render_prompts(plan: Mapping[str, Any]) -> str:
    result = _header(plan)
    for stage in plan["stages"]:
        result += _section(stage["name"], _prompt(stage["prompt"]))
    result += _section("每slice BUILD附加合同",
                       "将具体slice与BUILD提示词共同提供；不授予超出原计划的执行权限。")
    for row in plan["slices"]:
        result += _section(row["id"], _json_block(row))
    return result


def render_lab(plan: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    result = _header(plan)
    result += _section("项目与问题情景", plan["project_context"])
    result += _section("初学者前置", plan["prerequisites"])
    result += _section("当前业务全链路", plan["current_workflow"])
    result += _section("需求背景", plan["requirement"]["problem"])
    result += _section("用户、范围与验收", _json_block(plan["requirement"]))
    result += _section("单一权威决定的投影", _json_block(plan["decisions"]))
    result += _section("当前事实与推断的原始证据定位",
                       "E6始终为显式推断；其他等级也只保留输入原标签，不自动认证/晋级。")
    lookup = {row["id"]: row for row in evidence["items"]}
    for ref in plan["evidence_refs"]:
        row = lookup[ref]
        label = "（推断，不是已证事实）" if row["level"] == "E6" else "（输入证据，不代表运行证明）"
        result += _section(ref + " " + row["level"] + label,
                           row["summary"] + "\n\n" + _json_block(row))
    result += _section("架构影响", plan["impact"]["architecture"])
    result += _section("调用链变化", plan["impact"]["call_chain"])
    result += _section("已存在文件与计划新文件", _json_block(plan["impact"]))
    result += _section("切片与依赖（不自动批准或执行）", _json_block(plan["slices"]))
    for check in plan["checks"]:
        result += _section("检查计划 " + check["id"] + "（NOT_RUN）",
                           check["purpose"] + "\n\n工作目录：\n" + check["cwd"]
                           + "\n\n字面命令（未执行）：\n" + _fence(check["command"])
                           + "\n预期观察（不是实际结果）：\n" + check["expected"]
                           + "\n\n" + _json_block(check))
    check_lookup = {row["id"]: row for row in plan["checks"]}
    for stage in plan["stages"]:
        result += _section(stage["name"] + "：为何此时做", stage["explanation"])
        for key, title in (("learner_actions", "人怎样操作与判断"),
                           ("agent_actions", "AI协作、追问与修订"),
                           ("gate", "继续标准"), ("failure_recovery", "失败与恢复")):
            result += _section(stage["name"] + "：" + title, stage[key])
        result += _section(stage["name"] + "：完整任务提示词", _prompt(stage["prompt"]))
        for check_id in stage["checks"]:
            result += _section(stage["name"] + "：计划检查 " + check_id + "（NOT_RUN）",
                               _json_block(check_lookup[check_id]))
    result += _section("企业风险与不适用理由", plan["enterprise_hardening"])
    for item in plan["interview_followup"]:
        result += _section("追问与完整答案", item["question"] + "\n\n" + item["answer"])
    return result
