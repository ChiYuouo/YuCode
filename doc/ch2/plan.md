# YuCode 工具系统 Plan

## 架构概览

在现有 Provider → Conversation → TUI 链路中加入统一工具层。一次请求固定为：用户消息、首次模型流式响应、最多一个工具执行、工具结果回灌、第二次模型流式最终回复、结束。

文件工具强制限制在工作目录内；命令以该目录为启动位置，使用 PowerShell，并在 TUI 弹窗中逐次确认后执行。

## 核心数据结构

### 工具层

- `ToolDefinition`：工具名称、中文描述和 JSON Schema 参数定义。
- `ToolCall`：调用 ID、工具名称、已解析参数。
- `ToolResult`：调用 ID、工具名称、成功状态、摘要、完整结果内容和错误码。
- `Tool`：提供元信息，并接收参数与工作目录上下文后返回 `ToolResult`。
- `ToolRegistry`：集中注册工具、按名称查找、导出工具描述。

六个工具使用如下参数：`read_file(path)`、`write_file(path, content)`、`edit_file(path, old_text, new_text)`、`run_command(command)`、`find_files(pattern)`、`search_code(pattern, path?)`。

读取单文件最大 1 MiB；工具结果最大 12,000 字符；查找和搜索最多返回 200 项，并标记截断。命令合并标准输出与错误输出，超过 30 秒终止。

### 会话与流事件

`Message` 扩展为可保存文本、工具调用与工具结果的内容块。`StreamEvent` 扩展工具调用事件；Provider 只在参数完整、JSON 可解析后发出该事件。

## 模块设计

### `tools/`

**职责：** 提供统一工具协议、注册中心、文件操作、命令执行和统一异常包装。

**边界：** 文件工具通过解析真实路径确认其位于工作目录内，并拒绝通过符号链接或 `..` 越界的请求。读写均使用 UTF-8 文本；写入和替换先写临时文件再原子替换。查找与搜索跳过 `.git`、`.venv`、`__pycache__`、`.pytest_cache` 和 `dist`。

### Provider

**职责：** 将统一消息和工具定义转换为各后端协议，并把工具调用流转换为统一事件。

**协议：** OpenAI 按 `response.function_call_arguments.delta` 拼接参数、在完成事件读取名称和调用 ID；Claude 根据内容块索引累积 `input_json_delta`、在块结束时解析参数。两者均在后续请求中保留工具调用与结果。

### Conversation

**职责：** 保存可继续发送的完整历史，并执行单工具状态机。

**流程：** 首次响应中的第一项工具调用会执行，额外调用写入“本轮仅支持一个工具”的失败结果；工具结果写入历史后发起第二次响应；第二次出现的工具调用写入“本轮工具调用上限”的失败结果，不执行也不发起第三次请求。

### TUI

**职责：** 显示紧凑的工具活动摘要，协调命令确认和后台生成线程。

**交互：** 命令请求投递到 UI 线程后打开模态确认弹窗，展示完整命令和“执行 / 拒绝”操作。后台线程等待决定；拒绝、关闭弹窗或 `Ctrl+C` 均作为可恢复的工具结果返回。

## 模块交互

```text
TUI 输入
  → Conversation 首次请求
  → Provider 流式文本/工具调用
  → ToolRegistry + ToolExecutor
  → （命令时 TUI 确认）
  → ToolResult 写入会话历史与工具摘要
  → Conversation 第二次请求
  → Provider 流式最终文本
  → TUI 显示并恢复输入
```

## 文件组织

```text
src/yucode/
├── tools/
│   ├── base.py
│   ├── filesystem.py
│   ├── command.py
│   ├── registry.py
│   └── executor.py
├── providers/
│   ├── base.py
│   ├── openai.py
│   └── anthropic.py
├── conversation.py
└── tui/
    ├── app.py
    └── widgets.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具抽象 | 协议 + 注册中心 | 统一六项工具并可扩展 |
| 多后端适配 | Provider 内序列化/解析 | Conversation 不依赖供应商协议 |
| 文件安全 | 解析真实路径后校验根目录 | 防止 `..` 与符号链接绕过 |
| 命令安全 | PowerShell、工作目录启动、TUI 逐次确认 | 符合当前 Windows 环境；不假装提供系统级沙箱 |
| 超时 | 命令强制 30 秒；文件工具限制读取量和结果量 | 避免线程超时后写入仍继续的错误语义 |
| UI 输出 | 工具活动摘要 | 可观察且不让完整结果刷屏 |
