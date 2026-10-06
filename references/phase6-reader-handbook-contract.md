# Phase 6 读者手册契约

## 两种不同的产物

canonical Phase 6 章节是机器可审计的输入。它的固定标记、章节标题、claim 标识、脚注定义、证据绑定、摘录摘要和状态 sidecar，可供现有工具检查结构和部分来源关系。通过这些检查，只能说明相应检查实际覆盖的内容；它不代表初学者能够从章节中学会知识、不代表解释完整，也不代表 G11、G12 或 G13 已通过。

读者手册是另一种面向初学者的中文教学作品。它可以采用已经复核的 canonical 章节、通用课程、答案册及明确接受的其他证据，但必须为真正学习项目的人重新编排和讲解。仅用 `File.Copy`、改文件名或拼接 canonical Markdown，不构成读者转写；必须实际改写或编辑正文，并单独复核读者教学深度。不能只因某个 canonical 章节、生成 packet、状态文件或 whole-book assembly 格式有效，或标有 `REVIEWED_DRAFT`，就把它当作完成的读者手册。

机器审计信息留在读者正文之外。claim/evidence ID、哈希、packet/state 标识、schema 字段和解析器标记写入单独的 provenance 记录。正文使用读者能理解的源码名、路径、行号范围和链接，便于读者定位；目录导航使用实际中文章名和学习路线，不把内部 Phase、attempt 或 audit 状态写成读者概念。代码中的标识符保留原拼写；教学标题和解释使用中文。

## 学习路线

每个学习单元必须有与其学习结果相关的实质 AI 协作，按[学习者 AI 协作卡](learner-ai-collaboration-card.md)给出具体材料/操作、完整初轮提示、明确模拟的提案、人的证据核验、可复制追问、完整修订与继续/恢复标准。复用已有练习、源码与答案，仅补确认缺口；不改变读序或要求每步新增文档。卡片不代替旧17职责、源码前中后、完整专属行表和章级独立审查；结构完整、模拟答案或工具检查不证明语义教学/真实执行通过。

按学习者理解问题的顺序组织，而非按源码文件批次或内部生产阶段：

1. 项目目的，以及帮助读者建立方向感的整体地图。
2. 用户遇到的问题与业务流程。
3. 架构：已知组件、边界和证据限制。
4. 为理解接下来的功能而按需补充的前置概念。
5. 一条连贯的功能路径；证据支持到哪里，就连接到哪里，例如界面、客户端、服务端、存储、异步处理和用户可见结果。
6. 框架与底层机制、失败边界、验证方式，以及有清晰边界的扩展练习。

根据读者一次能够理解的内容，合理拆成章节和小节。读者不应需要先懂内部 phase、attempt、packet、validator 或 review state 才能看懂学习路线。

整体地图之后，先核对目标项目实际采用的工程基础：构建与依赖、配置、程序入口与装配、执行环境；在进入依赖这些基础的功能深读前，先把必要机制讲清。本轮 Ragent 的 Java 项目会涉及 POM/依赖和 Spring 启动装配，但其他项目应按其真实技术栈和文件讲解。当前六章是本轮 upload-to-retrieval 初始资料链，不限定最终教材的总章数，也不代表工程基础或全项目覆盖已经完成；最终主题和章节数由书级计划结合冻结材料确定。

本册应从整个项目要解决的问题、用户业务和已有整体架构开篇，再逐步进入当前 Phase 6 初版实际覆盖的功能主题；一个 curriculum unit 是证据输入，不是读者章节。开篇建立的是全项目方向感，后文功能深度仍须按已授权范围和实际证据如实表述。书级目录的 `00` 导读应说明阅读顺序、本轮材料覆盖与后续待覆盖主题；它是导航，不算源码覆盖，也不替代后续课程。

每册的项目名称、证据版本/源码快照和统一读者教材根目录由本次书级计划填写，不是本契约对所有项目的固定默认值。本轮 Ragent 实例的计划目录为 `D:/学习教材/Ragent/项目深读教材/`；其他项目应使用各自书级计划中的目录。书级计划可在根目录下使用少量清晰且有语义的篇目分组（如工程基础、业务功能、机制与工程验证），也可将篇名作为顺序章节文件名前缀；不得为每个 curriculum unit 建杂乱目录，也不要求一单元一章。既有按序平铺的中文 Markdown 文件保持兼容，不强制迁移旧预览或覆盖旧文件。每章将练习与完整答案放在同一正文中；短代码例子和结果直接出现在正文，长演示作为纯 Markdown 附录。源码缓存、脚本、审查记录、provenance、manifest 和检查报告留在开发 artifact，不混入读者根目录。既有两份 Ragent 试读预览保留为历史样本，不自动成为新教材或验收依据。

