# YuCode 权限模式边界与内嵌确认 Plan

## 架构概览

将当前会话的权限状态收敛为唯一来源：权限管理器保存 `default`、`acceptEdits`、`plan`、`bypassPermissions` 之一，并额外记住最近一次非 Plan 状态。Agent 每轮根据该状态决定使用完整工具集合还是只读工具集合；TUI 只发出状态切换意图，不再维护独立且可能冲突的 Do/Plan 状态。

执行前改为只有 `PermissionManager.evaluate()` 产出 `ALLOW`、`DENY` 或 `ASK`。任务执行意图仍由纯分类函数生成，工具流程仍由独立状态对象维护，但二者不再直接返回工具失败；它们只向权限管理器提供裁决输入。权限管理器按固定顺序组合危险边界、路径、执行意图、规则、流程前置条件、工具安全级别和模式，确保一次调用只有一个主结果。

原 `policy.py` 拆除：任务执行意图及敏感信息脱敏迁入权限模块，工具流程状态迁入新的 `workflow.py`。执行器不再先调用 policy、再调用 permissions，也不再使用独立的 Plan 工具拒绝覆盖权限原因。Agent 仍在 Plan 下只向模型公开只读工具，但模型臆造已有副作用工具时，由统一权限裁决返回 `permission_plan`。

人工确认从模态页面改为聊天流内的 `InlinePermissionCard`。模型工具调用到达后，原有工具活动行显示“等待权限”；App 在其下方挂载卡片并等待 Future。键盘事件优先交给卡片，卡片选中后原地更新，执行器再执行工具或回传拒绝结果。整个过程不切换 Screen，不改变聊天视图，也不让确认按键进入输入框。

## 核心数据结构

### `PermissionManager` 的状态恢复接口

```python
class PermissionManager:
    @property
    def mode(self) -> PermissionMode: ...

    def set_mode(self, mode: PermissionMode) -> None: ...
    def resume_do_mode(self) -> PermissionMode: ...
    def evaluate(
        self,
        call: ToolCall,
        tool: Tool,
        authorization: TaskAuthorization,
        workflow: ToolWorkflow,
    ) -> PermissionDecision: ...
```

内部保存 `last_do_mode`，其值只能是 `default`、`acceptEdits` 或 `bypassPermissions`。从任一 Do 状态进入 Plan 时记录该值；从 Plan 经 `/do` 离开时恢复该值，首次没有记录则选择 default。通过 Shift+Tab 直接切到非 Plan 状态时，同步更新 `last_do_mode`。

`evaluate()` 是唯一权限裁决入口，固定执行：危险命令 → 路径沙箱 → 用户执行意图 → 明确 deny/配置错误 → 工具流程前置条件 → 只读放行 → Plan 拒绝 → 明确 allow → bypass/acceptEdits/default 回退。它只返回一个 `PermissionDecision`；流程失败统一使用 `workflow_precondition`，用户没有授权副作用统一使用 `task_not_authorized`。

### `ToolWorkflow`

```python
@dataclass(frozen=True)
class WorkflowIssue:
    reason: str
    error_code: str = "workflow_precondition"

class ToolWorkflow:
    read_targets: set[str]
    pending_verifications: set[str]

    def check(self, call: ToolCall) -> WorkflowIssue | None: ...
    def record(self, result: ToolResult) -> None: ...
```

`ToolWorkflow` 只维护“是否已读取”“是否待验证”等运行期证据，并报告可修复问题，不了解权限模式、规则或人工确认，也不直接构造 `ToolResult`。用户执行意图由权限管理器检查；流程对象只检查专用工具优先、编辑/覆盖前读取和写后验证状态。

### `InlinePermissionCard`

```python
class InlinePermissionCard(Static):
    call_id: str

    def handle_key(self, key: str) -> ApprovalChoice | None: ...
    def mark_selected(self, choice: ApprovalChoice) -> None: ...
    def finish(self, result: ToolResult) -> None: ...
```

卡片持有已脱敏的权限请求摘要、影响说明和当前高亮选项。数字 `1` 到 `4` 直接返回对应选择；上/下改变高亮，Enter 确认，Escape 返回拒绝。选择后显示“已选择，正在继续”；拿到工具结果后在原位置显示最终执行或拒绝结果。卡片不保存完整文件内容、凭据或未脱敏的命令。

### `ChatApp` 等待状态

