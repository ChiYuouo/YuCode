# Skill 能力包 Plan

## 架构概览

本章在既有 `ToolRegistry`、`SystemPromptBuilder`、`Agent` 和命令分发层之上增加 `skills` 包。该包负责从三层目录发现和校验 Skill、维护热更新后的激活快照、构造每轮所需的提示与工具视图、执行目录 Skill 的专属工具、从 URL 原子安装能力包，并协调独立模式的隔离 Agent。

Skill 的可变状态不写入 `Conversation`、会话存档或静态命令定义。主 Agent 在请求模型前从 Skill 运行时取得当前快照：未激活时得到可用名称与说明及系统工具；激活后得到最醒目的 SOP 模块、建议工具提示和收窄后的工具视图。这样同一轮的模型请求、工具调用和热更新检测共享一个确定的快照，下一轮才采用新状态。

工具层从“全局注册表直接作为当前可用工具”拆分为“全局工具目录 + 每次请求的工具视图”。内置工具和已连接 MCP 工具继续放在全局目录；Skill 专属脚本工具、Skill 资源读取工具和系统级 `LoadSkill`/`InstallSkill` 只在视图中按规则出现。执行器也接收该视图，因此模型即使猜到被白名单隐藏的工具名称，也会得到未知工具结果，不能绕过收窄。

共享模式沿用当前主 Agent 的正常循环。独立模式由 `SkillForkRunner` 创建不带会话记录器的隔离 `Conversation` 与 Agent，只复制声明允许的主历史，并使用启动时冻结的 Skill、工具和模型快照；完成后把由运行记录生成的一条摘要回流主历史。隔离对话的中间消息和工具结果不写入主会话。

斜杠命令层保留已有内置命令表，并用实时命令目录叠加 `/skill` 和当前已激活 Skill 的 `/skill:<名称>`。这满足自动注册、Tab 补全和热更新，而不会同已有 `/review` 发生冲突。

## 核心数据结构

### `SkillDefinition`、`SkillSource` 与 `SkillCatalog`

```python
class SkillMode(str, Enum):
    INLINE = "inline"
    FORK = "fork"

@dataclass(frozen=True)
class HistoryScope:
    kind: Literal["none", "recent", "all"]
    turns: int = 0

@dataclass(frozen=True)
class SkillSource:
    tier: Literal["project", "user", "builtin"]
    package_root: Path
    entry_path: Path
    fingerprint: str

@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    allowed_tools: frozenset[str]
    mode: SkillMode
    history_scope: HistoryScope
    model: str | None
    sop: str
    source: SkillSource
    private_tools: tuple[SkillToolManifest, ...] = ()
    resource_root: Path | None = None

@dataclass(frozen=True)
class SkillCatalog:
    definitions: Mapping[str, SkillDefinition]
    diagnostics: tuple[SkillDiagnostic, ...]
```

`SkillDefinition` 是已经解析并验证格式的不可变能力描述。frontmatter 固定解析 `name`、`description`、`allowedTools`、`mode`、`history` 和可选 `model`；`mode` 只允许 `inline` 或 `fork`，`history` 只允许 `none`、`all` 或正整数 `recent`。正文中的唯一参数占位符定为 `$ARGUMENTS`，由加载或短命令参数替换为空字符串或原始参数文本。

发现目录固定为 `<工作区>/.yucode/skills/`、`%APPDATA%/YuCode/skills/` 和随包发布的内置目录。每层可包含顶层 `*.md` 单文件 Skill，或包含 `SKILL.md` 的一级目录能力包；目录包可选 `prompt.md`，其内容按顺序附加在 `SKILL.md` 正文之后。目录包中的 `tool.json`、实现脚本和 `references/` 均必须解析后位于能力包根目录内。按内置、用户、项目顺序发现并以名称覆盖，最终只暴露最高优先级定义。

### `SkillToolManifest` 与 `SkillTool`

