# 兼容工作流与高级工具

本指南收纳低频、可选的既有工作流。默认的自然语言模式不要求先读完这里。只有用户明确要求继续旧批次、编答案册、管理课程进度、组装全书、运行 DEEP 验证，或执行相应 Phase 任务时，才读取匹配小节。CLI 名称、历史阶段名和自然语言模式不是同一层概念。

## 明确区分三类内容

- **当前有入口的工具**：本包中真实存在的 CLI / 脚本；用对应脚本的 `--help` 确认参数，按其输入和状态限制运行。
- **旧教学兼容流程**：17 节批次、旧模板和质量脚本继续保留，用于显式续写现有批次或用户明确要求该格式；普通学习与新章节不机械套 17 节标题。
- **设计说明与验收记录**：说明历史决策或某次有界结果，不是需要按时间顺序重跑的运行步骤，也不自动证明当前输入或全产品已通过。

阅读任何工具产物时区分格式校验、来源字节证明、静态分析、真实运行证据和人的语义教学审查。hash、ID、coverage range、schema、日志或历史接受状态都不能单独证明意思正确、全覆盖、真实执行或正式发布通过。

## 旧 V2 意图到当前入口的兼容路由

下面保留旧 V2 契约中的六类用户意图，但把它们映射到当前自然语言模式；它们不是 CLI 名称，也不要求新主题采用旧册结构。收到“继续 / next”时，先检查用户给出的项目状态、最近 checkpoint 和当前对话，再从已有位置接续；只有用户明确要求旧 17 节批次时，才进入下一节的兼容流程。

| 旧意图表达 | 当前入口 | 如何处理 |
|---|---|---|
| 学习 / 深度拆解 / 开始 | `learn-project`；已经选好主题时用 `project-study` | 新项目先建立业务和架构背景，再选有界主题；已有学习材料时沿当前主题深入。 |
| 继续 / next | 续接当前入口；有 Git diff 更新时用 `project-update` | 先复用已有地图、材料和 checkpoint，不重开已完成工作；旧 17 节批次只在明确请求或项目状态指向该批次时恢复。 |
| 实验 / 验证机制 | `project-study`；需要核验门禁时用 `project-audit` | 教机制、预测和验证路径；实际运行只按用户授权及项目需要进行，静态证据不冒充运行结果。 |
| 架构挑战 / 重构 / 迁移 | `project-extend`；需要教授开发闭环时用 `project-vibecode` | 从需求和证据提出有界方案；实现由用户请求和确认的范围决定。 |
| Issue / 功能 / 修复 / 让 AI 改代码 | `project-extend`；完整训练时用 `project-vibecode` | 把 Issue 转成需求、设计、切片和验证；不把入口名称当作自动改代码授权。 |
| 验收 / 检查完整性 | `project-audit` | 对照实际适用门槛，逐项报告证据、未知和未运行项；不把文档存在或工具退出码写成整项目 PASS。 |

## 显式发布与已发布行回修

正式要求使用旧批次发布器时，`scripts/publish_batch.py` 的 `update_line` 操作用于回修已有派生记录的一整行；它是首次发布操作之外的第五种操作，不是新增内容的替代路径。必填字段、幂等判断、冲突处理和写入边界以[闸门与工具手册](../spec/操作手册-闸门与工具.md)为准。只在用户要求相应正式生命周期且输入满足前提时使用。

## 继续既有 17 节教学批次

只在项目状态明确指向某个旧批次，或用户明确要求完整 17 节批次格式时恢复。先读被学习项目的学习状态、批次清单和变更，复用有效的来源材料；新项目的一般学习优先使用读者手册路线。

旧项目若仍记录了多册授权，可用 `python scripts/study_scope.py --request <用户原话> [--state <已有状态.json>]` 解析旧的册范围。此工具只解决旧册授权兼容，不是十种新模式的命令，也不能从历史文字推断当前用户授权。

17 节教学职责的完整原文与顺序保存在[批次讲解全文模板](批次讲解全文模板.md)。用它保留原有全覆盖职责和适用质量要求，不代表每种新学习模式都要使用同样的 17 个标题或按批次排课。批次中真实源码仍需完整教学：在源码前交代业务场景与输入，在原位呈现并讲解，之后回放值变化、分支、返回、上下游和失败；按文件复杂度设计专属完整行表，讲清怎么用、上下游、怎么接和扩展；提供图、可执行的解释和完整练习答案。

