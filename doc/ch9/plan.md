# 上下文管理 Plan

## 架构概览

新增供应商无关的上下文管理层，由 `Agent` 在每次模型请求前调用。它先外置过大的工具结果，再根据 Token 预算决定是否生成摘要；因此 OpenAI 与 Anthropic Provider 继续只负责协议序列化和流式通信，不重复上下文策略。

上下文管理层由会话历史受控替换接口、会话缓存存储、Token 近似预算跟踪器，以及执行压缩与调用摘要模型的协调器构成。摘要复用当前 Provider，但请求不携带任何工具，并使用专用提示约束输出“草稿 + 正式摘要”；系统只保存正式摘要。

终端界面将 `/compact` 作为本地命令处理，不把它作为用户对话发送给模型。压缩过程与结果通过 Agent 事件显示为中文状态。Provider 同时提供可识别的上下文超限错误，使 Agent 可进行一次紧急压缩和重试。

## 核心数据结构

### `ContextConfig`

```python
@dataclass(frozen=True)
class ContextConfig:
    window_tokens: int = 128_000
```

全局上下文窗口配置。只描述预算，不依赖 Provider 协议；自动预算为 `window_tokens - 13_000`，手动目标为 `window_tokens - 3_000`。

### `ContextAction` 与 `ContextResult`

```python
class ContextAction(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"

@dataclass(frozen=True)
class ContextResult:
    action: ContextAction
    status: Literal["unchanged", "offloaded", "compacted", "failed", "circuit_open"]
    detail: str
    offloaded_count: int = 0
```

统一表示轻量外置、重量压缩、跳过和失败的可展示结果。`AUTO` 与 `EMERGENCY` 的摘要失败计入自动熔断；`MANUAL` 永不计入也不受熔断阻止。

### `TokenBudgetTracker`

```python
class TokenBudgetTracker:
    def record_usage(self, history: Sequence[Message], input_tokens: int) -> None: ...
    def estimate(self, history: Sequence[Message]) -> int: ...
```

保存最近一次成功模型请求的实际输入 Token 与当时历史字符总数。之后的估算为“该锚点 Token + 当前历史相对锚点的字符变化量 ÷ 4”，并限制为非负值。替换历史也按字符差量调整，因此不会因压缩后仍沿用旧的大预算而反复触发。没有锚点时，直接按全部历史字符数 ÷ 4 估算。

### `ContextArtifactStore`

```python
class ContextArtifactStore:
    def reset_session(self) -> None: ...
    def save_result(self, result: ToolResult) -> str: ...
```

管理工作区 `.yucode/context/` 下本会话的外置结果。启动时安全删除上一会话的整个缓存目录后重新创建；每项结果写入唯一 UTF-8 文本文件，返回工作区相对路径。文件保存原始 `ToolResult.content`，历史替换项保留工具名、目标、成功状态、预览和路径。

### `ContextManager`

```python
class ContextManager:
    async def prepare_request(self, tools: Sequence[ToolDefinition], cancellation: Cancellation) -> tuple[ContextResult, ...]: ...
    async def compact_manually(self, tools: Sequence[ToolDefinition], cancellation: Cancellation) -> ContextResult: ...
    def record_model_usage(self, usage: Usage) -> None: ...
```

持有会话、当前 Provider、缓存、预算与摘要失败状态。`prepare_request` 固定先执行轻量外置，再按自动预算选择重量压缩；手动和紧急路径直接调用重量压缩。重量压缩从末尾选择“至少 10K Token 且至少 5 条”的原始尾部，向 Provider 发送早期历史及结构化摘要指令，最后原子地替换会话历史为“正式摘要、边界提示、保留原文尾部”。

### `ContextUpdated`

```python
@dataclass(frozen=True)
class ContextUpdated:
    result: ContextResult
```

Agent 对 TUI 暴露的事件。界面据此显示持久的中文上下文状态，而不会把内部草稿或完整外置内容显示在聊天区。

### `ProviderError`

```python
class ProviderError(RuntimeError):
    code: str | None
```

保留现有错误文字，并新增可选标准化错误码。两个 Provider 从 HTTP 错误体和流式错误事件提取服务端 code；Agent 用 `prompt_too_long`、`context_length_exceeded` 等规范码及受控的文字兜底识别上下文超限。

