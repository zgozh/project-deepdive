# Phase 7B：作者开发计划编译合同

`project-vibecode` 是静态编译入口，不是自动修改目标项目的 agent。它读取一份 development-plan 1.0.0、同 revision 的 project-index 和 evidence，输出完整中文实验稿、十一阶段提示词和 canonical plan。实施/验证始终 `NOT_RUN`，独立语义/教学审查始终 `PENDING`；结构、引用、hash 或成功退出不能等同 G15、实际开发或教学验收通过。

下文的计划、验证或验收标题仅标识历史开发记录；本契约和包内实现定义当前使用方式，不依赖包外记录。

## 输入与命令

从 `skills/project-deepdive/` 运行；示例输入路径须换成实际冻结输入：

```powershell
python -X utf8 scripts/project_vibecode.py --plan plan.json --project-index project-index.json --evidence evidence.json --out out/new-lab
```

`--plan` 是唯一权威需求/决定/计划，`--project-index` 是冻结文件事实，`--evidence` 是原始等级/locator证据，`--out` 是父目录已存在、目标目录尚不存在的新输出目录。`-X utf8` 启用 UTF-8。可用 `--help` 查看必需参数；输入 command 均是字面文字，CLI绝不执行它们、不联网、不导入/运行目标代码。

三个输入使用 [artifact_contract](../scripts/artifact_contract.py) 严格加载，拒绝重复 JSON keys、非 JSON 数值、额外属性和未注册版本。公共身份采用 `repository_revision`，不是 `revision`。各 kind 使用其自己的已支持 schema_version，不强求 index/evidence 与 plan 的版本号相同。可直接复用 [完整合成fixture](../tests/fixtures/artifacts/v1/development-plan.json) 阅读实际形状；它不是已实施的 dogfood 实验。

## 权威内容与字段

[schema](../schemas/v1/development-plan.schema.json) 定义：公共identity；lab_id/title/project_context；prerequisites/current_workflow；requirement（problem/actors/in_scope/out_of_scope/acceptance）；decisions；evidence_refs；impact（architecture/call_chain/existing_files/planned_new_files）；checks；slices；stages；enterprise_hardening；interview_followup；三个固定初始状态。

顶层 `checks` 只定义一次 `{id,purpose,cwd,command,expected,evidence_refs}`。slice 的 `tests` 和 stage 的 `checks` 都是 checkID 数组，可多处复用同一check；引用数组自身不能重复。slice 还包含id/purpose/depends_on/allowed_existing/allowed_new/protected/interfaces/risks/done/acceptance_refs/evidence_refs。依赖必须指向前面的slice，拒绝未知、自依赖、前向引用/环；编译器不调度执行或并行writer。

stages严格为 BASELINE、DISCOVER、SPEC、PLAN、SLICE、BUILD、VERIFY、REVIEW、REPAIR、FULL-CHAIN、RETRO。每项包含 explanation、learner_actions、agent_actions、prompt、checks、gate、failure_recovery；prompt完整八项 goal/context/constraints/scope/evidence/verification/done_when/output_contract。Markdown正文、代码、表、答案、首次prompt/人的核验/追问/模拟修订/继续标准保持字面内容，无字数、claim或段落配额。

作者必须先解释术语、情景、输入和因果，再给实质操作的界面/cwd/branch状态、真实相对path、谁创建/修改、用途及实际内容或before→after、完整prompt、标模拟的返回辨认、人的检查/观察结果、偏离追问与恢复。无写步骤解释读/比什么，不强制每step新文件；通过预测、比较方案、解释diff训练主动控制。工具只能核结构与静态一致性，不能证明这些语义职责成功，未核定path/command/content依赖须在后续教学/行为验收保持pending。

development-plan.json是唯一可修订的权威需求/决定/计划。LAB.md和PROMPTS.md为投影视图，不能另作一套可变决定源；修订回到plan后重新编译到新目录。首次读取/再次读取canonical plan无需重扫项目，真实执行、结果/人工接受与本编译器分开记录。

## 静态校验与证据范围

plan/index/evidence的repository_revision必须相同；evidence提供snapshot_kind/source_metadata时须与index一致。index paths与evidence IDs唯一、file_count正确；引用必须真实存在且位于权威plan引用集合。项目source/config/test/dependency locator的path若存在必须安全且在index；官方外部locator不强制项目path，E6保持显式推断而非源事实。

影响文件严格POSIX相对形式：拒绝绝对/drive/UNC/反斜杠、空分段、`.`/`..`、控制字符；check cwd单独允许`.`表示项目根，仍拒绝越界。existing必须在index，planned-new不得已存在；slice allowed必须落在对应impact集合，protected必须是索引中的现有文件且不与本slice允许写集合交叉。检查cwd安全不代表目录实际存在或命令能运行。

LAB呈现完整作者业务/前置/需求/决定/架构/slice/操作/检查/审查/失败/企业风险/复盘/问答；PROMPTS呈现十一阶段与每slice BUILD附加合同。证据原level/kind/locator靠近事实区展示，作者仍需把具体源码/答案放在相应操作附近。工具不改写作者语义、不验证来源蕴含正文，也不承诺旧snapshot与当前工作树一致。

## 输出与失败

输出父目录必须已存在。排他mkdir拒绝既有文件、目录、正常或dangling symlink及另一writer抢先创建的目录；不覆盖原产物。拒绝输出到index声明的目标root内（relative root按当前cwd解释；护栏不认证目标身份）。发布三个文件前先完成静态校验和渲染，canonical development-plan.json最后写；缺它的输出不算完整。

写入失败只清理本次创建、目录/文件inode仍匹配的内容，不递归删除外部新增条目。若权限阻止清理或目录被其他进程替换，可能留下不完整输出，错误仍可见；不承诺跨进程崩溃事务或对恶意并发目录替换的sandbox。CLI成功exit0只表示编译完成，输入/输出错误exit2表示未完成；真实运行证据仍未产生。

Phase 7 实现与 V2 检查属于历史验证证据；当前 7B 行为以本契约和包内脚本为准。7C实案例、7D完整教学/行为验收及最终产品门槛均不在7B成功范围。
