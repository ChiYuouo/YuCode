# Hook 自动化系统 Plan

## 架构概览

本章新增独立的 `hooks` 包，将 YAML 中的 Hook 规则在启动时解析为不可变运行时定义。`HookEngine` 负责按事件顺序筛选规则、基于同一份上下文快照求值条件、执行或调度动作，并把提示词动作累积为供 Agent 下次构造模型请求时使用的运行时提示。

规则解析与校验留在配置层：配置加载完成后，应用获得可直接运行的 Hook 集合，运行中不重复解析 YAML。配置错误沿用 `ConfigError` 阻止启动，并携带规则索引及字段路径。

Agent 是生命周期事件的唯一编排者：在会话、轮次、消息、工具和压缩节点构造 `HookContext` 并交给 Engine。工具执行器只额外负责在实际等待权限确认、运行命令和成功更改文件的精确位置发出系统级上下文；它不解释规则，也不持有一次性标记。

工具前拦截使用与普通执行分离的入口：Engine 同步运行匹配的 `pre_tool_use` Hook，遇到拒绝时返回 `ToolRejectedError`。工具执行器把该异常转换为既有的失败 `ToolResult`，因此 Agent 原有“工具结果回填模型历史后继续下一轮”的流程无需新增分支。

## 核心数据结构

### `HookEvent`

```python
class HookEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    COMPACT = "compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"
```

唯一的事件白名单；YAML 中的 `event` 必须解析为其中一项。

### `Action` 与 `Hook`

```python
class ActionType(str, Enum):
    COMMAND = "command"
    PROMPT = "prompt"
    HTTP = "http"
    AGENT = "agent"

@dataclass(frozen=True)
class Action:
    type: ActionType
    command: str | None = None
    prompt: str | None = None
    url: str | None = None
    method: str = "POST"
    body: str | None = None
    timeout_seconds: float | None = None
    reject: bool = False
    reason: str | None = None

@dataclass(frozen=True)
class Hook:
    event: HookEvent
    action: Action
    condition: ConditionGroup | None = None
    once: bool = False
    async_run: bool = False
    source_index: int = 0
```

`Action` 只允许对应类型所需字段：`command` 需要 `command`，`prompt` 需要 `prompt`，`http` 需要合法 HTTP(S) `url`，`agent` 无额外必填字段。`reject: true` 只允许在 `pre_tool_use`；必须同时给出非空 `reason`，且该 Hook 不得 `async: true`。HTTP 的正文可省略；无正文时不发送请求体。

YAML 采用以下固定形状，避免同一语义存在多种写法：

```yaml
hooks:
  - event: pre_tool_use
    if:
      all:
        - field: TOOL_NAME
          operator: "=="
          value: run_command
    action:
      type: command
      command: "Write-Host $TOOL_NAME"
      reject: true
      reason: "该命令不符合当前安全规则。"
    once: false
    async: false
```

### `Condition` 与 `ConditionGroup`

```python
class ConditionOperator(str, Enum):
    EQUALS = "=="
    NOT_EQUALS = "!="
    REGEX = "=~"
    GLOB = "~="

@dataclass(frozen=True)
class Condition:
    field: str
    operator: ConditionOperator
    value: str

@dataclass(frozen=True)
class ConditionGroup:
    mode: Literal["all", "any"]
    conditions: tuple[Condition, ...]
```

`if` 只能是恰含 `all` 或 `any` 的对象；每项条件只能访问 `EVENT`、`TOOL_NAME`、`FILE_PATH`、`MESSAGE`、`ERROR` 或 `TOOL_ARGS.<键路径>`。`=~` 在加载时编译正则；`~=` 用 `fnmatchcase` 进行 glob 匹配。字段值统一转为文本后再比较。

### `HookContext`、执行结果与拒绝异常

```python
@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    tool_name: str = ""
    file_path: str = ""
    message: str = ""
    error: str = ""
    tool_args: Mapping[str, Any] = field(default_factory=dict)

class ToolRejectedError(Exception):
    def __init__(self, reason: str) -> None: ...

@dataclass(frozen=True)
class HookRunResult:
    prompts: tuple[str, ...] = ()
```

