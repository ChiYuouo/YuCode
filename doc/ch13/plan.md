# 斜杠命令注册与分发 Plan

## 设计依据与架构概览

本设计以已获用户明确批准的本章 `spec.md` 为准。当前处于技术设计阶段，尚未开始任务拆解、验收清单或实现。

新增 `commands` 包承载命令定义、注册、解析、分发及内置处理器。注册表在 CLI 启动时一次构建并冻结，帮助、补全和分发共享同一个实例，不引入装饰器自动发现、动态插件或额外依赖。

新增 `CommandUI` 协议作为界面控制边界，由 `TextualCommandUI` 适配现有 `ChatApp`。命令层不导入 Textual；普通聊天和提示词命令共用一个已解析用户消息的发送入口，避免二次分流和重复入库。

新增 `SessionController` 组合已有 `SessionManager` 和 `Agent`，负责新建、恢复及切换提交。存档管理仍由 `SessionManager` 负责；上下文状态仍由 `Agent` 和 `ContextManager` 管理。删除确认属于界面交互，最终合法性校验和删除属于存档服务。

保留现有 Provider、工具权限、压缩算法、记忆任务和 JSONL 格式。本章仅补充会话切换所需的状态快照与复位，以及空会话和删除支持。

```text
CLI 构建并校验 CommandRegistry
                   │
                   ├── 帮助 / 参数提示 / Tab 候选
                   │
Composer 回车 → parse_input → ChatApp 同步占用忙碌状态
                   ├── 空输入：返回
                   ├── 普通文本：统一发送入口 → Agent.run
                   └── 斜杠命令：Dispatcher → 内置处理器
                                            ├── CommandUI → TextualCommandUI
                                            ├── SessionController → Agent / SessionManager
                                            └── 固定提示词 → 统一发送入口 → Agent.run
```

## 核心数据结构与接口

以下签名是实现契约；省略的方法体在四份文档获批后实现。

### 命令定义与注册表

```python
class CommandKind(str, Enum):
    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"

CommandHandler = Callable[["CommandContext", str], Awaitable[None]]

@dataclass(frozen=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    handler: CommandHandler
    argument_hint: str | None = None
    hidden: bool = False

class CommandRegistrationError(ValueError): ...
class CommandUsageError(ValueError): ...

class CommandRegistry:
    def __init__(self, definitions: Sequence[CommandDefinition]) -> None: ...
    def get(self, name: str) -> CommandDefinition | None: ...
    def visible(self) -> tuple[CommandDefinition, ...]: ...
    def complete(self, prefix: str) -> tuple[CommandDefinition, ...]: ...

def build_builtin_registry() -> CommandRegistry: ...
```

名称和别名登记时不带 `/`，必须非空、无空白且不含 `/`；描述、用法和处理器必须有效。用 `.lower()` 规范化名称与别名，将全部键放进同一索引；在写入任何重复键前抛出包含冲突键、已有主名称和新主名称的异常。同一条定义内重复名称或别名同样报错。

先在临时索引中完成全部校验，再保留不可变定义和只读索引；构造完成后不提供注册或删除入口。`visible()` 按内置登记顺序返回公开项；`complete()` 按名称及别名前缀筛选，以主名称去重，保持同样顺序。空前缀返回全部公开项。

### 输入解析与分发

```python
class InputKind(str, Enum):
    EMPTY = "empty"
    CHAT = "chat"
    COMMAND = "command"

@dataclass(frozen=True)
class ParsedInput:
    kind: InputKind
    text: str
    name: str = ""
    arguments: str = ""

def parse_input(raw: str) -> ParsedInput: ...

class CommandDispatcher:
    def __init__(self, context: "CommandContext") -> None: ...
    async def dispatch(self, parsed: ParsedInput) -> None: ...
```

`parse_input` 对整段内容执行 `strip()`，然后区分空白、普通文本和斜杠。斜杠命令只寻找首个 `isspace()` 字符，分隔命令名和参数；保留参数内部空白，不使用 `shlex`。`/` 或 `/ 参数` 的命令名为空，仍返回命令输入，由分发器显示 `/help` 引导。