```python
@dataclass(frozen=True)
class SkillToolManifest:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    safety: ToolSafety
    command: tuple[str, ...]
    package_root: Path

class ScriptSkillTool(Tool):
    @property
    def definition(self) -> ToolDefinition: ...
    @property
    def safety(self) -> ToolSafety: ...
    async def execute(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext,
        call_id: str, cancellation: Cancellation,
    ) -> ToolResult: ...
```

目录包的 `tool.json` 使用一个工具或工具列表，每项要求名称、说明、JSON 输入 schema、安全级别和非空命令数组。命令参数中的脚本路径只能指向包内文件；执行时使用无 shell 的子进程、工作目录仍是当前项目根目录，标准输入传入 UTF-8 JSON 参数对象，标准输出必须返回包含成功状态、摘要和可选正文的 JSON 对象。取消、超时、不可执行脚本和无效输出都转换为中文结构化失败结果。`safety` 继续交给既有权限管理器判定，不向第三方脚本授予特殊权限。

`SkillReferenceTool` 是目录包自动提供的只读专属工具。它接受已激活 Skill 名称和相对资源路径，只读取该包 `references/` 内的 UTF-8 文本，并拒绝绝对路径、父目录、符号链接越界和二进制/超大资源。这样用户层和内置层的参考资料不会误被普通工作区文件工具读取。

### `ActiveSkill`、`SkillSnapshot` 与 `SkillRuntime`

```python
@dataclass(frozen=True)
class ActiveSkill:
    definition: SkillDefinition
    arguments: str

@dataclass(frozen=True)
class SkillSnapshot:
    catalog: SkillCatalog
    active: tuple[ActiveSkill, ...]
    tools: "ToolView"
    prompt: "SkillPromptState"

class SkillRuntime:
    def initialize(self, global_tools: ToolCatalog) -> tuple[SkillDiagnostic, ...]: ...
    def refresh(self) -> SkillRefreshResult: ...
    def snapshot(self, mode: PermissionMode) -> SkillSnapshot: ...
    def load(self, name: str, arguments: str) -> ActiveSkill: ...
    def clear_active(self) -> None: ...
    def active_items(self) -> tuple[ActiveSkill, ...]: ...
    async def install(self, url: str, context: ToolExecutionContext) -> InstallResult: ...
```

`SkillRuntime` 是主对话唯一的可变 Skill 状态所有者。`initialize` 在 MCP 连接结束后发现三层目录，并对所有可发现的有效 Skill 执行严格的 `allowedTools` 与专属工具名称校验；任何引用未知名称的有效 Skill 均抛出启动错误，界面不进入可输入状态。每个损坏文件仅产生诊断且不参与该校验。

`refresh` 不使用后台文件监听，而是在每次模型请求、系统工具调用和命令查询前重新计算文件指纹。新增、覆盖、删除及有效修改立即成为下一快照；已激活定义若当前文件暂时解析失败，则保持最后一次有效快照并产生一次诊断。若文件删除或被高优先级有效定义覆盖，则用新有效定义替换；没有替代时解除激活。正在执行的主/隔离任务持有自己的 `SkillSnapshot`，不会读取刷新后的对象。

### `ToolCatalog`、`ToolView` 与 `ToolExecutionContext`

```python
class ToolCatalog(Protocol):
    @property
    def context(self) -> ToolContext: ...
    def all_definitions(self) -> tuple[ToolDefinition, ...]: ...
    def get(self, name: str) -> Tool | None: ...

class ToolView(ToolCatalog):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...
    @property
    def read_only_definitions(self) -> tuple[ToolDefinition, ...]: ...

@dataclass(frozen=True)
class ToolExecutionContext:
    root: Path
    approval: ApprovalCallback | None = None
    authorization: TaskAuthorization | None = None
```

