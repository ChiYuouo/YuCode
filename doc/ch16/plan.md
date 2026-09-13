# 子 Agent 与后台任务 Plan

## 架构概览

本章以新的 `yucode.subagents` 包作为唯一的子 Agent 基础设施。它负责定义发现、角色解析、两类委派请求的解析、子 Agent 构建、非交互执行、后台任务、通知和调用链追踪。主 `Agent` 保持模型循环的唯一实现；子 Agent 也是普通 `Agent` 实例，只通过专用构造工厂获得隔离的会话、权限、上下文和受限工具视图。因此不会复制或分叉现有 Agent Loop。

统一的 `AgentTool` 被加入主 Agent 的系统工具集合。它以固定的工具名和固定 schema 暴露 `description`、`subagent_type`、`prompt`、`run_in_background` 四个字段；执行时交给 `SubagentService`，由服务解析为定义式或 Fork 式请求。角色清单只存在于工具参数的校验与系统提示说明中，不改变工具定义本身。

定义式创建空的 `Conversation`，用角色正文构造独立系统提示；Fork 创建父会话当前消息的不可变快照，并沿用父提示构造方式和工具排序。Fork 在快照之后仅追加子任务用户消息，所以首次请求保留父会话的完整可缓存前缀。两类子 Agent 都使用新的 `PermissionManager`、`ContextManager` 和 `Conversation`，但共享已创建的 Provider、HookEngine、受项目根限制的 ToolRegistry 与内存读取基础设施。

`TaskManager` 管理所有子 Agent 的 asyncio 任务。它保存任务生命周期、取消令牌、等待结果、120 秒前台计时、结果摘要和用量，并经 `TraceRegistry` 记录父子调用关系。任务结束时由 `TaskManager` 写入通知队列；主 Agent 在下一次模型请求前把队列内容转为临时 `task-notification` runtime message，而 TUI 同时显示通知。通知不写入主 `Conversation`，从而不污染持久对话历史。

工具过滤集中在 `ToolPolicy`：它从全局禁止项、角色 `tools`/`disallowedTools`、调用额外限制和后台白名单计算交集；子 Agent 的工具视图只含最终允许的工具。子 Agent 不能得到 `AgentTool`，构成默认的不可嵌套防线。最终执行仍走已有 `ToolExecutor` 与独立 `PermissionManager`，保留既有 Hook 和 TUI 确认流程。

## 核心数据结构

### `AgentDefinition` 与来源

```python
class AgentModel(str, Enum):
    INHERIT = "inherit"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"

@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    tools: frozenset[str] | None
    disallowed_tools: frozenset[str]
    model: AgentModel
    max_iterations: int
    permission_mode: PermissionMode
    prompt: str
    source: AgentSource

@dataclass(frozen=True)
class AgentSource:
    tier: Literal["plugin", "builtin", "user", "project"]
    path: Path
```

`tools=None` 表示不在定义层额外缩小工具集；非空集合表示定义层白名单。frontmatter 只接受 `name`、`description`、`tools`、`disallowedTools`、`model`、`maxIterations`、`permissionMode`，拒绝未知字段。`AgentCatalog` 保存按名称覆盖后的定义及各文件诊断。

### 委派请求与工具

```python
class SubagentKind(str, Enum):
    DEFINITION = "definition"
    FORK = "fork"

@dataclass(frozen=True)
class SubagentRequest:
    kind: SubagentKind
    prompt: str
    definition_name: str | None = None
    run_in_background: bool = False

class AgentTool(Tool):
    name = "Agent"
    async def execute(self, arguments: Mapping[str, Any], cancellation: Cancellation) -> ToolResult: ...
```

`subagent_type="fork"` 解析为 Fork；其他值按角色名解析为定义式。`run_in_background=true` 仅允许定义式显式后台；Fork 会忽略 false 并强制后台。`AgentTool` 由 `SubagentService` 创建，且只装配给允许委派的父 Agent。

### 任务、状态、通知与追踪

