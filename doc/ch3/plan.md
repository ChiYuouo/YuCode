# MewCode Agent Loop Plan

## 架构概览

将现有“同步 Provider → Conversation 单工具状态机 → TUI 后台线程”改为全链路异步的“Provider → Agent → TUI”。Provider 只负责供应商协议，Agent 统一驱动模型循环、收集流、调度工具和判断停止，Conversation 只维护合法历史，TUI 只解析入口并消费事件。

普通消息与 `/do` 使用完整工具集；`/plan` 使用同一套 Agent Loop，但只暴露只读工具并附加规划指令。三种入口共享当前会话历史，系统不自动提取、绑定或注入最近计划。

阻塞式文件操作放入工作线程；Provider 网络流、命令子进程、命令确认和 TUI 消费均使用异步等待。这样流式显示、取消和只读工具并发无需再由界面线程协调。

## 核心数据结构

### `RunMode` 与 `StopReason`

- `RunMode`：`FULL`、`PLAN`。
- `StopReason`：`COMPLETED`、`ITERATION_LIMIT`、`CANCELLED`、`UNKNOWN_TOOL_LIMIT`、`STREAM_ERROR`。

### Agent 事件

`AgentEvent` 是以下不可变事件的联合类型：

- `TextDelta`、`ThinkingDelta`：携带迭代序号和增量文本。
- `ToolCallStarted`：携带迭代序号与完整工具调用。
- `ToolResultReady`：携带迭代序号与结构化工具结果。
- `UsageUpdated`：携带本轮用量和本次请求累计用量。
- `ProgressUpdated`：携带当前迭代、迭代上限、阶段与可读说明。
- `AgentFinished`：携带停止原因、最终可见文本、累计用量与可选错误。

最终可见文本为本次请求各轮正式文本的顺序拼接；thinking 不写入该字段。`AgentFinished` 是每次运行的最后一个事件，正常、取消和预期错误路径都必须发出。

### `Agent`

```python
async def run(
    text: str,
    mode: RunMode,
    cancellation: Cancellation,
    approve_command: ApprovalCallback | None = None,
) -> AsyncIterator[AgentEvent]: ...
```

`Agent` 持有 Provider、Conversation、ToolRegistry 和最大迭代数，负责完整循环。每次 Provider 调用计一次迭代；第 10 次或配置上限对应的响应若仍含工具调用，不执行这些调用，而是写入 `iteration_limit` 失败结果后结束。

### Provider

```python
async def stream(
    messages: Sequence[Message],
    cancellation: Cancellation,
    tools: Sequence[ToolDefinition] = (),
    instructions: str | None = None,
) -> AsyncIterator[StreamEvent]: ...
```

Provider 的 `StreamEvent` 继续只表达供应商原始的 thinking、文本、完整工具调用和单次调用用量。OpenAI 把模式指令写入 `instructions`；Claude 写入顶层 `system`。

### 工具安全与执行

- `ToolSafety`：`READ_ONLY`、`SIDE_EFFECT`。
- `Tool` 提供安全分类，并以异步接口接收参数、上下文、调用 ID 和取消信号。
- `ToolExecutor.execute_many(...)` 接收一组有序调用，返回同序结果列表。
- `ApprovalCallback` 改为 `Callable[[ToolCall], Awaitable[bool]]`。

只读工具为读取文件、查找文件和搜索代码；副作用工具为写文件、改文件和执行命令。未知工具按单独屏障处理并返回 `unknown_tool`。

### 配置

```python
@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 10

@dataclass(frozen=True)
class AppConfig:
    provider: ProviderConfig
    agent: AgentConfig
```

现有顶层 Provider YAML 字段保持不变，新增可选配置：

```yaml
agent:
  max_iterations: 10
```

缺失时使用 10；布尔值、零、负数、非整数或结构错误均抛出清晰的 `ConfigError`。

## 模块设计

### Agent

**职责：** 解析后的单次请求编排、双路收集、历史提交、用量累计、未知工具计数和停止判断。

**对外接口：** `run(...)` 异步事件流。

**规则：** `FULL` 使用全部工具与执行指令；`PLAN` 只使用只读工具与“只规划、不声称已执行”的指令。若一轮全部调用均为未知工具，连续计数加一；出现任何已注册工具即清零，即使该工具执行失败。

### Conversation

**职责：** 保存当前进程内的消息历史，并提供追加用户文本、完整助手响应、成组工具结果和部分助手文本的操作。

**边界：** Provider 流成功完成后才保存工具调用；流错误或取消只保存已完成的部分文本，避免历史中出现没有结果的孤立调用。上限和工具阶段取消时，以明确失败结果补齐已经保存的调用。

### Provider

