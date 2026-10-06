# Phase 9：项目质量审计指南

本指南供 Project DeepDive Skill 在用户要求审阅生成物、检查质量门或准备交付时使用。它把现有确定性工具、现有质量报告契约和独立语义阅读组合起来；不增加审计引擎、CLI、SDK 或状态机。

本步骤适合初学者跟做。每一步都要在成稿里填入目标项目实际的页面、目录、文件和命令。首次出现的术语先用白话解释；命令要说明在哪个终端、哪个目录运行、参数表示什么。只检查时明确“不创建文件”；输出报告时指出真实写入目录与文件名。

先解释三个会反复出现的词：gate 是带有判定条件的质量检查点；artifact 是 Skill 已经生成、可在目录中打开的文件；schema 是描述这些文件必需字段和允许值的格式规则。API 是可供现有程序调用的代码入口，CLI 才是终端里可以输入的命令。

## 1. 先把审计范围说清楚

1. 在当前 Skill 对话或审计表头写明目标仓库的绝对根目录、目标 Git revision、快照类型（git-tree 或 worktree）、本次审计范围、FAST 或 DEEP 模式、输出目录。解释 revision 是代码版本标识，快照说明检查提交内容还是当前工作目录。
2. 列出本次实际可用的 project index、coverage、analysis、graph、prerequisite graph、curriculum、handbook/manuscript、source manifest、evidence store 和此前真实命令记录。逐项写文件路径与其声明的 revision；缺失项写未知或未提供，不从文件名猜内容。
3. 用目标仓库的现成 revision/snapshot 工具核对这些输入互相指向同一版本。若审计的是未提交 worktree，另记当前 HEAD 与脏工作区状态；不要只用 HEAD 冒充工作树内容的 revision。
4. 阅读 Skill 随附的[产品质量门镜像](product-quality-gates.md)中 G00–G21 的定义，判定本次哪些 gate 适用、哪些被模式或项目结构触发、哪些尚未执行。报告不得悄悄删掉正式 gate；不适用或未执行的原因写入该 gate 的 measurements 或 warnings。
5. 每个 gate 的记录应可复核：measurement 中指向输入证据、检查范围、真实命令记录（如有）、退出码（如有）和输出文件路径。schema 没有这些专用属性时，把必要信息放在既有 measurements 对象；不要扩展 schema。

打开产品质量门镜像后，先找到本次实际涉及的 gate ID 和原始通过条件，再比较证据与该条件；不要自行换百分比或放宽门槛。若检查由人或模型完成，在 measurement 中写审阅者实际读过的文件和范围、判断依据与未覆盖内容。

如果本次只有教材抽查或局部修复，明确它是范围内结论。局部 PASS 不表示所有 gate 通过，也不表示[产品验收镜像中的 AC-25](product-acceptance-criteria.md)发布条件已满足。

Skill 被安装到目标仓库之外时，审计应读取本 Skill references 中的质量门和验收标准镜像。维护 Project DeepDive 仓库时，修改正式根文档后同步对应镜像，并核对两者一致；运行时不要假定目标仓库根目录含有 Project DeepDive 的原始规格文件。

## 2. 先复用能确定的检查

先解释每个工具证明什么，再在目标项目实际存在相应输入时使用它。工具输出是确定性证据，不替代语义判断。

