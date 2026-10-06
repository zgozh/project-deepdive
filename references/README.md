# Skill references

本目录按当前 10 个自然语言模式提供按需路由，同时保留旧批次、模板和质量契约。一般使用只打开当前任务对应的 guide；不要把参考目录当成必须从头读完的流程。

开始使用时，先看包内[完整使用案例](../docs/使用案例.md)：其中包含首次项目学习、VibeCoding 多轮练习、可复制对话、人工核对、恢复步骤与工具限制。该入口随 Skill 一起复制，不依赖仓库根目录计划或外部 helper。

| 当前意图 | 先读 | 对应入口 / 输出方向 |
|---|---|---|
| `learn-project`：从零开始理解项目 | [基础事实指南](project-foundation-guide.md)、[读者手册契约](phase6-reader-handbook-contract.md)、[源码主题写作卡](phase6-source-topic-writing-card.md) | 建项目/业务/架构学习路线并开始一个有界主题；需要确定性项目事实时只补相应 Phase 2–5 工具。 |
| `project-scan`：盘点仓库和覆盖范围 | [基础事实指南](project-foundation-guide.md)、[覆盖策略](coverage-policy.md) | `scan_repository.py` 生成索引与覆盖，`validate_coverage.py` 审计；保留 UNKNOWN/PARTIAL。 |
| `project-map`：查看结构、业务关系和先修 | [基础事实指南](project-foundation-guide.md) | 按需复用 Phase 3 evidence，构建 Phase 4 source-backed graph 与 Phase 5 课程草案。 |
| `project-book`：创作或整理读者章节 | [直接源码教学指南](direct-source-teaching-guide.md)、[读者手册契约](phase6-reader-handbook-contract.md)、[源码主题写作卡](phase6-source-topic-writing-card.md)；用户要求正式生命周期时再读[读者素材化说明](phase6-reader-source-materialization.md)和[高级工作流](legacy-and-advanced-workflows.md) | 先按请求和真实输入选路：常规学习稿、无适配器、提取不全或缺少认证输入走直接源码路径，并分别做来源复核和教学复核；只有用户要求绑定/发布且正式输入满足前提时才走认证生命周期。 |
| `project-study`：交互式逐步学习主题 | [源码主题写作卡](phase6-source-topic-writing-card.md)、[学习者 AI 协作卡](learner-ai-collaboration-card.md)、需要形成独立复核章节时的[直接源码教学指南](direct-source-teaching-guide.md) | 以对话讲解、图、值回放和完整练习答案推进；工具 coverage 只作证据，不限讲解深度。 |
| `project-interview`：项目背景面试练习 | [面试指南](project-interview-guide.md)、[`project-interview-coach`](../prompts/project-interview-coach.md) | 生成项目事实绑定的问题、追问和答题反馈。 |
| `project-extend`：项目功能扩展 | [扩展指南](project-extension-guide.md)、[`project-extension-coach`](../prompts/project-extension-coach.md) | 从需求、架构和全链路分析形成有证据的扩展提案或实施切片。 |
| `project-vibecode`：AI 辅助开发训练 | [Phase 7 合同](phase7-vibecoding-contract.md)、[初学者 lab playbook](phase7-beginner-lab-playbook.md)、[`project-vibecode writer`](../prompts/phase7-beginner-lab-writer.md) | 以真实任务教完整 VibeCoding 流程；`project_vibecode.py` 是实际的静态 plan 编译器。 |
| `project-audit`：质量与验收审计 | [质量审核指南](project-quality-audit-guide.md)、[`quality auditor`](../prompts/project-quality-auditor.md)、[门禁镜像](product-quality-gates.md)、[AC 镜像](product-acceptance-criteria.md) | 输出事实、证据、未覆盖范围和教学缺口；只有真实执行并满足门槛才记录相应 PASS。 |
| `project-update`：基于 git diff 更新学习材料 | [增量更新指南](project-update-guide.md)、[`project-update coach`](../prompts/project-update-coach.md) | 追踪受影响事实/主题，保留仍有效证据，定向修订并标明未验证项。 |

旧 V2 工作流的“学习 / 继续 / 实验 / 架构挑战 / Issue / 验收”六类意图，按当前入口继续使用；新请求不用先读旧协议。查看[旧意图到当前模式的兼容路由](legacy-and-advanced-workflows.md)。

模式名表达用户意图，不是 CLI 命令。产品能力保持 provider-neutral；实际脚本、输入、输出和版本边界见各自 guide 的帮助与 schemas。正式 gates/criteria 完整复制在包内，根规范是权威，改动时须同步镜像。

仅因为认证工具可用，不自动运行 compilation、reader lifecycle 或目标项目测试。没有正式生命周期输入时，仍可依据[直接源码教学指南](direct-source-teaching-guide.md)完成诚实的人审学习稿；不得伪造 receipt 或称其已正式发布。

## 旧批次样例与模板地图（兼容项）

以下表格服务于明确续写旧 17 节批次、维护旧模板或追溯旧判据。新建的一般学习不需要先读完整旧批次资料，也不机械复用旧标题。

## 一、样例地图（写某一节 → 先读哪份）★

