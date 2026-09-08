# YuCode 权限模式边界与内嵌确认 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改、测试 | `src/yucode/permissions.py`、`tests/test_permissions.py` | 保存最近 Do 状态，并验证四档状态的恢复边界。 |
| 修改、测试 | `src/yucode/permissions.py`、`tests/test_permissions.py` | 正确识别“写”等直接中文执行意图，并统一输出权限裁决。 |
| 新建、测试 | `src/yucode/workflow.py`、`tests/test_workflow.py` | 维护读取、编辑和验证流程证据，只报告可修复的前置问题。 |
| 删除 | `src/yucode/policy.py`、`tests/test_policy.py` | 移除旧的第二套拒绝入口，并将职责迁移到权限与流程模块。 |
| 修改、测试 | `src/yucode/tools/executor.py`、`tests/test_tools.py` | 只调用统一权限裁决入口，并在执行后记录流程证据。 |
| 修改、测试 | `src/yucode/agent.py`、`tests/test_agent.py` | 仅从权限管理器读取 Plan/Do 工具范围。 |
| 修改、测试 | `src/yucode/tui/widgets.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py` | 提供对话流内权限确认卡片及其显示状态。 |
| 修改、测试 | `src/yucode/tui/app.py`、`tests/test_tui.py` | 接入内嵌确认等待、键盘分发和统一的模式切换。 |
| 验证 | 上述源码与测试文件 | 运行单元、集成与 tmux 端到端验收。 |

## T1：保存并恢复最近的 Do 权限状态

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** 无

**步骤：**

1. 在 `PermissionManager` 中记录最近一次 `default`、`acceptEdits` 或 `bypassPermissions` 状态。
2. 进入 `plan` 时保存当前 Do 状态；直接切换到任一 Do 状态时更新保存值。
3. 新增 `resume_do_mode()`，在 Plan 中恢复保存状态，首次无记录时恢复 `default`；在非 Plan 中保持当前状态。
4. 为 acceptEdits、bypassPermissions、首次恢复和直接切换补充单元测试，确认现有硬边界决定不回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q`，期望全部通过。

## T2：补齐直接中文写入意图识别

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** 无

**步骤：**

1. 将“写”纳入直接执行动词，并保持“写入”、创建、修改、编辑、删除、运行、执行的识别结果。
2. 为“写一个数字 1 到 hello.txt”等明确请求增加回归测试，确认其被判定为可执行。
3. 为解释、评审、建议和提问补充反例，确认没有明确执行授权时仍维持只读保护。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py -q`，期望全部通过。

## T3：让 Agent 只依赖权限状态选择工具范围

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`

**依赖：** T1

**步骤：**

1. 移除 `Agent.run` 来自 TUI 的独立 Do/Plan 参数。
2. 在每次运行开始时读取 `PermissionManager.mode`，在 Plan 下使用只读工具和规划提示，其余三档使用完整工具集合。
3. 保留现有拒绝结果回灌、取消、轮次上限和验证追踪行为。
4. 更新 Agent 测试，覆盖 Plan 工具过滤与非 Plan 工具范围，并确认不再存在第二个状态来源。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q`，期望全部通过。

## T4：实现对话流内权限确认卡片

**文件：** `src/yucode/tui/widgets.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py`

**依赖：** 无

**步骤：**

1. 删除模态 `PermissionConfirmation`，新增 `InlinePermissionCard`，只接收权限层已脱敏的工具、目标和影响摘要。
2. 在卡片中显示操作尚未执行、四项选择和当前高亮项；不显示完整文件内容或凭据。
3. 实现数字 1–4、上下方向键、Enter、Escape 的选择逻辑，以及“已选择、等待结果”和最终结果的原地展示。
4. 调整样式，使卡片在聊天记录中清晰可读，并为卡片键盘行为写组件级测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "permission or inline"`，期望内嵌确认相关测试通过。

## T5：接入内嵌等待、键盘分发和统一模式入口

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T1、T3、T4

**步骤：**

1. 删除 `ChatApp` 的独立 `_mode`，让状态栏、Shift+Tab 和下一轮 Agent 都读取权限管理器的当前状态。
2. 令 `/plan` 进入 Plan，`/do` 调用 `resume_do_mode()`；Shift+Tab 切换后同步显示实际状态。
3. 收到 `ASK` 时在对应工具活动下挂载卡片并等待 Future，且真实工具在选择前不得执行。
4. 将确认键优先交给活动卡片；选择、Escape、取消生成或状态失效都完成等待并清理状态，禁止确认键写入普通输入框。
5. 在工具完成或拒绝后原地更新同一张卡片，保留对话与滚动位置；移除所有 `push_screen`/`dismiss` 确认流程。
6. 为默认模式确认、acceptEdits 文件直通与命令确认、bypass 跳过未命中确认、Plan 只读、键盘选择和取消清理补充集成测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`，期望全部通过。

## T6：执行全量回归与终端端到端验收

**文件：** `src/yucode/permissions.py`、`src/yucode/agent.py`、`src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_permissions.py`、`tests/test_agent.py`、`tests/test_tui.py`

