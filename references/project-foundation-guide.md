# 项目基础事实与地图工具

本指南供 `project-scan`、`project-map` 或 `learn-project` 需要确定性项目事实时按需读取。它串联真实 Phase 2–5 入口，不要求每次学习都重跑所有阶段：先检查现有产物和快照身份，只补当前任务缺失或已过期的输入。脚本命令的 `--help` 与本指南描述才是实际 CLI；模式名是自然语言意图，不是 shell 命令。

## 输入链与共同约束

工具链按已认证输入逐步建立索引、静态事实、证据、图和课程草案：

```text
目标仓库的 Git 跟踪文件
  → Phase 2 project-index + coverage
  → Phase 3A stack-profile + evidence
  → 语言静态分析 bundles + E1
  → Phase 3 run（需要组合包时）
  → Phase 4 source-backed graph / claims
  → Phase 5 prerequisites / curriculum
```

从 Skill 包根目录 `skills/project-deepdive/` 运行下列 Python 命令。将示例路径换成实际项目与输出路径；分析产物放在目标仓库之外。脚本读取受限的 Git 跟踪快照，不执行目标项目代码、构建、测试或包管理器。需要实测时，先由用户明确要求，再走相应的运行验证流程。

已有文件只有在项目身份、Git revision 或 worktree 状态、schema version 与上游摘要仍匹配时才可复用。版本与输入不匹配应使用对应阶段的 validator 查明并重建必要的最小上游，不手工改 hash、ID 或状态。每个阶段可能给出 `PASS`、`PARTIAL`、`FAIL` 或适用的 `NOT_RUN`；`PARTIAL` 和未知文件数要向下游保留。结构校验成功不能证明语义教学、运行时行为或质量门禁通过。

## Phase 1：校验已有版本化产物

```powershell
python scripts/validate_artifact.py <artifact-a.json> <artifact-b.json>
```

该脚本按注册的 artifact kind/schema version 检查 JSON 形状；输入列表可含多个文件。它不认证项目身份、源码语义或教学质量，也不替代后续阶段的同快照/来源绑定。已支持版本和契约索引见包内 [schemas 说明](../schemas/README.md)。

## Phase 2：扫描和覆盖审计

```powershell
python scripts/scan_repository.py --root <目标仓库> --out <仓库外的新目录> --snapshot worktree
python scripts/validate_coverage.py --project-index <输出目录>/project-index.json --coverage <输出目录>/coverage.json --root <目标仓库>
```

`scan_repository.py` 接受必需参数 `--root`、`--out`，以及 `--snapshot worktree|git-tree`；可选 `--overrides`、`--generated-at`、`--require-complete`。它从 Git 跟踪文件生成 `project-index.json` 和 `coverage.json`。`validate_coverage.py` 接受必需的 `--project-index`、`--coverage`、`--root`，并可用 `--require-complete` 要求所有 tracked files 均被分类。选择 `worktree` 是审计当前工作树；选择 `git-tree` 是锁定提交中的 Git blob。覆盖不确定时保留 `UNKNOWN` 并如实标记 `PARTIAL`；只有确需完整分类时才加 `--require-complete`。

覆盖范围、忽略/排除规则、快照语义及敏感文件约束见[覆盖策略](coverage-policy.md)。不要把扫描统计当成已理解业务或源码。

`classification_policy_version` records which deterministic file-role rules produced Phase 2 coverage. The current auditor accepts schema 1.1 coverage made by the known `1.0.0` and `1.1.0` policies, and checks each `rule_id` against that policy's rule set while still checking the snapshot revision and file hashes. Reuse does not reclassify or rewrite the saved artifact: an old `UNKNOWN` stays unknown and keeps G01 `PARTIAL`; `--require-complete` still fails while one remains. New scans use policy `1.1.0`; an unknown policy version or a new rule ID claimed under `1.0.0` is rejected.

## Phase 3：栈、静态事实与可复核 run

`detect_stack.py` 消费通过 G01 检查的 Phase 2 对：

```powershell
python scripts/detect_stack.py --root <目标仓库> --index <project-index.json> --coverage <coverage.json> --out <仓库外的新目录>
```

必需项为 `--root --index --coverage --out`，可选 `--generated-at`。它根据受限 manifest 声明生成 `stack-profile` 与 `evidence`；清单存在不等于依赖已安装或服务运行。目标输入、快照摘要、未知分类与 G01 状态须保持一致。