## 教代码，而不只是告诉读者代码在哪

每个重要源码主题先交代具体业务场景、理解该片段所需的前置概念，以及一组明确标为假设的输入和预期结果。总览可以用少量源码例子定位关键职责，但不算完成核心源码深读。完整功能主题要展示并逐步解释能说明职责闭环的完整方法或连续完整职责段，以及理解它们所需的依赖；核心工程基础和核心源码深读保留旧 Skill 整类、整文件注入与详细展开的能力。长文件可按连续且完整的职责段组织；未教的方法和有效配置字段仍是未覆盖内容，几段关键摘录不能代替整份核心文件的教学。原文、带教学注释的版本和简化示例分开，并将解释指回相关源码行。接着用同一组假设输入逐步追踪变量、分支、赋值、输出或返回值及下一跳。不能用长代码摘录加一段摘要代替这个推演。

## 正文版式与源码定位

用清晰的 H2/H3 标题组织课程。首次定义关键概念，以及关键判断或结论时，可适量加粗；不要整段加粗，也不要把整行都加粗。代码符号、方法名和类名用行内代码标记，保留源码拼写。源码围栏里的字节保持原样，不翻译、不改写，也不加入 Markdown 强调标记。

不要在读者正文放 `[来源N]`、`EVID` 或其他机器引用占位符。把重要项目事实的源码路径与行号放在相关解释旁边；确认方法或类名确实有助于定位时，再一起写出，不能猜名称，也不能要求读者去末尾编号索引查找。在正式 authenticated/materialization 生命周期中，源码片段前使用 `reader_source_blocks.py` 根据已认证源码绑定生成的定位行：`> **源码位置**：` 后跟完整仓库相对路径和具体行号范围。直接读源码路径可依据实际固定的文件字节手工标出路径与行范围，并保留对应 SHA-256 和独立来源复核；这不生成 helper receipt，也不满足或替代正式生命周期的已认证输入门槛。两条路径都必须维持本契约的完整教学深度；直接读源码的实际步骤见[直接源码教学指南](direct-source-teaching-guide.md)。长路径在同一小节首次引用或代码片段前写全；后续可用文件名、已介绍且确认存在的方法名和具体行号减少重复。引用应靠近它支持的推演步骤，不用章首巨型索引或末尾编号清单代替就地定位。哈希和机器 ID 留在旁路 provenance。

如使用源码槽位，作者稿中把占位符单独放在正文一行，例如：

@@source:upload_entry@@

不要把该行包进 Markdown 代码围栏。回填 helper 会生成定位说明和源码围栏；源码由已绑定来源提供，作者不要手工重抄源码。

解释本主题实际用到的框架机制，并给出最小的非框架等价，说明与该场景有关的设计取舍。指出一条可定位到源码条件或语句的失败路径和相应验证方法，写清验证能覆盖的范围；练习答案要展示完整推理。全书开篇应给出整体项目地图，连续功能主题应呈现对应的数据或调用路径，并用源码逐段讲清图中交接；图无法渲染时仍要给读者可理解的文字路线。图表和表格只在帮助理解时使用。静态源码能确定的控制流和赋值应直接讲清；只对确实缺少证据的运行时装配、外部效果、持久化或用户可见结果说明未知，不要求每个调用后重复免责声明。

通用原理可以深入讲解。先定义读者学完本主题应能解释、推演或判断什么，再选择和组织 claims/evidence；source ledger 是证据导航索引，不是课程范围上限。已接受的 claim/evidence 是可复用的证据资产。重要机制缺少证据时，在已授权范围内针对该缺口窄补证据；无需因此重扫整个仓库。区分源码直接观察、已验证的运行时结论和作者解释；不能猜测填空，也不能用免责声明代替教学。