现有 `ToolRegistry` 成为全局 `ToolCatalog`：仍负责内置工具和 MCP 工具的注册、名称冲突检查与工作目录。`ToolView` 由某一 `SkillSnapshot` 构造，冻结名字到工具实现的映射。无激活 Skill 时包含原有全部普通工具及系统工具；有激活 Skill 时只包含所有 `allowedTools` 的并集、已激活目录包的专属工具与资源工具，以及系统工具。随后再按计划模式筛成只读视图；`LoadSkill` 因为只改变内存激活状态可在计划模式可用，`InstallSkill` 因文件写入而仍遵循计划模式和权限限制。

执行器改为每次执行显式接收当前 `ToolView` 和 `ToolExecutionContext`。所有既有文件、命令与 MCP 工具只读取 `root`，行为不变；系统工具可使用同一次调用的审批回调，在独立 Agent 内继续显示和等待既有权限确认。

### `SkillPromptState` 与 `SystemPromptBuilder`

```python
@dataclass(frozen=True)
class SkillPromptState:
    available: tuple[tuple[str, str], ...]
    active: tuple[ActiveSkill, ...]
    suggested_tools: tuple[str, ...]

class SkillPromptSource(Protocol):
    def prompt_state(self) -> SkillPromptState: ...

class SystemPromptBuilder:
    def build(
        self, context: RuntimeContext, tools: Sequence[ToolDefinition],
        history: Sequence[Message], skill_state: SkillPromptState | None = None,
    ) -> ModelRequest: ...
```

没有激活 Skill 时，稳定提示只注入“可用 Skill：名称 + 一句话说明”和调用 `LoadSkill` 的说明，不包含 SOP、目录资源或专属工具。激活后，`active_skills` 模块置于基础系统约束之后、普通项目自定义指令之前，逐项显示名称、替换后的完整 SOP 与参数；同一 Skill 重载时替换旧项。该模块与实际工具定义共同参与提示缓存键，确保白名单或 SOP 更新不会错误复用旧缓存。

运行期补充消息还显示当前白名单并以“建议工具”标识。只有本轮 `ToolView` 中的普通工具会送给 Provider；`LoadSkill` 和 `InstallSkill` 的描述明确它们是系统级能力。`SystemPromptBuilder` 提供克隆接口，使隔离 Agent 复用项目指令、长期记忆与基础约束，但使用隔离快照，绝不读取主对话之后的动态状态。

### `SkillSystemTool` 与 `SkillForkRunner`

```python
class LoadSkillTool(Tool):
    async def execute(..., context: ToolExecutionContext, ...) -> ToolResult: ...

class InstallSkillTool(Tool):
    async def execute(..., context: ToolExecutionContext, ...) -> ToolResult: ...

@dataclass(frozen=True)
class ForkResult:
    status: Literal["completed", "failed", "cancelled"]
    summary: str
    usage: Usage

class SkillForkRunner:
    async def run(
        self, active: ActiveSkill, parent_messages: Sequence[Message],
        snapshot: SkillSnapshot, cancellation: Cancellation,
        approve: ApprovalCallback | None,
    ) -> ForkResult: ...
```

`LoadSkillTool` 接收 `name` 与可选 `arguments`。它先刷新并激活定义，成功后为 inline Skill 返回“已加载、下一轮遵循 SOP”的结构化结果；fork Skill 则以已冻结的激活定义、声明的历史范围和本轮审批回调交给 `SkillForkRunner`，将隔离运行完成后的摘要作为工具结果返回父 Agent。两者都保留激活状态，因此 `/skill` 可见并能再次执行。加载错误不改动已有激活集。

`InstallSkillTool` 接收单个 `http` 或 `https` URL，是副作用工具。它仅下载用户已明确提供或授权的 URL，支持单个 Markdown Skill 或 zip 格式目录包；下载限制大小和重定向次数，解压时拒绝绝对路径、父目录、符号链接和超限文件数。安装先在用户 Skill 根目录的临时目录校验完整能力包，再以同卷原子移动提交。若用户层同名 Skill 已存在，安装拒绝覆盖并说明原因。安装失败清理临时目录，原有定义不变；成功后刷新目录并返回安装名称。

