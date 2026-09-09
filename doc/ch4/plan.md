# YuCode 结构化系统提示与缓存策略 Plan

## 架构概览

新增独立的提示层，负责把固定规则、可选规则、运行模式和环境上下文组装成一次模型调用所需的“稳定前缀 + 运行期补充消息 + 工具描述”。Agent 不再自行选择两段常量指令；每轮在发起 Provider 请求前向提示层索取不可变请求描述。Conversation 仍只保存真实用户、助手和工具消息，运行期补充消息绝不写入历史。

Provider 接收统一的请求描述，再映射为 OpenAI Responses 或 Anthropic Messages 的原生字段。稳定系统提示和增强后的工具描述在内容不变时保持字节级稳定；Provider 为各协议选择缓存边界与缓存键。环境和 `<system-reminder>` 内容仅作为本轮运行期信息发送，排在稳定前缀之后。

缓存观测作为用量的一部分从 Provider 流回传，经 Agent 的既有用量事件进入 TUI。终端状态栏新增本次会话的缓存读取/写入指标；未返回字段的供应商明确显示“缓存数据不可用”。不新增持久化日志系统。

在提示层之外新增请求授权与执行策略层。提示负责引导，策略层在工具执行前拒绝未授权副作用、未读取的编辑和不满足前置条件的调用；拒绝结果会回灌模型，而不是产生副作用。

## 核心数据结构

### `PromptModule`

```python
@dataclass(frozen=True)
class PromptModule:
    name: str
    content: str
    stable: bool
```

表示一个可单独排序和测试的提示模块。固定模块的 `stable=True`；环境、会话模式重复提示和其他运行期内容的 `stable=False`。空内容模块不进入输出。

### `RuntimeContext`

```python
@dataclass(frozen=True)
class RuntimeContext:
    workspace_root: Path
    mode: RunMode
    iteration: int
```

表示一轮 Agent 请求时会变化、且不进入 Conversation 的上下文。首期只从 ToolRegistry 的工作目录和当前 `RunMode` 生成环境与模式提示；自定义指令、Skill、长期记忆以后可作为可选 `PromptModule` 传入，不在本章加载。

### `RuntimeMessage` 与 `ModelRequest`

```python
@dataclass(frozen=True)
class RuntimeMessage:
    content: str

@dataclass(frozen=True)
class ModelRequest:
    history: tuple[Message, ...]
    stable_instructions: str
    runtime_messages: tuple[RuntimeMessage, ...]
    tools: tuple[ToolDefinition, ...]
    prompt_cache_key: str
```

`RuntimeMessage.content` 始终以 `<system-reminder>` 包裹。`ModelRequest` 是提示层和 Provider 的唯一边界：历史只包含 Conversation 消息；稳定指令、运行期补充和工具分别存放，禁止 Provider 或 Agent 将它们拼为一条用户消息。

`prompt_cache_key` 是由提示层版本、运行模式、稳定指令和规范化工具定义计算出的无内容语义哈希。它只在稳定前缀或工具集合改变时改变，用于支持该字段的 Provider 归类相同前缀；不包含工作目录、用户文本、工具结果或补充消息。

### `CacheUsage` 与 `Usage`

```python
@dataclass(frozen=True)
class CacheUsage:
    available: bool = False
    read_input_tokens: int = 0
    write_input_tokens: int = 0

@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache: CacheUsage = CacheUsage()
```

`available=False` 表示该响应未提供任何可解释的缓存字段，不等同于零命中。`read_input_tokens` 表示服务端从缓存读取的输入 token；`write_input_tokens` 表示创建或写入缓存的输入 token。Agent 累加各轮数值，并以“任一轮提供缓存字段”作为本次请求的可用标记。

### `SystemPromptBuilder`

