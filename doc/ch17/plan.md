# Git Worktree 隔离与子 Agent 集成 Plan

## 架构概览

本章新增 `yucode.worktrees` 作为唯一的 Worktree 领域入口。`WorktreeManager` 持有主仓库根目录，封装名称校验、Git 操作、环境初始化、会话持久化、当前目录选择和安全删除；其他模块不得自行拼接 `.yucode/worktrees` 路径或直接调用 `git worktree`。该包只管理 Git Worktree，不处理分支合并、跨目录同步或多 Agent 调度。

当前主会话使用 `WorkspaceRuntime` 管理“主仓库或当前 Worktree”的运行时资源。它按规范化绝对路径缓存系统提示项目指令、上下文管理器和项目记忆；每次 Agent 回合开始固定一个工作目录快照，以该快照建立工具视图并构造模型请求。工具目录通过 `ToolContext.root` 显式传递，整个流程不调用 `chdir`。

子 Agent 服务在创建定义式 Agent 时读取角色的 `isolation` 声明。需要隔离时先向 `WorktreeManager` 申请临时 Worktree，再用其绝对目录构造子 Agent；运行前以临时 `worktree_notice` 注入目录和分支信息。任务结束回调检查变更并清理安全的临时目录，或保留有保护变更的目录并将原因附入已有任务通知。

项目配置新增 Worktree 配置块；`.yucode/worktrees/session.json` 保存可恢复的当前目录和已登记 Worktree 元数据。CLI 在构造主 Agent 前处理 `--resume`，验证持久化状态后决定初始目录；Slash 命令调用同一个 Manager，因此命令和自动化路径具有相同的校验和保护语义。

## 核心数据结构

### `WorktreeConfig`

```python
@dataclass(frozen=True)
class WorktreeConfig:
    worktreeinclude: tuple[str, ...] = ()
    symlink_directories: tuple[str, ...] = ("node_modules", ".venv", "vendor")
    cleanup_after: timedelta = timedelta(days=7)
    cleanup_interval_seconds: float = 3600
```

对应 `yucode.yaml` 的 `worktrees` 配置。`worktreeinclude` 是从主工作目录复制到新 Worktree 的仓库内相对文件或目录列表；`symlink_directories` 是从主目录链接到新 Worktree 的大型依赖目录列表。解析阶段拒绝绝对路径、`..`、空路径、重复项和错误类型。

### `WorktreeSlug` 与路径边界

```python
@dataclass(frozen=True)
class WorktreeSlug:
    value: str
    parts: tuple[str, ...]

def parse_worktree_slug(value: str) -> WorktreeSlug: ...
def resolve_worktree_path(root: Path, slug: WorktreeSlug) -> Path: ...
```

仅接受 ASCII 字母、数字、`-`、`_` 与 `/`；总长度和每段长度均受上限约束。解析拒绝空段、`.`、`..`、反斜杠、绝对路径和其他字符。解析后的目标必须经 `resolve(strict=False)` 验证为 `root/.yucode/worktrees` 的后代；所有删除、复制、链接与 Git 路径入口均复用该函数。

### `WorktreeRecord`、`WorktreeSession` 与检查结果

```python
class WorktreeState(str, Enum):
    READY = "ready"; INITIALIZING = "initializing"; FAILED = "failed"; REMOVED = "removed"

@dataclass(frozen=True)
class WorktreeRecord:
    slug: str
    path: Path
    branch: str
    base_commit: str
    temporary: bool
    state: WorktreeState
    created_at: datetime
    last_used_at: datetime
    initialization_error: str | None = None

@dataclass(frozen=True)
class WorktreeSession:
    version: int
    active_slug: str | None
    records: tuple[WorktreeRecord, ...]

@dataclass(frozen=True)
class ChangeProtection:
    has_uncommitted_changes: bool
    has_unpushed_commits: bool
    inspection_error: str | None = None
```

会话存储于 `.yucode/worktrees/session.json`，使用临时文件替换实现原子写入。`base_commit` 记录创建时提交，用于没有上游分支时识别本地新增提交。`inspection_error` 表示无法安全判断状态，所有自动清理和非强制删除都将它视为受保护状态。

### `GitWorktreeClient`

```python
class GitWorktreeClient:
    def ensure_repository(self) -> None: ...
    def create(self, path: Path, branch: str) -> str: ...
    def list(self) -> tuple[GitWorktreeInfo, ...]: ...
    def inspect_changes(self, record: WorktreeRecord) -> ChangeProtection: ...
    def remove(self, path: Path, *, force: bool) -> None: ...
    def delete_branch(self, branch: str, *, force: bool) -> None: ...
```

