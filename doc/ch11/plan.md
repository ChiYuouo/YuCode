# 工具结果截断移除 Plan

## 架构概览

移除内置工具层的 12K 文本结果截断，让工具在既有资源限制内完整构造 `ToolResult.content`。工具执行后，完整结果先进入当前进程和 Conversation；Agent 下一次向模型请求前，现有 `ContextManager.prepare_request()` 已会检查该完整结果，并按 50K / 200K 规则外置。因此不需要为每个工具增加单独的存盘逻辑。

文件读取字节上限、搜索/查找数量上限、命令超时与取消逻辑仍留在工具层。它们控制“工具实际取得多少数据”；上下文外置控制“其中多少数据进入模型历史”，两者职责分离。

## 核心数据结构

本次不新增数据结构。继续使用既有：

```python
@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    success: bool
    summary: str
    content: str
    error_code: str | None
    target: str
```

变化是 `content` 不再被通用 12K 字符上限裁剪；它包含工具在其他既有资源限制内实际获得的完整文本。

## 模块设计

### `src/yucode/tools/filesystem.py`

**职责：** 继续限制可读取文件大小和结果条数，但不截断已经收集到的文本结果。

**对外接口：** `ReadFileTool`、`FindFilesTool`、`SearchCodeTool` 的工具定义和调用参数不变。

**修改：** 删除 `MAX_RESULT_CHARS` 和通用文本裁剪函数。读取文件直接返回完整解码文本；查找文件仅按 `MAX_MATCHES` 选择路径并以完整列表拼接；代码搜索仅按 `MAX_MATCHES` 停止匹配并以完整列表拼接。工具摘要只在条数上限命中时说明结果受限，不再出现“内容已截断”。

**保留：** `MAX_READ_BYTES`、`MAX_MATCHES`、跳过目录、工作区路径校验、二进制/编码错误处理。

### `src/yucode/tools/command.py`

**职责：** 保留 PowerShell 生命周期控制并返回完整已收集输出。

**对外接口：** `RunCommandTool` 调用参数、权限要求、成功/失败结果和错误码不变。

**修改：** 删除对 `MAX_RESULT_CHARS` 的导入和输出切片；成功或非零退出时，直接把合并后的 stdout/stderr 作为 `ToolResult.content`。摘要不再附加“输出已截断”。

**保留：** 30 秒超时、取消时杀死进程、标准输出与错误输出收集、退出码判断。

### `src/yucode/context.py` 与 `src/yucode/agent.py`

**职责：** 无生产代码改动；作为完整工具结果进入模型前的统一保护层。

**验证：** 增加集成测试：Agent 通过内置读取或命令工具产生大于 50K 字符的完整结果后，下一轮请求前 ContextManager 外置结果，缓存文件保持完整内容，Provider 收到的历史仅含路径与预览。

### `tests/test_tools.py`、`tests/test_agent.py`、`tests/test_context.py`

**职责：** 将原来的截断断言替换为完整结果断言，并覆盖资源限制与上下文外置的边界。

**测试范围：**

- 大于 12K、小于 1MiB 的读取、搜索、查找和命令结果完整返回。
- 超过 1MiB 的文件、200 条匹配/路径和命令超时仍保持现有行为。
- 大于 50K 的内置工具结果在下一轮模型请求前被写入 `.yucode/context/`，模型历史不含完整原文。
- 工具摘要不再出现文本截断提示。

## 模块交互

```text
内置工具执行
  │
  ├─ 文件 ≤ 1MiB / 命令在 30 秒内 / 匹配未超 200 条
  │     ↓
  │   ToolResult.content（完整文本）
  │     ↓
  │   Conversation.append_tool_results
  │
  └─ 下一轮 Agent 请求前
        ↓
      ContextManager.prepare_request
        ├─ 单项 > 50K 字符
        ├─ 单消息合计 > 200K 字符
        ↓
      .yucode/context/tool-result-xxxx.txt（完整文本）
        ↓
      Provider 历史：预览 + 路径 + 重读提示
```

## 文件组织

```text
src/yucode/
├── tools/
│   ├── filesystem.py  # 移除 12K 文本截断
│   └── command.py     # 移除 12K 命令输出截断
tests/
├── test_tools.py      # 完整结果与既有资源限制
├── test_agent.py      # 内置大结果 → 外置集成
└── test_context.py    # 外置缓存完整性回归
doc/ch11/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 12K 截断 | 完全移除 | 让 ContextManager 能看见并保存完整内置工具结果。 |
| 工具资源限制 | 保持不变 | 防止文件、搜索与命令在工具执行阶段无限消耗资源。 |
| 外置时机 | 保持下一次模型请求前 | 复用现有统一策略，避免内置工具与 MCP 工具出现两套存储逻辑。 |
| 大结果内存驻留 | 允许在当前 Agent 轮次短暂存在 | 结果须先由工具产生，随后即可在模型请求前外置；换取完整可重读内容。 |
| 工具摘要 | 不再报告文本截断 | 12K 截断已经移除；仅保留真实资源限制导致的不完整提示。 |