```python
class SystemPromptBuilder:
    def build(
        self,
        context: RuntimeContext,
        tools: Sequence[ToolDefinition],
    ) -> ModelRequestParts: ...

    def enhance_tools(
        self,
        tools: Sequence[ToolDefinition],
    ) -> tuple[ToolDefinition, ...]: ...
```

`build` 先按固定优先级生成七个模块：身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出；相邻模块以一个空行连接。可选模块按自定义指令、Skill、长期记忆追加；首期均为空。环境模块仅生成运行期消息。

模式模块含两部分：稳定的模式身份说明，以及按轮次生成的运行期强化消息。第 1、5、10……轮产生完整规则，其他轮产生语义等价的精简规则。`enhance_tools` 复制而不修改注册表原对象，按照工具类别为描述追加确定性的使用规则。

### `TaskAuthorization` 与 `ExecutionPolicy`

```python
class TaskAuthorization(str, Enum):
    ANSWER_ONLY = "answer_only"
    READ_ONLY = "read_only"
    EXECUTE = "execute"

@dataclass
class ExecutionPolicy:
    authorization: TaskAuthorization
    read_targets: set[str]
    pending_verifications: set[str]

    def preflight(self, call: ToolCall) -> ToolResult | None: ...
    def record(self, result: ToolResult) -> None: ...
```

授权从用户原文和运行模式得出：`/plan` 固定为 `READ_ONLY`；明确要求创建、修改、删除、运行、实现、修复或验证的任务为 `EXECUTE`；纯解释、评审、建议、比较或问答为 `ANSWER_ONLY`；无法确定的输入为 `READ_ONLY`。后两者均不允许副作用工具。

策略在一次 Agent 运行中记录成功读取的目标和待验证的修改目标。`edit_file` 必须已有同一目标的成功 `read_file`；`write_file` 仅在目标已存在时要求此前读取。副作用成功后将目标加入待验证集合，后续成功读取同一目标才移除。存在待验证目标时，Agent 不得以成功结束；它会用运行期提醒要求模型验证，仍未验证则发出明确失败状态。

### `SensitiveDataRedactor`

```python
class SensitiveDataRedactor:
    def redact(self, text: str) -> str: ...
```

对模型可见的工具结果和用户可见的文本增量使用同一脱敏规则。首期覆盖常见密钥、令牌、密码、Cookie 和 Bearer 凭据形式，将值替换为固定占位符；不记录原值。

### Provider 接口

```python
class Provider(Protocol):
    async def stream(
        self,
        request: ModelRequest,
        cancellation: Cancellation,
    ) -> AsyncIterator[StreamEvent]: ...
```

`StreamEvent` 继续承载文本、thinking、工具调用和用量；用量事件改为携带扩展后的 `Usage`。Provider 不再接收可被混淆的 `messages/tools/instructions` 多参数组合。

## 模块设计

### `src/yucode/prompting.py`

**职责：** 定义提示模块、运行期上下文、请求描述、稳定提示构建、模式轮次策略、环境文本和工具描述增强。

**对外接口：** `SystemPromptBuilder.build(...)`、`SystemPromptBuilder.enhance_tools(...)`、`ModelRequest`、`RuntimeContext`。

**依赖：** `RunMode`、`Message`、`ToolDefinition` 和 `Path`；不依赖 Provider、Conversation 或 TUI，避免循环依赖。

**固定文本策略：** 所有固定模块以元组常量登记并按优先级遍历，禁止由字典迭代决定顺序。完整/精简模式提示也使用常量，保证同一轮次类别重复构造时文本一致。环境消息包括规范化工作目录和当前轮次信息，并与模式强化合并为一条 `<system-reminder>`。

**工具规则策略：** 固定模块使用“必须/不得/仅当”表述：有适用专用工具时不得以命令替代；编辑既有文件前必须读取；修改后存在可验证目标时必须读取验证；没有工具证据不得声称完成。`read_file`、`edit_file`、`write_file`、`run_command` 的描述分别追加与其职责一致的规则，其中 `edit_file` 明确读取前置条件，`run_command` 明确仅在无适用专用工具时使用。增强过程不改变工具名称和 schema。