| 文件 | 覆盖维度 | 何时读 | 不可照抄 |
|---|---|---|---|
| `批次讲解全文模板.md` | **节序与骨架的唯一权威**（17 节 + 各节空骨架） | **由 `scripts/new_batch.py` 读取**（开批时抽骨架）；日常**不要求模型全文读取** | `>` 指导语是写给你看的，不是批次内容 |
| `十七节黄金样例-节选.md` | **形态锚**：② 流水线图 / ⑤ 双清单 / ⑥ 问题开场+要点表+★回放 / ⑭ 完整答案 | **开批一次**，只取与本批语言/难点匹配的短节；闸门报 FAIL 时按规则 ID 补读 | ⑫ 的叙事视角（旧写法）、节标题措辞 |
| `六节九子块样例（ragent批次3节选）.md` | **⑥ 九子块三段式**（用户钦定基准 = ragent 阶段1批次3）：问题开场→白话开场→构造方式与手法→代码→逐行要点表→边界与副作用→【讲解】→【怎么用】→【上下游】→【怎么接】 + ②③④⑤ 排版实物 | 开批一次；⑥ 形态被闸门 ⓪d/⓪f 点名时对照 | 代码与行号（批次3 的 Java 事实）；节选 ≠ 全批达标线 |
| `结构密度样例.md` | **结构密度**：⑦ 五小节 / ④ 深潜 / ⑫ 单条厚度 / ⑩ ❌✅ 对照块 / ③ 教学片段 / 排版六条 / ⑫ 多轮提示词块 | 开批一次；⑦④⑫⑩③ 被 ⓪e 点名时对照 | 代码与行号（属批次1 的源码事实） |
| `十七节黄金样例-py（deer-flow批次1-3节选）.md` | 非 Java 项目对照（Python / LangGraph） | 写 py 项目批次时为主参照 | Java 项目的具体写法 |
| `黄金样例-索引.md` | canonical 定义 + 各参照物形态状态 + **维护规则**（判据升级后要复检） | 想确认"哪份能照抄/哪份是旧形态"时 | — |
| `零件类讲解模板.md` | 零件类件（DTO / 枚举 / 常量）的讲法 | 讲零件件时 | 机制类件的深度要求 |
| `业务闭环定位图模板.md` | ② 场景开场 + 流水线图骨架 | 写 ② 时 | — |
| `系统全景地图模板.md` | ① 全景图与跨批关系 | 写 ① 时 | — |
| `类清单表模板.md` | ③ 分组三表 | 写 ③ 时 | — |
| `AGENTS.md-模板.md` / `AGENTS.md-复刻增量模板.md` | 宿主项目的约定文件 | 项目开局 / 增量批时 | — |
| `第一册执行协议.md` / `第一册质量卡.md` | 默认第一册的开批、续传、组装和终检 | 第一册每批按需查 | — |
| `V2执行协议.md` / `通用工程约定.md` / `三本书与工程日志.md` | 扩展册与工程任务约定 | 用户明确请求相应任务时读 | — |

## 二、其余模板索引（按触发时机）

| 模板 | 用途 | 触发时机 |
|---|---|---|
| `实验卡模板.md` | 第二册实验记录 | 设计 / 执行实验时 |
| `证据卡模板.md` | 证据分级登记（事实-源码 / 实测 / 推断 / 外部事实） | 需要落证据时 |
| `预测卡模板.md` | 架构挑战的预测与验证 | 第三册挑战 |
| `架构挑战模板.md` | 挑战任务书 | 第三册 |
| `工程Issue模板.md` | Issue 实战 | 阶段 G |
| `Debug记录模板.md` | 调试记录 | 遇到 bug 时 |
| `Diff审查模板.md` | AI 产出 diff 审查 | 每轮 AI 协作后 |
| `AI失误样本模板.md` | AI 失误归档 | 发现 AI 错误时 |
| `上下文包模板.md` | 给 AI 的上下文包 | 大任务委派时 |
| `概念词典模板.md` / `能力账本模板.md` | 宿主项目活文档 | 每批回填时 |

## 三、spec 与 examples 的分工（改文档前先看这张表）

| 位置 | 是什么 | 谁消费 | 改判据时要同步吗 |
|---|---|---|---|
| `spec/00-质量契约.json`（492 行） | 旧 17 节批次质量判据的**机器可读兼容副本**（A1… / S22… 条款）；不是根目录产品 Quality Gates 的替代物 | `gate_all.py` / `skill_selfcheck.py` | 改旧批次判据时要同步旧工具与该契约 |
| `spec/V2质量契约.json` | 旧 V2 执行流程的**精简兼容契约**（历史意图路由与默认册范围） | `V2执行协议.md` 与兼容工具 | 仅改旧 V2 工作流时同步；不要求普通模式使用 |
| `spec/操作手册-闸门与工具.md` | 每个脚本的命令行手册 | 人 | 加 / 改脚本时 |
| `spec/血证档案.md` | 事故档案（判据的"为什么这么定"） | 人 | 新增判据时附血证 |
| `spec/阶段工作流.md` | 七阶段工作流 | 人 | 工作流变更时 |
| `spec/散文符号白名单.txt` | ⑥ 散文引用判据的白名单 | `gate_lecture.py` | 遇误报时 |
| `docs/判据变更史.md` | **2.1–2.25 的历史登记全表** | 只在追溯旧 17 节判据演进时查阅 | 后续旧判据变更追加；当前产品 gates 以正式镜像为准 |
| `docs/复刻式学习方法-通用.md` / `安装与执行边界.md` | 方法论 / 边界 | 人 | — |
| `docs/archive/V2试运行手册.md`（已归档） | V2 切换时的试运行记录 | 人 | — |
| `examples/黄金样例.md` | 六段时代的深度基准（**旧形态，7 节**，非 17 节） | 人（只借深度定性） | 不改 |
| `examples/黄金样例-python.md` | 非 Java 对照（旧六段） | 人 | 不改 |
| `examples/archive/V2工程任务样例.md`（已归档） | 工程任务（Issue / Diff）结构样例 | 人 | 不改 |