分发器只接收 `COMMAND`，查表后调用处理器。未知命令提示 `/help`；`CommandUsageError` 显示原因和所命中命令的登记用法；会话或普通服务异常显示中文操作错误，保留内部诊断，不改送 AI。`StreamCancelled`、异步任务取消和进程退出不按普通命令故障吞掉，由运行入口完成取消与清理。启动注册异常在运行分发器之外处理。

### 界面控制与执行上下文

```python
@dataclass(frozen=True)
class CommandStatus:
    provider: str
    model: str
    mode: PermissionMode
    session_id: str | None
    message_count: int
    estimated_context_tokens: int
    last_turn_usage: Usage | None

class CommandUI(Protocol):
    async def show_message(self, text: str, *, error: bool = False) -> None: ...
    async def send_user_message(self, text: str) -> None: ...
    async def clear_chat(self) -> None: ...
    async def replace_history(self, messages: Sequence[Message]) -> None: ...
    def set_mode(self, mode: PermissionMode) -> None: ...
    def status(self) -> CommandStatus: ...
    def reset_turn_usage(self) -> None: ...
    def refresh_status(self, text: str = "准备就绪") -> None: ...
    async def show_context_result(self, event: ContextUpdated) -> None: ...
    async def confirm_session_delete(self, summary: SessionSummary) -> bool: ...
    async def request_exit(self) -> None: ...

@dataclass(frozen=True)
class CommandContext:
    registry: CommandRegistry
    ui: CommandUI
    agent: Agent
    sessions: SessionController | None
    cancellation: Cancellation
```

每次执行创建带独立取消信号的上下文；定义和处理器不捕获某次会话、输入控件或可变取消信号。允许 `sessions=None` 兼容不启用持久化的现有测试和构造方式；此时 `/session` 显示“当前未启用会话管理”，状态显示会话 ID 不可用，其余命令照常工作。

`show_message` 只挂载通知或错误组件，不调用 `Conversation.append_user`。`send_user_message` 直接等待统一对话执行入口，不再次解析提示词、不创建第二个 Worker。`replace_history` 清空旧显示后重绘传入消息；`clear_chat` 只操作控件，不访问历史替换接口。

### 上下文快照与会话协调

```python
@dataclass(frozen=True)
class ContextState:
    input_tokens_anchor: int | None
    history_chars_anchor: int
    automatic_failures: int
    automatic_circuit_open: bool

# ContextManager 新增
def estimated_tokens(self) -> int: ...
def snapshot_state(self) -> ContextState: ...
def restore_state(self, state: ContextState) -> None: ...
def reset_conversation_state(self) -> None: ...

@dataclass(frozen=True)
class AgentSessionState:
    messages: tuple[Message, ...]
    context: ContextState
    recovery_time_gap: str | None
    recovery_context_guard: bool

# Agent 新增
def estimated_context_tokens(self) -> int: ...
def snapshot_session_state(self) -> AgentSessionState: ...
def restore_session_state(self, state: AgentSessionState) -> None: ...
def reset_session_state(self) -> None: ...

class SessionController:
    def __init__(self, agent: Agent, store: SessionManager) -> None: ...
    def new_session(self) -> str: ...
    async def resume_session(
        self, session_id: str, cancellation: Cancellation
    ) -> AsyncIterator[ContextUpdated | SessionRestoreFinished]: ...

# SessionManager 新增
def get_summary(self, session_id: str) -> SessionSummary: ...
def delete_session(self, session_id: str) -> None: ...
```

`SessionController` 的存档引用供会话处理器查询清单、概要和删除，不重新包一套相同存档接口。快照不包含项目指令、权限、Provider 或长期记忆：它们在切换时保留。

`reset_conversation_state` 重置 Token 估算锚点及自动压缩失败计数、熔断标志，不重建 `ContextManager`，不调用现有会清空外置文件目录的 `ContextArtifactStore.reset_session()`。同一进程中保持外置文件序号单调增加，避免恢复尝试或新会话覆盖旧历史仍引用的文件。失败尝试留下的未引用外置文件允许保留到既有启动清理，不在回退中删除共享缓存。

## 模块设计

### `src/yucode/commands/models.py`、`registry.py`、`parser.py`、`dispatcher.py`

