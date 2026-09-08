# MewCode 五层权限系统 Plan

## 架构概览

新增独立的权限层，作为 `ToolExecutor` 与真实工具之间唯一的决策入口。它对每个有效工具调用依次执行危险操作黑名单、专用文件工具路径沙箱、会话与 YAML 规则匹配、权限模式判断和可选的人工确认；任何拒绝均转换为现有的 `ToolResult`，因此不会启动真实工具，也会像普通工具结果一样写回对话历史。

权限层由无副作用的规则/判断组件和有状态的会话授权、项目本地规则写入组件组成。规则从用户全局、项目共享、项目本地三个 YAML 来源读取；会话授权只保存在当前 `Agent` 的运行期权限对象中。项目本地文件由专用存储器以原子写入维护，并在 `.gitignore` 中排除。

既有 `ExecutionPolicy` 保留“用户任务授权、修改前读取、修改后验证”等流程约束，但不再把一次策略或权限拒绝转换为 `POLICY_VIOLATION` 终止。Agent 将拒绝结果回灌模型并照常进入下一轮；模型可以替换工具、使用项目内路径或解释需要用户怎样授权。既有迭代上限、取消和未知工具保护不变。

交互层把目前仅适用于命令的二选一确认弹窗替换为通用权限确认弹窗。它接收经过脱敏的调用摘要，返回“本次允许 / 本会话允许 / 永久允许 / 拒绝”之一；权限层而非界面决定哪些调用需要该弹窗、何时允许和如何持久化规则。终端以 `Shift+Tab` 在四档权限模式间循环，并将当前实际生效的模式显示在状态区域；`plan` 模式复用既有只读 Agent 能力。

## 核心数据结构

### `PermissionMode`、`RuleEffect` 与 `RuleSource`

```python
class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"

class RuleEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"

class RuleSource(IntEnum):
    USER = 1
    PROJECT = 2
    LOCAL = 3
    SESSION = 4
```

`RuleSource` 的数值只表达固定优先级。规则匹配按 `SESSION → LOCAL → PROJECT → USER` 逐层停止；同层内任一 `deny` 匹配优先于任何 `allow`。

### `PermissionRule` 与 `RuleLayer`

```python
@dataclass(frozen=True)
class PermissionRule:
    tool_name: str
    pattern: str
    effect: RuleEffect
    source: RuleSource

@dataclass(frozen=True)
class RuleLayer:
    source: RuleSource
    rules: tuple[PermissionRule, ...]
    error: str | None = None
```

规则 YAML 采用稳定、便于人工编辑的列表格式：

```yaml
rules:
  - rule: "run_command(git *)"
    action: allow
  - rule: "write_file(.env)"
    action: deny
```

`规则名(模式)` 必须完整覆盖整个字符串。未使用 glob 元字符的模式为精确匹配；含 `*`、`?` 或字符集合的模式使用标准 glob 匹配。`run_command` 的匹配对象是完整命令文本；专用文件工具的匹配对象是规范化后的项目相对路径；其他内置工具使用其主要字符串参数。规则中的工具名必须是当前注册表公开的规范名称，避免看似生效但实际不会命中的别名。

### `PermissionDecision`、`PermissionRequest` 与 `ApprovalChoice`

```python
class PermissionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

class ApprovalChoice(str, Enum):
    ONCE = "once"
    SESSION = "session"
    PERMANENT = "permanent"
    REJECT = "reject"

@dataclass(frozen=True)
class PermissionDecision:
    outcome: PermissionOutcome
    reason: str
    error_code: str | None = None
    source: RuleSource | None = None
    rule: PermissionRule | None = None

@dataclass(frozen=True)
class PermissionRequest:
    call: ToolCall
    summary: str
    impact: str
```

`PermissionRequest` 在需要确认且有副作用调用未命中任何规则时产生：`default` 的所有副作用调用，以及 `acceptEdits` 的命令调用。`summary` 和 `impact` 使用既有脱敏器生成：文件调用显示工具与项目相对路径，命令调用显示脱敏后的命令；不暴露完整写入内容或凭据。`PermissionDecision` 会被转换为包含机器可判定错误码和人可读下一步建议的 `ToolResult`。

