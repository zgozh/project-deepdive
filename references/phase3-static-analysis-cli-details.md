# Phase 3 static-analysis CLI details

This is the complete relocated Phase 3B1 through 3D2B detail from SKILL.md; only relative-link destinations were adjusted for this directory. See [SKILL.md](../SKILL.md) for capability selection. Historical plan, verification, and acceptance titles below identify development provenance only; this shipped document and its in-package scripts/contracts contain the operating instructions.

Phase 3B1 Python AST 静态事实切片已实现：

```bash
python scripts/analyze_python.py --root <Git 仓库或子目录> --index <project-index.json> --coverage <coverage.json> --stack-profile <stack-profile.json> --evidence <evidence.json> --out <产物目录>
```

- `scripts/analyze_python.py` 复用 G01 审计、`detect_stack._snapshot_reader`、Phase 2 路径护栏和 `_publish`；发布前再次审计快照。输入必须是 Phase 2 `project-index`/`coverage` 加来源摘要一致的 Phase 3A v1.1 `stack-profile`/`evidence`。输出目录保留 stack profile 原文，生成合并 evidence 与 `static-analysis.json`。
- `scripts/python_static_analysis.py` 只解析同一 Git 快照中后缀为 `.py` 且 Phase 2 分类为 `COVERED` 或 `CLASSIFIED` 的普通文件。每个文件最多 1 MiB，AST 最多 50,000 个节点；二进制、超限、不支持编码、语法错误、未知或排除文件会带固定原因记录为 `SKIPPED`，使分析状态为 `PARTIAL`。旧 Phase 2 v1.0 文件没有 `content_kind`，因此候选 `.py` 会先经限长快照读取，再检查二进制特征。
- 目前只提取 module/class/function/method 定义以及语法导入。所有导入目标保持未解析；不执行或导入目标代码，也不提取调用、继承、路由、模型、Java、前端、图谱或教材。输出含一基行号、稳定 ID、`python-ast` 提取方法、事实确定性、E1 evidence IDs 和与 Phase 3A 一致的五字段 `source_metadata`。
- 三个输出通过现有发布器按已处理失败路径回滚；这不提供进程崩溃原子性或多写入者事务保证。worktree 在最终复审之后仍存在普通竞态窗口。

Phase 3B1 命令默认仍产生 `static-analysis` v1.0.0。显式传 `--analysis-version 1.1.0` 启用已接受的 3B2：直接调用候选、保守本地继承候选、具有受支持导入/构造器证据的 FastAPI/Flask 路由声明，以及 Pydantic/SQLAlchemy declarative/dataclass、`test_` 函数、导入的 `pytest.mark` 和 `unittest.TestCase` 候选角色。该接受状态记录于 main@4c239bc421c4c117797a1c204df7a9338b085ba0。

- 只有唯一且同快照的本地定义绑定才填写调用或基类目标 ID；导入、遮蔽、属性和动态表达式保持未解析。调用关系表示语法调用候选，不证明实际执行。
- 路由识别不以 `.get` 等属性名单独为依据。Endpoint 路径、查询值及动态参数均以固定脱敏描述替代；路由候选不证明框架已注册或开始服务。
- 模型角色不证明数据库表，测试角色不证明测试已发现或运行。文件名或类名相似本身不建立角色。
- v1.1 的关系和角色都带稳定 ID、一基源码位置及 E1 evidence；v1.0 schema、产物和默认 CLI 行为保持不变。3B2 精确计划与验证记录是历史开发记录标题，仅作追溯；当前行为以本文件及包内脚本为准。

## Phase 3C1 Java parse-only 基础事实

从 skills/project-deepdive/ 运行：

```bash
python scripts/analyze_java.py --root <Git 仓库或子目录> --index <project-index.json> --coverage <coverage.json> --stack-profile <stack-profile.json> --evidence <evidence.json> --out <静态分析产物目录>
```

Java CLI 复用已审计的 Phase 2 输入、G01、同快照读取和发布前复审，输出单独的 static-analysis v1.2.0 契约并合并新 E1 证据。它提取 package/import/type/method/constructor/extends/implements 语法事实；导入及继承目标保持未解析。JDK 缺失或源文件不受当前 JDK 语法支持时，文件状态为 SKIPPED、整体为 PARTIAL；匿名类成员未提取时会把文件标为有界限制并将整体标为 PARTIAL。运行时 javac 只把本工具自带的桥接器编译到临时目录；目标源码只调用 JavacTask.parse()，不做目标语义分析、注解处理、类生成或运行。框架静态候选由显式 opt-in 的 C2 切片提供。

资源边界分阶段计算：每个 Java 文件最多 1 MiB；源文本最多按 8 MiB/16 文件一批读取和解析，解析完即释放该批输入后再读下一批。每文件 AST 上限为 50,000 个节点，单个序列化语法标签上限为 4 KiB。解析子进程 stdout/stderr 合计最多 64 MiB，桥接器编译子进程 stdout/stderr 合计最多 1 MiB；读取到上限即终止子进程并在发布前失败。最终产物按整个输入集合聚合，没有总字节上限；批次上限不代表 stack-profile、合并 evidence 或 static-analysis 的总大小上限。