- artifact contract 的 Python 模块公开 **validate_artifact(data)**、**load_artifact(path)** 和 **dumps_artifact(data)**，供已有 Python 流程使用。给学习者验证实际 JSON 时，使用现有 **validate_artifact.py** CLI：把终端 cwd 切到本 Skill 根目录（含 scripts 与 schemas 的 project-deepdive 目录），再运行 **python scripts/validate_artifact.py <真实JSON路径>**。路径可以是目标项目输出目录中的绝对路径。CLI 接受一个或多个 UTF-8 JSON 路径，实际校验时逐项显示 PASS/FAIL，至少一个失败时以退出码 1 结束；单独运行 **--help** 只显示用法，不算执行了验证。
- quality report 使用现有 [v1 schema](../schemas/v1/quality-report.schema.json)：artifact_kind 为 quality-report，schema_version 为 1.0.0；必填字段包括 repository_revision、UTC generated_at、mode、status、gates、critical_gaps 和 warnings。gates 是数组，每项必须含 id、result、measurements、failures、warnings、evidence_ids、recommended_repair。status 与 gate result 只允许 PASS、PASS_WITH_WARNINGS、PARTIAL、FAIL、NOT_RUN；mode 只允许 FAST、DEEP。禁止新增字段，禁止写 PENDING 或 N_A。
- evidence v1 的 E3 条目应实际对应已观察的运行，kind 为 runtime，level 为 E3。现有 locator 可记录 path、command、observation；把 cwd、输入、时间、退出码、脱敏日志位置等放入该 path 指向的运行记录，不改 schema。evidence_ids 只能引用 evidence store 中已存在的 ID；没有真实 ID 时使用空数组。
- 证据等级用白话解释后再写入教材：E0 是用户提供的信息，E1 是源码，E2 是配置/依赖/构建/测试等文件，E3 是真实运行观察，E4 是官方资料，E5 是项目历史或 issue，E6 是模型推论。E6 不当作已验证的项目事实。
- **coverage_audit.audit_coverage(...)** 是 Python 函数，用于比对 index、coverage、revision、路径集合和目标快照；该模块没有独立 CLI。若已有 phase2/后续流程调用了它，复用其实际结果和输出位置。
- **audit_phase3.py --help** 显示它可对显式的 Phase 2、Phase 3A 和可选语言分析 bundle 做只读审计。只有输入 bundle、目标 root 和 revision 匹配时才使用。
- **audit_claim_evidence.py --help** 与 **audit_claim_evidence_4c.py --help** 展示各自版本的 claim/source evidence 审计参数。按 claims 与 package 的真实 schema 版本选用，不要混配。
- **whole_book_workflow.py --help** 提供 whole-book assemble 入口；**whole_book_audit_workflow.py --help** 提供已有 assembly 的只读 audit 入口。只对实际存在的 Phase 6 产物调用；先读取对应 **--help** 子命令确认参数。
- **reader_manuscript_authoring.py --help** 说明如何从 lesson/answers 模板、冻结 source cache 与输出目录本地渲染稿件。它不能证明正文教学正确。
- 最终正文包含 lesson 与完整答案时，**reader_source_explanation_coverage.py --help** 的 **--assembled-chapter** 会一并检查 source manifest 绑定的源码块与行号表。它只统计行号覆盖，不认证来源，也不判断解释是否正确。

source manifest 是列出正文中哪些源码片段来自目标文件及其范围的清单；读者应先打开清单指向的正文和冻结源码，再看覆盖报告。若最终稿和 answers 是分开的，只有在调用参数明确把完整章交给工具时，才可把结果称作整章覆盖。

仅使用目标项目已生成且 revision 匹配的材料。缺少输入时窄补证；不为审计重扫整个仓库，不把计划中的报告说成已经生成。FAST 使用静态和现成配置/测试资料，不运行目标项目；DEEP 的运行行为按 [Phase 10 指南](project-runtime-guide.md) 自愿且有界地处理。

## 3. 独立审阅教材与项目事实

由当前审计 Skill 在独立审阅上下文中阅读实际稿件和答案。作者的自评、工具结构检查、文件存在、hash、报告计数、多人投票都不能替代这次判断。输入不够时保留未知，不让模型补造源码或调用链。

对本次范围内的核心教材逐项判断：

1. 学习者第一次遇到术语前，是否先有白话直觉和所需先修概念；命令是否解释用途、位置和参数。
2. 每个项目源码块前是否交代业务场景、架构位置、输入和调用者；块中是否解释关键语句、变量、分支、返回、异常与交接；块后是否用同一输入回放结果、后续步骤和有依据的失败路径。
3. 项目事实是否能回到目标 revision 的 E0–E5 证据；E6 是否清楚标成推论；文档声明、静态推论、实测行为有没有混写。
4. 初学者能否按完整解释复现判断；练习、完整答案与所教代码推理是否一致；扩展点、面试追问和 frontend→backend→result 路径是否由真实项目事实支持。
5. 对整个最终装配章验收时，阅读整章和每份完整答案，并实际回放一条正常路径及一条有意义的失败路径。只接受样章或摘要时，把其余范围写入 uncovered scope，不能给整章 PASS。

若作者通过[直接源码教学指南](direct-source-teaching-guide.md)写稿，分别核对人工 source review 与独立 teaching review 及其绑定正文 hash。缺少适配器或正式输入本身不证明稿件教得差，也不证明正式 reader receipt 存在；在 summary 如实记录人工复核和未签发的正式状态。只有用户要求正式生命周期/发布且输入满足前置时才运行对应流程，不为普通学习请求自动编译、执行生命周期或运行目标代码测试。

整章最终接受时，为确切的装配文件记录 SHA-256，并把该值、完整阅读/回放范围、未覆盖部分、学习缺口和未知项目行为写入人读 summary 或 gate measurements。任何正文或答案变化都会改变审阅对象，不能沿用旧 hash 的语义接受。发生实质教学修复后重审受影响内容；最终接受前仍须阅读和回放完整装配章。

SHA-256 是根据文件字节计算的摘要，用来区分确切的稿件版本。按现有受支持工具计算并读回它；不要把缓存源码的 hash 当成最终整章的 hash，也不要把数值摘要说成语义通过。