**职责：** 使用 `httpx.AsyncClient` 发起流式请求，通过异步 SSE 解码器产生统一 Provider 事件，并在取消时关闭活动响应。

**兼容：** 保留现有两种供应商的工具参数碎片拼接、富消息序列化、thinking、错误映射和实际 Token 用量解析。

### ToolRegistry 与 ToolExecutor

**职责：** Registry 按安全分类筛选定义并查询工具；Executor 生成有序批次、执行、确认并统一包装异常。

**批次规则：** 连续只读调用组成一个并发批次；每个副作用调用和未知调用各自形成单项屏障。每批结束后按原索引整理结果，再发事件并写入历史。取消后不启动后续批次，未启动调用返回 `cancelled`。

### 文件与命令工具

**职责：** 文件工具通过工作线程运行现有受限操作；命令工具通过异步 PowerShell 子进程运行。

**取消：** 文件操作开始前检查取消，开始后等待短操作安全结束；命令同时等待进程、30 秒超时和取消信号，后两者触发时终止并回收进程。

### TUI 与 CLI

**职责：** TUI 保留用户输入的原文展示，但将 `/plan`、`/do` 前缀剥离后生成 `RunMode + text`；空 `/plan` 或 `/do` 只显示用法错误，不写入历史。异步 Worker 遍历事件并更新文本、thinking、工具活动、进度和 Token。CLI 负责组装 AppConfig、Provider、Registry、Conversation 与 Agent。

## 模块交互

```text
用户输入
  → TUI 解析模式并校验正文
  → Agent 写入用户历史
  → ProgressUpdated（第 N 轮请求模型）
  → Provider 异步流
      ├─ 文本/Thinking：立即发事件，同时在收集器累积
      ├─ 工具调用：立即发事件，同时按顺序收集
      └─ 用量：更新本轮和累计值并发事件
  → 成功完成的助手响应写入历史
      ├─ 无工具：AgentFinished(COMPLETED)
      ├─ 已达上限：补齐上限失败结果并结束
      └─ 有工具：Executor 按屏障分批执行
          → 同序 ToolResultReady
          → 成组工具结果写入历史
          → 检查取消与未知工具连续计数
          → 进入下一轮
  → AgentFinished
  → TUI 恢复输入
```

### 失败与停止路径

- Provider 流错误：保存部分文本，丢弃未提交工具调用，发 `STREAM_ERROR`。
- 用户取消模型流：关闭活动响应，保存部分文本，发 `CANCELLED`。
- 用户取消工具阶段：当前文件批次安全收尾或命令立即终止；剩余调用记为取消，不进入下一轮。
- 达到迭代上限：不执行最后响应中新请求的工具，以失败结果补齐历史后发 `ITERATION_LIMIT`。
- 连续两轮全部未知：第二轮结果写入历史后发 `UNKNOWN_TOOL_LIMIT`，不再请求模型。
- 命令拒绝、参数错误、工具异常或非零退出：作为普通工具失败结果回灌，不单独终止 Agent。

## 文件组织