分别负责数据契约、一次性登记校验、纯文本解析、查找执行与错误转换。`models.py` 对 Agent 和会话服务等仅为注解所需的依赖使用 `TYPE_CHECKING`；实际依赖方向为处理器依赖契约和服务，服务不导入命令包。`__init__.py` 只导出装配所需的公共入口。

### `src/yucode/commands/builtins.py`

保存十一条定义：十条公开命令及隐藏的 `exit`（别名 `quit`）。仅 `help` 登记公开别名 `?`，其他别名暂为空。参数验证必须在所有副作用前完成：

| 处理器 | 校验与调用 |
| --- | --- |
| help | 零或一个参数；查询参数去掉一个可选 `/` 后查表，隐藏项按未公开命令处理；从定义生成全部帮助文本。 |
| compact | 不接受参数；消费 `Agent.compact(cancellation)`，通过界面展示进度和结果；无较早历史沿用现有提示。 |
| clear | 不接受参数；等待清空聊天组件，再显示简短清屏通知并刷新状态；上下文与用量不变。 |
| plan / do | 不接受参数；分别设置 `PermissionMode.PLAN` 与 `PermissionMode.DEFAULT`；已处于目标模式时提示而不重复变更。 |
| session | 无参数显示当前 ID 和 `len(conversation.messages)`；固定子操作严格校验参数数量，ID 保持原值；调用存档服务和协调层。 |
| memory / review | 参数作为完整自由文本，不按词拆分；使用 spec 中固定提示词，非空参数以“补充要求：”分段追加，调用一次 `send_user_message`。 |
| permission | 零或一个参数；无参数显示模式和用法，否则使用明确映射切换，非法值不改变状态。 |
| status | 不接受参数；将 `CommandStatus` 格式化为中文状态，标注上下文估算量及最近一轮实际报告量，缺失值显示不可用。 |
| exit | 不接受参数；走已有退出清理，等待有限时长的记忆任务收尾、关闭 MCP，再退出。 |

`/permission` 对外参数 `accept_edits`、`bypass_permissions` 映射到既有内部值 `acceptEdits`、`bypassPermissions`，不修改配置文件值或权限枚举。`/do` 直接调用 `set_mode(DEFAULT)`，不再调用 `resume_do_mode()`；后者的独立服务兼容性不在本章改动。

提示词命令不临时放宽模式，不增加授权通道或工具权限。固定提示词属于提交给模型的任务约束，沿用普通对话的权限机制；不把提示词本身误称为新建的硬性权限隔离。

### `src/yucode/tui/command_ui.py`、`app.py`

`TextualCommandUI` 适配 `CommandUI`，将显示、清屏、历史重绘和确认委托给 `ChatApp`。构造时持有应用引用，类型注解使用 `TYPE_CHECKING`，避免循环导入。`ChatApp` 通过可选关键字参数接收注册表；直接构造时也必须在显示交互前构建并校验默认注册表。

回车入口先检测统一忙碌标记，再解析输入；空输入返回，否则同步置忙、禁用输入、创建取消信号后启动唯一交互 Worker，避免两个快速回车先后创建排队任务。Worker 内部根据解析结果发送普通消息或执行命令。现有 `_generating` 用于生成动画，新增 `_busy` 统一覆盖启动加载、命令、生成、审批、压缩、恢复和删除确认；快捷键也检查 `_busy`。

将当前 `generate` 和 `compact_context` 中的实际异步工作提取为可等待流程，删除它们各自开启互斥 Worker 的嵌套结构；外层 Worker 在 `finally` 中唯一负责恢复输入、清除取消信号和忙碌状态。Agent 完成事件只负责记录用量、结束消息和展示错误，不能提前解锁输入。

Ctrl+C 取消当前请求并拒绝尚未处理的工具审批；删除确认期间 Ctrl+C、Esc 和关闭弹窗都返回 `False`。快捷退出与退出清理保留既有行为，不将忙碌期间输入的命令排队。服务失败与取消均执行外层清理。

### `src/yucode/tui/command_widgets.py`、`widgets.py`、`app.tcss`

新增 `CommandMenu`、`CommandHint` 和 `SessionDeleteConfirm`。旧 `ModeMenu` 和只用于 `/resume` 的 `SessionPicker` 及其入口移除；本章 `/session list` 显示清单，恢复须输入 ID，不额外引入选中即恢复的行为。