**授权规则策略：** 运行期提醒携带当前授权与待验证目标。纯解释和评审请求明确禁止副作用；意图不清时允许只读理解但要求模型说明需要用户确认；执行任务才允许副作用。稳定系统约束明确不得猜测、伪造工具结果、隐藏失败或输出敏感值。

### `src/yucode/agent.py`

**职责变化：** Agent 持有 `SystemPromptBuilder`、`ExecutionPolicy` 与 `SensitiveDataRedactor`。它在每轮选择模式可用工具后创建包含授权与待验证目标的 `RuntimeContext`，构建 `ModelRequest` 并交给 Provider。

**对外接口：** `Agent.run(...)` 及所有既有 Agent 事件签名保持不变；`UsageUpdated.current/total` 的 `Usage.cache` 增加缓存观测数据。

**依赖：** 新提示层、策略层、现有 Conversation、ToolRegistry、ToolExecutor 和 Provider。

**边界：** Agent 不自行拼接 `<system-reminder>`，不将补充消息追加到 Conversation，也不根据缓存数字调整工具执行或停止条件；但它必须在调用执行器前应用策略，并在向历史或界面发出文本前调用脱敏器。

### `src/yucode/tools/executor.py`

**职责变化：** 在保持既有只读并发和副作用顺序屏障的前提下，对每项调用先执行 `ExecutionPolicy.preflight`。被拒绝的调用不触发真实工具，以结构化 `policy_violation` 结果占据原顺序位置；已执行结果立即交给 `record` 更新读取与验证证据。

**边界：** 原有模式可用工具筛选、命令确认、工作目录边界和取消语义保持不变；策略检查不能绕过这些已有限制。

### `src/yucode/providers/base.py`

**职责变化：** 放置扩展后的 `CacheUsage`、`Usage`、`ModelRequest` 所需的共享消息类型与简化后的 Provider 协议。

**对外接口：** Provider 只接收 `ModelRequest`。保留现有 `Message` 及内容块，确保历史序列化和工具回灌协议不变。

### `src/yucode/providers/openai.py`

**职责：** 将 `ModelRequest` 映射到 Responses 请求：`stable_instructions` 放入 `instructions`，增强后工具放入 `tools`，`runtime_messages` 作为在历史之前的 developer 级输入项，历史随后序列化到 `input`。发送确定性的 `prompt_cache_key`，使相同稳定前缀归入同一缓存桶。

**缓存用量：** 在 `response.completed` 的 `usage.input_tokens_details` 中读取 `cached_tokens` 与可选的 `cache_write_tokens`；字段不存在则产出 `CacheUsage(available=False)`，而非零值。

**边界：** `<system-reminder>` 是 developer 级补充内容，不会进入 Conversation 或以 user 角色发送。

### `src/yucode/providers/anthropic.py`

**职责：** 将稳定指令序列化为顶层 `system` 内容块，将增强后工具序列化为 `tools`，并在稳定的系统/工具前缀末端设置 ephemeral 缓存断点。运行期补充作为不写入 Conversation 的前置消息内容，与首个用户消息合并；其 `<system-reminder>` 标签保持系统补充语义，同时位于稳定缓存前缀之后。

**缓存用量：** 从流的 `message_start` 和最终 `message_delta` 用量对象读取 `cache_read_input_tokens`、`cache_creation_input_tokens`，同时保留既有输入、输出和 thinking token 解析。任一缓存字段存在即标记可用。

**边界：** Claude Messages API 没有 developer 角色；运行期补充用特殊标签附着在消息内容中，明确要求模型将其视为系统补充而非待回答的用户文本。它不进入 Conversation，因此不会污染后续历史。

### `src/yucode/tui/app.py` 与 `src/yucode/tui/widgets.py`