所有 Git 子进程以主仓库或目标 Worktree 的绝对目录作为 `cwd`。创建使用 Git Worktree 机制及新分支，返回基准提交；列表使用机器可读输出而非解析面向人类的文本。检查分别读取工作区状态、上游关系与提交差异；任何命令失败均返回不可安全删除的检查结果，而非假定目录干净。

### `WorktreeManager`

```python
class WorktreeManager:
    def create(self, slug: str, *, temporary: bool = False) -> WorktreeRecord: ...
    def enter(self, slug: str) -> WorktreeRecord: ...
    def exit(self) -> None: ...
    def list(self) -> tuple[WorktreeView, ...]: ...
    def remove(self, slug: str, *, force: bool = False, automatic: bool = False) -> RemovalResult: ...
    def resume(self) -> ResumeResult: ...
    def current_root(self) -> Path: ...
    def cleanup_stale(self) -> tuple[CleanupResult, ...]: ...
```

创建分两条路径：目录不存在时执行 Git 创建、持久化 `INITIALIZING` 记录、完成初始化后转为 `READY`；目录已存在时只读取会话、文件系统和 Git 列表进行快速恢复，绝不调用会创建或修改 Git 状态的命令。初始化失败保留 `FAILED` 记录与目录，拒绝进入，便于用户诊断或显式移除。

`remove` 先解析名称并确认记录、路径与 Git Worktree 三者一致，再检查变更。`force=True` 只允许交互命令的显式传入；`automatic=True` 永远不得绕过保护，且只接受 `temporary=True` 的记录。删除 Worktree 目录与删除分支是两个显式步骤：默认只执行前者并把保留的分支名放入结果；仅 `delete_branch=True` 且用户已明确传入时，才在目录移除成功后执行强制分支删除。删除成功后才从会话移除记录；若删除的是当前目录，先退出至主目录。随后从已删除叶目录向上清理受控根内的空父目录，遇到 `session.json` 或受控根即停止。`cleanup_stale` 只调用 `automatic=True` 路径，形成统一的三层验证与失败关闭行为。

### `WorkspaceRuntime`

```python
@dataclass(frozen=True)
class WorkspaceResources:
    root: Path
    prompt_builder: SystemPromptBuilder
    context: ContextManager
    memory: MemoryManager
    skills: SkillRuntime

class WorkspaceRuntime:
    def current(self) -> WorkspaceResources: ...
    def for_root(self, root: Path) -> WorkspaceResources: ...
```

该对象以 `Path.resolve()` 的绝对字符串为缓存键。为每个目录延迟加载指令、项目记忆与 Skill，并为其创建独立 `ContextManager`；用户级记忆仍共享。它向 `ToolRegistry` 请求绑定同一绝对根目录的冻结视图，因此文件、命令、提示词和相关缓存不会跨 Worktree 泄漏。当前回合只使用启动时取得的 `WorkspaceResources`，避免命令切换造成同一回合内目录漂移。

### Agent 隔离扩展

```python
class AgentIsolation(str, Enum):
    NONE = "none"; WORKTREE = "worktree"

@dataclass(frozen=True)
class AgentDefinition:
    ...
    isolation: AgentIsolation = AgentIsolation.NONE

class SubagentService:
    async def delegate(...) -> ToolResult: ...
    async def _finish_isolated_task(self, task_id: str, outcome: TaskOutcome) -> TaskOutcome: ...
```

角色加载器接受且仅接受 `isolation: worktree` 或省略字段（默认 `none`）。定义式隔离任务以任务标识生成内部安全 slug，创建临时 Worktree，并把其路径作为 Factory 的明确根目录。Factory 为该 Agent 构造专属 `WorkspaceResources` 与工具视图；运行器在第一轮请求的 runtime messages 中追加 `worktree_notice`。完成回调由 Manager 决定清理、保留或失败原因，并把目录、分支及结果补充到最终摘要。

### 单次委派隔离覆盖

```python
@dataclass(frozen=True)
class SubagentRequest:
    kind: SubagentKind
    prompt: str
    definition_name: str | None = None
    run_in_background: bool = False
    isolation: AgentIsolation | None = None

class AgentTool(Tool):
    # schema 新增可选 isolation: "none" | "worktree"
    async def execute(...) -> ToolResult: ...
```

`AgentTool` 校验可选 `isolation` 是 `none` 或 `worktree`，再写入 `SubagentRequest`；不存在、非字符串或未知值均在创建任务前返回中文参数错误。`SubagentService` 计算 `effective_isolation = request.isolation or definition.isolation`，只有最终值为 `worktree` 时创建隔离目录。这样项目角色可保留默认策略，用户或主 Agent 在某一次委派中可明确覆盖；工具描述与主 Agent 系统提示同时列举“隔离执行、使用 Worktree、不影响主目录”应传该字段的规则。

