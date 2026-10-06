# Project DeepDive

Project DeepDive 帮助刚接触某个仓库的学习者、开发者和维护者，从业务背景走到架构、源码、运行边界与项目扩展。它把文件和调用关系连成可追溯的功能链，解释正常与失败路径，也支持互动学习、完整教材、AI 辅助实现和质量审查。有前端的项目会把浏览器页面、接口、后端和数据结果连接起来。

这个 GitHub 仓库只负责分发。安装后，指导、模板和 CLI 都从本地包中读取；目标项目源码由用户提供或按本包指南读取。工具结果只证明工具检查的范围，不能替代来源审查、教学审查或用户验收。

## 安装

仓库根本身就是 Skill 包根，里面直接有 `SKILL.md`。把仓库克隆到正在使用的宿主会读取的 Skill 目录；不要在目标目录下再套一层 `project-deepdive/`。如果目录已存在，先检查本地改动，再按你的宿主更新方式处理，不要直接覆盖未保存的内容。

安装时保留完整目录：`SKILL.md`、`references/`、`prompts/`、`scripts/`、`schemas/`、`docs/`、`spec/` 和 `assets/`。这些是本包内材料与可选工具的入口；直接读源码学习不要求安装目标项目或语言解析依赖。

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

默认使用 HTTPS 克隆，无需先配置 SSH key。如果本机已经配置 GitHub SSH key，可将命令中的远端改为 `git@github.com:zgozh/project-deepdive.git`。

需要确认安装结构时，进入安装目录并运行：

```text
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
```

这些自检检查包内契约，不读取目标项目，也不证明某个项目、章节或教学结果已通过。

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

续接已有工作时可以说：“请先读我提供的最近项目地图、学习材料和 checkpoint，核对它们是否仍适用于当前源码，再从上次停下的位置继续；保留未知项，不要假设前置材料已通过。”

## 教学与开发方法

传统 17 节教学职责是一套按需采用的深度清单：系统全景、业务闭环、文件职责、概念前置、批前清单、逐件源码、调用链与状态、底层机制、工程与面试常识、失败反例、测试视角、VibeCoding、验证与限制、完整复习答案、先修与未覆盖项、教材自检、导航索引。新主题按读者和业务流程组织，不要求每种请求套用相同标题；完整职责见[17 节讲解模板](references/批次讲解全文模板.md)。

AI 协作可按 `BASELINE → DISCOVER → SPEC → PLAN → SLICE → BUILD → VERIFY → REVIEW → REPAIR → FULL-CHAIN → RETRO` 推进：先确认起点和证据，再计划、实现、验证与复盘。根据当前任务选择阶段，不把阶段名当作 CLI 参数，也不把模型输出或静态检查当成真实运行证据。

旧 V2 工作流中的六类说法继续映射到这些入口；继续学习时先检查现有 checkpoint。映射和显式续写旧 17 节批次的条件见[兼容工作流](references/legacy-and-advanced-workflows.md)。自然语言模式、CLI 和旧批次名称是不同层次，不能把意图标签当作脚本参数。

## 工具与依赖

阅读本包的 Markdown、互动讲解或按用户给出的源码直接做教学，不需要 Python、目标项目依赖或语言解析器。要运行包内自检和相应 CLI 时需要 Python；Phase 3A 静态分析要求 Python 3.11 或更高版本。Java 分析需要本机 JDK。前端快照解析器及其可选安装方式见[基础事实指南](references/project-foundation-guide.md)和[Phase 3 CLI 细节](references/phase3-static-analysis-cli-details.md)。这些解析器不是直接源码学习的前置条件；不满足某个 adapter 的依赖时，应诚实保留未知或部分结果。

默认不安装目标项目依赖、不执行目标代码、不启动服务，也不保证静态工具能完整理解任意项目。脚本的输入、版本和限制以对应指南及 `--help` 为准；需要人工核对的事实仍由人审查。

项目地图、学习材料、审查报告和代码改动都写入用户指定的目标项目或输出目录；安装目录只提供入口、文档和工具，不是默认生成位置。具体写入仍以本次请求和[安装与执行边界](docs/安装与执行边界.md)为准。

更多入口见[参考目录](references/README.md)；包内安装、执行和写入边界见[安装与执行边界](docs/安装与执行边界.md)。

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