```python
class TaskStatus(str, Enum):
    PENDING = "pending"; RUNNING = "running"; BACKGROUND = "background"
    COMPLETED = "completed"; FAILED = "failed"; CANCELLED = "cancelled"; TIMED_OUT = "timed_out"

@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    parent_task_id: str | None
    kind: SubagentKind
    definition_name: str | None
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    summary: str | None
    error: str | None
    usage: Usage

@dataclass(frozen=True)
class TaskNotification:
    task_id: str
    status: TaskStatus
    summary: str
    usage: Usage

@dataclass(frozen=True)
class TraceNode:
    task_id: str
    parent_task_id: str | None
    kind: SubagentKind
    definition_name: str | None
    status: TaskStatus
    usage: Usage
    started_at: datetime | None
    ended_at: datetime | None
```

`TaskManager` 持有可变内部记录与 asyncio 任务，所有读取均返回不可变 `TaskSnapshot`。`TraceRegistry` 仅保存当前进程内的 `TraceNode`，通过父标识计算某节点的后代及聚合用量。通知队列仅由主 Agent 所属服务消费，消费后不会重复注入。

### 工具策略与隔离构建

```python
@dataclass(frozen=True)
class ToolPolicy:
    global_disallowed: frozenset[str]
    background_allowed: frozenset[str] | None

    def allowed_names(self, available: Iterable[ToolDefinition], definition: AgentDefinition | None,
                      *, background: bool, allow_delegation: bool) -> frozenset[str]: ...

class SubagentFactory:
    def create_definition(self, request: SubagentRequest, definition: AgentDefinition, parent: Agent) -> Agent: ...
    def create_fork(self, request: SubagentRequest, parent: Agent) -> Agent: ...
```

`ToolPolicy` 按“全局禁止 → 角色白名单 → 角色黑名单 → 后台白名单 → 禁止嵌套”依次收窄名称集合；每一层都只会移除工具。`SubagentFactory` 根据最终名称构建 `ToolRegistryView`，并以独立 `PermissionManager` 复制角色模式或父模式快照；不会复用父 Agent 的权限对象、会话、上下文或文件读缓存。

### 服务、运行器与事件

```python
class SubagentService:
    async def delegate(self, request: SubagentRequest, parent: Agent,
                       approve: ApprovalCallback | None) -> ToolResult: ...
    def promote_foreground(self) -> TaskSnapshot | None: ...
    def drain_notifications(self) -> tuple[TaskNotification, ...]: ...

class RunToCompletion:
    async def run(self, task_id: str, agent: Agent, prompt: str,
                  cancellation: Cancellation, approve: ApprovalCallback | None) -> TaskOutcome: ...
```

`RunToCompletion` 消费子 Agent 事件至 `AgentFinished`，只保留终止原因、最终文本、工具概要和总用量，不向父暴露私有事件流。`TaskManager` 在 `TaskStart`、`TaskStop`、`TaskComplete`、`SendMessage` 时通过扩展后的 `HookEvent` 触发 Hook；Hook 上下文带任务标识、父任务标识和状态。Hook 失败遵循既有 Engine 的“记录但不中断”语义。

## 模块设计

### `yucode.subagents.models`

**职责：** 定义 AgentDefinition、来源、请求、任务、通知、追踪节点、执行结果和生命周期枚举。

**对外接口：** 上述所有不可变数据对象与 `TaskStatus`、`SubagentKind`、`AgentModel`。

**依赖：** `permissions.PermissionMode`、`providers.base.Usage` 与标准库。

### `yucode.subagents.loader`

**职责：** 从插件、内置、用户、项目四层读取 `.md` 定义，切分并校验 frontmatter 与 Markdown 正文，生成按优先级覆盖的 `AgentCatalog`。

**对外接口：** `AgentDefinitionLoader(project_root, plugin_roots=(), user_root=None, builtin_root=None).discover() -> AgentCatalog`。

**依赖：** PyYAML、`models`、`PermissionMode`。插件根路径由应用组合层提供；当前没有已加载插件时传入空元组，接口为插件系统后续接入预留。

### `yucode.subagents.policy`

**职责：** 校验定义引用的工具名，计算最终工具集合，并从已有注册表生成只暴露允许工具的视图。

**对外接口：** `ToolPolicy`、`build_filtered_view(base_view, names)`。

**依赖：** `tools.base`、`models`。不执行工具、不处理权限。

### `yucode.subagents.runner`

**职责：** 将子 Agent 的事件流压缩成可回传的 `TaskOutcome`，识别完成、取消、轮次限制、模型错误和超时的终止状态。