`run_phase3.py` 是 Phase 2 pair → 3A → 适用静态分析 adapter → E1 审计 → run package 的已实现组合入口：

```powershell
python scripts/run_phase3.py --root <目标仓库> --project-index <project-index.json> --coverage <coverage.json> --out <仓库外的全新目录>
python scripts/validate_phase3_run.py <phase3-run目录> --root <目标仓库>
```

实际必需 flags 为 `--root --project-index --coverage --out`。输出目录须新建且位于目标仓库之外；入口先审计 G01，只为适用源码族调用已存在 adapter。它不是新的扫描器，也不保证每个语言 bundle 都是完整结果。G01 `PARTIAL` 会保留，适用文件被跳过或 adapter 受限也可能令 run 保持 `PARTIAL`。运行时注册、请求链、数据库状态和测试执行不由静态候选证明。

语言 CLI 的实际版本、配对输入、参数、文件/资源限制和 `PARTIAL` 原因详见[Phase 3 CLI 细节](phase3-static-analysis-cli-details.md)；不要依靠旧日志猜测默认版本。重要入口包括 Python `analyze_python.py`（默认 v1.0.0，显式 `--analysis-version 1.1.0` 才启用 3B2 候选）、Java `analyze_java.py`（v1.2.0；需要 JDK parse API）、Java 框架候选 `analyze_java_frameworks.py`（显式 C2 v1.3.0）、前端快照 `analyze_frontend_snapshot.py`（默认 v1.4.0，显式 v1.5.0 才启用 3D2B 候选）。所有关系、路由、模型、测试、页面、store 或 API 角色仍需按各自 analyzer 的证据和限制表述为静态候选。

前端工具自带固定版本 TypeScript parser，不安装目标项目依赖。首次需要前端快照 adapter 时，在包内 `scripts/frontend_parse_bridge/` 执行已有 lockfile 对应的 `npm ci --ignore-scripts --no-audit --no-fund`；本地文件 CLI `analyze_frontend.py` 只解析显式文件，不等于快照 artifact。Java 工具需要本机 JDK；Phase 2、Phase 3A 和 Python 工具使用 Python，Phase 3A 要求 Python 3.11+。是否需要额外安装取决于所选 adapter，不要为目标仓库执行安装命令。

如果 Phase 2/3 bundles 已经存在，只需要审计时用只读 `audit_phase3.py`：它要求 Phase 2 对、Phase 3A stack-profile/evidence；可选地接收一对 Python v1.0/v1.1、Java v1.2/v1.3 或前端 v1.4/v1.5 analysis/evidence。精确 pair flags 用该 CLI 的 `--help` 查阅。若只需将已有 bundles 归档为可复核目录，`assemble_phase3_run.py` 接收同一批必需参数、可选语言 pairs 和必需的仓库外新目录 `--out`；`validate_phase3_run.py --root <目标仓库> <phase3-run目录>` 从磁盘复验成员摘要并重跑 G01/E1。两者均不扫描仓库或重新执行语言分析。已有匹配 run 有效时复用即可。

## Phase 4：来源图、提案和声明证据

只对与本项目和快照绑定的 `phase3-run` 建图。确定性 CLI 均需要真实输入目录与新输出目录；按实际任务选择一步或几步，不要求每次全跑。

| 目的 | 实际入口和关键输入 | 结果边界 |
|---|---|---|
| 编译来源图 | `build_phase4_graph.py --run-dir <phase3-run> --root <目标仓库> --out <新目录>` | 从已验证 Phase 3 run 构建 versioned graph / evidence fold；不是完整业务模型。 |
| 导入语义提案 | `import_semantic_proposals.py --proposals <proposals.json> --graph <graph.json> --evidence <evidence.json> --run-dir <phase3-run> --root <目标仓库> --out <新目录>` | 将提案按来源证据并入；模型或作者提案仍须标成候选，导入不等于语义正确。 |
| 检查 claim 引用 | `audit_claim_evidence.py --claims <claims.json> --graph <graph.json> --evidence <evidence.json> --run-dir <phase3-run> --root <目标仓库> --out <新目录>` | 检查 claim 引用能否映射到 source-backed 输入；不能单独证明 claim 的语义蕴含。 |
| 审计 Phase 4C package | `audit_claim_evidence_4c.py --claims <claims.json> --package <package-dir> --run-dir <phase3-run> --root <目标仓库> --out <新目录>` | 认证明确绑定的输入与文档声明候选，不把 Markdown 陈述自动提升为事实。 |
| 构建 claim evidence graph | `build_claim_evidence_graph.py --phase4c-package <package-dir> --claims <claims.json> --audit-report <report.json> --run-dir <phase3-run> --root <目标仓库> --out <新目录>` | 从经审计的 claims 构建紧凑 overlay；仍须保留证据等级、未支持项和 source status。 |