### `PermissionManager` 与 `LocalRuleStore`

```python
ApprovalCallback = Callable[[PermissionRequest], Awaitable[ApprovalChoice]]

class PermissionManager:
    @property
    def mode(self) -> PermissionMode: ...
    def set_mode(self, mode: PermissionMode) -> None: ...
    def decide(self, call: ToolCall, tool: Tool) -> PermissionDecision: ...
    async def resolve_prompt(
        self, request: PermissionRequest, approve: ApprovalCallback | None
    ) -> PermissionDecision: ...
    def record_session_allow(self, call: ToolCall, tool: Tool) -> None: ...
    def persist_local_allow(self, call: ToolCall, tool: Tool) -> None: ...

class LocalRuleStore:
    def load_layers(self) -> tuple[RuleLayer, ...]: ...
    def append_exact_allow(self, rule: PermissionRule) -> None: ...
```

`decide` 只执行不需要界面的四层判断，并返回 `ASK`、`ALLOW` 或 `DENY`。`resolve_prompt` 只接受已经得到 `ASK` 的请求；拒绝、取消或缺少回调均返回拒绝。选择“本会话允许”时追加一个仅匹配当前工具和当前规范化目标的精确 `SESSION allow`；“永久允许”同样生成精确规则，并原子写入本地文件后才允许当前调用。写入失败时返回 `permission_config_error`，不执行调用。

## 模块设计

### `src/mewcode/permissions.py`

**职责：** 定义权限枚举、规则解析与匹配、硬性命令黑名单、路径沙箱判定、模式回退、会话规则和本地持久化。

**对外接口：** `PermissionManager`、`PermissionRequest`、`ApprovalChoice`、`PermissionMode`、`LocalRuleStore`。

**依赖：** `Path`、`fnmatch`、`re`、PyYAML、已有 `ToolCall`/`Tool`/`ToolResult` 和 `SensitiveDataRedactor`；不依赖 Agent、Provider 或 Textual。

**危险命令策略：** 使用固定、编译后的跨 Shell 正则组识别以下类别：根目录、盘符或广泛通配目标的递归/强制删除；格式化与分区工具；关机和重启；系统目录删除；以及 `git clean`、`git reset --hard` 等破坏性 Git 清理。模式常量不读取 YAML，也没有关闭开关。命中时返回 `dangerous_command`，并明确说明该保护不可配置放开。

**路径策略：** 提取专用文件工具的路径参数，使用共享路径解析函数将其解析为规范化项目相对路径。解析函数先取得真实项目根目录，再对候选路径执行非严格解析，借此解析已存在的符号链接和其父级；候选路径不能相对真实根目录表示时返回 `path_outside_workspace`。`find_files` 的 glob 在枚举前拒绝绝对路径和含 `..` 的路径段；`search_code` 的可选起始路径走同一解析。文件工具实际执行前也复用这一解析函数，避免“权限检查通过、执行时以另一套路径规则访问”的双重实现。命令工具不进入该路径策略，始终由 `RunCommandTool` 的工作目录启动。

**规则策略：** 规则来源固定为 `~/.mewcode/permissions.yaml`、`mewcode.permissions.yaml`、`mewcode.permissions.local.yaml`。不存在的规则文件等价于空层；已存在但无法读取、不是 YAML 映射、`rules` 不是列表、规则结构不完整、动作不是 allow/deny 或规则语法不合法时，将该层标记为读取错误并形成该层的拒绝决定。这样不会因坏配置默许执行，同时更高优先级的正常匹配仍可生效。本地写入使用临时文件和替换操作，保留已有合法规则，且只追加精确 allow。

**模式策略：** 对所有通过硬边界且未命中规则的只读调用返回允许。`default` 对未命中副作用返回 `ASK`；`acceptEdits` 自动允许 `write_file` 和 `edit_file`，对命令返回 `ASK`；`plan` 拒绝所有副作用；`bypassPermissions` 允许所有未命中副作用。明确 `deny` 总是先于模式生效；黑名单与路径沙箱更早生效。`set_mode` 只更新当前会话的内存状态，供下一次 Agent 工具选择和权限判断共同读取。

