# Project DeepDive

Project DeepDive 帮助刚接触某个仓库的学习者、开发者和维护者，从业务背景走到架构、源码、运行边界与项目扩展。它把文件和调用关系连成可追溯的功能链，解释正常与失败路径，也支持互动学习、完整教材、AI 辅助实现和质量审查。有前端的项目会把浏览器页面、接口、后端和数据结果连接起来。

这个 GitHub 仓库只负责分发。安装后，指导、模板和 CLI 都从本地包中读取；目标项目源码由用户提供或按本包指南读取。工具结果只证明工具检查的范围，不能替代来源审查、教学审查或用户验收。

本 Skill 不绑定特定模型、厂商或多智能体框架。使用它的宿主需要能加载 Skill 文件夹，并允许模型读取目标仓库、写入你指定的学习目录。换模型或宿主时，重新加载本包并提供持久化的进度文件；模型对相同任务的表现可能不同，不能保证质量完全一致。

## 安装

仓库根本身就是 Skill 包根，里面直接有 `SKILL.md`。把仓库克隆到正在使用的宿主会读取的 Skill 目录；不要在目标目录下再套一层 `project-deepdive/`。如果目录已存在，先检查本地改动，再按你的宿主更新方式处理，不要直接覆盖未保存的内容。

从 GitHub 克隆需要安装 Git 并能连接 GitHub；若不使用 Git，可下载仓库文件并解压/复制整个根目录。关键是安装目标的顶层直接含有 `SKILL.md`，而且宿主实际会扫描该目录。

安装时保留整个 `project-deepdive/` 文件夹及其原有目录结构，例如 `SKILL.md`、`references/`、`prompts/`、`scripts/`、`schemas/`、`docs/`、`spec/`、`examples/` 和 `tests/`。无需另找开发仓库根目录；直接读源码学习不要求安装目标项目或语言解析依赖。

**Bash / POSIX shell：Codex skills 根目录**

```bash
mkdir -p "$HOME/.codex/skills"
git clone https://github.com/zgozh/project-deepdive.git "$HOME/.codex/skills/project-deepdive"
```

**Windows PowerShell：Codex skills 根目录**

```powershell
$skillTarget = Join-Path $HOME '.codex/skills/project-deepdive'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillTarget) | Out-Null
git clone https://github.com/zgozh/project-deepdive.git $skillTarget
```

如果当前宿主读取已有 `.agents/skills` 根目录，把目标分别改为 `$HOME/.agents/skills/project-deepdive` 或 `Join-Path $HOME '.agents/skills/project-deepdive'`。若宿主在启动时加载 Skills，安装后按宿主说明刷新或重新启动。

### 更新已有安装

先确认宿主实际读取哪个 Skills 目录。若通过 Git 克隆安装，在副本中先运行 `git status --short`：工作区干净时再按宿主版本管理方式更新；存在本地改动时，先提交或备份并核对差异，再更新。若通过复制安装，先把现有整个文件夹备份到旁边，再将新版本复制到另一个临时目录；对比并保留你的本地改动，确认新目录根有 `SKILL.md` 后再切换。不要把新文件直接覆盖到有未保存改动的目录。

Git 克隆副本在确认工作区干净并位于要更新的分支后，可用 `git -C <安装路径> pull --ff-only` 快进更新；若本地改动或分支有分叉，先保存并人工合并，不要强制覆盖。

默认使用 HTTPS 克隆，无需先配置 SSH key。如果本机已经配置 GitHub SSH key，可将命令中的远端改为 `git@github.com:zgozh/project-deepdive.git`。

需要确认安装结构时，进入安装目录并运行：

```text
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
```

这些自检检查包内契约，不读取目标项目，也不证明某个项目、章节或教学结果已通过。

## 一句话开始

普通自然语言就够用，不必先写专用提示词。想从零了解一个仓库，可以说：“请用 Project DeepDive 带我认识这个仓库：先讲它解决什么问题，再用一个真实业务流程串起主要模块，并解释必要的先修概念。目标仓库是 `<项目路径>`，学习资料写到 `<输出目录>`。”

如果你明确要整本初学者教材，可以直接说：

```text
请使用 Project DeepDive，从零带我完成 <项目路径> 的完整初学者教材，正文写在 <输出目录>/教材，进度记录写在 <输出目录>/工作记录。请按业务和先修关系规划语义路线，按顺序持续完成章节、完整答案和复核；每个里程碑保存进度，不要每章结束都停下来等我确认。只有缺少必要信息、权限/额度不足或我明确暂停时才停，并记下下一步。
```

整本教材不是固定章数或旧批次的机械拼接。通常先认识项目与业务，补足实际需要的工程基础和架构，再按读者路线讲真实功能链。每章围绕一个语义主题，依次给先修、场景、真实源码、逐行责任、机制、正常与失败路径、练习和完整答案；后续再做跨层全链路、VibeCoding、面试或扩展练习。17 项教学职责都应按主题适用地覆盖，但不要求每章套同一组标题。更多状态保存与换模型说明见[中断恢复与模型适配](docs/中断恢复与模型适配.md)。

## 从哪里开始

先读[完整使用案例](docs/使用案例.md)，再按当前任务选自然语言入口。下面的名称说明用户意图，不是终端命令。

