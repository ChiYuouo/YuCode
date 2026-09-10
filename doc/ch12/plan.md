# 持久记忆与会话恢复 Plan

## 架构概览

本章新增三个独立的持久化组件，并由启动入口统一装配：

- `InstructionLoader` 在启动时读取三层 `YUCODE.md`，安全展开 `@include`，将项目根级、项目配置级、用户级内容按优先级拼成稳定系统提示的一部分。
- `SessionManager` 负责 JSONL 会话的创建、追加、扫描、恢复、清理和恢复前校验。`Conversation` 只负责内存历史；在创建时接收记录器回调，将每次正常追加的消息同步写入当前会话文件。
- `MemoryManager` 管理用户级和项目级的独立 Markdown 笔记、索引生成/缩减，以及在 Agent 自然结束后的后台笔记提取任务。

启动时依次加载指令和记忆索引、清理过期会话、创建新会话、再构造 `SystemPromptBuilder`、`Conversation`、`ContextManager` 和 `Agent`。因此第一个模型请求已有项目规则和长期记忆，但不自动带入任何历史对话。

```text
启动
  ├─ InstructionLoader ─┐
  ├─ MemoryManager ────┼─> SystemPromptBuilder ─> Agent
  └─ SessionManager ───┴─> 新 Conversation（追加记录）

用户输入 ─> Agent Loop ─> Conversation ─> SessionManager（JSONL 追加）
                                  │
自然结束（无工具调用） ────────────└─> MemoryManager（后台 LLM 更新）

/resume ─> SessionManager（扫描、校验、恢复） ─> Conversation
                                               ├─> ContextManager（至多一次恢复压缩）
                                               └─> 下一请求的一次性时间提醒
```

## 核心数据结构

### 项目指令

```python
@dataclass(frozen=True)
class LoadedInstructions:
    content: str
    warnings: tuple[str, ...]

class InstructionLoader:
    def load(self, workspace_root: Path) -> LoadedInstructions: ...
```

`content` 是已展开的 Markdown 文本，顺序固定为项目根、项目 `.yucode`、用户 `~/.yucode`，前者优先级更高。加载器对每份入口文件维护独立的 `visited` 集合和包含深度计数；`@include relative/path.md` 相对当前文件解析。项目文件仅可解析到工作目录内，用户文件仅可解析到用户 `.yucode` 目录内。无效引用产生 warning，不使整个加载失败。

### 会话记录与恢复结果

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    last_active_at: datetime
    message_count: int

@dataclass(frozen=True)
class RecoveredSession:
    summary: SessionSummary
    messages: tuple[Message, ...]
    last_active_at: datetime
    warnings: tuple[str, ...]

class SessionManager:
    def create_session(self) -> str: ...
    def record_event(self, event: ConversationEvent) -> None: ...
    def list_sessions(self) -> tuple[SessionSummary, ...]: ...
    def recover(self, session_id: str) -> RecoveredSession: ...
    def cleanup_expired(self, active_session_id: str) -> tuple[str, ...]: ...
```

会话文件的一行代表一次追加操作，包含格式版本、ISO 8601 时间、记录类型和经过 JSON 编码的载荷；工具调用的参数和工具结果的状态、摘要、正文、错误码、目标均完整保留。文件名承担会话 ID，格式为 `YYYYMMDD-HHMMSS-xxxx.jsonl`，其中 `xxxx` 为安全随机的四位十六进制后缀。不存在独立 meta 文件。

会话概要每次直接扫描 JSONL：标题取第一个有效用户文本的单行截断形式，最近活动时间取最后一条有效记录的时间，消息数取成功恢复的消息数。恢复器先逐行解析和校验，再将有效记录还原为 `Message`。损坏行仅记录 warning 后跳过；含工具调用的助手记录会暂存，只有收到调用 ID 完全匹配的工具结果才提交。一旦发现未配对调用或错误配对，丢弃该调用及其后的记录。

### 会话记录器与对话恢复状态

```python
@dataclass(frozen=True)
class ConversationEvent:
    kind: Literal["user", "assistant", "partial_assistant", "tool_results"]
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()

ConversationRecorder = Callable[[ConversationEvent], None]

class Conversation:
    def __init__(self, recorder: ConversationRecorder | None = None) -> None: ...
    def replace_for_recovery(self, messages: Sequence[Message]) -> None: ...

class Agent:
    async def restore_session(
        self, recovered: RecoveredSession, cancellation: Cancellation
    ) -> AsyncIterator[AgentEvent]: ...