Tab 仅在去除左侧空白后以 `/` 开始、命令名尚未出现分隔空白且光标位于该片段末尾时启用。补全时不对原编辑文本使用 `strip()` 判断参数位置，避免把 `/session ` 误判为命令名区域。单匹配替换为主名称并追加空格；多匹配弹出使用注册表数据的菜单，显示名称和简短描述。只在 Tab 请求后打开菜单，单独输入 `/` 再回车仍由解析器给帮助引导。

菜单打开时上下键移动，Enter 或鼠标选择仅填充，Esc 仅关闭且保留输入；修改文本时更新候选，进入参数区关闭菜单。参数提示从已匹配定义读取，不查询文件、会话或子操作候选。Shift+Tab 在空闲时照常切换权限。

删除弹窗显示 ID、标题、最近活动时间、消息数和不可恢复说明，默认焦点为取消；只有用户显式选择“确认删除”才返回 `True`。复用当前弹窗布局风格，调整窄终端尺寸，不额外引入组件依赖。

`ChatStatus` 使用固定模式标签映射，显示 `[DEFAULT]`、`[PLAN]`、`[ACCEPT_EDITS]`、`[BYPASS_PERMISSIONS]`。模式切换与刷新共用同一权限对象，不另存业务模式副本。

### `src/yucode/sessions.py`、`session_controller.py`

`SessionManager` 继续保存当前项目存档和活动 ID。`get_summary` 从原有恢复逻辑派生概要；合法空文件或仅含空白的文件返回空历史，标题“空会话”，时间使用现有 ID 时间戳。包含坏记录或未配对调用、且没有任何可恢复消息的文件仍报错，不能当作正常空会话。

所有读取、激活、删除和清理共用目标校验：ID 通过现有格式校验，存档目录及文件解析后仍在当前项目预期目录内，拒绝越界符号链接或目录联接。`delete_session` 自身再次检查目标非活动会话、文件存在且合法，然后只删除该单个存档；不接受目录删除或通配符。删除不使用 `missing_ok=True`，目标消失或文件系统错误都必须给出真实结果。

清单和恢复读盘可以通过 `asyncio.to_thread` 返回只读结果，在调用前刷新“正在读取”状态；线程不修改活动会话、不负责提交。新建、激活和单文件删除的短同步提交不跨 `await`；取消读盘后的结果不提交。列表仍按活动时间倒序，不可恢复坏存档沿用跳过策略，直接用 ID 恢复时明确报错。

删除流程为“完整参数校验 → 读取概要 → 确认 → 重新校验并删除 → 刷新状态”。用户取消后不调用删除服务。新建和恢复的具体提交顺序见下节。

### `src/yucode/agent.py`、`context.py` 与现有记忆接口

新增上述上下文快照和复位能力。`Agent.restore_session` 扩充为完整回退：开始前保存消息、估算锚点、压缩熔断状态和恢复提醒；使用新估算状态准备目标历史；压缩失败、取消或异常时全部恢复。成功后才设置目标会话的时间提醒与恢复保护。恢复空会话不请求摘要，也不添加无意义的历史时间提醒。

`SessionController` 再保护活动 ID 的提交边界，避免 Agent 恢复完成但存档激活失败时留下“历史来自 A、写入 B”的状态。所有历史替换均沿用不写存档的接口，不重复追加旧消息。

现有 `MemoryManager.schedule_update` 已复制本轮消息为元组，后台任务只写用户级或项目级记忆，不持有 Conversation 的追加回调，也不写会话存档。本章保留该机制，切换会话不等待或取消已有笔记任务；通过跨会话回归测试证明旧消息不会进入新存档、删除文件不会重建。项目级记忆更新仍是预期的共享持久知识。

### `src/yucode/cli.py` 与 Token 展示

CLI 在配置、Provider、存档创建和 MCP 启动前构建内置命令表；注册错误用中文输出后 `SystemExit(1)`，不创建新会话或进入界面。构建后的同一注册表传入 `ChatApp`，避免再次重复登记。