`SkillForkRunner` 以不带 recorder 的 `Conversation` 建立子 Agent。`HistoryScope` 为 `none` 时不复制消息；为 `recent` 时复制最近完整的 N 轮用户发起单元及其相关模型/工具消息；为 `all` 时复制主历史全部消息。它使用指定模型或主配置模型、新建的 Provider、冻结工具视图和隔离的 Skill prompt source。运行中只累计状态、最终文本与每个工具的成功/失败/目标概要，不把任何事件转写入主会话。结束时生成固定中文摘要（状态、主要结果、可见副作用、失败原因和下一步），由父工具结果或短命令协调器回流；指定模型无法创建时直接返回失败摘要，不静默降级。

### 动态命令目录与 UI 协调

```python
class SkillCommandCatalog:
    def get(self, name: str) -> CommandDefinition | None: ...
    def visible(self) -> tuple[CommandDefinition, ...]: ...
    def complete(self, prefix: str) -> tuple[CommandDefinition, ...]: ...

class SkillCommandController(Protocol):
    async def execute_skill(self, active: ActiveSkill, arguments: str) -> None: ...
    async def show_active_skills(self) -> None: ...
```

`SkillCommandCatalog` 将不可变内置 `CommandRegistry` 与 `SkillRuntime.active_items()` 合并。新增公开 `/skill`：无参数时列出激活项的名称、说明、模式与参数；其他参数显示用法。每个激活 Skill 生成名字为 `skill:<name>` 的动态命令定义，供解析、帮助和 Tab 补全共用；未激活的 `skill:` 输入由分发器拦截并给出加载引导，绝不发送给模型当普通聊天。

`/skill:<name>` 每次先刷新并重取定义：inline 方式先更新激活参数，再将用户调用作为正常主对话任务送入 `Agent.run`；fork 方式通过 `SkillForkRunner` 运行，聊天区只显示和主会话只记录一条回流摘要。`/clear` 的处理器在清空显示后调用 `SkillRuntime.clear_active()`；`/session new` 及成功的 `/session resume` 通过 `Agent.reset_session_state`/恢复完成钩子清除激活状态。历史恢复失败不改变当前激活集。

`ChatApp` 保持一个 `_busy` 工作单元。它把 Skill 命令的共享执行复用现有生成 UI，把独立执行显示为“正在独立执行”，仅在完成时挂载摘要；隔离子 Agent 的权限请求仍复用现有卡片。MCP 加载完成后才初始化 Skill：若白名单启动校验失败，显示错误、关闭 MCP 并退出，输入框始终禁用；普通单文件解析警告则和既有启动警告一起展示。

## 模块设计

### `src/yucode/skills/models.py`、`loader.py`、`runtime.py`

**职责：** 定义 Skill 领域对象；解析单文件与目录包、控制三层优先级和安全边界；维护热更新与已激活快照。

**对外接口：** `SkillLoader.discover()` 返回 `SkillCatalog`；`SkillRuntime.initialize()`、`refresh()`、`load()`、`snapshot()`、`clear_active()` 和 `active_items()` 供 CLI、Agent、命令和系统工具调用。

**依赖：** `PyYAML`、`pathlib`、工具目录抽象。只依赖工具的公共模型，不依赖 TUI、Provider 或命令实现。

### `src/yucode/skills/tools.py`、`references.py`、`install.py`

**职责：** 将 `tool.json` 转为受控的脚本工具，读取能力包参考资料，提供系统级加载和安装工具，并形成 `ToolView`。

**对外接口：** `build_tool_view()`、`LoadSkillTool`、`InstallSkillTool`、`ScriptSkillTool`、`SkillReferenceTool` 与 `SkillInstaller.install()`。