`HookContext` 在事件边界由现有值复制创建。模板变量固定为 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR` 与 `$TOOL_ARGS.xxx`；未找到的变量替换为空字符串。`ToolRejectedError` 仅跨越 Engine 到工具执行器的边界，最终转为错误码 `hook_rejected` 的 `ToolResult`。

### `HookEngine`

```python
class HookEngine:
    def __init__(self, hooks: Sequence[Hook], root: Path) -> None: ...

    async def run_hooks(self, context: HookContext) -> HookRunResult: ...
    async def run_pre_tool_hooks(self, context: HookContext) -> HookRunResult: ...
    def drain_prompts(self) -> tuple[str, ...]: ...
```

`run_hooks` 处理所有非拦截事件，按 `source_index` 顺序执行；异常记录日志后继续。`run_pre_tool_hooks` 只接收 `pre_tool_use` 上下文，强制同步，并在首个命中的拒绝动作后抛出 `ToolRejectedError`。`once` 成功调度后将 Hook 的配置序号加入内存集合；异步任务由 Engine 持有并消费异常。提示词动作的结果写入运行时缓冲，`drain_prompts` 在每次模型请求前取出并清空。

## 模块设计

### `yucode.hooks.models`

**职责：** 定义事件、规则、动作、条件、上下文、执行结果和拒绝异常等纯数据结构。

**对外接口：** `HookEvent`、`ActionType`、`Action`、`Hook`、`Condition`、`ConditionGroup`、`HookContext`、`HookRunResult`、`ToolRejectedError`。

**依赖：** 标准库的 dataclass、enum、typing。

### `yucode.hooks.conditions`

**职责：** 校验条件字段和操作符，编译正则，读取嵌套工具参数，并对 `HookContext` 进行条件求值。

**对外接口：** `parse_condition_group(raw)`、`matches(group, context)`、`context_value(context, field)`。

**依赖：** `models`、`re`、`fnmatch`。

### `yucode.hooks.template`

**职责：** 在动作文本中替换固定上下文变量，复用条件模块的字段读取逻辑，确保缺失值统一为空字符串。

**对外接口：** `render_template(template, context)`。

**依赖：** `models`、`conditions`、`re`。

### `yucode.hooks.loader`

**职责：** 将 YAML 的 `hooks` 列表解析为 `Hook` 元组，集中校验字段类型、动作必填项、HTTP 地址、超时、条件、变量引用和 `reject`/`async` 约束。

**对外接口：** `load_hooks(raw) -> tuple[Hook, ...]`；格式错误抛出带规则索引的 `ValueError`，由配置层包装为 `ConfigError`。

**依赖：** `models`、`conditions`、`template`、`urllib.parse`。

### `yucode.hooks.executors`

**职责：** 根据 `Action.type` 执行命令、提示词、HTTP 和 Agent stub。命令使用当前工作目录与每条动作的超时；HTTP 使用已有 `httpx`；提示词返回渲染后内容；Agent stub 写入日志且返回空结果。

**对外接口：** `ActionExecutor.execute(action, context) -> HookRunResult`。

**依赖：** `models`、`template`、`asyncio`、`httpx`、`logging`。

### `yucode.hooks.engine`

**职责：** 维护运行时一次性标记、异步任务、提示词缓冲，并协调条件筛选、执行器及拒绝语义。

**对外接口：** `HookEngine` 的三个公开方法。

**依赖：** `models`、`conditions`、`executors`、`asyncio`、`logging`。

### `yucode.config`

**职责：** 将 `hooks` 加入 `AppConfig`，调用 Hook 加载器，并把其错误转化为现有的中文配置错误。

**对外接口：** `AppConfig.hooks`。

**依赖：** `hooks.loader`、`hooks.models`。

### `yucode.agent`、`yucode.tools.executor` 与 `yucode.cli`

**职责：** 创建唯一的 Engine，插入生命周期事件，并在工具边界把拦截映射为既有工具结果。

**对外接口：** Agent 构造函数新增可选 `hook_engine`；工具执行器新增同一依赖；CLI 在构建 Agent 前创建 Engine。

**依赖：** `hooks.engine`、`hooks.models`。

## 模块交互

```text
yucode.yaml
    │
    ▼
config.load_config ──► hooks.loader ──► 不可变 Hook 规则
    │                                      │
    ▼                                      ▼
