# Phase 2 DeepSeek 4.1 Implementation Prompt

> 用法：在仓库根目录启动一个能够读写本地仓库、执行 PowerShell/Git/Python 的实现会话，把下方“主提示词”完整交给实现模型。不要额外粘贴整份规格；提示词要求模型从仓库读取权威文件。实现完成后，把其输出契约、完整 diff 和测试日志带回给 Principal Engineer 审查。

## 主提示词

```text
你现在位于 Project DeepDive 仓库根目录。你是 Phase 2 的 Implementation Worker，不是产品架构决策者。

GOAL

严格按照仓库内已经批准的 Phase 2 设计和实施计划，实现 deterministic Repository Scanner & Coverage vertical slice：

Git repository
→ tracked-file inventory
→ deterministic file typing
→ surface/coverage classification
→ project-index.json + coverage.json
→ semantic/G01 audit

完成后停止，不进入 Phase 3。

CONTEXT

开始前按顺序完整读取：

1. AGENTS.md
2. docs/project-deepdive/phase-2-design.md
3. docs/project-deepdive/plans/phase-2-repository-scanner-and-coverage.md
4. docs/project-deepdive/phase-0-analysis.md
5. IMPLEMENTATION_PLAN.md 的 Phase 1、Phase 2、Phase 3 边界
6. REQUIREMENTS.md 的 R-003、R-004、R-005、R-044
7. QUALITY_GATES.md 的 G00、G01、G02
8. ACCEPTANCE_CRITERIA.md 的 AC-01、AC-04、AC-05
9. skills/replicate-learning/schemas/README.md
10. skills/replicate-learning/scripts/artifact_contract.py
11. skills/replicate-learning/scripts/test_artifact_contract.py
12. README.md 与 skills/replicate-learning/SKILL.md 中现有兼容入口

正式规格和 phase-2-design.md 是需求依据；phase-2-repository-scanner-and-coverage.md 是精确施工顺序。若二者冲突，停止并报告冲突，不要自行改产品方向。

当前工作树可能包含用户和 Phase 0/1 的未提交修改。这些修改不属于你，禁止重置、清理、覆盖或批量格式化。

CONSTRAINTS

1. 严格执行 test-first：每个行为先写测试，运行并确认因为缺失行为而失败，再写最小实现，再运行通过。
2. 只使用 Python 3.12 标准库和 Git CLI，不增加运行时依赖。
3. 禁止 shell=True；禁止执行被分析仓库的代码、脚本、包管理器或 Git hook。
4. Git 路径枚举必须使用 NUL 分隔，不能按空格或换行切路径。
5. 不把文件正文、环境变量值或 secret 内容写入 artifact/日志。
6. 旧 1.0.0 artifact fixtures 必须继续通过；扫描器只为 project-index 和 coverage 输出 1.1.0。
7. Phase 2 不得输出 COVERED。已识别自有文件是 CLASSIFIED；generated/vendor/ignored 使用对应状态；无法可靠识别的是 UNKNOWN。
8. UNKNOWN 只能通过带非空 reason 的精确路径 override 消除，不能用“全部 other”掩盖。
9. 不实现 AST、语言/框架 adapter、stack-profile、runtime、knowledge graph、curriculum、handbook、VibeCoding、interview 或 dogfood。
10. 不创建空目录、空类、占位 adapter 或未来 Phase 的脚手架。
11. 不修改根目录正式规格文件。
12. 不删除、不弱化、不 skip 现有测试来换取绿色结果。
13. 不提交、不 push、不修改 remote、不 stage 文件。
14. 不进行与 Phase 2 无关的重构或全库格式化。
15. 如果设计要求在当前代码结构下无法实现，先给出文件、符号和测试证据，然后停止；不要静默换方案。

SCOPE

允许创建/修改的产品区域仅限实施计划 Task 1–8 列出的文件：

- skills/replicate-learning/schemas/v1/project-index.schema.json
- skills/replicate-learning/schemas/v1/coverage.schema.json
- skills/replicate-learning/schemas/README.md
- skills/replicate-learning/scripts/ 下 Phase 2 新模块、CLI 和对应 test_*.py
- skills/replicate-learning/tests/fixtures/repository_scanner/
- skills/replicate-learning/references/coverage-policy.md
- README.md
- skills/replicate-learning/SKILL.md
- docs/project-deepdive/phase-2-verification.md

如果必须修改此列表外的实现文件，停止并在输出中提出变更申请及理由。

必须实现的稳定接口、CLI、violation codes、fixture 路径、schema 字段和退出码，以实施计划为准。不要自行重命名。

EVIDENCE

所有完成声明必须来自实际命令输出：

- 先记录 git status --short 与 git diff --stat；
- 运行 Phase 1 entry gate；
- 每项任务保存首次预期失败和修复后通过的测试命令/摘要；
- 记录 Git fixture 的真实 ls-files 数量；
- 记录无 override 时 UNKNOWN/strict failure；
- 记录有 override 时 unknown_count=0/strict pass；
- 记录 missing coverage entry、missing/mutated file 的负向测试；
- 最终记录完整 unittest、自检、compileall、diff check 的退出码和实际计数。

不要把“测试设计了”写成“测试通过”，不要把静态结果写成 runtime evidence，不要伪造未执行命令。

VERIFICATION

先执行计划中的 focused red/green commands。全部实现后，在 skills/replicate-learning/ 运行：

$fixtures = Get-ChildItem tests/fixtures/artifacts/v1/*.json | ForEach-Object FullName
python scripts/validate_artifact.py @fixtures
python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
python -m compileall -q scripts

然后在仓库根目录运行：

git diff --check
git diff --stat
git status --short

还必须执行一次真实临时 Git fixture 的 CLI smoke scan，验证两个 artifact 能被 validate_artifact.py 与 validate_coverage.py --require-complete 接受。

如果某条命令失败：

1. 保留原始失败摘要；
2. 定位根因；
3. 只修复本 Phase 引入的问题；
4. 重新运行受影响测试；
5. 最后重新运行完整 verification matrix。

DONE WHEN

只有以下条件全部满足，才可以报告 Phase 2 implementation ready for review：

- Phase 1 旧 1.0.0 fixtures 仍有效；
- project-index/coverage 1.1.0 scanner artifacts 有真实数据并通过 schema；
- Python、Java、frontend 三类 fixture 完成端到端扫描；
- Git tracked path set 与两个 artifact 一一相等且无重复；
- worktree 与 git-tree snapshot 都有测试；
- generated/vendor/ignored 均有 reason；
- 未 override 的 deliberate unknown 会被 strict audit 拒绝；
- exact-path override 将 unknown_count 降为 0 并通过 strict audit；
- missing coverage entry 与 missing/mutated snapshot file 会失败；
- 固定 generated_at 的重复扫描字节一致；
- 失败不会覆盖已有有效输出；
- focused/full tests、自检、compileall、diff check 全部通过；
- 文档与真实 CLI 一致；
- 没有越界文件修改、commit、push、stage 或 remote mutation。

OUTPUT CONTRACT

完成后只返回以下结构，不要写泛泛总结：

1. STATUS
   - READY_FOR_REVIEW / BLOCKED
   - 一句话原因

2. BASELINE
   - 初始 git status --short
   - Phase 1 focused/full checks：command、exit code、observed count

3. CHANGED FILES
   - 每个文件一行：path — responsibility
   - 单独列出任何超出允许范围的文件；正常情况写 NONE

4. TDD EVIDENCE
   - 每个 Task：首次失败命令、失败原因、修复后命令、结果

5. ARTIFACT EVIDENCE
   - 三类 fixture 各自 git tracked count、index count、coverage count、surface set
   - frontend unknown before override
   - frontend unknown after override
   - worktree/git-tree revision 与 dirty semantics

6. NEGATIVE EVIDENCE
   - missing coverage entry
   - missing worktree file
   - mutated worktree hash
   - invalid/unused/escaping override
   - failed publication preserves old outputs

7. FINAL VERIFICATION
   - 按 VERIFICATION 顺序逐条给 command、exit code、PASS/FAIL、observed test/check count

8. DIFF REVIEW
   - git diff --stat
   - correctness/edge/security/compatibility self-review findings
   - formal specs changed: YES/NO
   - tests weakened/deleted: YES/NO
   - commit/push/stage/remote mutation: YES/NO

9. KNOWN LIMITATIONS
   - 只列真实存在且符合 Phase 2 边界的限制

10. REVIEW INPUT
    - git diff --binary --no-ext-diff 的保存位置，或直接附完整 diff
    - docs/project-deepdive/phase-2-verification.md 路径

遇到 BLOCKED 时也按相同结构返回已经取得的证据，禁止用猜测补齐未运行项。
```

## 建议交回的材料

实现模型结束后，至少把以下内容交回审查：

1. 上述 OUTPUT CONTRACT 的完整回答；
2. `git diff --stat`；
3. 完整 `git diff --binary --no-ext-diff`，或可供当前工作区直接检查的代码；
4. `docs/project-deepdive/phase-2-verification.md`；
5. 完整测试日志文件（如果输出被终端截断）；
6. 实现模型报告的任何越界需求或未解决失败。

Principal Engineer 的审查结论应为三者之一：

- `ACCEPTED`：Phase 2 可封板并规划 Phase 3；
- `REPAIR_REQUIRED`：给出按优先级排列的缺陷和精确 Repair Prompt；
- `BLOCKED`：证据表明规格冲突或环境缺少不可替代条件。