应用新增 `_current_turn_usage: Usage | None` 和 `_last_turn_usage: Usage | None`。收到 `UsageUpdated` 才记录当前轮数据，完成或取消时将其保存为最近一轮；全程无用量事件则保持不可用，不能把默认 `Usage()` 当成模型报告的零。一次用户请求内多次模型调用沿用 Agent 的累加用量；本章不重定义 Provider 数据精度。

`/status` 使用最近一轮报告量，缓存是否可用使用 `CacheUsage.available`；上下文估算通过 Agent 的公开查询转发至原有预算器。现有累计状态栏可保留但标为当前加载会话期间累计；新建或恢复成功时将累计及最近一轮数据一起重置，因存档没有用量事件，不从消息字符数伪造历史实际用量。清屏、切模式、查询、压缩不清除最近一轮数据。

## 模块交互与提交顺序

### 普通命令与提示词命令

1. 回车入口解析并同步占用 `_busy`，创建本次取消信号。
2. 命令分发器查注册表，处理器先验证全部参数。
3. 本地命令只调用服务和界面；提示词命令构造固定文本并等待与普通聊天相同的发送流程。
4. 对话历史只在 `Agent.run` 中追加一次展开后的用户消息。模型结果、工具审批和自动记忆仍走已有路径。
5. 外层 Worker 无论成功、失败或取消都统一释放输入；通知不写入 Conversation。

### 新建会话

1. 保存原活动 ID 和 Agent 状态；调用已有 `create_session()`，只有成功创建文件才会改变活动 ID。
2. 在同一同步提交段调用无 I/O 的 `Agent.reset_session_state()`：清空消息、估算锚点、熔断和恢复提醒。
3. 创建失败不执行第 2 步；复位若意外异常则恢复原活动 ID 和快照，新建但未投入使用的空存档可留在列表中，不冒充切换成功。
4. 成功后清空显示、重置用量、展示新 ID。权限对象、项目指令、MCP、记忆管理器保持原实例。

### 恢复会话

```text
校验 ID / 当前会话判断
  → 读取目标历史（此时不切换）
  → 保存原 Agent 状态
  → Agent.restore_session（暂载目标，必要时压缩）
       ├─ 失败 / 取消：完整恢复原状态
       └─ 成功：再次检查取消 → 激活目标存档
                    ├─ 激活失败：完整恢复原状态，原活动 ID 保留
                    └─ 提交成功：返回成功事件 → 替换显示 / 重置用量
```

协调层转发进度，但截留 Agent 的成功结束事件，必须先完成目标存档激活才向界面报告成功。临时替换到激活提交期间由 `_busy` 排除新生成，不会触发 Conversation 追加。界面在提交前保留旧历史；提交后的纯渲染错误应提示“会话已切换，显示刷新失败”，按当前已提交历史重绘，不宣称业务恢复失败或把活动 ID 偷偷回退。

### 失败与停止

手动压缩继续沿用“摘要完成后才替换历史”的实现；压缩前外置的大结果仍通过有效文件保留可读取内容。恢复中的压缩失败则由完整快照回退。取消信号在恢复准备前、必要摘要期间和最终提交前检查；只读后台扫描完成后发现取消时丢弃结果。确认弹窗取消不执行删除，单文件删除提交后不报告为已取消。

## 文件组织

```text
src/yucode/
├── commands/
│   ├── __init__.py             # 公共装配入口
│   ├── models.py               # 定义、类别、控制协议与执行上下文
│   ├── registry.py             # 校验、查找、公开清单与补全
│   ├── parser.py               # 空白、聊天与命令解析
│   ├── dispatcher.py           # 执行与错误转换
│   └── builtins.py             # 十条公开命令及隐藏退出命令
├── session_controller.py       # 新建与恢复的完整提交/回退
├── sessions.py                 # 空存档、概要、目标校验与删除
├── agent.py                    # Agent 状态快照、复位与恢复保护
├── context.py                  # 估算查询、估算器和熔断状态快照
├── cli.py                      # 最早构建注册表并失败退出
└── tui/
    ├── command_ui.py           # CommandUI 的 Textual 适配
    ├── command_widgets.py      # 命令菜单、提示与删除确认
    ├── app.py                  # 统一输入分流、忙碌控制与用量
    ├── widgets.py              # 模式标记、移除旧模式/恢复菜单
    └── app.tcss                # 菜单、参数提示和确认弹窗样式
tests/
├── test_commands.py            # 登记、解析、帮助、别名、参数与假界面
├── test_session_controller.py  # 新建、恢复、取消和提交失败回退
├── test_sessions.py            # 空会话、删除确认后的存档行为与边界
├── test_agent.py               # 完整恢复、提醒与后台记忆回归
├── test_context.py             # 估算及熔断复位、共享外置文件保留
├── test_cli.py                 # 注册冲突早于应用与服务启动
└── test_tui.py                 # Tab、回车、状态、忙碌与删除确认
README.md                      # 更新用户命令用法
doc/ch13/                      # 当前审批文档及后续验收记录
```