`1–3` 个学习结果和 few steps 只是建议的主题切分粒度，不限制核心机制深度。每个 source-rich 读者主题都必须在写作前使用[源码主题写作卡](phase6-source-topic-writing-card.md)：它把旧模板的 17 项职责变成作者覆盖骨架，并提供逐核心文件可填写的小节模板；它不是第二套 curriculum、canonical 稿、读者固定标题或自动语义评分器。blueprint 应在准备作者输入前列明每个核心文件的完整职责/待覆盖范围和适用教学职责，不能用少量 claims 代替这个写作计划；blueprint 限定获准目标，写作卡不授权无关扩题。读者正文可按学习顺序选择标题，不设 17 节、固定总章数或字数要求；canonical 自有固定标题，不控制读者正文。当前 Phase 6 初始教材不要求独立八股/面试课程或完整 VibeCoding 训练，⑨与完整⑫标为 `not_requested`，不得因此宣称全 17 项通过；本主题需要的⑧机制仍应讲解。最小非框架等价必须演示实际声称的机制，并解释组件职责、输入/输出、失败与资源边界；不可通过改名调用或省掉关键问题来冒充等价。必要机制应先于依赖它的源码。未教会的核心目标必须指向具体后续课程，并保持未完成状态。现有读者章生命周期能绑定精确字节、输入和报告，但不自动判断教学深度或是否完整讲完核心文件。

## 证据和复核记录

每份读者正文都应在正文之外记录：

- 所依据的 canonical 产物版本和源码快照；
- 复用的已复核证据及其精确绑定；
- 为本稿收集的补充证据和它支持的范围；
- 作者新加、需要独立复核的解释、示例和答案；
- 仍待完成的语义复核、工程基础覆盖和书级覆盖检查。

对未改变的源码和 claim 复用已有证据，避免重复完整审计。实质改写、新项目事实或新增答案不会自动继承旧 canonical 文本的批准，需独立复核。读者深度审查必须绑定实际读者正文的精确 SHA-256；正文任何修改都使该深度审查失效。canonical review 只能支持未变事实，不能继承为读者深度审查；已实现的读者章生命周期会绑定正文与报告字节，但不会自动执行语义判断。Principal 应拿一组明确输入实际推演代表性课程：读者能否据此解释关键变量和结果、框架机制及取舍、具体失败位置和验证方式，并用推理回答配套练习。记录实际审查范围，不把样本检查说成全项目覆盖。

目标项目没有可用适配器或认证输入时，按[直接源码教学指南](direct-source-teaching-guide.md)记录实际源码来源，并分别复核来源事实与整稿教学；这条人工路径遵守本契约，但不发放正式生命周期 receipt。

不得以字数、文件数、测试数或引用 claim 数衡量质量。如果课程只列出文件和类名，却没有教会读者解释行为、条件、边界及证据，就补充所缺的教学，而不是凑字数或只改课程标题。

## 可执行读者章生命周期

脚本 `scripts/reader_handbook_lifecycle.py` 提供显式的 `prepare`、`review-input` 和 `publish` CLI。三条命令都接受既有 curriculum run plan 及 Phase 6 authenticated inputs；`prepare` 还接受重复 `--unit`、blueprint、可选 book plan、writer session 和新输出目录。新 `prepare` 输出使用 prepared input 1.2：除了绑定 blueprint、可选路线与 writer prompt，还把本包的源码主题写作卡、读者手册契约和学习者 AI 协作卡逐字快照并附各自 SHA-256。writer packet 与 review input 都携带这三份全文，review 会校验快照与当前包内原文一致，并从同一快照重建 packet。1.0/1.1 的既有 attempt 仍按原有格式读取和验证。快照和哈希只绑定规范输入，不提升 `PREPARED` / `NOT_REVIEWED` 状态，也不证明正文质量。该工作流不调用模型或运行目标项目。每次独立 CLI 调用都会从输入重建真实认证上下文；同一批 API 调用可复用一个仍存活的上下文。序列化 review packet 不是可重用的认证上下文。仅调整书级阅读路线本身不要求重新认证所有源码；需要 live context 的新作者批次若没有存活上下文，应真实认证一次并在同一批处理中复用，不得把序列化计划或 review packet 当作认证。

作者在准备目录中提交 `lesson.md`、`answers.md` 和 `sources.json`。正文必须是非空严格 UTF-8、恰有一个代码围栏之外的 H1、围栏平衡，且围栏外不能残留 `@@source:...@@` 槽位；不设字数或固定标题数量门槛。答案的第一个非空标题必须是 H2，答案文件不含 H1。默认 `append` 原样保留 lesson 和答案字节，只在中间加入两个 LF 字节作为空行分隔；不另加标题，答案自己的 H2 是本节唯一标题。`embedded` 只在完整答案字节于 lesson 中精确出现一次时接受，并原样保留 lesson。改写答案或重复合并都不符合这两个模式。