cli.main ───────────────────────────► HookEngine
                                           │
                     ┌─────────────────────┼──────────────────────┐
                     ▼                     ▼                      ▼
                 Agent 生命周期        ToolExecutor           ActionExecutor
                     │                 权限/命令/文件              │
                     ▼                     │                      ▼
               HookContext ───────────► run_hooks / run_pre_tool_hooks
                     │                         │
                     │                    ToolRejectedError
                     ▼                         ▼
           下一次模型请求的提示词缓冲       ToolResult(hook_rejected)
```

事件时序如下：

1. CLI 创建 Engine 后依次触发 `startup`、`session_start`；应用退出的 `finally` 中触发 `session_end`、`shutdown`。
2. `Agent.run` 先触发 `turn_start`，再以用户输入触发 `pre_send`，随后写入用户消息。模型回复成功收集后以完整回复文本触发 `post_receive`；每条返回路径在结束前触发一次 `turn_end`。Agent 内部异常同时触发 `error`，但 Hook 异常不会递归触发 `error`。
3. Agent 收到工具调用后，工具执行器先运行 `pre_tool_use`。若拒绝，生成失败工具结果并跳过权限和工具本体；否则保留既有权限判断。出现用户确认界面前触发 `permission_request`。
4. 允许的 `run_command` 在实际执行前触发 `command_execute`；允许的 `write_file`、`edit_file` 在成功完成后触发 `file_change`，两者都携带原工具参数与目标路径。每项工具调用（含拒绝与执行失败）最终触发 `post_tool_use`。
5. 手动、自动或紧急压缩在产生“已压缩”结果后触发 `compact`。每次准备模型请求前，Agent 取出 Engine 暂存的提示词，作为短期运行时提示附加到该次请求；不写入持久会话历史。

## 文件组织

```text
src/yucode/
├── hooks/
│   ├── __init__.py
│   ├── models.py          # Hook 核心数据结构与异常
│   ├── conditions.py      # 条件解析与求值
│   ├── template.py        # 上下文变量替换
│   ├── loader.py          # YAML 规则解析和校验
│   ├── executors.py       # 四类动作执行器
│   └── engine.py          # 运行时调度与拦截
├── agent.py               # 生命周期事件与提示注入
├── config.py              # hooks 配置接入
├── cli.py                 # 创建/关闭 HookEngine
└── tools/executor.py      # 工具前后、权限、命令、文件事件

tests/
├── test_hooks_conditions.py
├── test_hooks_template.py
├── test_hooks_loader.py
├── test_hooks_engine.py
├── test_hooks_executors.py
├── test_hooks_integration.py
└── test_config.py          # 扩充 hooks 配置覆盖

yucode.yaml.example         # Hook 配置示例与字段说明
doc/ch15/                   # 本章 spec、plan、task、checklist
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 配置位置 | 在现有 `yucode.yaml` 顶层新增 `hooks` 列表 | 保持所有应用级运行时配置在同一入口，启动时可一次校验。 |
| 条件语法 | 固定 `if.all` 或 `if.any` 列表 | 天然禁止混用逻辑关系，错误可在加载期精确定位。 |
| 变量语法 | 使用 `$EVENT` 等固定 `$` 变量及 `$TOOL_ARGS.xxx` | 与用户提出的变量名称直接对应，便于在 YAML 动作文本中阅读。 |
| 条件与变量读取 | 共享同一个字段读取函数 | 防止条件和模板对嵌套参数或缺失值出现不同解释。 |
| 拦截边界 | 在工具执行器最前端同步处理 | 可确保被拒绝的调用不会进入权限确认或工具本体，同时能复用 `ToolResult` 回填模型。 |
| 提示词注入 | Engine 缓冲，下一次模型请求前一次性取用 | 让 Hook 不改写会话历史，且“后续请求可见”的范围稳定可测。 |
| 异步 Hook | `asyncio.create_task` 后由 Engine 捕获异常 | 不阻塞 Agent，又避免后台任务异常漏出。 |
| 一次性语义 | 成功调度后仅存内存配置序号 | 满足运行期一次性要求，重启自然重置，不引入持久化。 |
| HTTP 客户端 | 使用项目已有的 `httpx` | 不新增依赖，并可在异步 Agent 循环中直接等待请求。 |
| Agent 动作 | 执行器记录 stub 日志并返回空结果 | 严格遵守本章不创建、不运行、不注入子 Agent 的范围。 |