```python
class ChatApp:
    _pending_approval: asyncio.Future[ApprovalChoice] | None
    _active_permission_card: InlinePermissionCard | None
    _permission_cards: dict[str, InlinePermissionCard]

    async def _request_permission_approval(
        self, request: PermissionRequest
    ) -> ApprovalChoice: ...
    def handle_permission_key(self, event: Key) -> bool: ...
```

`_pending_approval` 只代表当前串行副作用调用的选择；`_permission_cards` 保留到该工具获得结果，支持原地更新。取消生成、Escape 或卡片状态失效都会完成 Future 为 `REJECT`。所有已处理的键盘事件都停止传播并阻止默认输入编辑。

## 模块设计

### `src/yucode/permissions.py`

**职责变化：** 将四档状态作为唯一会话权限来源，维护并恢复最近的 Do 状态；同时承接任务执行意图分类、敏感信息脱敏和最终权限裁决，使外部模块不能直接产生权限拒绝。

**对外接口：** 保留 `mode`、`set_mode`、`resume_do_mode`，以 `evaluate` 替换分散的 `decide`/policy 前置判断；确认范围接口不变。

**状态与裁决策略：** `set_mode(plan)` 首次进入时保存当前 Do 状态；`set_mode` 接受任一 Do 状态时更新恢复值。`resume_do_mode` 在 Plan 外调用时保持当前状态，在 Plan 内恢复已保存值或 default。`evaluate` 按 Spec F9 的顺序返回唯一结果；acceptEdits 仅自动允许已获用户执行授权、满足流程前置条件且位于项目内的 `write_file`/`edit_file`。

### `src/yucode/workflow.py`

**职责：** 承接原执行策略中的工具流程状态和前置条件，返回 `WorkflowIssue`，不再拥有权限裁决能力。

**边界：** 不读取权限模式或规则，不进行任务意图分类，不弹出确认，不直接构造拒绝工具结果。原 `policy.py` 删除。

### `src/yucode/tools/executor.py`

**职责变化：** 解析工具和参数后只调用 `PermissionManager.evaluate()`。结果为 ASK 时仍由权限管理器处理人工确认；结果为 DENY 时统一转换为结构化 `ToolResult`；结果为 ALLOW 才执行真实工具。工具结束后只调用 `ToolWorkflow.record()` 更新证据。

**边界：** 未知工具和无效参数仍属于执行器输入错误；已注册工具的 Plan、任务授权、流程、规则与模式结果全部来自权限管理器。

### `src/yucode/agent.py`

**职责变化：** 删除来自 UI 的独立 Do/Plan 模式输入，以权限管理器当前状态作为每次运行的唯一模式来源。Agent 在 plan 时选择既有只读工具集合与规划提示，在其他三档选择完整工具集合；每轮创建 `TaskAuthorization` 与 `ToolWorkflow`，交给执行器作为统一裁决输入。

**对外接口：** `Agent.run` 不再接收外部 RunMode；保留内部将权限状态映射为提示运行模式的逻辑。原有拒绝回灌、取消、迭代上限和验证追踪不变。

### `src/yucode/tui/widgets.py`

**职责变化：** 删除模态 `PermissionConfirmation`，新增聊天流内的 `InlinePermissionCard`。卡片用文本高亮表示当前选择，提供 1–4、方向键、Enter、Escape 的键盘语义及执行结果展示。

**依赖：** 权限请求、确认选择和工具结果；不依赖 Agent、执行器或 Future。

### `src/yucode/tui/app.py`

**职责变化：** 以权限管理器状态替代 `_mode`；`/plan` 调用 `set_mode(plan)`，`/do` 调用 `resume_do_mode`，Shift+Tab 调用 `set_mode`。状态栏统一读取权限管理器当前值。

**确认流程：** App 收到确认请求时挂载卡片、保存 Future 和卡片索引、滚动到卡片；键盘先交给 `handle_permission_key`；选择后完成 Future。收到相同调用的 `ToolResultReady` 时，更新对应卡片和原有工具活动行。取消时完成拒绝并更新卡片，绝不 push/dismiss Screen。

**边界：** 生成期间仍禁止切换模式；普通输入在 Agent 运行时保持禁用；没有待确认卡片时，数字和方向键遵循原有输入行为。

### 测试

**文件：** `tests/test_permissions.py`、`tests/test_workflow.py`、`tests/test_tools.py`、`tests/test_agent.py`、`tests/test_tui.py`。

**职责：** 覆盖 Do 状态恢复、四档工具决定、中文“写”意图、统一裁决优先级、稳定错误分类、流程补读重试、Agent 仅依赖权限状态、内嵌卡片和现有模式切换回归。