## 模块设计

### `yucode.worktrees.models`、`slug` 与 `session`

**职责：** 定义 Worktree 值对象、名称校验、路径边界、JSON 会话编码/解码和原子保存。

**对外接口：** `parse_worktree_slug()`、`resolve_worktree_path()`、`WorktreeSessionStore.load()`、`save()`。

**依赖：** 标准库 `pathlib`、`json`、`datetime`；不依赖 Git、Agent 或 TUI。

### `yucode.worktrees.git`、`setup` 与 `manager`

**职责：** 将受验证的 Git 调用集中到 Client；按配置完成复制、hooks 与软链接；协调完整生命周期、会话、变更保护、快速恢复与清理。

**对外接口：** `GitWorktreeClient.delete_branch()`、`WorktreeInitializer.initialize()`、`WorktreeManager.remove(..., delete_branch=False)`。

**依赖：** `models`、`slug`、`session`、`config`。初始化器只接收已验证的主目录、目标目录和相对规则，复制与链接前再次验证源、目标边界。

### `yucode.worktrees.runtime`

**职责：** 以绝对目录隔离并缓存项目指令、上下文、记忆和 Skill 资源；为主 Agent 与子 Agent 提供固定目录资源。

**对外接口：** `WorkspaceRuntime.current()`、`for_root()`。

**依赖：** `WorktreeManager`、`InstructionLoader`、`ContextManager`、`MemoryManager`、`SkillLoader`、`ToolRegistry`。不写入 Git 或会话状态。

### `yucode.tools.registry`、`agent`、`prompting`、`tui`

**职责：** 让工具目录按明确根目录生成 `ToolView`；让 Agent 在每个回合绑定 `WorkspaceResources`，并使用该资源构建工具、系统提示、上下文和记忆；TUI 状态与欢迎信息显示 Agent 当前根目录，而非 `Path.cwd()`。

**对外接口：** `ToolRegistry.view_for(root)`、`Agent.workspace_root`、Agent 内部请求构造中的 `worktree_notice` runtime message。

**依赖：** `WorkspaceRuntime`。文件工具、命令工具和权限执行器继续只从 `ToolContext.root` 取得目录，保持原有权限边界。

### `yucode.subagents.*`

**职责：** 解析角色默认 `isolation` 与工具调用临时覆盖值，为最终需要隔离的定义式 Agent 分配 Worktree，令 Factory 使用该目录，任务结束后调用统一的安全清理。

**对外接口：** `AgentIsolation`、扩展后的 `AgentDefinitionLoader`、`AgentTool(isolation=...)`、`SubagentRequest.isolation`、`SubagentFactory.create_definition(..., workspace_root=...)` 与 `SubagentService`。

**依赖：** `WorktreeManager`、`WorkspaceRuntime`、已有 `TaskManager`。Fork 式 Agent 未声明角色定义，不自动隔离，维持现有行为。

### `yucode.config`、`cli` 与 `commands.builtins`

**职责：** 解析 `worktrees` 配置，处理 `--resume`，在主运行时组合 Manager/Runtime/Cleanup 服务，并注册 `/worktree` 命令。

**对外接口：** `WorktreeConfig`、`parse_cli_args()`、`/worktree list|create|enter|exit|remove [--force] [--delete-branch] <名称>`。

**依赖：** `worktrees`、`Agent`、命令上下文。命令全部为 `LOCAL`，不会写入模型对话；命令从 Agent 暴露的 Manager 获取当前状态。

## 模块交互

```text
yucode.yaml ──► WorktreeConfig ─────────────────────┐
                                                       ▼
主仓库 + .yucode/worktrees/session.json ──► WorktreeManager ──► GitWorktreeClient
                                                   │       │
                                                   │       └──► WorktreeInitializer
                                                   ▼
                                         WorkspaceRuntime（绝对路径键）
                                                   │
CLI --resume ──────────────────────────────────────┼──► 主 Agent / ToolRegistry.view_for(root)
/worktree 命令 ─────────────────────────────────────┘

AgentDefinition 默认 isolation + AgentTool 单次 isolation 覆盖
                    │
                    ▼
SubagentService ──► WorktreeManager.create(temporary=True)
                    │              │
                    │              └──► WorkspaceRuntime.for_root(worktree)
                    ▼
            SubagentFactory ──► 子 Agent + worktree_notice
                    │
                    ▼
             TaskManager 完成回调 ──► Manager.remove(automatic=True)
                                      └──► 保留原因写入 task-notification
```