**依赖：** `skills.models/runtime`、工具协议、`httpx`、既有取消与权限协议。脚本、解包、参考资料都经路径和大小校验；不导入 Agent。

### `src/yucode/skills/prompt.py`、`execution.py`

**职责：** 把 Skill 快照变为最高优先级提示模块和建议工具文本；创建独立模式的隔离 Agent 并汇总结果。

**对外接口：** `SkillPromptState`、`SkillPromptSource`、`SkillForkRunner.run()`、`select_history()`。

**依赖：** `Agent` 的公开构造/事件、Provider 工厂、`SystemPromptBuilder` 克隆接口、冻结的工具视图。子 Agent 不接收 `MemoryManager` 和会话 recorder。

### `src/yucode/tools/base.py`、`registry.py`、`executor.py`

**职责：** 保留全局工具目录，并支持按请求执行冻结的 `ToolView`。

**对外接口：** 新增 `ToolCatalog`/`ToolView` 和 `ToolExecutionContext`；`ToolExecutor.execute/execute_many` 接收视图和执行上下文。`ToolRegistry` 新增全量定义查询，现有 `definitions` 语义保持为全量可注册工具。

**依赖：** 不依赖 Skills 的具体解析；只使用通用工具协议，避免工具层反向依赖业务模块。

### `src/yucode/prompting.py`、`agent.py`

**职责：** 每轮刷新 Skill、使用相同快照建立提示与工具视图，并在系统工具导致状态改变后于下一轮重建。

**对外接口：** `SystemPromptBuilder.build(..., skill_state=...)` 和 `clone_with_skill_source()`；`Agent` 接收可选 `SkillRuntime`，公开 `clear_active_skills()`。

**依赖：** `skills` 只通过运行时、提示源和工具视图的公共接口接入。普通无 Skill 的构造仍使用全部工具，保持现有单元测试可直接构造。

### `src/yucode/commands/*`、`src/yucode/tui/app.py`

**职责：** 把 `/skill`、动态 `/skill:<名称>`、`/clear` 与会话切换接到 Skill 运行时；保证 Tab、帮助、解析和分发使用同一动态命令目录；将独立任务摘要展示并回流。

**对外接口：** `CommandContext` 增加 `skills` 和 `skill_controller`；命令查找改依赖 `CommandCatalog` 协议；`ChatApp` 实现独立执行、列表展示和启动阶段校验。

**依赖：** 依赖 `skills.runtime` 与 `skills.execution` 的公共接口，不读取能力包文件。

### `src/yucode/cli.py`、内置 Skill 与打包配置

**职责：** 装配全局工具目录、Provider 工厂、Skill 运行时、动态命令目录和 TUI；随发行包带上三个内置 Skill。

**对外接口：** `create_provider` 继续是唯一 Provider 创建入口，并以注入方式提供给 fork runner。

**依赖：** CLI 不解析 Skill 内容；`pyproject.toml` 显式将 `src/yucode/skills/builtin/**` 打入 wheel。

内置目录包含 `commit/SKILL.md`、`review/SKILL.md`、`test/SKILL.md`。`commit` 为 inline，声明读取、编辑和命令工具并明确仅在用户要求时提交；`review` 为 fork、仅声明只读工具；`test` 为 fork、声明读取与命令工具。三者均使用 `$ARGUMENTS`，可由高优先级同名定义覆盖。

## 模块交互

### 启动与首次对话

```text
CLI 创建 ToolRegistry、SkillRuntime、动态命令目录与 ChatApp
  → ChatApp 启动 MCP 并注册远端工具
  → SkillRuntime.initialize(全局工具目录)
      ├─ 损坏文件：收集警告，继续
      ├─ 有效白名单含未知工具：关闭 MCP、报错退出
      └─ 成功：显示警告、开放输入
  → Agent 首次请求前 refresh + snapshot
  → Prompt 仅注入 Skill 名称/说明和 LoadSkill 提示
```

### 模型加载 Skill 与白名单更新