## 模块设计

### `src/yucode/context.py`

**职责：** 实现字符阈值检查、工具结果外置、Token 预算、摘要请求、历史裁剪、失败计数与熔断。

**对外接口：** `ContextArtifactStore`、`TokenBudgetTracker`、`ContextManager`、`ContextAction`、`ContextResult`。

**依赖：** `Conversation` 的受控历史替换接口、Provider、`ModelRequest`、消息/工具数据结构、工作区路径与 `ContextConfig`。

轻量逻辑遍历每条消息的 `ToolResultContent`：先外置单项超过 50K 字符的结果；若同消息剩余工具结果合计仍超过 200K 字符，则按内容长度降序继续外置。替换结果的 `content` 只包含固定预览、缓存位置和“需要细节必须用 `read_file` 重新读取”的提示，确保标准文件工具可读取实体文件。

重量逻辑只在轻量结束后开始。摘要请求的 `tools` 为空，稳定提示要求输出临时 `<analysis-draft>` 和 `<structured-summary>` 两段；解析器只接受后者，缺失、格式错误或 Provider 异常都视为摘要失败。摘要输入要求输出九个固定标题，逐字摘录必须保留的用户要求、列出近期读文件快照、当前工具名和外置路径。完成后写入一条正式摘要和一条明确禁止按摘要臆测细节的边界消息。原始用户文本只被逐字引用或保留，绝不被系统重写。

自动/紧急摘要连续失败三次后设置会话级自动熔断，成功自动摘要清零失败数。手动摘要始终允许执行且不改变计数。外置的磁盘写入失败应保持原历史不变、返回失败状态并阻止本次依赖该外置结果的请求。

### `src/yucode/conversation.py`

**职责：** 在保留既有追加语义的同时，提供上下文管理所需的历史快照与原子替换。

**对外接口：** 新增 `replace_messages(messages: Sequence[Message])`。只有 ContextManager 使用它，先完成摘要与缓存写入，再一次性替换，避免失败时留下半压缩历史。

### `src/yucode/config.py`

**职责：** 读取、校验和提供全局上下文窗口设置。

**对外接口：** `AppConfig.context: ContextConfig` 与 YAML 的 `context.window_tokens`。省略时使用 128000；拒绝布尔值、非整数或小于可用安全余量的值，并用中文说明字段错误。

### `src/yucode/agent.py`

**职责：** 把上下文管理嵌入模型请求生命周期，并处理紧急重试。

**对外接口：** 构造时接收 `ContextManager`；新增 `compact(cancellation)` 异步事件流或等价公开方法；`AgentEvent` 联合类型加入 `ContextUpdated`。

每轮调用 `prompt_builder.build` 前执行 `prepare_request` 并发出状态。一次普通请求完成并收到 usage 后，将实际输入用量交给 ContextManager 建立锚点。若请求抛出可识别的上下文超限错误，则丢弃该次未完成响应、不执行其中工具调用，执行一次 `EMERGENCY` 压缩，再用相同迭代仅重试一次；第二次失败走现有错误收尾路径。取消、权限、工具和流式回复流程保持原行为。

### `src/yucode/providers/base.py`、`openai.py`、`anthropic.py`

**职责：** 将服务端错误代码无损转换为统一 `ProviderError`。

**对外接口：** `ProviderError` 的可选 `code`；既有 `Provider.stream` 签名不变。两个 Provider 的错误解析辅助函数同时返回用户可读消息及错误码，不改变成功事件、工具调用或 usage 的序列化语义。

### `src/yucode/tui/app.py` 与 `src/yucode/tui/widgets.py`

**职责：** 识别本地 `/compact` 命令，并显示压缩状态。

**对外接口：** 输入 `/compact` 时启动独占的压缩 worker；新增轻量 `ContextActivity` 显示组件或复用活动消息样式。

`/compact` 不写入 Conversation，不创建普通用户消息，不发送到模型作为任务。执行期间禁用输入，完成、跳过、失败与熔断均显示中文结果并恢复输入。模式菜单也应将 `/compact` 排除，避免提示“请选择 /plan 或 /do”。