```

`Conversation` 的正常 `append_user`、`append_assistant`、`append_partial_assistant`、`append_tool_results` 在内存更新成功后产生一个原始追加事件。记录的是“本次新增加了什么”，而不是变化后的完整 `Message`：这样当新用户文本与前一轮工具结果在内存中合并时，不会把工具结果重复写入 JSONL。恢复器按事件顺序回放既有追加接口；上下文外置和压缩的 `replace_messages` 不产生事件，避免将同一历史或派生摘要重复写入 JSONL。

`restore_session` 先以不触发记录器的方式替换内存历史。若估算 token 已超过自动安全线，调用 `ContextManager` 做一次恢复专用压缩；压缩后仍高于安全线时产生明确失败事件，不发送模型请求。恢复后的下一次模型上下文错误不走现有紧急压缩重试。若最后活动时间距当前至少 24 小时，则在下一次请求的运行期消息中加入一次性的 `<session-time-gap>` 提醒；该提醒发送后立即清除，不写入原始会话记录。

### 自动笔记与索引

```python
class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"

class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION = "correction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"

@dataclass(frozen=True)
class MemoryIndex:
    scope: MemoryScope
    content: str

class MemoryManager:
    def load_indexes(self) -> tuple[MemoryIndex, ...]: ...
    def schedule_update(self, completed_turn: tuple[Message, ...]) -> None: ...
    async def wait_for_pending_updates(self) -> None: ...
```

每条笔记是带 YAML frontmatter 的 Markdown，frontmatter 至少包含稳定 ID、范围、类别、创建/更新时间和标题；正文是可直接阅读的事实或偏好。项目笔记放在工作目录的 `.yucode/memory/`，用户笔记放在 `~/.yucode/memory/`。两处各有一个 `index.md`，内容是可注入系统提示的紧凑索引。

`schedule_update` 只在 `StopReason.COMPLETED` 且该轮最终回复没有工具调用时调用。它以本轮消息、两个现有索引和严格 JSON 输出约束请求当前 Provider；模型为每条候选笔记选择创建、更新或忽略，并同时决定用户级/项目级范围及四种类别。管理器验证 JSON、范围、类别和目标 ID 后执行文件更新；模型判断重复时返回更新或忽略，而不是增加重复笔记。

每次笔记更新后重建相应索引。索引超过 200 行或 UTF-8 25 KB 时，管理器用同一 Provider 将索引压缩为按类别聚合的索引条目，并保留原始笔记 ID 引用；若压缩失败，则保留最近、最相关的完整条目并记录中文诊断。所有后台异常只进入诊断队列，不影响已完成的主对话。

## 模块设计

### `src/yucode/instructions.py`

**职责：** 加载三层手写指令、展开安全引用、报告可展示 warning。

**对外接口：** `InstructionLoader.load(workspace_root)`。

**关键规则：** 入口依次为工作目录 `YUCODE.md`、工作目录 `.yucode/YUCODE.md`、用户目录 `.yucode/YUCODE.md`。缺失入口静默跳过；包含最大深度固定为 5；循环、越界、缺失或无法读取的被引用文件只产生 warning。

### `src/yucode/sessions.py`

**职责：** 以 JSONL 记录完整会话，扫描派生概要，恢复可信历史，创建唯一 ID，按 30 天保留期清理。

**对外接口：** `SessionManager`、`SessionSummary`、`RecoveredSession` 与消息编码/解码 helpers。

**关键规则：** 存档目录为 `.yucode/sessions/`；追加使用单次打开、写入一行 JSON、刷新后关闭，避免崩溃影响既有行。恢复拒绝非当前项目目录内的会话 ID；清理只删除已验证位于会话目录内、超过 30 天且不等于当前 ID 的 JSONL 文件。

### `src/yucode/memory.py`

**职责：** 读写两级 Markdown 笔记和索引，调度不阻塞的自动笔记提取，维护索引容量。

**对外接口：** `MemoryManager.load_indexes()`、`MemoryManager.schedule_update()`、`MemoryManager.drain_diagnostics()`、`MemoryManager.wait_for_pending_updates()`。

**关键规则：** 后台任务使用 `asyncio.create_task` 并保存 task 引用以回收异常；退出前等待有限时长后取消未完成任务。诊断由 TUI 在空闲时显示为中文活动消息。自动笔记使用专用的稳定提示和无工具请求，不能执行 Agent 工具或把模型返回文本直接当作文件内容。

### `src/yucode/conversation.py`

**职责：** 保持现有内存消息语义，并在正常追加时通知可选记录器；提供不回写日志的恢复替换入口。

**对外接口：** 既有追加接口保持不变；新增 recorder 构造参数和恢复替换接口。

### `src/yucode/context.py` 与 `src/yucode/agent.py`

**职责：** 支持会话恢复的“一次压缩/不重试”保护和一次性时间提醒；自然结束后调度记忆更新。

**对外接口：** `ContextManager.prepare_recovered_history()` 及 `Agent.restore_session()`；`RuntimeContext` 新增可选恢复提醒字段。

**关键规则：** 正常新会话仍沿用现有自动外置、自动压缩和一次紧急重试。仅恢复会话启用恢复保护，避免改变普通会话的既有错误处理。

### `src/yucode/prompting.py`

**职责：** 将加载后的三层项目指令及两级记忆索引作为稳定提示模块注入；按现有稳定模块顺序保留系统约束的优先级。

**对外接口：** `SystemPromptBuilder` 构造参数保持兼容；调用方传入已排序指令和合并后的记忆索引。

### `src/yucode/cli.py`、`src/yucode/tui/app.py` 与 `src/yucode/tui/widgets.py`

**职责：** 启动时装配持久化组件、显示加载 warning、提供 `/resume` 会话选择与恢复反馈、显示后台记忆诊断。

**关键规则：** `/resume` 在输入菜单中与 `/plan`、`/do` 并列；执行后显示当前项目会话的 ID、标题、时间、消息数，选择后禁用输入直至恢复校验/必要压缩结束。恢复成功后显示历史并允许继续对话；无会话、损坏会话或恢复超限均显示中文原因并保持当前新会话可用。

## 模块交互

```text
`/resume` 选择会话
  ↓