**职责变化：** `TokenTotals` 同时累计缓存读/写 token 与是否可用；`ChatStatus` 在不改变既有输入/输出 token 显示的前提下追加简短缓存状态。

**显示规则：** 收到可用缓存数据时显示“缓存读 N / 写 M”；本轮结束后若完全没有此字段，显示“缓存数据不可用”。缓存数据仅作观测，不向用户推断节省金额、命中率或失败原因。

### 测试模块

- `tests/test_prompting.py`：覆盖模块顺序、空行、可选模块、稳定性、环境分离、标签包装、每五轮模式策略、工具描述强化和缓存键变化边界。
- `tests/test_policy.py`：覆盖授权分类、编辑前读取、已有文件覆盖前读取、待验证目标、拒绝结果顺序与敏感值脱敏。
- `tests/test_agent.py`：Fake Provider 改接收 `ModelRequest`，断言历史未混入补充消息、环境变化进入下一轮、Plan 每轮工具边界不退化、缓存用量累计、未验证不完成与最终文本脱敏。
- `tests/test_openai_provider.py`：断言 `instructions`、developer 补充输入、历史、工具、`prompt_cache_key` 的相对位置；覆盖有/无 OpenAI 缓存字段。
- `tests/test_anthropic_provider.py`：断言稳定/运行期 system 块、工具缓存断点和历史分离；覆盖有/无 Claude 缓存字段。
- `tests/test_tui.py`：断言缓存状态的可用/不可用展示及既有 Token 显示回归。

## 模块交互

```text
用户输入
  → Conversation 仅追加用户消息
  → Agent 选择本模式的工具
  → 根据用户原文与模式创建 TaskAuthorization / ExecutionPolicy
  → SystemPromptBuilder
      ├─ 稳定七模块 + 可选模块 → stable_instructions
      ├─ 工作目录 + 第 N 轮模式策略 → <system-reminder>
      ├─ 工具规则增强 → tools
      └─ 稳定内容哈希 → prompt_cache_key
  → ModelRequest(history, stable, runtime, tools, cache key)
  → Provider 按供应商映射请求并建立稳定缓存边界
  → 模型流
      ├─ 文本 / thinking → 脱敏后发既有 Agent 事件和历史流程
      └─ Usage + CacheUsage → UsageUpdated → TUI 缓存状态
  → Executor 先检查授权/先读条件，再执行工具并记录验证证据
  → 工具结果脱敏后写入 Conversation
  → 存在待验证目标时强制下一轮验证提醒；未验证不得完成
  → 下一轮重新构建运行期补充；稳定前缀不变时复用缓存
```

### 模式轮次流程

```text
第 1 轮：稳定 Plan 身份 + 完整 <system-reminder>
第 2–4 轮：稳定 Plan 身份 + 精简 <system-reminder>
第 5 轮：稳定 Plan 身份 + 完整 <system-reminder>
第 6–9 轮：稳定 Plan 身份 + 精简 <system-reminder>
第 10 轮：稳定 Plan 身份 + 完整 <system-reminder>
```

完整和精简提醒都只强化既有的模式限制；实际可用工具仍由 Agent 的只读筛选决定，构成双层安全保证。

## 文件组织