`conversation.py`、`memory.py` 和 `permissions.py` 优先复用现有公共接口，不为命令机制增加额外层次。测试中旧 `/resume` 菜单、直接选择即切模式、`/do` 恢复上一权限档位的断言更新为已批准的新行为，不能继续保留相互冲突的期待。

## 技术决策与验证策略

| 决策点 | 选择与理由 |
| --- | --- |
| 注册方式 | 显式不可变定义列表；比装饰器扫描更容易发现重复登记，启动顺序确定。 |
| 界面边界 | `Protocol` 加薄适配层；处理器可用假界面验证，不需要继承 Textual。 |
| 三类执行 | 同一异步处理契约、分类作为登记元数据；本地服务或提示词发送由明确处理器完成，不另建三套调度器。 |
| 参数处理 | 顶层保留完整参数，固定子操作局部严格拆分；足够覆盖当前命令，避免提前引入命令行语法。 |
| 会话切换 | 快照、准备、最终激活；复用现有 Agent 与上下文配置，失败时一起恢复消息及相关状态。 |
| 补全交互 | Tab 明确打开候选，Enter 先填入再提交；不会把命令菜单选择和业务执行混在一起。 |
| 状态来源 | 权限和历史从现有对象查询，Token 单独区分估算、报告量和缺失；不伪造持久化用量。 |
| 测试 | 纯逻辑用 pytest 与假界面，交互用现有 Textual `run_test`，最终用 tmux 真实模型端到端验收。 |

验证覆盖注册冲突、空白与参数边界、零意外模型请求、一次提示词提交、数据保留、会话原子切换、确认取消及失败恢复，不只检查内部方法是否被调用。测试数据使用临时项目、独立记忆目录和模拟 Provider；不得修改用户现有会话或依赖测试顺序。

本机已确认 Windows 有 `uv`，WSL Ubuntu 有 `/usr/bin/tmux`、`/usr/bin/python3`，并可找到 Windows PowerShell。后续先用 `uv run pytest` 验证单元与界面集成；端到端在 Ubuntu tmux 中承载 Windows PowerShell 启动同一 Windows 项目环境，保持现有 PowerShell 工具语义。tmux 会话能启动不等于终端交互已通过，必须实际验证键盘、渲染、真实请求与工具结果；若终端桥接不可用，记录实际原因后再选兼容方式，不用模拟请求冒充真实验收。本阶段仅检查环境，没有运行实现或测试。

## 需求覆盖

| Spec 需求 | 设计归属 | 验收关联 |
| --- | --- | --- |
| F1 统一登记与启动校验 | models、registry、CLI | AC1、AC6 |
| F2 解析与输入分流 | parser、dispatcher、ChatApp | AC2、AC3 |
| F3 执行类别与界面解耦 | CommandUI、处理器、统一发送入口 | AC4、AC5、AC19 |
| F4 帮助、别名与补全 | registry、help、CommandMenu / Hint | AC6、AC7 |
| F5 十条公开命令 | builtins、服务与界面适配 | AC3—AC11 |
| F6 会话管理 | SessionManager、SessionController、Agent 快照 | AC12—AC17 |
| F7 模式与状态联动 | CommandStatus、ChatStatus、统一权限对象 | AC10、AC11、AC13、AC15 |
| F8 忙碌、取消与失败 | 唯一交互 Worker、恢复回退、确认弹窗 | AC14、AC18 |
| N1—N5 非功能约束 | 共享登记、假界面、原有权限、异步反馈 | AC1、AC4、AC5、AC6、AC18、AC19 |
| 完整用户场景 | tmux 真实终端验收 | AC20 |
