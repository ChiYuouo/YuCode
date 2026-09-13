# Agent Team 与 Coordinator Mode Plan

## 架构概览

新增 `yucode.teams` 作为团队领域的唯一入口。`TeamService` 负责团队创建、成员登记、后端选择、成员启动与恢复、消息派发、停止、收敛和删除；其他模块不得直接拼接团队目录、邮箱或成员会话路径。团队持久化根目录固定为当前项目的 `.yucode/teams/`，与项目一起隔离，不再读写用户级团队目录或旧 `.mewcode/teams/`。团队子目录严格使用 `TeamCreate` 收到的名称，例如团队 `demo` 保存到 `.yucode/teams/demo/`。

团队的共享任务与现有 `TaskManager` 分开：后者继续管理某个子 Agent 的 asyncio 运行状态和通知；新增的 `TeamTaskStore` 管理可持久化、可分配、带依赖的协作任务。两个系统通过成员完成通知关联，但不共享数据模型。

每个成员由 `MemberRuntime` 持有独立 Conversation、持久化 transcript、工具视图和明确工作目录。成员记录显式保存 `writable`；`TeamCreate` 未传该字段时使用 `false`。只读成员使用当前项目目录且工具视图不含写入能力；只有 `writable: true` 时才经现有 `WorktreeManager` 创建临时 Worktree。成员处于空闲或停止状态后，下一条消息通过 transcript 恢复同一 Conversation。tmux 与 iTerm2 后端启动一个新的 YuCode 成员进程；in-process 后端在当前 asyncio 事件循环中运行同等的 `MemberRuntime`，三者统一回调到 `TeamService`。

邮箱使用每位成员一个 JSONL 文件和同目录锁文件。`MailboxStore` 是唯一读写入口，负责名称注册、原子追加、过期锁接管、未读状态和消息协议。发送独立进程消息后，后端负责唤醒目标 pane；成员启动和每个回合前都会抽取未读邮件，生成运行时提醒注入模型请求。

主 Agent 构造时附加 Team Lead 工具。仅在某个 `MemberRuntime` 构造的工具视图中附加共享任务和 `SendMessage`；普通入口、普通子 Agent 和 Fork 均不注册这些工具。Coordinator Mode 在主 Agent 构造期由配置及环境变量共同判定，替换 Lead 工具可见范围并增加固定的四阶段运行时提示。

## 核心数据结构

### 团队模型

```python
class TeamBackend(str, Enum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in_process"

class MemberState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    STOPPED = "stopped"
    FAILED = "failed"

@dataclass(frozen=True)
class TeamMember:
    name: str
    agent_id: str
    role: str
    writable: bool
    workspace_root: Path
    worktree_slug: str | None
    backend: TeamBackend
    requires_approval: bool
    state: MemberState
    transcript_id: str | None
    backend_handle: str | None

@dataclass(frozen=True)
class AgentTeam:
    version: int
    name: str
    lead_id: str
    root: Path
    members: tuple[TeamMember, ...]
    created_at: datetime
    updated_at: datetime
```

团队名、成员名和 `agent_id` 使用独立值对象校验：只接受受限 ASCII 字符和长度，不接受路径分隔符、`.`、`..`、重复名称或跨团队引用。团队目录必须位于当前项目的 `.yucode/teams` 下；成员目录和 Worktree slug 均在每次读写前解析并验证仍位于其受控根目录中。加载成员时交叉验证 `writable`：只读成员必须指向项目目录且不得登记 Worktree，可写成员必须指向登记的受控 Worktree。

### 共享任务与消息模型

```python
class TeamTaskState(str, Enum):
    BLOCKED = "blocked"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

@dataclass(frozen=True)
class TeamTask:
    task_id: str
    title: str
    detail: str
    assignee: str | None
    dependencies: tuple[str, ...]
    state: TeamTaskState
    created_at: datetime
    updated_at: datetime

class MessageKind(str, Enum):
    TEXT = "text"
    BROADCAST = "broadcast"
    SHUTDOWN = "shutdown"
    PLAN_REQUEST = "plan_request"
    PLAN_DECISION = "plan_decision"
    REMINDER = "reminder"
    TASK_UPDATE = "task_update"

@dataclass(frozen=True)
class TeamMessage:
    message_id: str
    sender: str
    recipients: tuple[str, ...]
    kind: MessageKind
    body: str
    summary: str
    created_at: datetime
    read_by: frozenset[str]
    protocol: Mapping[str, str]
```

`PLAN_DECISION` 的 `protocol` 固定包含 `request_id` 与 `decision`（`approved` 或 `rejected`）。待审批成员只有收到自身未过期 `PLAN_REQUEST` 对应的 `approved` 决定，才可把相关任务改为 `IN_PROGRESS` 并获得写入执行许可。