源码认证与教学质量是不同检查。Phase 6 source materialization 流程验证冻结源码字节和 revision 绑定；**reader_source_explanation_coverage.py** 核对最终正文中的来源块与行号覆盖。行数完整不证明逐行解释正确，hash 正确不证明事实或教学成立。语义判断要单独记录其审阅范围、发现、未覆盖内容和未知行为。

最终教材审阅沿用 [源码主题写作卡](phase6-source-topic-writing-card.md)、[读者稿 source materialization 说明](phase6-reader-source-materialization.md) 和 [Phase 7 初学者 playbook](phase7-beginner-lab-playbook.md)；VibeCoding 交互沿用 [学习者 AI 协作卡](learner-ai-collaboration-card.md)。不要再造一套课程骨架。

## 4. 写质量报告并决定状态

在目标项目已有的报告输出目录生成 quality-report.json，旁边写一份简短人读 summary。报告记录当前目标 revision、实际模式与生成时间；将 gate 证据和本次范围写入已有字段。当前任务只审计 Skill 文档或教材样本时，在 summary 明说输出是局部审阅记录，不能冠名完整产品验收。

打开输出目录时，让学习者确认这两个文件实际落在目标目录中。summary 用普通语言解释状态、主要依据和限制；机器读取的 JSON 只放 schema 允许的字段。时间使用 UTC 的 YYYY-MM-DDTHH:MM:SSZ 形式。

状态按缺口优先级决定，而不是按绿色计数投票：

- 已确认的伪造来源、错误 revision、错误文件/符号、与证据矛盾的关键事实，或适用正式检查确实失败：FAIL，并给出可复核位置和最小修复。
- 关键证据、必需路径或实质教学内容缺失、未能审查，或重要适用 gate 未执行：PARTIAL；关键缺口进入 critical_gaps。
- 本次适用且有证据的检查通过，但有清晰标注的非关键限制或范围外 gate 未运行：PASS_WITH_WARNINGS，并逐项写明原因和当前结论边界。
- 只有本次明确范围内所有适用 gate 通过、没有关键缺口或警告时，才用 PASS。FAST 中 G19 写 NOT_RUN，并说明 Deep Mode runtime 未触发；这不构成 DEEP 成功。
- 根本未开展审计时用 NOT_RUN；不得以待办文字伪装成检查结果。

若某 gate 不适用于项目结构，在该 gate 的 measurements 记录 applicability 与判定依据，并以 NOT_RUN 表示未执行；不要创造 N/A 状态。每个非 PASS gate 都要写 recommended_repair；PASS 时该字段仍按 schema 保留为空字符串。没有证据 ID 就填空数组。报告的 schema 通过只证明字段形状正确，不会把 gate 变成 PASS。

修复只针对审计确认的问题。保留仍有效的 revision、schema、coverage、源码字节和命令证据；确认修复影响哪些旧证明，只重跑受影响检查。禁止为了刷绿数字删 gate、降低标准或掩盖失败。

结论写成读者能回查的发现：先给准确位置，再给证据和可见差异，然后说明它影响哪个 gate 或学习步骤。若现有材料不足以区分缺陷与未知，就写待补的最小证据和当前限制，不把猜测列为 bug。

人工/LLM 判断也要有明确覆盖边界。报告指出实际阅读者检查了哪个文件/小节、检查使用的代表性输入、观察到的分支和答案；没有走到的源码、失败路径或章节留在 uncovered scope。后续作者可从已确认范围继续，不需重扫已证明的文件。

## 5. 让实际学习者可以跟做

写给学习者的审计流程要沿用 [学习者 AI 协作卡](learner-ai-collaboration-card.md) 的初轮、人工判断、追问和修订方法，保持简短。每一步指出读者在哪个编辑器页面、文件、审计报告或终端进行；为什么现在做；要看什么真实路径/字段/参数；哪些文件已存在，是否产生新输出；读者怎样判断正确并在失败时恢复。

初轮 AI 提示必须附上真实 root/revision、适用 gate、具体 artifact 路径、当前完整材料与审计范围。要求 AI 对证据引用逐条指出依据，分开陈述静态事实、运行观察、推断、未知，并交付真实审计表和短 summary。人要打开对应文件或命令记录核实一项关键结论，再决定继续。缺源文件、revision 不同、schema 不通过或答案与正文冲突时，提示 AI 只修该问题并重审受影响范围。

不要在教材里放只有路径占位却声称可直接运行的命令。若 Skill 尚未读到目标文件或目标命令，就把所需输入准确列为缺失；实际命令必须在目标项目完成发现并核对后填入。对 documentation-only 编辑按 V0 做读回与 diff 检查；局部实现按 V1，跨模块契约按 V2；V3 只在正式交付、高风险或当前验收门明确要求时触发。
