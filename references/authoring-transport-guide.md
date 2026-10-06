# 作者写入 Markdown 的传输指南

本指南是在作者环境容易误读文本时按需使用的可选实践。它不增加运行依赖，不绑定固定宿主，也不要求每章重复探针；章节的源码证据和教学标准仍由相应契约决定。

## 选择安全的写入路径

优先使用当前宿主提供的原生 patch 或字面 UTF-8 文件接口。每次改一个文件；大章节可按完整教学小节分批写入。替换已有文件前先保留当前字节快照，避免失败后失去恢复依据。

在当前 `functions.exec` 环境中，可用普通 JavaScript 双引号字符串把 patch 交给 `tools.apply_patch`。下面这段语法已用中文、反引号、`${project.version}`、`$()`、Windows 路径、引号和普通 `@@` / `***` 文本实际写入并读回：

```js
const patch = "*** Begin Patch\n*** Add File: artifacts/authoring-transport-fix-20261006/copyable-example.md\n+# UTF-8 写入探针\n+\n+保留 `Markdown`、`${project.version}`、`$()` 和路径 `C:\\work\\module\\pom.xml`。\n+双引号内容原样写入：他说：\"继续原句。\"\n+正文中的 `@@` 和 `***` 是普通文本。\n*** End Patch";
await tools.apply_patch(patch);
```

普通双引号字符串中的 `\n` 会成为 patch 换行；正文中的双引号要写成 `\"`，正文里的反引号、`${...}` 和 `$()` 保持字面内容。这里只适用于当前提供 `functions.exec` 与 `tools.apply_patch` 的环境；其他宿主应使用其原生的字面 UTF-8 接口，不能假定有这些工具名。

不要把任意 Markdown 放进 JavaScript 模板字符串。此前一段 Maven 文本里的 `${project.version}` 被当作 JavaScript 插值，产生 `ReferenceError: project is not defined`，错误发生在 patch 工具调用之前。该失败说明文本传输路径不安全，不说明 Maven 源文件或 Skill parser 有缺陷。

## 出错后的检查

本环境的 patch 必须以独占最后一行 `*** End Patch` 结束。一次故意省略该行的小复现被 parser 拒绝，并返回 `The last line of the patch must be '*** End Patch'`。遇到此错误时先核对末行、文件路径和 patch 外层标记。

无论失败看起来发生在哪一层，都先重新读回目标并查看差异，再决定是否重试。JavaScript 在调用 patch 前报错表示 patch 未送达；parser 拒绝也要以目标文件读回确认状态。文件写入可能在报错前已截断或部分更新：先前一次 `Path.write_text` 写入曾使 README 变短，因此不能从异常推断目标字节保持原样。用已保存的快照比较并修复实际差异。

正文过长时，可先将内容或完整小节按 UTF-8 保存，再由纯代码从这些现成文件/片段构造写入数据；这是可选方式，不要再用模板字符串把正文注入可执行代码。不要靠 shell 转义删除正文字符，也不要截短教材来迎合命令启动参数。PowerShell 5.1 下不要把含中文的 Python 源码经 stdin 管道传入；需要 Python 拼接时，先保存 UTF-8 `.py` 文件，再用 `python -X utf8 <脚本路径>` 执行，路径参数保持 ASCII 可读形式。

完成一个连贯写入切片后，按严格 UTF-8 读回目标，核对中文、反引号、围栏、`${...}`、`$()`、引号和路径等预期字面内容，再检查快照差异。文档、提示和探针属于 V0：读回与 diff 足够时不运行应用测试。这个探针只证明本次字节传输结果，不证明来源准确或教学质量，也不能保证所有作者或模型永不误用。

## PowerShell 的字面回读

在 PowerShell 命令中检查含 Markdown 反引号、`${...}` 或 `$()` 的文本时，把待查内容放进 PowerShell 单引号字符串；若把检查语句保存成 UTF-8 `.ps1` 文件，也在脚本里按此方式引用。不要把它们放进可插值的双引号命令文本。PowerShell 单引号字符串内要表示一个单引号，写成两个连续单引号（`''`）。不要用 JavaScript 模板字符串构造这类命令。此建议只针对 PowerShell 的命令参数解析；其他宿主应使用各自的字面传输方式。

以下只读命令从现有 UTF-8 探针副本读取，并用 `-SimpleMatch` 查找完整字面 `` `Markdown` ``；本次返回 `True`：

```powershell
Get-Content -LiteralPath 'artifacts/authoring-transport-fix-20261006/copyable-example.md' -Encoding UTF8 | Select-String -SimpleMatch '`Markdown`' -Quiet
```