### 后端、持久化与服务接口

```python
class TeamBackendDriver(Protocol):
    def is_available(self) -> BackendAvailability: ...
    async def start(self, member: TeamMember, request: MemberRequest) -> BackendHandle: ...
    async def wake(self, member: TeamMember) -> None: ...
    async def stop(self, member: TeamMember) -> StopResult: ...

class TeamRepository:
    def create(self, team: AgentTeam) -> AgentTeam: ...
    def load(self, name: str) -> AgentTeam: ...
    def save(self, team: AgentTeam) -> AgentTeam: ...
    def delete(self, name: str) -> None: ...

class TeamTaskStore:
    def create(self, team: AgentTeam, request: CreateTask) -> TeamTask: ...
    def update(self, team: AgentTeam, task_id: str, patch: TaskPatch) -> TeamTask: ...
    def list(self, team: AgentTeam) -> tuple[TeamTask, ...]: ...

class MailboxStore:
    def send(self, team: AgentTeam, message: OutgoingMessage) -> tuple[TeamMessage, ...]: ...
    def unread_for(self, team: AgentTeam, member: TeamMember) -> tuple[TeamMessage, ...]: ...
    def mark_read(self, team: AgentTeam, member: TeamMember, ids: Iterable[str]) -> None: ...

class TeamService:
    async def create(self, request: TeamCreateRequest) -> AgentTeam: ...
    async def spawn(self, team_name: str, member_name: str, prompt: str) -> TeamMember: ...
    async def send(self, team_name: str, sender: str, request: OutgoingMessage) -> SendResult: ...
    async def merge(self, team_name: str) -> TeamMergeResult: ...
    async def stop(self, team_name: str, member_name: str) -> StopResult: ...
    async def delete(self, team_name: str) -> DeleteResult: ...
```

`TeamRepository` 以临时文件替换写入 `team.json`；`TeamTaskStore` 以同样方式写入 `tasks.json`。`MailboxStore` 只以追加 JSONL 与锁文件操作邮箱，锁文件包含持有者随机令牌和创建时间；释放锁时必须核验令牌，防止删除后来获得的锁。

## 模块设计

### `teams/models.py`、`identity.py`、`repository.py`

**职责：** 定义团队、成员、消息、共享任务、后端状态和结果模型；校验不可信身份；持久化团队元数据。

**对外接口：** `TeamRepository` 的创建、读取、原子保存、列出和删除；`TeamIdentity` 的解析与受控目录解析。

**依赖：** 标准库 JSON、路径与时间；不依赖 Agent、TUI 或 Git。

### `teams/tasks.py` 与 `teams/mailbox.py`

**职责：** 管理团队共享任务、依赖状态转换及邮箱协议。创建或更新任务时检查依赖均存在、无自依赖和无环；根据所有前置任务是否完成在 `BLOCKED` 与 `READY` 间重算。邮箱按收件人落盘，广播展开为多个单播写入，读取后通过原子重写更新 `read_by`。

**对外接口：** `TeamTaskStore`、`MailboxStore`；面向成员的 `TaskCreate`、`TaskGet`、`TaskList`、`TaskUpdate`、`SendMessage` 工具。

**依赖：** `TeamRepository` 的安全路径与身份登记。现有 `TaskManager` 只接收成员运行完成/失败的通知，不被用作任务清单存储。

### `teams/backends.py` 与 `teams/member_runtime.py`

**职责：** 探测、选择和控制 tmux、iTerm2、in-process 后端；统一成员恢复与消息唤醒。

**对外接口：** `TeamBackendSelector.select(requested)`，以及各 `TeamBackendDriver` 的 `start/wake/stop`。

**依赖：** tmux 驱动使用受控参数调用 `tmux new-window/split-window/send-keys`；iTerm2 驱动仅在 macOS 检测 `osascript` 与 iTerm2 可用时启动/唤醒 pane；进程内驱动创建 asyncio Task。自动选择默认优先级为 tmux、iTerm2、in-process，并把完整候选检测结果返回给 Lead。显式选择不可用后端或自动选择没有候选，均返回失败而不启动成员。

在原生 Windows，iTerm2 驱动恒不可用，tmux 驱动只有 YuCode 运行在可调用 tmux 的 POSIX/WSL 环境时可用；因此本机原生 Python 通常选择 in-process。这是环境检测结果，不属于静默降级。每个成员启动的是同一套成员入口，入口从团队受控目录恢复 transcript、构造 Agent 并读取邮件；不允许子进程从未验证的参数取得团队路径。

### `teams/runtime.py`、`tools.py` 与 `prompts.py`

**职责：** 构造成员专用工具池、Lead 顶层工具与运行时通知，并实现审批门。