SessionManager.recover
  ├─ 跳过 JSONL 坏行
  ├─ 验证 ToolCall ↔ ToolResult
  └─ RecoveredSession（历史、时间、warning）
  ↓
Agent.restore_session
  ├─ Conversation.replace_for_recovery
  ├─ 超安全线 → ContextManager.prepare_recovered_history（一次）
  └─ 超限失败 → TUI 保留当前新会话并展示错误
  ↓
下一次 Agent.run
  └─ 运行期消息注入一次 `<session-time-gap>`（如 ≥24 小时）

自然完成的一轮对话
  ↓
Agent 追加最终 assistant 消息并发出 AgentFinished(COMPLETED)
  ↓
MemoryManager.schedule_update（后台）
  ↓
Provider（无工具、受限 JSON）
  ↓
验证动作 → 更新独立笔记 → 重建/压缩索引 → TUI 诊断队列
```

## 文件组织

```text
src/yucode/
├── instructions.py       # 三层指令与安全 @include
├── sessions.py           # JSONL 会话追加、扫描、恢复与清理
├── memory.py             # 自动笔记、Markdown 文件和两级索引
├── agent.py              # 恢复保护与后台笔记触发
├── conversation.py       # 消息记录器与无回写恢复
├── context.py            # 恢复专用的一次压缩
├── prompting.py          # 指令、索引和时间提醒注入
├── cli.py                # 启动装配与过期清理
└── tui/
    ├── app.py            # /resume、恢复流程、诊断展示
    └── widgets.py        # 命令与会话选择菜单
tests/
├── test_instructions.py  # 优先级、嵌套、循环与越界引用
├── test_sessions.py      # JSONL、概要、恢复异常、清理
├── test_memory.py        # 笔记分类、去重、索引上限、后台失败
├── test_agent.py         # 恢复压缩与时间提醒
├── test_conversation.py  # 记录器与恢复不回写
├── test_prompting.py     # 指令/记忆提示顺序
├── test_cli.py           # 启动装配与清理
└── test_tui.py           # /resume 选择、恢复反馈与可继续对话
doc/ch12/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 指令优先级 | 根项目 → 项目 `.yucode` → 用户 `.yucode` | 高优先级文本在前，且与参考需求的三级来源一致。 |
| `@include` 根目录 | 按入口来源限制在项目根或用户 `.yucode` | 支持用户公共指令，同时阻断路径穿越和任意文件读取。 |
| 会话元信息 | 直接扫描 JSONL 派生 | 不引入需和存档同步的 meta 文件。 |
| JSONL 写入粒度 | 每次消息追加立即写一行 | 崩溃最多损失最后一条未完成记录，已完成内容可恢复。 |
| 工具调用恢复 | 调用与结果成对提交 | 防止模型看到无结果调用后继续产生不可信状态。 |
| 恢复压缩 | 超安全线时仅执行一次，随后禁止紧急重试 | 满足恢复场景的确定性，避免重复压缩和无限恢复尝试。 |
| 时间跨度阈值 | 24 小时 | 对隔夜或跨天工作给出提醒，同时避免短暂重启产生噪声。 |
| 自动笔记模型 | 复用当前 Provider，采用无工具的受限 JSON 请求 | 不增加配置与外部依赖，并限制后台任务副作用。 |
| 索引超限 | 先由模型按类别压缩，失败时降级保留有限条目 | 保持索引可用且不无限增长，单次失败不影响对话。 |
| 过期清理时机 | 每次启动、创建当前会话后执行 | 自动维护，无需后台常驻定时器，并可排除当前会话。 |