**依赖：** T2、T5

**步骤：**

1. 运行编译检查和完整测试套件，修复本章改动造成的回归。
2. 使用 tmux 启动 YuCode，依次验证：acceptEdits 下明确写入请求、default 下内嵌确认、Plan 到 `/do` 的状态恢复，以及 Plan 不执行写入。
3. 使用独立的端到端临时文件，不修改现有 `hello.txt`、`note.txt` 或其他用户文件；测试后仅清理本章创建且已核对的临时文件。
4. 按 `doc/ch6/checklist.md` 记录每项验收结果和未覆盖原因（若有）。

**验证：** 运行 `.venv\Scripts\python.exe -m compileall -q src` 与 `.venv\Scripts\python.exe -m pytest -q -rA`，期望编译成功、测试全部通过；tmux 场景与 checklist 逐项一致。

## T7：拆出无权限职责的工具流程状态

**文件：** `src/yucode/workflow.py`、`tests/test_workflow.py`

**依赖：** T1–T6

**步骤：**

1. 新建 `WorkflowIssue` 和 `ToolWorkflow`，迁移读取目标、待验证目标、专用工具优先和编辑/覆盖前读取规则。
2. `check()` 只返回可修复问题，不构造 `ToolResult`，不读取权限模式或规则。
3. `record()` 只根据成功工具结果更新读取与验证证据。
4. 新建流程测试，覆盖缺少读取、补读后重试、写后待验证和失败结果不计入证据。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_workflow.py -q`，期望全部通过。

## T8：建立唯一权限裁决入口

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** T7

**步骤：**

1. 将任务执行意图分类和敏感信息脱敏迁入权限模块。
2. 以 `evaluate()` 替换旧 `decide()`，按危险命令、路径、执行意图、deny、流程、只读、Plan、allow、模式回退的固定顺序返回结果。
3. 为任务未授权和流程问题分别使用稳定的 `task_not_authorized`、`workflow_precondition` 分类。
4. 增加优先级矩阵测试，确认一次调用只有一个结果，acceptEdits/allow 不跳过流程，黑名单、路径和 deny 不被任何模式放开。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q`，期望全部通过。

## T9：让执行器与 Agent 使用统一裁决

**文件：** `src/yucode/tools/executor.py`、`src/yucode/agent.py`、`tests/test_tools.py`、`tests/test_agent.py`

**依赖：** T8

**步骤：**

1. 执行器解析已注册工具与参数后只调用 `PermissionManager.evaluate()`；删除独立 policy preflight。
2. 移除仅用于 Plan 二次拒绝的 `allowed_names` 通道，使已注册的臆造副作用工具统一返回 `permission_plan`。
3. 工具真实执行成功或失败后调用 `ToolWorkflow.record()`，DENY/ASK 继续转为同一结构化结果。
4. Agent 每轮创建执行意图和流程状态，提示所需的授权、读取与验证信息继续从对应对象读取。
5. 更新执行器和 Agent 测试，覆盖 Plan 臆造工具、拒绝回灌、补读重试及写后验证。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_agent.py -q`，期望全部通过。

## T10：移除旧 policy 模块并完成引用迁移

**文件：** `src/yucode/policy.py`、`tests/test_policy.py`、`src/yucode/permissions.py`、`src/yucode/workflow.py`、相关引用文件

**依赖：** T9

**步骤：**

1. 将旧 policy 测试分别迁入权限和流程测试。
2. 删除 `src/yucode/policy.py` 与 `tests/test_policy.py`。
3. 搜索全部源码和测试，确认不存在 `ExecutionPolicy`、`policy_violation` 或旧模块导入。
4. 运行编译和定向测试，确认模块依赖无环且导入成功。

**验证：** 运行 `rg -n "ExecutionPolicy|policy_violation|yucode\.policy" src tests`，期望无输出；随后运行 `.venv\Scripts\python.exe -m compileall -q src tests`，期望成功。

## T11：执行统一裁决全量与 tmux 验收

**文件：** 上述全部源码、测试及 `doc/ch6/checklist.md`

**依赖：** T10

**步骤：**

1. 运行完整测试，确认四档模式、内嵌确认和 UTF-16 文本兼容功能不回归。
2. 在 tmux 中以 AcceptEdits 触发覆盖前未读取场景，观察单一 `workflow_precondition`；让 Agent 补读后重试并完成。
3. 在 Plan、危险命令、路径逃逸和 deny 场景检查唯一稳定分类，不出现同时“允许”和“策略拒绝”。
4. 仅使用本章专用临时文件，验收后核对并清理，将实际证据记录到 checklist。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q -rA` 与 tmux 场景，期望无测试失败且新增清单全部通过。

## 执行顺序

```text
T1 ─┐
    ├→ T3 ─┐
T4 ─────────┼→ T5 ─┐
T2 ─────────────────┼→ T6
                    └→ T7 → T8 → T9 → T10 → T11
```

T1–T6 为已完成的原功能阶段。架构收敛按 `T7 → T8 → T9 → T10 → T11` 执行；每一步都依赖前一步提供的稳定接口，不形成循环依赖。