没有单独 CLI 的 `phase4_graph.py`、`phase4_proposals.py`、`phase4_claim_evidence.py` 和 `phase4_claim_graph.py` 是被上述脚本复用的实现模块，不要当作命令调用。每个 CLI 的精确参数可用 `python scripts/<入口>.py --help` 查看。

Markdown 项目文档证据使用显式文件和行范围，不遍历所有文档猜业务：

```powershell
python scripts/repository_documentation_evidence.py --root <目标仓库> --project-index <project-index.json> --coverage <coverage.json> --selection <tracked.md>:<起始行>:<结束行> --out <仓库外的新目录>
```

`--selection PATH:LINE_START:LINE_END` 可重复传入。输出是受控的声明证据，不是全文副本；只有与同一 Phase 2 快照绑定且被后续 Phase 4 输入接受时才能继续使用。

## Phase 5：先修关系和课程草案

```powershell
python scripts/build_prerequisite_graph.py --candidates <prerequisite-candidates.json> --package <Phase 4 package目录> --run-dir <phase3-run> --root <目标仓库> --out <新目录>
python scripts/build_curriculum.py --candidates <curriculum-candidates.json> --prerequisite-candidates <prerequisite-candidates.json> --prerequisite-graph <prerequisite-graph.json> --package <Phase 4 package目录> --run-dir <phase3-run> --root <目标仓库> --out <新目录>
```

前者编译 Phase 5A prerequisite graph；后者在匹配的 Phase 4、先修候选和先修图上编译 Phase 5B curriculum outline。两条命令均需要已存在并相互绑定的候选、package、run 和目标仓库，不会自己发现或编写它们。当前注册 profile 使用 versioned Phase 4 graph/evidence/claims 与 Phase 5 prerequisite/curriculum artifacts；代码拒绝不支持版本、混用快照和无效来源关系。具体 schema/profile 映射见包内 [schemas 说明](../schemas/README.md)。

当前文档感知的版本配对是 Phase 4 source-run profile `1.1.0`（knowledge-graph `1.2.0`、evidence `1.4.0`、semantic-proposals `1.1.0`），Phase 5 prerequisite candidates/graph `1.1.0`，以及 curriculum candidates `1.1.0` → curriculum `1.2.0`。仍接受的较早配对包括 Phase 4 profile `1.0.0`（graph `1.1.0`、evidence `1.2.0`、proposals `1.0.0`）和 Phase 5 `1.0.0` artifacts。文档 claim profile 使用 claims `1.2.0`、审计 report `1.2.0`、overlay `1.1.0`；对应的旧 claim profile 与其输出版本也必须成对。版本号相同不意味着快照相同；仍需精确的 manifest、revision 和摘要绑定。

这些确定性投影有助于发现先修顺序、来源缺口和课程结构；它们不证明路径适合每位初学者，不替代逐文件教学计划、源码解释、习题完整答案或人的教学复核。Phase 4/5 候选、未知文件和 `PARTIAL` 必须继续留在质量报告里；ID、哈希、关系边和结构通过不是语义 PASS。

## 日常决策

1. 已有 Phase 2–5 输入先用对应 validator/CLI 检查其 project identity、revision、版本和 digest；输入有效则复用。
2. 新扫描或变更了目标范围时，从 Phase 2 开始补齐缺失输入；仅当确需相应产物时再跑 3–5。
3. 如果工具返回 `PARTIAL`，阅读逐文件跳过与未知项，保持状态并只补影响当前教学问题的缺口；不要通过改 status、删行或伪造证据来“修复”。
4. 要把证据变成教程时回到[源码主题写作卡](phase6-source-topic-writing-card.md)与读者手册契约。工具提供来源与覆盖线索，不限制正文长度、学习范围或讲解深度。

本 Skill 包内的 `product-quality-gates.md` 与 `product-acceptance-criteria.md` 是正式根文件的完整镜像。正式门槛仍以其镜像所注明的仓库根文件为权威；不要因工具结构或静态分析结果而降低正式要求。