**对外接口：** `RunToCompletion.run(...) -> TaskOutcome`。

**依赖：** `agent`、`cancellation`、`models`。

### `yucode.subagents.tasks`

**职责：** 创建、启动、等待、后台化、超时、取消及查询任务；维护通知队列和当前前台任务标识。

**对外接口：** `TaskManager.start(...)`、`wait_foreground(task_id, timeout_seconds=120)`、`promote(task_id)`、`cancel(task_id)`、`list()`、`info(task_id)`、`drain_notifications()`。

**依赖：** `asyncio`、`runner`、`TraceRegistry`、`HookEngine`。后台超时由每个任务自己的计时协程负责，完成时统一清理。

### `yucode.subagents.trace`

**职责：** 记录任务创建和状态变化，返回父子链与聚合 token 用量。

**对外接口：** `TraceRegistry.register()`、`update()`、`tree(task_id)`、`aggregate_usage(task_id)`。

**依赖：** `models`。

### `yucode.subagents.service` 与 `yucode.subagents.tool`

**职责：** 解析统一工具请求，构建隔离 Agent，选择前台/后台路径，并把任务结果映射为 `ToolResult`；为主 Agent 提供任务通知消费与 ESC 后台化入口。

**对外接口：** `SubagentService`、`AgentTool`。

**依赖：** `loader`、`policy`、`factory`、`tasks`、`trace`、`agent`、`tools.base`。

### `yucode.subagents.factory`

**职责：** 按定义式或 Fork 式规则创建子 Agent。定义式使用空对话与角色 prompt；Fork 使用父消息快照、同一提示前缀、同一工具排序，并只以新任务文本作为后续追加消息。

**对外接口：** `SubagentFactory`。

**依赖：** `agent`、`conversation`、`context`、`permissions`、`prompting`、`policy`。Provider 与 HookEngine 用已有实例；权限、上下文和会话新建。

### `yucode.agent`、`yucode.tools.executor` 与 `yucode.hooks`

**职责：** Agent 在每次构建模型请求前取出服务通知，作为 `RuntimeMessage` 添加；工具执行器支持注入系统 AgentTool 并让其获得当前的权限确认回调；Hook 事件与上下文扩展任务生命周期字段。

**对外接口：** `Agent` 新增可选 `subagent_service`；`HookEvent` 新增四项任务事件；`HookContext` 增加 task 字段。

**依赖：** `subagents.service`；保持循环和原有工具语义不变。

### `yucode.commands`、`yucode.skills` 与 `yucode.tui`

**职责：** 注册任务 Slash 命令；将现有 `skills.execution.run_fork` 收敛为对 `SubagentService` 的调用；TUI 展示任务通知与详情，ESC 优先尝试把当前可切换前台任务转后台，无法切换时保持原取消行为。

**对外接口：** `/tasks`、`/task info <id>`、`/task cancel <id>`；`ChatApp.show_task_notification()`、`ChatApp.action_background_generation()`。

**依赖：** `subagents.service`、`commands.models`。Skill 的原有摘要渲染由任务通知替代，不再保留独立 Fork 运行器。

### `yucode.config`、`yucode.cli` 与内置定义

**职责：** 解析子 Agent 的全局禁止工具、后台白名单和超时配置，创建全局唯一的 `SubagentService`，将其装配进主 Agent、命令和 TUI；提供三个内置 Markdown 定义。

**对外接口：** `AppConfig.subagents`、`SubagentConfig`。

**依赖：** `subagents` 包、`config`、`cli`。`src/yucode/subagents/builtin/` 中放置 Explore、Plan、general-purpose 定义。

## 模块交互

```text
项目 / 用户 / 内置 / 插件 agents/*.md
                  │
                  ▼
       AgentDefinitionLoader ──► AgentCatalog
                                      │
主 Agent 的固定 AgentTool ────────────┤
        │                             ▼
        └── subagent_type ──► SubagentService ──► ToolPolicy
                                          │              │
                                  ┌───────┴───────┐      ▼
                                  ▼               ▼  FilteredToolView
                           定义式 Factory      Fork Factory
                           空 Conversation     历史快照 + 任务消息
                                  └───────┬───────┘
                                          ▼
                                   TaskManager ───► RunToCompletion
                                          │                 │
                          TraceRegistry ◄──┘                 ▼
                                │                        Agent.run
                                ▼                             │
                     /tasks /task info /task cancel           ▼
                                          └──── TaskNotification 队列
                                                               │
                                      主 Agent 下次请求 + TUI ◄┘
```