### `src/yucode/cli.py`、`yucode.yaml.example` 与 `README.md`

**职责：** 创建 ContextManager 并向用户说明配置、缓存目录、自动/手动触发与紧急重试行为。

**对外接口：** CLI 把 Provider、Conversation、工作区与 `AppConfig.context` 组装后注入 Agent；示例配置展示默认 `context.window_tokens`；README 增加简短中文使用说明。

## 模块交互

```text
用户输入 / 普通任务
        │
        ├─ /compact ─→ ChatApp ─→ Agent.compact ─→ ContextManager
        │                                            ├─ TokenBudgetTracker
        │                                            ├─ ContextArtifactStore (.yucode/context)
        │                                            └─ Provider（无 tools 的摘要请求）
        │
普通任务 ─→ Agent.run ─→ ContextManager.prepare_request
                              ├─ 轻量：外置 >50K / 单消息 >200K 的工具结果
                              └─ 重量：达到窗口 - 13K 时生成摘要
                                        └─ Conversation.replace_messages

Agent.build request ─→ Provider.stream ─→ Usage ─→ TokenBudgetTracker.record_usage
        │
        └─ ProviderError(prompt too long) ─→ 紧急压缩 ─→ 原请求仅重试一次
```

摘要调用不会继承普通模型请求的工具列表：ContextManager 传入空工具集合，并在稳定提示中禁止工具调用。摘要原始输出在内存中解析，草稿立即丢弃；只有正式摘要、边界提示和保留的近期原文进入 Conversation。

## 文件组织

```text
src/yucode/
├── context.py                 # 新增：外置、预算、摘要和熔断
├── conversation.py            # 新增原子历史替换接口
├── agent.py                   # 请求前压缩、紧急重试、上下文事件
├── config.py                  # ContextConfig 与 YAML 校验
├── cli.py                     # 组装 ContextManager
├── providers/
│   ├── base.py                # ProviderError 错误码
│   ├── openai.py              # 提取 OpenAI 上下文超限错误码
│   └── anthropic.py           # 提取 Anthropic 上下文超限错误码
└── tui/
    ├── app.py                 # /compact 本地命令与事件展示
    └── widgets.py             # 上下文状态组件
tests/
├── test_context.py            # 新增：外置、估算、摘要、熔断
├── test_agent.py              # 请求前调用、一次紧急重试
├── test_config.py             # context.window_tokens 校验
├── test_tui.py                # /compact 与中文状态
├── test_openai_provider.py    # 错误码提取
└── test_anthropic_provider.py # 错误码提取
  yucode.yaml.example          # 上下文配置示例
  README.md                    # 用户说明
doc/ch9/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 轻量阈值单位 | 工具结果按字符数（50K / 200K） | 与规格一致，检查成本低，且工具结果的体积可直接获得。 |
| 整体预算估算 | API input usage 锚点 + 历史字符差 ÷ 4 | 无需 tokenizer，能利用真实调用数据，并对新增和被压缩的历史作增量修正。 |
| 压缩位置 | Agent 请求前的独立 ContextManager | 保证两个 Provider 行为一致，并固定“先轻量、后重量”的顺序。 |
| 外置存储 | 工作区 `.yucode/context/` 的会话缓存 | Agent 可用现有 `read_file` 工具重读；启动即清理，符合不跨会话保留的范围。 |
| 摘要模型调用 | 复用当前 Provider，空工具列表、专用结构化提示 | 无需第二套模型配置；空工具列表从能力层面禁止工具调用。 |
| 草稿处理 | 用明确标记分段，解析后只保留正式摘要 | 满足先分析再总结，同时不让草稿进入历史、缓存或界面。 |
| 边界消息 | 与正式摘要一同写入受控历史 | 让后续模型在读取摘要时立即获得“重新读取、不得臆测”的约束。 |
| 超限恢复 | 结构化错误码优先，紧急压缩后仅重试一次 | 不把普通网络/服务错误误判为上下文问题，也避免无限循环。 |
| 熔断范围 | 仅自动与紧急摘要累计三次连续失败 | 自动保护不会循环消耗；用户仍可通过 `/compact` 主动恢复。 |