```text
src/mewcode/
├── agent.py                  # Agent Loop、事件、模式、停止原因与收集器
├── cancellation.py           # Provider 与工具共享的异步取消信号
├── config.py                 # AppConfig、AgentConfig 与配置校验
├── conversation.py           # 供应商无关的会话历史
├── cli.py                    # 对象装配
├── providers/
│   ├── base.py               # 异步 Provider 协议与原始流事件
│   ├── sse.py                # 异步 SSE 解码
│   ├── openai.py             # OpenAI 异步流与 instructions
│   └── anthropic.py          # Claude 异步流与 system
├── tools/
│   ├── base.py               # 工具安全分类与异步协议
│   ├── registry.py           # 注册、查询和只读筛选
│   ├── executor.py           # 顺序屏障、并发批次与结果排序
│   ├── filesystem.py         # 异步包装现有文件操作
│   └── command.py            # 可取消的异步 PowerShell 子进程
└── tui/
    ├── app.py                # 命令解析、异步事件消费与确认协调
    └── widgets.py            # 进度与工具活动展示

tests/
├── test_agent.py             # 循环、停止、批次、事件与历史
├── test_conversation.py      # 历史原子提交和失败状态
├── test_tools.py             # 安全分类、并发、取消与命令回收
├── test_openai_provider.py   # OpenAI 异步协议
├── test_anthropic_provider.py# Claude 异步协议
├── test_config.py            # agent.max_iterations
├── test_tui.py               # 模式入口、事件显示与取消
└── test_cli.py               # 新对象装配
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 异步边界 | Provider、Agent、命令与 TUI 全链路异步 | 让流式显示、取消和工具并发使用同一控制模型 |
| Agent 事件 | 明确的不可变事件联合类型 | 避免大量可空字段，使界面按事件类型处理 |
| 历史职责 | Conversation 只存储，Agent 负责编排 | 消除当前固定两次请求的状态机耦合 |
| 工具并发 | 相邻只读并发，副作用与未知调用作为屏障 | 保持模型顺序和依赖语义，同时获得安全并发 |
| 结果顺序 | 等待整批后按原索引发出并回灌 | 并发完成顺序不会影响界面、模型或历史 |
| 文件异步化 | 阻塞操作放入工作线程并安全收尾 | 复用已验证实现，避免阻塞事件循环和半写状态 |
| 命令执行 | 异步子进程 + 超时/取消竞速 | Ctrl+C 能主动终止并回收命令进程 |
| 模式隔离 | 工具筛选 + 供应商原生系统指令 | `/plan` 在能力和模型意图两层都保持只读 |
| 上限语义 | 最后一轮仍要工具时不执行，并写失败结果 | 不产生无法由模型解释的新副作用，且历史保持合法 |
| 配置兼容 | 顶层 Provider 字段不变，新增可选 agent 节 | 旧配置继续工作，Agent 设置职责清晰 |
| 测试异步代码 | 使用 `asyncio.run` 驱动单元测试 | 当前项目无需额外引入异步测试插件 |

---

# TUI 活动动效补充 Plan

## 架构概览

在 TUI 层增加单一的活动时钟，按固定低频帧推进所有当前活动状态。模型等待由当前助手消息的活动提示显示；思考由折叠标题显示；工具调用由独立的进行中工具行显示。Agent 继续提供已有事件，业务循环和工具执行协议不改变。

## 核心结构

### `ActivityIndicator`

- 继承静态文本组件，保存当前阶段文字、可见状态和最新旋转帧。
- `start(label: str) -> None` 显示并更新阶段文字；`advance(frame: str) -> None` 重绘标记；`stop() -> None` 隐藏组件。

### `ThinkingBox`

- `start_thinking() -> None` 显示折叠区并进入活动标题状态。
- `advance(frame: str) -> None` 更新折叠标题；`finish() -> None` 保留内容并改为静态“已思考”。

### `PendingToolActivity`

- 保存工具调用标识与工具名称，用 `advance(frame)` 显示“正在执行”。
- 对应结果到达时，由静态 `ToolActivity` 原位替换。

### `ChatApp` 活动时钟状态

- 保存帧序号、每 120ms 刷新的计时器和按工具调用标识索引的进行中工具行。
- `_start_activity_clock()` 在请求启动时创建计时器；`_advance_activity_frame()` 更新当前助手与待完成工具行；`_stop_activity_clock()` 在最终事件中停止并清空状态。

## 模块设计

### `src/mewcode/tui/widgets.py`

用 `ActivityIndicator` 替换静态生成提示；让 `ThinkingBox` 接收时钟帧；新增 `PendingToolActivity`；让 `AssistantMessage` 暴露启动等待、接收思考帧、停止活动和推进帧的方法。

### `src/mewcode/tui/app.py`

消费此前未使用的 `ToolCallStarted`；维护一个请求级动画时钟；在工具结果到达时原位替换对应进行中行；在完成、取消和错误路径统一停止时钟。保留文本滚动、Token、模式、确认与取消逻辑。

### `src/mewcode/tui/app.tcss` 与 `tests/test_tui.py`

前者定义低对比度终端动效样式；后者覆盖帧推进、思考标题、工具进行中到完成替换，以及完成/取消后的停止行为。

## 模块交互

```text
ProgressUpdated(MODEL) ──→ 当前助手消息开始等待动效
ThinkingDelta ──────────→ 思考标题开始并随时钟转动
ToolCallStarted ────────→ 挂载对应的进行中工具行
ToolResultReady ────────→ 原位替换为静态工具结果
AgentFinished ──────────→ 停止时钟、清除活动状态、恢复输入

ChatApp 120ms 时钟 ──→ 当前助手消息、思考标题、所有进行中工具行
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 动效范围 | 模型等待、思考、工具执行共用同一帧序列 | 状态表达一致，符合终端 Agent 的克制反馈方式。 |
| 刷新方式 | 一个请求级 120ms Textual 计时器 | 约 8 FPS 足够可感知，避免为每个工具创建独立刷新循环。 |
| 工具开始信号 | 复用已有 `ToolCallStarted`，直到结果到达 | 不改变 Agent/Executor 接口；工具请求后紧接进入工具阶段。 |
| 结果展示 | 进行中工具行原位替换 | 每项工具在会话中只有一条记录，完成后页面稳定。 |
| 动效结束 | 统一由最终 Agent 事件停止 | 正常完成、取消、流错误和触顶均覆盖，防止后台刷新残留。 |