3C1 计划与验证记录是历史开发记录标题，仅作追溯；本段已列出当前切片边界。Phase 3 的限定静态分析里程碑已接受；前端深层流程、知识图谱、课程和 Handbook 不属于此接受范围，里程碑验收记录标题仅作历史追溯；本句已列出接受范围。

## Phase 3C2 Java 框架候选（opt-in v1.3.0）

只有用户明确要做 Java 框架路由、模型或测试候选扫描时，才使用新 CLI；常规静态 Java 基础事实继续使用上面的 C1 CLI。该 C2 切片已获 Principal 接受，仍是显式 opt-in 能力；此接受不代表 Phase 3 或 Phase 3D 已验收。3C2 acceptance record 是历史记录标题；本句已列出当前验收边界。

从 `skills/project-deepdive/` 运行，并传入 Phase 2、Phase 3A 以及配对的 C1 v1.2/evidence 文件：

```bash
python scripts/analyze_java_frameworks.py --root <Git 仓库或子目录> --index <project-index.json> --coverage <coverage.json> --stack-profile <stack-profile-v1.1.json> --phase3a-evidence <phase3a-evidence-v1.1.json> --java-analysis-v1.2 <static-analysis-v1.2.json> --java-evidence-v1.2 <evidence-v1.1-c1-pair.json> --out <C2 五文件产物目录>
```

在提取框架注解前，工具从锁定快照重建 C1 v1.2 和 evidence，并要求与传入配对产物规范化序列化后逐字节一致。输出保留原 stack-profile、重建后的 C1 配对和新的 v1.3/evidence 配对；v1.3 仅 static-analysis 接受，并记录四个输入规范化 SHA-256。Python v1.0 默认、Python v1.1 opt-in、Java v1.2、evidence v1.1 和旧教学流程不变。

CLI 使用 [Java 框架语义验证模块](../scripts/java_framework_static_analysis.py)核验 C1 重建结果、PDJ2 声明候选、E1 引用和 v1.3 输入摘要；它是 CLI 的实现模块，不是单独命令。

只识别 19 个精确 Spring Controller/方法映射、JPA 模型与 JUnit 测试注解；需 FQN 或唯一的显式单类型导入。没有唯一绑定就不输出候选。路由目标保持未解析；所有新增事实为候选，不证明运行时注册、HTTP 路径、数据库表、测试发现或测试执行。注解参数不跨 PDJ2 边界、不写入证据或诊断。JDK 缺失时，无法从当前快照重建的完整 C1 配对 fail closed；仅当重建的 C1 PARTIAL 配对与输入完全一致，才输出不含新 Java 候选的 PARTIAL 结果。工具自带桥接器可以由 javac 编译；目标源码仅调用 JDK parse API，不做目标编译、加载或执行。

3C2 计划与验证记录是历史开发记录标题；当前命令说明见本节及包内 CLI。

## Phase 3D1 JavaScript/TypeScript parse-only CLI（已接受，仅限本地解析内核）

Principal 于 2026-09-25 接受了这一显式文件解析内核；该决定只覆盖此 parse-only 内核，不覆盖后续 3D artifact。后续切片与限定 Phase 3 静态分析里程碑分别记录在各自的验收记录中；本内核不产生正式 artifact、Git revision 或 E1 证据。Phase 3D1 acceptance record 是历史记录标题，仅用于追溯该切片决定。

该实验 CLI 只解析用户显式传入的 `.js`、`.jsx`、`.ts`、`.tsx` 源文件；不扫描仓库、不读取目标项目依赖、不编译、加载或执行目标代码。工具自己的 TypeScript parser 固定为 6.0.3。它生成临时本地 JSON 报告，不注册 schema，也不发布正式 artifact；报告中的导入/导出保留位置但不输出模块字符串字面值，组件、Hook 和 API 调用均为语法候选，动态目标保持未解析；源码字符串字面量和调用参数不写入报告。

本地报告仍包含显式文件路径和部分源码标识符，因此不能假设可直接公开。该 CLI 没有 Git snapshot、revision 或 E1 证据；显式路径包含性与符号链接检查不等于 Phase 2 的并发安全快照读取，文件仍可能在检查与读取之间被替换。

从仓库根目录安装工具自己的 parser 依赖并运行：

```powershell
cd skills/project-deepdive
cd scripts/frontend_parse_bridge
npm ci --ignore-scripts --no-audit --no-fund
cd ../..
python scripts/analyze_frontend.py --root 'C:\path\to\target-repo' --file src/App.tsx --file src/api.ts
```