**对外接口：** Lead 使用 `TeamCreate`、`TeamDelete`、`TeamSpawn`、`TeamStop`、`TeamMerge`、共享任务工具和 `SendMessage`；成员使用共享任务工具和 `SendMessage`。`TeamCreate` 接收团队名和成员花名册，其中 `writable` 缺省为 `false`；`TeamSpawn` 将具体工作交给已登记成员。Lead 的团队提示明确要求：读取、搜索、解释、审查和总结任务传 `writable: false`，修改任务才传 `writable: true`。

**依赖：** 复用现有 `ToolRegistry.view_for`、`SubagentFactory` 的工具过滤机制和 `WorktreeManager`。成员视图先应用原有角色/后台限制，再追加只属于该团队的协作工具；普通主 Agent、普通定义式子 Agent 与 Fork 不追加。审批门在成员执行器前检查任务与邮件中的计划决定，不能仅依靠提示词约束。

### `teams/merge.py` 与 `teams/lifecycle.py`

**职责：** 管理成员 idle/stop/恢复，收敛 Worktree 分支，以及安全清理。

**对外接口：** `TeamMergeService.merge(team)` 和 `MemberLifecycle.idle/resume/stop/delete`。

**依赖：** 复用 `WorktreeManager` 获取已登记 Worktree 和现有安全删除保护。合并按共享任务依赖的拓扑顺序执行：每个成员分支先以无提交的合并方式在 Lead 目录试合并；Git 自动合并成功即提交到主分支。若发生文本冲突，仅对可证明为“双方在同一文件不同连续块追加、且三方基线完整”的情况执行确定性三方合并并重新校验；其他冲突立即 abort 本成员合并，保留成员 Worktree 和分支，报告冲突文件与下一步。任何本次收敛无法完成的成员不会留下半合并状态，已成功合并的先前成员保留并逐项报告。

成员完成回合后持久化 transcript 与 idle 状态，向 Lead 邮箱写 `TASK_UPDATE`。新邮件到达 idle/stopped 成员时先恢复其 transcript，再以 `REMINDER` 注入消息内容并启动后端；无法恢复时保留邮件未读、标记失败并通知 Lead。

### `teams/coordinator.py`、`config.py`、`agent.py` 与 `cli.py`

**职责：** 解析团队配置、判定 Coordinator Mode 并在应用装配点注册 Team 服务和工具。

**对外接口：** 新增 `teams` 配置块：`enabled`、`backend_priority`、`coordinator_enabled` 和邮箱锁超时；`CoordinatorPolicy.active(config, env)`。

**依赖：** Coordinator 仅在 `teams.coordinator_enabled: true` 且 `YUCODE_COORDINATOR=1` 时激活。激活后从 Lead 的工具视图移除 `WriteFile` 与 `EditFile`，保留 `ReadFile`、`FindFiles`、`SearchCode`、`RunCommand`、团队工具和只读技能工具；同时向请求追加 Research → Synthesis → Implementation → Verification 的运行时提示。成员不继承此收窄策略。`cli.py` 组装 `TeamService` 并传入主 Agent，`agent.py` 将 Team 事件与邮箱通知同现有任务通知一起注入请求。

### `commands/builtins.py` 与 `tui/app.py`

**职责：** 提供不发送给模型的 `/team` 本地命令和可见状态提醒。

**对外接口：** `/team list`、`/team info <名称>`、`/team delete <名称>`、`/team kill <团队>/<成员>`。

**依赖：** 调用同一个 `TeamService`，不重复后端、持久化或删除逻辑。TUI 周期内启动低频 Team 通知轮询，Lead idle 时出现消息、审批、完成或失败通知立即刷新显示。

## 模块交互

```text
Lead TeamCreate
  → TeamService → TeamRepository 创建 team.json
  → writable 缺省为 false，只读成员直接使用项目目录
  → 仅为 writable=true 的成员调用 WorktreeManager 创建隔离目录
  → BackendSelector 检测并选择后端
  → BackendDriver 启动 MemberRuntime
       → 成员专用工具视图（Task* + SendMessage）

成员 TaskUpdate / SendMessage
  → TeamTaskStore 或 MailboxStore（锁文件）
  → BackendDriver.wake(目标成员)
  → Lead 通知队列 → TUI / 下一次模型请求

空闲成员收到新消息
  → MailboxStore.unread_for
  → MemberRuntime 从 transcript 恢复 Conversation
  → REMINDER 注入 → 同一后端继续执行

Lead TeamMerge
  → 依赖排序 → TeamMergeService
  → Git 试合并 / 有限确定性冲突处理
  → 成功提交或 abort + 保留 Worktree + 中文结果

Coordinator 启动
  → 配置开关 AND YUCODE_COORDINATOR=1
  → 收窄 Lead 工具视图 + 注入四阶段提示
```