现存脚本包括：

| 步骤 | 当前脚本 | 作用 |
|---|---|---|
| 建批 / 清单 / 计时 | `new_batch.py`、`batch_manifest.py`、`batch_trace.py` | 建批次结构、登记范围和来源、记录实际计时。 |
| 源码片段与构建 | `inject_source.py`、`batch_build.py`、`assemble_batch.py` | 按源码定位槽注入真实片段并从计划重建章节，避免手改生成物造成漂移。 |
| 预检 / 门禁 / 复核 | `batch_preflight.py`、`gate_lecture.py`、`gate_all.py`、`lecture_checks.py` | 在写入或发布前检查批次结构、逐文件覆盖和具体质量规则；查看各脚本 `--help` 选择实际入口。 |
| 发布 / 同步 | `publish_batch.py`、`sync_gate_result.py` | 按绑定的真实门禁结果和清单更新批次状态，或发布被门禁精确绑定的内容。 |

具体每批操作见包内[第一册执行协议](第一册执行协议.md)、[质量细则](第一册质量细则.md)和相关模板；不要为了新路线重建这些已有工具，也不要绕过发布脚本或把候选当发布结果。依据实际操作记录汇总真实耗时，未测内容明确标为未实测。

## Phase 6 读者章、源码呈现和选用工具

新学习的主题范围和每个相关文件的教学职责从[源码主题写作卡](phase6-source-topic-writing-card.md)开始；完整的读者手册结构与发布边界见[读者手册契约](phase6-reader-handbook-contract.md)。按主题和业务流程连续组织正文，不把 curriculum unit、源码文件批次或内部 Phase 当作读者导航。正文使用一套语义中文目录；机器证据、审计收据、hash 和内部 ID 单独保存。

读者章工具按明确工作选择，不是每次都要全部执行：

| 工作 | 当前入口 | 关键边界 |
|---|---|---|
| 从已认证输入准备、复核输入并精确发布读者草稿 | `reader_handbook_lifecycle.py prepare`、`review-input`、`publish` | `publish` 只写 reviewer attestation 绑定的精确字节；必须先有完整正文、答案和分开的来源/教学复核。格式/哈希不能代替完整章独立阅读、正常与失败路径回放或 G11/G12/G13 接受。 |
| 组织作者输入 / 源码材料 | `reader_handbook_workflow.py`、`reader_theme_sources.py` | 检查各自 `--help`；只补当前主题缺少的文件覆盖和证据。冻结 source pack 不能替代当轮所需的 authenticated inputs。 |
| 将已认证的 B1 复核发现整理成 B2a 写作交接 | `chapter_repair_triage.py`（运行前查看 `--help`） | 需要对应认证包和章节/复核目录；产出分流记录与写作者交接，不会修改章节，也不把 teaching-only 修订提升为来源通过。 |
| 将源码槽渲染进完整草稿 | `reader_source_blocks.py` | 依作者 plan 从固定缓存生成源码块和 provenance；不写出整章，不独立认证来源或教学质量。 |
| 拼合本地模板 / 答案部分 | `reader_manuscript_authoring.py` | 纯本地、可复现辅助渲染；其 `LOCAL_RENDERED` / `NOT_AUTHENTICATED` / `NOT_REVIEWED` 结果不是可发布审核。详见[源码材料化说明](phase6-reader-source-materialization.md)。 |
| 对照正文的源码行解释范围 | `reader_source_explanation_coverage.py` | 只给数值范围线索；不判断语义是否解释、学生是否理解，也不限制 prose 长度、学习范围或行表粒度。 |
| 导出已有发布草稿 | `reader_handbook_export.py` | 只按该工具的显式输入和格式执行，不将文件导出视为正式 Handbook。 |

如某脚本没有所需子命令或帮助项，不要推断一个类似命令存在。真实的生成、独立审查、精确字节发布和正式验收是不同活动；正式发布只在用户实际要求发布时启动，并满足适用发布 gate。

## Phase 6D 显式 opt-in 扩展

这些脚本是已经存在的可选工作流，并不会因为 `project-book` 或 `learn-project` 就自动运行。通常先通过 `python scripts/<script>.py --help` 阅读所选命令的身份、必需输入和输出护栏。