手动创建的关键顺序为：解析安全 slug → 确认当前仓库 → 计算并验证目标路径 → 若已存在则只读快速恢复；否则 Git 创建 → 写入初始化中记录 → 初始化目录 → 写入就绪记录。任一步失败均保留可诊断记录，且不会把失败目录设为当前目录。

自动清理的关键顺序为：枚举会话中的过期临时记录 → 验证登记、受控根目录与 Git 列表一致 → 检查未提交变更与未推送提交 → 仅在全部检查成功且干净时删除。任何检查异常或不一致均写入跳过结果，不删除目录。

## 文件组织

```text
src/yucode/
├── worktrees/
│   ├── __init__.py
│   ├── models.py              # 记录、状态、检查与展示值对象
│   ├── slug.py                # slug 与受控路径验证
│   ├── session.py             # session.json 原子读写与恢复
│   ├── git.py                 # Git Worktree 命令与状态检查
│   ├── setup.py               # worktreeinclude、hooks、软链接初始化
│   ├── manager.py             # Create/Enter/Exit/List/Remove/Cleanup
│   ├── runtime.py             # 绝对路径键的项目运行时资源
│   └── cleanup.py             # 定时清理生命周期封装
├── config.py                  # WorktreeConfig 与 worktrees 配置解析
├── cli.py                     # --resume、Manager/Runtime 组装与生命周期
├── agent.py                   # 每回合固定 WorkspaceResources、通知注入
├── tools/registry.py          # view_for(root)
├── commands/builtins.py       # /worktree
├── tui/app.py                 # 当前目录和清理服务生命周期
├── tui/widgets.py             # 不再直接显示 Path.cwd()
└── subagents/
    ├── models.py              # AgentIsolation 与 AgentDefinition 扩展
    ├── loader.py              # isolation frontmatter 校验
    ├── factory.py             # 显式 workspace_root 的子 Agent 构建
    ├── service.py             # 自动创建、notice、结束清理
    └── tasks.py               # 支持任务完成后的隔离收尾回调

tests/
├── test_worktree_slug.py
├── test_worktree_session.py
├── test_worktree_git.py
├── test_worktree_setup.py
├── test_worktree_manager.py
├── test_worktree_runtime.py
├── test_worktree_cleanup.py
├── test_worktree_commands.py
├── test_subagent_loader.py    # isolation 解析与非法值
├── test_subagent_service.py   # 隔离创建、notice、保留/清理
├── test_config.py             # worktrees 配置
└── test_cli.py                # --resume 组合与无效恢复
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 受控目录 | 固定为 `<仓库>/.yucode/worktrees/` | 与 YuCode 现有项目状态目录保持一致，且可由 `.gitignore` 排除。 |
| 输入安全 | 独立 `WorktreeSlug` 值对象，所有路径入口二次验证 | LLM、Slash 命令和会话文件都是不可信边界，不能只在创建时校验。 |
| 快速恢复 | 已存在目录只读取会话、文件系统和 Git 列表 | 防止恢复操作意外新建分支、覆盖目录或修改 Git 状态。 |
| 删除保护 | 默认拒绝未提交、未推送或检查失败；仅手动 `--force` 可绕过；删除分支必须额外显式指定 | 将“清理目录”和“丢弃提交”分开，保留用户的 merge 或检查选择；自动清理始终失败关闭。 |
| 未推送判定 | 上游差异优先；无上游时相对创建基准提交检查 | 新建 Worktree 分支通常无上游，不能误判为安全可删。 |
| 初始化规则 | `worktreeinclude` 负责复制，`symlink_directories` 负责复用大型目录 | 区分独立运行文件与不应复制的大型依赖，规则可审计。 |
| 工作目录传递 | `ToolRegistry.view_for(root)` + `ToolContext.root`，不使用 `chdir` | 同一进程可并存多个 Agent/Worktree，避免全局状态竞争。 |
| 路径缓存 | `WorkspaceRuntime` 以规范化绝对路径为键 | 无需在 enter/exit 清空缓存，也不会把相对路径的内容混到另一目录。 |
| 会话格式 | `.yucode/worktrees/session.json` 原子持久化 | 与当前项目状态一致、可在启动前读取，且避免改写用户 API 配置。 |
| 子 Agent 隔离 | 定义式角色默认 `isolation`，单次 Agent 调用可显式覆盖；Fork 保持原行为 | 角色可声明稳定默认值，同时满足用户在某一次任务中明确要求隔离的需求。 |
| 定时清理 | TUI 生命周期内的 asyncio 后台任务，启动先执行一次 | 与现有单进程异步架构契合，退出时可取消，不引入常驻外部服务。 |