前台定义式任务的时序如下：

1. `AgentTool` 创建任务并触发 TaskStart；工具执行等待该任务最多 120 秒。
2. 任务完成则工具结果携带摘要；满 120 秒或用户按 ESC 后，`TaskManager` 标记后台并立即返回任务标识给主 Agent。
3. 后台任务继续由 `RunToCompletion` 消费。结束后更新 Trace、触发 TaskStop/TaskComplete、入队 `TaskNotification` 并触发 SendMessage。
4. 主 Agent 下一次构造请求时消费通知；TUI 在收到服务回调后显示同一份中文通知。Fork 从第 1 步即为后台，不进入前台等待。

权限时序如下：过滤视图先拒绝不允许的名称；允许的调用进入子 Agent 的 `ToolExecutor`，其独立权限管理器判定是否可直接执行或请求 TUI 确认。这样后台白名单与角色限制不能被确认卡片突破，确认卡片也不会被子 Agent 绕过。

## 文件组织

```text
src/yucode/
├── subagents/
│   ├── __init__.py
│   ├── models.py
│   ├── loader.py
│   ├── policy.py
│   ├── factory.py
│   ├── runner.py
│   ├── tasks.py
│   ├── trace.py
│   ├── service.py
│   ├── tool.py
│   └── builtin/
│       ├── explore.md
│       ├── plan.md
│       └── general-purpose.md
├── agent.py                    # 注入 AgentTool、消费 task-notification
├── config.py                   # SubagentConfig 与配置校验
├── cli.py                      # 组合服务和运行时依赖
├── hooks/models.py             # 四类任务事件和上下文字段
├── commands/builtins.py        # /tasks、/task info、/task cancel
├── skills/execution.py         # 改为调用统一服务
├── skills/commands.py          # 移除专属 Fork 展示通道
└── tui/app.py                  # ESC 转后台、任务通知与任务命令上下文

tests/
├── test_subagent_loader.py
├── test_subagent_policy.py
├── test_subagent_service.py
├── test_task_manager.py
├── test_trace_registry.py
├── test_subagent_tool.py
└── test_subagent_tui.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 子 Agent Loop | 复用 `Agent.run`，外层用 `RunToCompletion` 消费事件 | 维持现有工具、Hook、上下文压缩和终止语义，避免两套循环漂移。 |
| Fork 缓存 | 历史快照保持原顺序与工具排序，任务文本最后追加 | 最大化可验证的请求前缀一致性，不假设供应商缓存计费实现。 |
| 运行时隔离 | 新建 Conversation、PermissionManager、ContextManager；共享 Provider、HookEngine、文件根与注册表底座 | 既隔离状态与用量，又不重复连接和安全基础设施。 |
| 角色加载 | 遍历 plugin → builtin → user → project 后以名称覆盖 | 自然得到要求的高优先级覆盖，并能报告每一来源的错误。 |
| 工具限制 | 预先生成只读过滤工具视图，而非只在执行器拒绝 | 模型看不到被禁止工具，执行时仍保留第二道校验。 |
| 嵌套 | 不把 AgentTool 装配到子 Agent 的工具视图 | 结构上防止无限递归，避免仅依赖提示词或运行时标志。 |
| 后台实现 | 单进程 asyncio Task + 内存 TaskManager | 与既有异步 TUI 匹配，满足本章不跨会话持久化的范围。 |
| 前台超时 | 工具等待 120 秒后仅转换展示与返回路径，不取消子协程 | 实现“自动切后台”，任务可以无缝继续。 |
| 通知 | 服务队列 + 主 Agent 临时 runtime message + TUI 同步展示 | 让模型能在后续请求看到完成结果，同时不写入持久历史或泄露私有过程。 |
| 生命周期事件 | 扩展现有 HookEvent/HookContext | 重用已验证的 Hook 分发与失败隔离逻辑，不引入第二个事件系统。 |
| Skill Fork 迁移 | 适配到 `SubagentService` | 消除现有独立 Fork 运行器与权限对象共享问题，使查询、限制、追踪一致。 |