实现入口为 `scripts/analyze_frontend.py` 和 `scripts/frontend_static_analysis.py`。此本地解析报告不能替代已接受的 Python/Java 工件流程或教材事实审核；同一 worktree 中另有已接受的有界 3D2A 快照 artifact 切片，不能把 D1 自身的本地报告当作其输入。

## Phase 3D2A 前端快照 artifact（已接受的有界切片）

该显式 CLI 仅新增 `static-analysis` v1.4.0 前端契约；Python v1.0 默认/v1.1 opt-in、Java v1.2/v1.3 和其它 artifact 保持原样。它消费经 G01 审计的 Phase 2 project-index/coverage 及 source metadata 匹配的 Phase 3A v1.1 stack-profile/evidence，从同一 Git 快照重新读取、验证源字节并调用 D1 固定的 TypeScript 6.0.3 parser。成功时输出 `static-analysis.json`、含原有记录及新增 E1 source evidence 的 `evidence.json`，并保持输入 `stack-profile.json` 字节完全一致。

从 `skills/project-deepdive/` 运行：

```powershell
python scripts/analyze_frontend_snapshot.py --root 'C:\path\to\target-repo' --index project-index.json --coverage coverage.json --stack-profile stack-profile.json --phase3a-evidence evidence.json --out out/frontend-v1.4
```

候选输入限于 coverage primary/secondary surface 包含 frontend 且 project-index 明确标识为 regular text 的 Git 跟踪 `.js`、`.jsx`、`.ts`、`.tsx` 文件。每批最多 32 个文件、8 MiB，单文件最多 1 MiB。v1.0 Phase 2 输入仍可审计，但因没有 `content_kind`，其前端路径会固定标为 SKIPPED 而不被假定为文本。排除、binary、超限及非 regular 项均有固定原因；语法错误文件为 PARTIAL 且无事实；无法唯一附着的事实关系会省略并令对应文件 PARTIAL。缺少解析器、source drift、无效协议、输入/证据不一致和 schema 校验失败均不发布。

事实只含解析器支持的 module/function/method/constructor/class 符号、语法 `DEFINES`/`IMPORTS`/`EXPORTS`、同 span 声明上的 `component_candidate`/`hook_candidate` 角色，以及 source symbol 为唯一包含函数（否则 module）的 CANDIDATE `USES_API`，其 target_id 始终为 null。不输出 page/store/API-client 角色或 frontend/backend endpoint 匹配。真实前端 dogfood 显示裸 `fetch` 不在 D1 allowlist 内，且 API/client facade 层未连到页面或 Hook；缺少 API 候选不等于没有请求路径。源码、字符串字面值、URL、module specifier 和调用参数不进入 artifact/普通日志。使用现有 publisher，但不承诺崩溃原子性或并发安全。3D2A 计划、验证与验收是历史记录标题；本段已列出当前工具边界。

## Phase 3D2B 前端候选扩展（有界切片已接受）

从 `skills/project-deepdive/` 显式选择 `--analysis-version 1.5.0`，输出 v1.5 frontend bundle；不带该选项仍生成原有 v1.4。该版本仅注册在 `static-analysis`，不改变 Python 默认/v1.1 opt-in、Java v1.2/v1.3 或其它 artifact。D1 本地 parser 可用 `--protocol-version 2` 选择同一 parser 协议；默认 PDJS1 保持旧报告。

```powershell
python scripts/analyze_frontend_snapshot.py --root 'C:\path\to\target-repo' --index project-index.json --coverage coverage.json --stack-profile stack-profile.json --phase3a-evidence evidence.json --out out/frontend-v1.5 --analysis-version 1.5.0
```

PDJS2 只在整个文件没有任何可能名为 `fetch` 的绑定时识别裸 `fetch(...)`；为了避免遮蔽误报，任何作用域出现同名参数、变量、解构绑定或 import 都会抑制该文件全部裸 `fetch` 候选。已有 `globalThis.fetch` 与明确 axios import 规则保持。`page_candidate` 要求同一锁定快照中 Phase 2 已索引的最近 `package.json` 明确声明 `next` dependency、相对该包根符合 Next `app/**/page.*` 或 eligible `pages/**` 路径、且同一 exported component declaration；manifest 缺失/无效/非 Next 时不继承父目录 marker，无法验证读取/摘要时发布失败。`store_candidate` 只要求直接从 `zustand` 导入 named `create`、全文件唯一绑定且变量直接由该调用初始化；`api_client_candidate` 只附着到直接承载已识别 HTTP 候选的 function/module，可以与 Hook 重叠。不能证实的角色不产生。候选均为 CANDIDATE 并绑定 E1 path/line/source digest；不声称页面运行时注册、状态生命周期或 backend endpoint。字符串、URL、参数值、原始源码和 package manifest 内容均不进入 parser output、artifact 或 CLI 摘要。此接受只覆盖 3D2B；Phase 3 里程碑验收记录标题仅作历史追溯。具体限制已在本段说明；V2 检查、dogfood 和接受决定是历史验证证据，不是使用前置。