### `src/mewcode/tools/filesystem.py`

**职责变化：** 将当前私有的工作区路径判断提升为可复用解析函数，并让读取、写入、编辑、查找和搜索的路径型参数统一复用该函数。

**对外接口：** 导出返回“规范路径或结构化路径失败”的路径解析边界，供权限层和各文件工具调用。

**边界：** 不在文件工具中解析权限模式、YAML 或人工确认；文件工具仍负责 UTF-8、大小、原子写入和各自的业务错误。

### `src/mewcode/config.py` 与 `mewcode.yaml.example`

**职责变化：** 新增 `PermissionConfig(mode: PermissionMode)` 并从主配置的 `permissions.mode` 读取 `default`、`acceptEdits`、`plan` 或 `bypassPermissions`，缺省值为 `default`。不合法值在启动时作为配置错误明确报告。

**对外接口：** `AppConfig.permissions`；现有 Provider 与 Agent 配置接口保持不变。

**配置边界：** 主配置只决定整体模式；三层规则仍由固定 YAML 路径加载，避免把本地规则路径或规则内容混入可提交的主连接配置。示例配置补充三种模式说明和项目共享规则示例的入口说明。

### `src/mewcode/tools/executor.py`

**职责变化：** 在现有未知工具、模式可用工具和参数对象检查之后、真实工具执行之前调用 `ExecutionPolicy` 与 `PermissionManager`。通过 `PermissionManager` 的 `ASK` 决定创建 `PermissionRequest` 并等待回调结果；得到允许才调用真实工具。

**对外接口：** 将原 `ApprovalCallback[[ToolCall], bool]` 改为 `PermissionApprovalCallback[[PermissionRequest], ApprovalChoice]`；`execute` 与 `execute_many` 接收一个 `PermissionManager` 和该回调。

**执行顺序：** 对已注册、当前模式可用且参数是对象的调用：先保留既有任务授权/先读门禁，再进行危险命令检查、专用文件路径沙箱、规则层匹配、权限模式、必要的确认，再执行工具。Plan 模式先通过既有只读工具过滤阻止副作用暴露，再以权限决定为第二层保护。任一拒绝都不调用真实工具，并以 `dangerous_command`、`path_outside_workspace`、`rule_denied`、`permission_plan`、`permission_rejected` 或 `permission_config_error` 等错误码返回。批次顺序、只读并发和取消语义保持不变；有副作用的待确认调用仍是顺序屏障。

### `src/mewcode/policy.py` 与 `src/mewcode/agent.py`

**职责变化：** `ExecutionPolicy` 继续维护任务授权、读取证据和验证目标，但不再以 `blocking_failure` 让 Agent 在一次拒绝后提前停止。Agent 为一次会话持有同一个 `PermissionManager`，保证本会话允许和当前权限模式在后续 Agent 运行中有效；每次运行仍创建独立的 `ExecutionPolicy`，避免读取/验证证据跨用户任务泄漏。

**对外接口：** `Agent.run(...)` 改为接收通用权限确认回调，并根据 `PermissionManager.mode` 在 `plan` 时使用既有只读工具集合与规划提示、在其余三档时使用完整工具集合；`AgentFinished` 不再用 `POLICY_VIOLATION` 表示单次工具拒绝。权限失败工具结果写入 Conversation 后，正常发起下一轮模型请求；终止仍仅来自完成、取消、迭代上限、连续未知工具、Provider 失败或既有未验证修改。

**边界：** Agent 不直接读取 YAML、不执行权限规则，也不自行弹窗；它仅把执行器的结果按现有协议回灌并继续循环。现有敏感信息脱敏继续覆盖模型文本、工具结果和新增确认摘要。

### `src/mewcode/tui/widgets.py` 与 `src/mewcode/tui/app.py`