## 文件组织

```text
src/yucode/
├── teams/
│   ├── __init__.py
│   ├── models.py              # 团队、成员、任务、邮件与结果模型
│   ├── identity.py            # 团队/成员身份与路径安全校验
│   ├── repository.py          # team.json 原子持久化
│   ├── tasks.py               # 共享任务、依赖与状态转换
│   ├── mailbox.py             # JSONL 邮箱、锁、未读与协议
│   ├── backends.py            # tmux、iTerm2、in-process 探测与驱动
│   ├── member_runtime.py      # 独立成员入口、transcript 恢复与 idle
│   ├── runtime.py             # Lead/成员工具视图及审批门
│   ├── tools.py               # Team*、Task*、SendMessage 工具
│   ├── merge.py               # 分支收敛、有限冲突处理与回滚
│   ├── lifecycle.py           # spawn、stop、恢复、清理
│   ├── coordinator.py         # 双锁、工具收窄和四阶段提示
│   └── service.py             # 团队领域总入口
├── config.py                  # TeamConfig 及 YAML 校验
├── cli.py                     # TeamService 与成员入口装配
├── agent.py                   # Team 工具与通知注入
├── sessions.py                # 支持受控成员 transcript 目录/恢复
├── subagents/factory.py       # 组合成员工具视图
├── subagents/tasks.py         # 接收成员运行状态通知
├── commands/builtins.py       # /team
└── tui/app.py                 # 团队提醒生命周期

tests/
├── test_team_identity.py
├── test_team_repository.py
├── test_team_tasks.py
├── test_team_mailbox.py
├── test_team_backends.py
├── test_team_member_runtime.py
├── test_team_tools.py
├── test_team_merge.py
├── test_team_lifecycle.py
├── test_team_coordinator.py
├── test_team_commands.py
├── test_config.py             # 扩展 teams 配置解析
├── test_agent.py              # 扩展通知和工具可见性
└── test_cli.py                # 团队服务装配与成员入口
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 团队持久化根目录 | 当前项目 `.yucode/teams` | 与 YuCode 项目命名保持一致，让团队信息在项目资源管理器中可见，并保证不同项目天然隔离。旧用户级目录与旧 `.mewcode/teams` 都不扫描、不自动迁移，避免错误归属到当前项目。 |
| 错误路径清理 | 仅删除本轮验收生成的 `.mewcode/teams/demo` | 该目录是当前实现误生成的可确认测试数据；不删除 `.mewcode` 下其他未知内容，避免影响用户数据。 |
| 成员默认权限 | `writable` 缺省为 `false`；只读成员使用项目目录 | 读取和总结任务无需创建 Worktree，也不会意外获得修改能力。 |
| 可写成员隔离 | 仅 `writable: true` 时创建临时 Git Worktree | 避免并行写入互相覆盖，并使收敛有明确分支边界。 |
| 后端默认顺序 | tmux → iTerm2 → in-process，完整回报检测结果 | 保持图中顺序并优先强隔离；无可用后端明确失败。 |
| Windows 支持 | 原生 Windows 直接使用 in-process；tmux 仅在运行时确实可调用时可选；iTerm2 仅 macOS | 不伪装平台能力，也允许 WSL 中运行的 YuCode 使用 tmux。 |
| 邮箱格式 | 每成员 JSONL + 令牌化锁文件 | 追加友好、故障局部化，且可在进程间安全恢复。 |
| transcript | 每成员的受控会话目录，复用现有 JSONL Conversation 编解码与恢复校验 | 续写时保留真实上下文，避免重新 spawn 丢失协作历史。 |
| 共享任务 | 新建 `TeamTaskStore`，不复用 `TaskManager` 数据 | 运行任务与可分配工作项生命周期不同，混用会损坏现有 `/task` 语义。 |
| 审批执行 | 结构化消息关联请求 ID，并在执行器前硬性检查 | 提示词可被模型忽略，必须由程序保证“未批准不写入”。 |
| 冲突策略 | Git 自动合并 + 明确可证明安全的有限三方合并；其余 abort 并保留分支 | 不以猜测覆盖用户代码，同时尽可能自动完成简单冲突。 |
| Coordinator 双锁 | `teams.coordinator_enabled` 与 `YUCODE_COORDINATOR=1` 同时满足 | 配置代表项目允许，环境变量代表本次用户明确意图。 |
| Coordinator 工具限制 | 仅从 Lead 视图移除写文件工具，保留 Shell 和团队能力 | Lead 仍可执行 Git 收敛；成员不受影响，可完成代码实现。 |