## 模块交互

```text
状态切换
  Shift+Tab → PermissionManager.set_mode(next)
  /plan     → PermissionManager.set_mode(plan)，保存 last_do_mode
  /do       → PermissionManager.resume_do_mode()
  → TUI 状态栏读取 mode
  → 下一轮 Agent 依据 mode 选择只读或完整工具

需要确认的工具调用
  模型工具调用 → ToolActivity 显示等待
  → PermissionManager.evaluate(call, tool, authorization, workflow)
       → 硬边界/意图/deny/流程问题：返回唯一 DENY
       → 只读/allow/模式直通：返回 ALLOW
       → default 或 acceptEdits 命令未命中：返回 ASK
  → App 在聊天流挂载 InlinePermissionCard 并等待
  → 1/2/3/4 或 ↑/↓/Enter/Escape
       → 卡片原地标记选择
       → Future 返回权限选择
       → 执行器执行工具，或构造拒绝 ToolResult
       → ToolResultReady 更新活动行与同一张卡片
       → 结果回灌模型，Agent 继续
```

## 文件组织

```text
src/yucode/
├── permissions.py       # 唯一权限裁决、执行意图、规则与模式
├── workflow.py          # 读取/编辑/验证流程证据与可修复问题
├── agent.py             # 从权限状态选择工具集合和提示模式
├── tools/executor.py    # 调用统一裁决入口并执行工具
└── tui/
    ├── app.py           # 内嵌确认等待、键盘分发、/do /plan 同步
    └── widgets.py       # InlinePermissionCard

tests/
├── test_permissions.py  # 四档状态、意图和统一裁决优先级
├── test_workflow.py     # 工具流程状态与前置条件
├── test_tools.py        # 执行器统一入口集成
├── test_agent.py        # 权限状态决定 Plan/Do 工具范围
└── test_tui.py          # 对话内卡片、数字/方向键和取消恢复

doc/ch6/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Do/Plan 表示 | 权限管理器为唯一状态来源，Do 是三种非 Plan 状态的统称 | 消除 TUI RunMode 与权限 Plan 同时存在的冲突。 |
| `/do` 行为 | 恢复最近 Do 状态，首次回 default | 从 Plan 返回不会意外降低或扩大用户先前选择的信任等级。 |
| acceptEdits 门禁 | 修复明确中文写入意图识别，保留解释/评审的副作用保护 | 解决用户已明确要求写文件却被旧关键词遗漏拒绝的问题。 |
| 确认样式 | 聊天流内静态卡片，不使用模态页面 | 保留上下文和终端连续性，贴近 Claude Code/OpenCode 的交互。 |
| 确认操作 | 数字直选 + 方向键/Enter + Escape 拒绝 | 高效、可发现，并避免鼠标依赖。 |
| 卡片生命周期 | 请求、选择、工具结果均在同一卡片更新 | 用户可追溯当前调用是否执行，避免独立弹窗脱离工具活动。 |
| 权限裁决入口 | `PermissionManager.evaluate` 统一组合全部裁决输入 | 消除 policy 与 permissions 先后返回矛盾结果的问题。 |
| 流程保护 | 独立 `ToolWorkflow` 只报告问题，由权限管理器决定最终 DENY | 保留先读后改与写后验证，同时让失败原因属于稳定的流程分类。 |
| Plan 臆造工具 | 工具列表负责预防，权限管理器负责最终拒绝 | 既减少模型误调用，又保证已有工具的拒绝原因统一为 `permission_plan`。 |
| 原 policy 模块 | 删除并按“权限裁决 / 工具流程”重新归属 | 文件边界与职责一致，避免继续形成第二个权限中心。 |

## Spec 覆盖映射

| Spec | 实现归属 |
|---|---|
| F1、F2 | `PermissionManager` 状态与 Agent 工具选择 |
| F3 | `last_do_mode`、TUI `/do`/`/plan`/Shift+Tab |
| F4 | `classify_authorization` 的执行意图覆盖 |
| F5、F6、F7 | `InlinePermissionCard` 与 App Future/键盘流程 |
| F8 | 既有执行器 ToolResult 回灌与 Agent 循环回归 |
| F9、F10、N5 | `PermissionManager.evaluate`、`ToolWorkflow` 与执行器集成测试 |
| N1 | 状态栏、权限决定、Agent 工具集合的集成测试 |
| N2、N4 | 键盘事件拦截、Future 清理和取消测试 |
| N3 | 复用权限层脱敏请求，卡片仅接收安全摘要 |