**职责变化：** 用 `PermissionConfirmation` 替换 `CommandConfirmation`。弹窗展示脱敏的工具摘要、影响说明和四个动作按钮：仅本次允许、本会话允许、永久允许、拒绝。App 将选择转换为 `ApprovalChoice`，并在取消生成时解除等待、自动按拒绝处理。App 增加 `Shift+Tab` 快捷键：在空闲时按 `default → acceptEdits → plan → bypassPermissions → default` 循环，调用权限管理器的 `set_mode`，同步状态栏，并让下一次请求使用该模式对应的 Agent 工具集合；生成期间不允许切换。

**对外接口：** `ChatApp` 提供 `PermissionApprovalCallback`，但不会决定规则优先级或写文件。工具活动行继续显示结构化拒绝的摘要和错误状态。

**边界：** 不新增图形化规则编辑器或审计视图；主配置只设定启动时模式，Shift+Tab 只改变当前会话。旧的默认命令确认行为由默认模式的通用确认覆盖，其他副作用文件工具也获得同等级确认。现有 `/plan` 入口保留，并与权限 `plan` 模式同步；`/do` 恢复 `default`。

### `.gitignore`、规则示例与测试

**职责变化：** 将 `mewcode.permissions.local.yaml` 加入忽略列表。新增受版本控制的 `mewcode.permissions.yaml.example`，说明项目共享规则的 YAML 格式、规范工具名与精确/glob 例子；不生成真实用户全局或本地规则文件。

**测试职责：** 新增权限单元测试覆盖五层判断、匹配与坏配置；扩展文件工具、执行器、Agent、配置和 TUI 测试覆盖复用路径解析、回调选择、拒绝回灌、循环继续、模式切换、持久化以及弹窗交互。

## 模块交互

```text
启动
  → load_config() 读取 permissions.mode
  → CLI 创建 PermissionManager(项目根目录、模式)
  → PermissionManager 加载：用户 → 项目 → 本地规则层
  → Agent / TUI 持有同一 PermissionManager（会话状态）
  → Shift+Tab 可在空闲时循环更新当前会话模式与状态栏

模型工具调用
  → ToolExecutor：注册表 / 当前模式 / 参数 / 既有流程门禁
  → PermissionManager.decide(call, tool)
       ├─ 命令：危险黑名单命中 → 拒绝
       ├─ 专用文件工具：真实路径不在项目内 → 拒绝
       ├─ SESSION → LOCAL → PROJECT → USER 规则命中
       │    ├─ 同层 deny → 拒绝
       │    ├─ allow → 允许
       │    └─ 规则层损坏且无更高命中 → 拒绝
       └─ 未命中：只读允许；副作用按四档模式决定
  → default，或 acceptEdits 中的命令 + ASK
       → TUI PermissionConfirmation
       → once：只允许当前调用
       → session：追加精确会话 allow 后允许
       → permanent：原子追加本地精确 allow 后允许
       → reject / 取消：结构化拒绝
  → 允许：真实工具执行；拒绝：不执行
  → ToolResult 回灌 Conversation
  → Agent 下一轮继续请求模型
```

## 文件组织