`sources.json` 使用严格的 `reader-theme-sources` v1.0 清单格式；纯 shape preflight 与真正认证后的 review 共用同一验证函数。最小字段示例如下，尖括号中的 revision 与摘要占位符必须替换为书级冻结材料提供的准确值：

```json
{
  "artifact_kind": "reader-theme-sources",
  "version": "1.0",
  "source_revision": "<authenticated-plan-revision>",
  "source_blocks": [
    {
      "slot_id": "controller-entry",
      "path": "src/main/java/example/Controller.java",
      "start_line": 10,
      "end_line": 14,
      "language": "java",
      "annotations": {"10": "请求入口"},
      "expected_source_sha256": "<64-lowercase-hex>",
      "expected_excerpt_sha256": "<64-lowercase-hex>",
      "manuscript": "lesson.md",
      "supports": ["说明请求进入业务处理的位置"]
    }
  ],
  "unknowns": [
    {"learning_outcome": "部署后的行为", "reason": "本次材料未覆盖真实部署", "critical": false}
  ],
  "general_references": [
    {"title": "Framework guide", "url": "https://example.test/guide", "supports": ["解释通用框架概念"]}
  ]
}
```

`artifact_kind` 不可改成 `kind`；`supports` 必须是非空字符串数组；`manuscript` 只能是 `lesson.md` 或 `answers.md`；`unknowns` 是带 `learning_outcome`、`reason` 和严格布尔 `critical` 的对象数组；顶层和各对象都拒绝未知字段。源码槽位还需唯一，行号、相对路径、语言、注释和小写 SHA-256 字段须满足来源 helper 的 shape 限制。`critical: true` 会原样保留，并继续触发现有阻断门禁。

本地纯预检可在 scripts 已加入 `sys.path` 后调用：

```python
import json
from pathlib import Path
from reader_handbook_review import validate_reader_theme_source_manifest

manifest = json.loads(Path("attempt/sources.json").read_text(encoding="utf-8"))
validate_reader_theme_source_manifest(manifest, source_revision=expected_revision)
```

这个函数只检查结构及传入的 revision 是否一致；它不认证 revision、不读目标或快照、不比对摘要、不渲染源码槽位，也不判断来源真实性或教学质量。实际的 `prepare_reader_review(...)` 仍需要 live authenticated context，并会用相同 schema validator 后读取绑定快照、校验摘要并生成来源证明。该路径保留 `reader_source_blocks.py` 已支持的语言和安全注释行为，不另加 Java/Python 之外的解析器。

`review-input` 重新准备并冻结最终正文、完整答案、原始 review input、源码证明和快照字节，并将 `final-chapter.md` 与 `review-input.json` 写入新 staging 目录。1.2 review input 也携带 prepare 阶段冻结的三份核心写作规范全文和摘要；漏文件、改文件、改摘要或改 packet 都会令重建失败。包内 SHA-256 绑定最终正文和 review input；review input 中的 `fingerprint_sha256` 对应不含该 fingerprint 字段的 canonical JSON。本生命周期只处理项目源码章节，因此无论 curriculum unit 的 route 是项目路由还是 `D2A_GENERAL_LEARNING`，都必须至少有一个有效 source proof；无来源证明即阻断。概念课程的 general/canonical 学习走其独立路径，不由本函数接收。源码 helper 只提供局部字节和来源绑定，不判断陈述含义。

两个独立 JSON 复核记录都必须使用 `version: "1.0"`、`verdict: "ACCEPTED"`、相同的最终正文及 review-input SHA、`complete_manuscript_read: true`、具体非空评估、空的 `blocking_findings`，并且不能有未知字段或重复 JSON 键。来源记录的 `kind` 是 `reader-source-evidence-review`，另含 `evidence_assessment` 和 `limitation_assessment`；教学记录的 `kind` 是 `reader-principal-teaching-review`，另含 `normal_trace`、`failure_trace`、`beginner_assessment`、`source_explanation_assessment` 和 `answer_consistency`。作者、来源复核者和教学复核者的声明 session ID 必须互不相同；任一 critical unknown、拒绝/待定意见、缺项、哈希变化或阻塞发现都会阻止接受。