| 用户实际要求 | 脚本入口 | 限定用途 |
|---|---|---|
| 为已复核的单章构建答案册 | `answer_book_workflow.py prepare` / `build` | 输入绑定既有章与答案候选；候选答案仍是草稿，事实与初学者复核状态按工具实际结果保留。 |
| 对既有答案册做独立复核 | `answer_review_workflow.py prepare` / `finalize` | 输出 review sidecar；不改写或提升原答案包。 |
| 为 Phase 5 的一个通用概念单元写独立先修课 | `general_unit_workflow.py prepare` / `build` | 是 teaching-only 产物；不写目标项目行为声明，不自动并入项目章。 |
| 复核通用单元事实和初学者教学 | `general_learning_review_workflow.py prepare` / `finalize` | 重建绑定输入并产只读复核 sidecar；由分开的 reviewer 提交意见。 |
| 开始、推进、续接或有限修复 curriculum run | `curriculum_run_workflow.py`、`curriculum_run_execution_workflow.py start|advance|resume|repair` | 以同一认证计划和 append-only state 为边界。`advance --max-actions N` 是已有的有界批处理选项；不会调用模型、自动修复或把 review draft 提升为 PASS。 |
| 从显式选择的 attempts 组装全书草稿 | `whole_book_workflow.py assemble` | 必须显式选择 unit/attempt 并满足先修闭包；保留缺章、答案和状态缺口，不独立重复语义审查。 |
| 检查已组装书的锚点和结构一致性 | `whole_book_audit_workflow.py audit` | 输出固定代码的结构报告，不审核解释意思、外链内容或全书事实语义。 |

这些流程不替代源头作者输入和独立评审。除非该阶段用户目标确实要求，否则不生成第二册/第三册、不创建全书、不追补历史 acceptance logs，也不以旧记录声明当前教材已通过。

## Phase 7–12 的按需路线

每个模式有自己的任务说明与作者提示，先读相应契约，再检查工具是否适用于本次需求：

| 当前意图 | 包内材料 | 实际工具与边界 |
|---|---|---|
| 编写 beginner VibeCoding lab | [Phase 7 合同](phase7-vibecoding-contract.md)、[初学者 lab playbook](phase7-beginner-lab-playbook.md)、`../prompts/phase7-beginner-lab-writer.md` | `project_vibecode.py` 只编译明确输入的 plan/index/evidence；目标代码、示例 commands 不执行，编译不是实施或教学验收。 |
| 项目扩展与面试训练 | [扩展指南](project-extension-guide.md)、[面试指南](project-interview-guide.md)、`../prompts/project-extension-coach.md`、`../prompts/project-interview-coach.md` | 以现有地图、源码和学习上下文递进，不将通用问题冒充项目事实。 |
| 质量审计 | [质量审核指南](project-quality-audit-guide.md)、`../prompts/project-quality-auditor.md`、本包 gates / acceptance 镜像 | 输出证据范围、未覆盖项和独立教学缺口；不得将未执行检查记为 PASS。 |
| FAST / DEEP 项目运行验证 | [运行时指南](project-runtime-guide.md) | FAST 不执行目标命令；只有用户明确选择 DEEP 且完成安全步骤后才运行实际目标命令，记录真实日志和来源证据。 |
| 两项目代表性 dogfood | [dogfood 指南](project-dogfood-guide.md) | 复用已有效证据、只验证代表输入；不是整书发布要求。 |
| 根据实际 git diff 增量更新 | [更新指南](project-update-guide.md)、`../prompts/project-update-coach.md` | 从 diff 定位受影响事实/主题；保留仍有效证据，恢复失效的绑定，不无差别重跑所有 Phase。 |

上述 Phase 路由表示相应的指导文档和按需入口，不表示 7–12 每阶段都已完成正式验收。项目源文件的用途和安全范围仍以工具 `--help`、versioned schemas 及本包正式门槛为准。

## 包内权威与复用

[Product Quality Gates](product-quality-gates.md) 和 [Acceptance Criteria](product-acceptance-criteria.md) 是仓库根文件的完整打包镜像；根文件保持权威，改变正式文件时必须同步镜像。使用 Skill 不要求访问仓库外计划、报告或历史验收日志。

旧模板、提示、schemas 和脚本都继续留在此包中。查找其他兼容资料时使用[参考目录](README.md)；只有追溯历史设计时才回仓库档案，不把历史叙述作为必经 runtime 步骤。