```text
用户普通请求 → Agent 取得快照 → Provider 收到目录摘要和当前工具
  → 模型调用 LoadSkill(name, arguments)
  → ToolView 中的 LoadSkillTool 校验并激活 Skill
      ├─ inline：返回“已加载”结果
      └─ fork：SkillForkRunner 以冻结子快照完成任务，返回摘要
  → Agent 将工具结果写入主历史
  → 下一迭代 refresh + 新快照
  → Prompt 在高优先级位置注入完整 SOP；ToolView 为白名单并集 + 专属工具 + 系统工具
```

### 用户短命令与清除

```text
输入 /skill → 动态目录列出 ActiveSkill
输入 /skill:<name> args → refresh → 命令控制器重取定义
  ├─ inline：激活/替换参数 → 复用主 Agent 正常生成和存档
  └─ fork：创建子 Agent → UI 仅展示并存档一条摘要

输入 /clear 或成功 new/resume session
  → 清除 ActiveSkill → 动态命令和 ToolView 恢复未激活状态
```

### 热更新与 URL 安装

```text
下次请求 / Tab / LoadSkill / /skill 查询 → SkillRuntime.refresh
  ├─ 有效新定义：按三层优先级更新目录和已激活快照
  ├─ 已激活文件暂时损坏：保留最后有效快照并报告警告
  └─ 已激活定义彻底消失：解除激活，撤销短命令与专属工具

InstallSkill(URL) → 现有权限确认 → 下载到用户目录临时区 → 完整校验
  → 原子提交或清理失败临时区 → refresh → 返回结果
```

## 文件组织