```text
src/yucode/
├── prompting.py              # 提示模块、构建器、模式轮次与工具规则
├── policy.py                 # 任务授权、工具前置条件、验证追踪与脱敏
├── agent.py                  # 每轮创建 ModelRequest，累计扩展用量
├── providers/
│   ├── base.py               # Provider 协议、CacheUsage、Usage 与共享消息
│   ├── openai.py             # Responses 缓存键、developer 补充、用量解析
│   └── anthropic.py          # system 缓存断点、补充 system 块、用量解析
└── tui/
    ├── app.py                # 缓存用量累计与状态更新
    └── widgets.py            # 缓存状态文本展示

tests/
├── test_prompting.py         # 新提示层单元测试
├── test_policy.py            # 授权、流程门槛、验证与脱敏
├── test_agent.py             # 请求构建、模式与缓存累计
├── test_openai_provider.py   # OpenAI 请求/缓存观测映射
├── test_anthropic_provider.py# Claude 请求/缓存观测映射
└── test_tui.py               # 缓存状态与回归

doc/ch4/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 提示职责边界 | 新建独立提示层 | 防止 Agent、Provider 和 TUI 各自拼接规则，保证顺序和缓存身份可测试。 |
| 稳定内容表示 | 单一确定性文本 + 确定性工具序列 | 服务端缓存依赖公共前缀；固定顺序和空白避免无意义缓存失配。 |
| 动态内容表示 | 独立 `RuntimeMessage`，强制 `<system-reminder>` 包装 | 不污染历史或稳定前缀，并使模型明确其系统补充语义。 |
| API 抽象 | Provider 接收 `ModelRequest` | 显式区分稳定指令、动态补充、历史和工具，避免继续使用含义重叠的字符串参数。 |
| 缓存策略 | OpenAI 使用稳定缓存键；Claude 对稳定 system/tool 前缀使用显式 ephemeral 缓存断点，并将动态补充置于消息部分 | 分别利用两家 API 的原生缓存机制，避免动态环境破坏稳定前缀，同时保持内部观测语义一致。 |
| 缓存观测 | `CacheUsage.available` 与读/写 token 分离 | “未返回”不应被误判为“零命中”，也不对不同供应商的字段做虚假等价。 |
| 模式强化频率 | 第 1 轮及每 5 轮完整，其余精简 | 遵循已批准的频率，兼顾长任务约束保持与动态上下文成本。 |
| 硬性流程执行 | 提示强化 + `ExecutionPolicy` 执行前拒绝 | 仅靠提示无法保证遵守；拒绝会以结果回灌模型，而不产生错误副作用。 |
| 用户授权 | 原文分类为仅回答、只读或执行 | 让解释和评审不会因模型擅自调用工具产生副作用；意图不清时保守处理。 |
| 修改后验证 | 追踪修改目标，未验证时不以成功结束 | 防止模型把工具成功或计划误报为任务完成。 |
| 敏感信息 | 工具结果与可见文本统一脱敏 | 降低模型复述工具输出中明显凭据的风险，且不新增持久化秘密存储。 |
| 可选模块 | 只预留构建位置，不新增加载器 | 满足后续扩展顺序，严格保持本章不做项目指令、Skill 加载和记忆。 |
| 人工评估 | checklist 定义四个可重复 tmux 场景 | 本章只做定性人工对比，不引入自动化评估框架。 |

## Spec 覆盖映射

| Spec | 实现归属 |
|---|---|
| F1、F2 | `SystemPromptBuilder` 的固定/可选模块排序与测试 |
| F3、F5、F6 | `ModelRequest`、运行期消息和两个 Provider 的请求映射 |
| F4 | 全局工具模块与 `enhance_tools` |
| F7 | `RuntimeContext.iteration` 与模式轮次策略 |
| F8 | `CacheUsage`、Provider 解析、Agent 累计和 TUI 状态 |
| F9 | `doc/ch4/checklist.md` 的四个 tmux 人工场景 |
| F10 | 硬性提示、`ExecutionPolicy` 和 Executor 前置检查 |
| F11 | `TaskAuthorization`、运行期授权提醒和副作用拒绝 |
| F12、N6 | 强制措辞、`SensitiveDataRedactor`、失败/未验证停止与测试 |
| N1、N4 | 统一 `ModelRequest`、双 Provider 回归、既有 Agent/TUI 不变 |
| N2、N3 | 确定性构建、稳定键与 Conversation 隔离测试 |
| N5 | 无新增远程连接、加载器或自动化评估依赖 |