| 入口 | 适合的请求 |
|---|---|
| `learn-project` | 从业务背景和主要架构开始认识项目。 |
| `project-scan` | 盘点文件、分类范围或查看覆盖缺口。 |
| `project-map` | 梳理模块关系、业务链和学习先修。 |
| `project-book` | 写或修订一章完整的读者教材。 |
| `project-study` | 互动学习一个文件、机制、功能或失败路径。 |
| `project-interview` | 用项目事实练习问题、回答与追问。 |
| `project-extend` | 设计或实现一个有界的项目改进。 |
| `project-vibecode` | 学习从需求、计划、切片到验证和审查的 AI 开发闭环。 |
| `project-audit` | 检查证据、教学范围、质量门禁或验收状态。 |
| `project-update` | 根据真实 Git diff 更新受影响的知识与材料。 |

例如可以先说：“请从零带我理解这个仓库。先解释它解决什么业务问题，再给我一张主要模块图；然后选一个完整功能链，讲清正常和失败路径，最后给我练习和完整答案。”如果已经有项目地图或学习断点，要求 Skill 先核对并接着已有进度。

如果只想按计划完成整本教材，把上面全书请求中的项目路径和输出目录换成实际位置即可。无需复制本文件里的长案例；需要具体用例时再打开[完整使用案例](docs/使用案例.md)。

续接已有工作时可以说：“请先读我提供的最近项目地图、学习材料和 checkpoint（里程碑进度记录），核对它们是否仍适用于当前源码，再从上次停下的位置继续；保留未知项，不要假设前置材料已通过。”

这里的 checkpoint 指保存在输出目录里的里程碑进度记录；具体格式见[中断恢复与模型适配](docs/中断恢复与模型适配.md)。

## 教学与开发方法

旧 17 项是教学职责，不是必须按 17 批或 17 个标题输出的模板。整本书先给系统全景、业务闭环、文件职责和必要先修；进入主题前列本章阅读路线，再按真实用户输入讲功能链。源码块前说明场景和输入，块内用适配代码的完整行表解释职责、调用、状态、机制和分支，块后用同一输入回放正常与失败路径，并说明测试视角、运行/验证边界和未知。每章提供完整练习与答案，完成后自检章节、更新导航和未覆盖项；需要时按写作、独立复核、修订和再复核多轮闭环。全书再连接跨层复盘、VibeCoding、工程判断和项目面试。按读者先修组织先后，不固定章数；旧批次兼容可看[17 节讲解模板](references/批次讲解全文模板.md)。

AI 协作可按 `BASELINE → DISCOVER → SPEC → PLAN → SLICE → BUILD → VERIFY → REVIEW → REPAIR → FULL-CHAIN → RETRO` 推进：先确认起点和证据，再计划、实现、验证与复盘。根据当前任务选择阶段，不把阶段名当作 CLI 参数，也不把模型输出或静态检查当成真实运行证据。

旧 V2 工作流中的六类说法继续映射到这些入口；继续学习时先检查现有 checkpoint。映射和显式续写旧 17 节批次的条件见[兼容工作流](references/legacy-and-advanced-workflows.md)。自然语言模式、CLI 和旧批次名称是不同层次，不能把意图标签当作脚本参数。

## 工具与依赖

阅读本包的 Markdown、互动讲解或按用户给出的源码直接做教学，不需要 Python、目标项目依赖或语言解析器。要运行包内自检和相应 CLI 时需要 Python；Phase 3A 静态分析要求 Python 3.11 或更高版本。Java 分析需要本机 JDK。前端快照解析器及其可选安装方式见[基础事实指南](references/project-foundation-guide.md)和[Phase 3 CLI 细节](references/phase3-static-analysis-cli-details.md)。这些解析器不是直接源码学习的前置条件；不满足某个 adapter 的依赖时，应诚实保留未知或部分结果。

默认不安装目标项目依赖、不执行目标代码、不启动服务，也不保证静态工具能完整理解任意项目。脚本的输入、版本和限制以对应指南及 `--help` 为准；需要人工核对的事实仍由人审查。

项目地图、学习材料、审查报告和代码改动都写入用户指定的目标项目或输出目录；安装目录只提供入口、文档和工具，不是默认生成位置。具体写入仍以本次请求和[安装与执行边界](docs/安装与执行边界.md)为准。

更多入口见[参考目录](references/README.md)；包内安装、执行和写入边界见[安装与执行边界](docs/安装与执行边界.md)。

若要跨模型或跨对话继续同一本教材，把输出目录中的进度记录、当前教材文件、仓库路径和版本状态一并交给新会话。模型不会自动共享此前对话记忆；请看[checkpoint 示例与恢复步骤](docs/中断恢复与模型适配.md)。

## 包内结构与贡献

仓库根是 Skill 安装根：

```text
SKILL.md       宿主读取的入口与十种模式路由
README.md      安装、首次使用与能力边界
CONTRIBUTING.md 反馈和改进方式
LICENSE        上游 Apache License 2.0
docs/          完整使用案例、方法和安装边界
references/    主题指南、质量规则、模板与兼容材料
prompts/       分主题的作者和教练提示
scripts/       按需运行的本地工具
schemas/       版本化数据合同
spec/          旧流程合同与 CLI 手册
examples/      教学样例
tests/         Skill 工具的定向回归测试
```

可在[贡献指南](CONTRIBUTING.md)报告教材可读性或工具问题，并查阅[许可证](LICENSE)。包内链接优先；Skill 不要求联网获取运行资料。