```text
src/mewcode/
├── permissions.py                 # 五层判断、规则加载/匹配、会话与本地规则存储
├── config.py                      # PermissionConfig 与四档启动模式解析
├── policy.py                      # 保留任务授权、先读和验证追踪，去除拒绝即终止
├── agent.py                       # 持有会话权限对象并持续回灌拒绝结果
├── tools/
│   ├── executor.py                # 调用权限层后才执行真实工具
│   └── filesystem.py              # 共享的符号链接安全路径解析
└── tui/
    ├── app.py                     # 通用权限确认回调与 Shift+Tab 模式切换
    └── widgets.py                 # 四选项权限确认弹窗

tests/
├── test_permissions.py            # 黑名单、沙箱、规则、模式、确认与持久化
├── test_config.py                 # 四档 permissions.mode 解析与非法值
├── test_tools.py                  # 共享路径解析与执行器不执行拒绝调用
├── test_policy.py                 # 既有流程门禁不再中止循环的回归
├── test_agent.py                  # 拒绝回灌后模型调整、四档工具暴露与继续
└── test_tui.py                    # 四种确认选择、Shift+Tab 与取消行为

mewcode.permissions.yaml.example   # 可提交项目规则示例
.gitignore                          # 忽略本地规则文件
mewcode.yaml.example                # 权限模式示例
doc/ch5/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 权限入口 | `ToolExecutor` 执行前统一判断 | 所有真实工具均经过此处，能保证拒绝没有副作用且结果可回灌模型。 |
| 硬性层顺序 | 黑名单 → 文件路径沙箱 → 规则 → 模式/确认 | 满足规格的不可绕过关系；后续层不能覆盖前置拒绝。 |
| 命令路径安全 | 命令固定项目工作目录，不伪造操作系统级路径沙箱 | PowerShell 文本可通过变量、脚本和外部程序绕过字符串解析；规则、黑名单和人工确认是本章的诚实边界。 |
| 规则优先级 | 会话 > 本地 > 项目 > 用户；同层 deny 优先 | 同时满足已确认的个人例外覆盖和最小意外放行原则。 |
| 规则匹配 | 规范工具名 + 单一规范化主参数，精确或 glob | 对当前工具可解释且可测试，避免按参数字典序列化造成不稳定匹配。 |
| 坏规则文件 | 在所属层形成拒绝，较高层有效匹配仍优先 | 失败默认安全，同时保留会话或本地的更具体安全控制。 |
| 永久授权范围 | 原子追加一条精确本地 allow，并加入 Git 忽略 | 不扩大到其他调用或其他项目，也不污染团队共享配置。 |
| 权限模式 | default / acceptEdits / plan / bypassPermissions | 对应图片要求；acceptEdits 降低常规编辑摩擦，plan 复用只读能力，bypassPermissions 仅跳过未命中确认。 |
| 模式切换 | Shift+Tab 循环，状态栏同步显示 | 不必编辑 YAML 即可在当前会话调整信任等级，且用户能观察实际生效模式。 |
| 人工确认 | 将命令二选一确认升级为所有需要确认的副作用调用的四选项确认 | 默认模式的一致体验，完整覆盖一次/会话/永久/拒绝语义。 |
| 既有执行策略 | 保留流程门禁，取消“拒绝即 Agent 终止” | 既不移除先读和验证保护，也让模型能在拒绝后调整策略。 |
| 路径解析 | 权限层和专用文件工具复用一套符号链接安全解析 | 消除检查与实际执行的路径理解差异，避免 TOCTOU 之外的逻辑绕过。 |

## Spec 覆盖映射

| Spec | 实现归属 |
|---|---|
| F1 | `PermissionManager` 的固定危险命令正则组与执行器前置调用 |
| F2 | `filesystem.py` 的共享路径解析和权限层文件工具路径判定 |
| F3 | `PermissionRule`、三层 YAML 加载器、规则示例 |
| F4 | `RuleSource`、分层匹配器与同层 deny 优先 |
| F5 | 四档 `PermissionMode`、主配置解析和未命中模式回退 |
| F6、F7 | `PermissionRequest`、`ApprovalChoice`、会话规则、本地原子写入和 Textual 弹窗 |
| F8 | 执行器拒绝 `ToolResult`、Conversation 回灌和 Agent 继续循环 |
| F9 | `PermissionManager.decide` 的固定判断顺序与集成测试 |
| F10 | `PermissionManager.set_mode`、Agent 的 plan 工具过滤、TUI Shift+Tab 与状态栏 |
| F11 | 只读默认允许、现有执行器批处理与 Agent 多轮回归 |
| N1 | 决策对象包含来源/规则/原因，确定性规则测试 |
| N2 | `RuleLayer.error` 与所属层拒绝决定、配置错误测试 |
| N3 | 固定三层规则路径、会话内存状态、本地规则 Git 忽略 |
| N4 | 无配置入口的黑名单与统一共享路径解析 |
| N5 | 复用 `SensitiveDataRedactor` 生成确认和回灌摘要 |
| N6 | TUI 模式状态与 PermissionManager 当前模式的集成测试 |