```text
src/yucode/
├── skills/
│   ├── __init__.py                 # Skill 公共装配入口
│   ├── models.py                   # 定义、来源、快照、诊断与历史范围
│   ├── loader.py                   # 三层发现、frontmatter/目录包与路径校验
│   ├── runtime.py                  # 激活状态、热更新、白名单启动校验
│   ├── tools.py                    # ToolView、LoadSkill、脚本工具、资源工具
│   ├── install.py                  # URL 下载、zip/Markdown 校验和原子安装
│   ├── prompt.py                   # 可用摘要、激活 SOP、建议工具状态
│   ├── execution.py                # fork Agent、历史选择与回流摘要
│   ├── commands.py                 # 动态命令目录和 /skill 处理器
│   └── builtin/
│       ├── commit/SKILL.md
│       ├── review/SKILL.md
│       └── test/SKILL.md
├── tools/
│   ├── base.py                     # ToolCatalog、ToolView、执行上下文
│   ├── registry.py                 # 全局目录与全量定义查询
│   └── executor.py                 # 对指定视图执行调用
├── prompting.py                    # 读取 SkillPromptState、缓存键与克隆
├── agent.py                        # 每轮冻结 SkillSnapshot、清除激活状态
├── commands/
│   ├── models.py                   # 命令目录与 Skill 控制器协议
│   ├── builtins.py                 # /skill、/clear、session 处理器接入
│   ├── dispatcher.py               # 未激活 skill: 命令的中文引导
│   └── registry.py                 # 静态表兼容 CommandCatalog
├── tui/app.py                      # 启动校验、fork UI 与动态补全
├── cli.py                          # 装配 SkillRuntime 和 Provider factory
└── __init__.py
pyproject.toml                      # 包含内置 Markdown 资源
README.md                           # Skill 目录、格式、命令与 URL 安装说明
tests/
├── test_skills_loader.py           # 格式、覆盖、损坏跳过、资源/工具边界
├── test_skills_runtime.py          # 激活、白名单、热更新、启动失败
├── test_skills_tools.py            # 脚本、资源、LoadSkill/InstallSkill
├── test_skills_execution.py        # inline、fork、历史、模型与摘要隔离
├── test_skills_commands.py         # /skill、动态补全、/clear、会话清除
├── test_prompting.py               # Skill 提示位置、可见目录、缓存键
├── test_agent.py                   # 每轮工具视图和 fork 结果回流
├── test_tools.py                   # ToolView 与执行上下文回归
├── test_cli.py                     # MCP 后严格校验与内置资源装配
└── test_tui.py                     # 启动失败、fork 展示、权限和 Tab
doc/ch14/
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| Skill 热更新 | 按相关操作重新扫描和比较指纹，不常驻监听线程 | 跨 Windows/tmux 环境稳定，能满足“下一次相关操作生效”，也不会引入线程同步。 |
| 入口格式 | 单文件 `*.md` 或目录 `SKILL.md`；目录正文可附加 `prompt.md` | 同时满足正文 SOP 的简单写法与可分发目录包的组织需求。 |
| 三层路径 | 项目 `.yucode/skills`、用户 `%APPDATA%/YuCode/skills`、打包内置目录 | 与现有项目/用户配置约定一致，项目覆盖用户、用户覆盖内置。 |
| 指令注入 | 可用摘要在初始稳定提示；激活 SOP 紧接基础系统约束 | 实现两阶段加载，并保证激活指令不被普通历史淹没。 |
| 白名单 | `ToolView` 在每轮冻结可见工具，而非从全局注册表删除工具 | MCP、内置工具和多个/隔离 Skill 可安全共存；热更新不会破坏正在运行任务。 |
| 私有工具 | `tool.json` + 无 shell 子进程 JSON 协议，安全级别显式声明 | 允许能力包携带实现，同时保留输入 schema、取消、超时和权限链路。 |
| 资源访问 | 只读 `SkillReferenceTool`，路径限制在包内 `references/` | 让非项目目录的参考资料可用，且不扩大工作区文件工具边界。 |
| 独立模式 | 新 Agent、无 recorder、冻结历史/工具/提示快照，固定格式摘要回流 | 不污染主历史，仍可使用同一 Provider、权限和工具行为。 |
| 独立模型 | 由注入的 Provider factory 用 `dataclasses.replace` 替换模型名创建 | 避免 Provider 内部状态共享；模型不可用时可明确失败而不降级。 |
| 系统工具 | `LoadSkill` 始终在视图；`InstallSkill` 同样不受白名单但受模式/权限 | 满足两阶段加载与直接 URL 安装，同时不形成越权入口。 |
| 动态短命令 | 用动态命令目录叠加静态注册表，固定 `/skill:` 命名空间 | 帮助、Tab 和分发数据一致；不需要运行时改写内置命令表，也保留 `/review`。 |
| URL 安装 | 仅直接 URL，支持 Markdown/zip，临时验证后原子安装且拒绝覆盖 | 提供截图要求的第三方获取能力，又不引入市场、依赖或版本管理。 |

## 需求覆盖

| Spec 需求 | 设计归属 | 验收关联 |
| --- | --- | --- |
| F1 定义与目录包 | `models`、`loader`、`tools`、资源工具 | AC1 |
| F2 三层发现、覆盖与容错 | `loader`、`runtime.initialize` | AC2—AC4 |
| F3 两阶段加载与持续上下文 | 系统工具、`runtime`、`prompting`、`agent` | AC5—AC6 |
| F4 白名单与建议工具 | `ToolView`、`SkillPromptState`、执行器 | AC4、AC6、AC10—AC11 |
| F5 inline/fork 执行 | `execution`、`Agent`、TUI 协调 | AC7—AC8 |
| F6 命令、热更新与清除 | 动态命令目录、`runtime`、内置处理器 | AC9—AC12 |
| F7 内置样板 | `skills/builtin`、打包配置 | AC13 |
| F8 URL 安装 | `install`、`InstallSkillTool`、权限链路 | AC14 |
| N1—N5 | 中文诊断、冻结快照、无 recorder、权限复用 | AC1—AC15 |
| 端到端 | tmux 真实模型流程 | AC15 |