复核 JSON 中的 session ID、完整阅读声明和评估文本都是人的记录，不是密码学身份或自动语义证明。最终上下文重检 prepared/authored files 和精确报告字节。review packet 捕获已认证 revision 的原始源码 bytes 与 hash；之后工作树磁盘变化不会改写这份已捕获证据。新的独立 CLI 阶段会重建真实认证上下文，并由现有 snapshot helper 对当前绑定源码重新校验，变化的源码会令新准备失败。此界线不声称整个仓库始终新鲜，也不反复扫描无关路径。

`write_reader_chapter_review_input` 与 `publish_accepted_reader_chapter` 的 `inputs` 必须与 review context 里原样绑定的 authenticated inputs 相等；不一致时以 `INPUT_CONTEXT_MISMATCH` 拒绝，所有输出边界都使用绑定 inputs 检查。Staging、章节和 receipt 均不得写入 Skill、当前 attempt 或 review staging 目录。章节必须是新建的普通 `.md` 文件；receipt 是读者目录之外的新 `.json` 文件。已有文件不会被覆盖；receipt 写入失败时，只有确认属于本次操作且字节未变的章节文件才会回收，否则报告部分发布。receipt 记录 `READER_REVIEWED_DRAFT`、`PARTIAL`、已发布 SHA、review-input fingerprint 和 SHA、两份报告 SHA、声明的 session ID 与源码 revision/snapshot。它不提供 whole-book、跨项目、canonical Verified Handbook 或正式 Phase 6 PASS 结论。

调用形状如下，所有命令都还需要同一组 `curriculum_run_workflow` Phase 6 输入参数及 `--plan`：

| 命令 | 必需阶段参数 | 结果 |
|---|---|---|
| `prepare` | `--unit`（可重复）、`--blueprint`、`--writer-session`、`--out`；可选 `--book-plan` | 作者输入包和待填写的 chapter attempt |
| `review-input` | `--attempt-dir`、`--out`；可选 `--answer-mode append\|embedded` | 精确的 `final-chapter.md` 与 review packet |
| `publish` | `--attempt-dir`、`--review-input-dir`、`--source-review`、`--teaching-review`、`--chapter-out`、`--receipt-out`；可选 `--answer-mode` | 精确字节的读者章和外置 receipt |

外部读者交付是一个文件夹，其中平铺存放按阅读顺序命名的多份中文 Markdown 章节；每章整合自己的完整答案。审计收据、ID、哈希和来源快照保留在正文之外。

## 状态边界

现有 canonical parser、证据检查、schema 和质量门禁不因本契约而改变。上述读者生命周期实现正文组合、精确输入绑定、独立复核记录和受限发布；它不自动写作或判断教学质量。项目范围内其他章节、whole-book 覆盖、跨项目泛化、canonical Verified Handbook 与正式 Phase 6 门禁仍须分别审查和执行。读者路线的具体完成状态以精确章节 hash、复核记录和当前 Phase 6 计划为准。

## 通用读者稿呈现工具

`reader_handbook_export.py` 当前只支持 `general-lesson` 和 `general-answer` 两种已知 Markdown 格式；`canonical-v2` 明确尚未支持。它使用调用者给定的中文标题，只翻译固定标题和标签；其余教学正文及围栏代码保留，唯一移出的正文行是已知的源草稿状态标记，该原文和行号写入 provenance JSON。工具不生成教学内容、不改源文件，也不改变输入审查状态；输出教学深度始终为 `NOT_REVIEWED`，canonical gates 和正式 reader binding 均未接入本工具。

`reader_source_blocks.py` 是纯呈现 helper：只从调用者提供的源码读取函数填充整行槽位，输出按原字节选取的源码、中文 caption 和保留源码缩进且不显示机器 ID 的教学注释（ID 只保存在 provenance；恢复片段时按正文位置和逐行文本核对）。它不认证 revision、访问仓库、发布内容、语义审查或提升状态；Python/Java 只在明确安全位置插注，TS/JS 使用围栏外逐行说明，未知语言只允许原文片段，无法安全插注就拒绝。它不是跨语言解析器。

工具输出只进入内部 staging，并始终保留 `NOT_REVIEWED`。Principal 按上面的输入推演标准逐段审阅后，负责发布的 worker 才写入正式输出；这是工作分工，不是用户 approval 系统。概念或课程单元不必与读者章节一一对应。需要时，在授权范围内用已提供的官方材料或资料工具窄补重要机制证据。
