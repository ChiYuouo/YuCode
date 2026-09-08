# MewCode 权限模式边界与内嵌确认 Checklist

> 每一项均以可观察的运行行为、测试结果或终端交互验证。

> 第一阶段执行记录（2026-09-08）：`compileall` 成功；定向测试与完整 pytest 均无失败，2 项既有 Windows 符号链接用例因当前环境无创建权限跳过。已在 WSL 的 tmux 3.6 中完成真实 TUI 验收：AcceptEdits 写入并读取临时文件成功；Default 内嵌卡片分别以 `1` 允许和 `4` 拒绝；Plan 阻止写入且临时文件内容保持 `1`；`/do` 分别恢复 AcceptEdits 和 BypassPermissions。临时文件与 tmux 会话均已清理。

> 单一裁决入口增补执行记录（2026-09-08）：旧 `policy.py` 与对应测试已移除；源码和测试编译成功，完整 pytest 无失败，2 项 Windows 符号链接用例按平台条件跳过。真实 tmux TUI 中，AcceptEdits 首次直接覆盖已有临时文件只返回 `workflow_precondition`，Agent 随后补读、重试并读取验证，最终内容为 `final`；Plan 新建文件只返回 `permission_plan` 且文件未出现；危险命令只返回 `dangerous_command`；项目本地 deny 只返回 `rule_denied` 且目标文件未出现。四次拒绝后 Agent Loop 均继续回复。专用临时文件、本地规则和 tmux 会话均已核对并清理。

## 权限状态与硬边界

- [x] 四种状态只保留 `default`、`acceptEdits`、`plan`、`bypassPermissions`，且状态区域、Agent 可用工具和权限决定一致。（验证：运行权限与 TUI 集成测试，并观察 Shift+Tab 后状态文字和下一次调用行为。）
- [x] `default` 对未匹配的有副作用调用请求确认；`acceptEdits` 直接执行项目内专用文件创建、写入和编辑，但命令仍请求确认。（验证：运行权限测试，分别发起文件与命令调用并比对决定结果。）
- [x] `plan` 只暴露只读工具；`bypassPermissions` 跳过未命中确认。（验证：运行 Agent 与权限测试，检查 Plan 工具集和 bypass 的决定结果。）
- [x] 危险命令黑名单、路径沙箱和明确 `deny` 在四种状态中均持续拒绝。（验证：在每种状态运行黑名单、项目外路径和 deny 规则用例，观察结构化拒绝结果。）

## Do 与 Plan 边界

- [x] 从 `acceptEdits` 进入 Plan 后执行 `/do`，恢复为 `acceptEdits`。（验证：在 TUI 或集成测试中依次切换并观察状态区域。）
- [x] 从 `bypassPermissions` 进入 Plan 后执行 `/do`，恢复为 `bypassPermissions`。（验证：在 TUI 或集成测试中依次切换并观察状态区域。）
- [x] 首次在 Plan 执行 `/do` 时恢复为 `default`；直接切换到任一 Do 状态后会成为下次恢复目标。（验证：运行 `PermissionManager` 状态恢复测试。）
- [x] Shift+Tab 每次切换后立即显示实际生效状态，且下一次工具调用按该状态处理。（验证：端到端连续切换并发起对应文件或命令调用。）

## 明确执行意图与副作用保护

- [x] “写一个数字 1 到 hello.txt”“创建文件”“修改已有文件”等直接中文请求会被识别为执行授权，不再出现“未明确授权副作用操作”的误拒绝。（验证：运行任务授权测试及 acceptEdits 集成用例。）
- [x] 单纯解释、评审、建议、比较或提问时，即使模型尝试写入也不会产生副作用。（验证：运行反例测试，观察写入调用被任务授权层拒绝且目标文件不出现或不改变。）

## 对话内人工确认

- [x] 默认模式的待确认操作在对应工具活动旁显示内嵌卡片，不打开独立页面或遮挡聊天。（验证：启动 TUI 发起需确认调用，观察卡片位置和 Screen 数量。）
- [x] 卡片显示已脱敏的工具、目标、影响说明和“尚未执行”状态，不显示完整写入内容或凭据。（验证：使用包含敏感片段的请求，检查卡片可见文字。）
- [x] 数字 `1` 至 `4` 分别完成仅本次允许、本会话允许、永久允许、拒绝；相应授权范围在后续调用中生效。（验证：逐一选择并重复相同调用，观察是否再次询问及本地规则写入行为。）
- [x] 上下方向键加 Enter 可选择卡片项；Escape、取消生成和失去有效确认状态都按拒绝处理。（验证：运行 TUI 键盘和取消测试，观察 Future 完成、卡片状态与输入恢复。）
- [x] 待确认期间真实工具尚未执行；允许或拒绝后同一卡片原地显示选择和最终结果，对话、工具活动与滚动位置仍保留。（验证：在工具执行前后检查目标文件、卡片文本和聊天记录。）
- [x] 确认键不会被写入普通消息输入框；无活动卡片时，输入框仍保留原有键盘行为。（验证：在两种状态按数字、方向键、Enter、Escape 并检查输入内容。）

## 拒绝回灌与 Agent Loop

- [x] 明确拒绝、黑名单、路径拒绝和规则拒绝均以结构化结果回传模型，Agent 可尝试安全替代调用或解释所需授权。（验证：运行 Agent 拒绝回灌测试，观察首次调用无副作用、后续安全调用或回复继续产生。）

## 编译与测试

- [x] 源码可编译。（验证：运行 `.venv\Scripts\python.exe -m compileall -q src`，命令成功结束。）
- [x] 权限、任务授权、Agent 和 TUI 定向测试通过。（验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_workflow.py tests/test_agent.py tests/test_tui.py -q`。）
- [x] 完整测试套件通过。（验证：运行 `.venv\Scripts\python.exe -m pytest -q -rA`，除平台限制的既有跳过项外无失败。）

## tmux 端到端场景

- [x] 在 tmux 中启动 MewCode，切换到 `acceptEdits` 后请求向本章专用临时文件写入 `1`；观察文件调用直接完成，且不会被“未明确授权副作用操作”拒绝。（验证：读取临时文件确认内容；不触碰现有 `hello.txt`、`note.txt` 或其他用户文件。）
- [x] 在 `default` 下请求有副作用操作；观察聊天流内出现四选项确认卡片，使用 `1` 允许后工具执行并更新同一卡片。（验证：观察终端画面与临时文件结果。）
- [x] 在 `acceptEdits` 和 `bypassPermissions` 各自进入 Plan 后执行 `/do`；观察恢复到进入前的 Do 状态。随后在 Plan 请求写入，观察只读规划回复且临时文件不变化。（验证：观察状态栏、聊天回复和临时文件内容。）
- [x] 端到端完成后，只清理本章创建且已核对路径的临时文件，并将每项实际结果记录回本清单。（验证：确认用户原有文件未改动，清单条目保留通过或失败状态与简要证据。）

## 单一裁决入口增补验收

- [x] 同一个已注册工具调用只得到一个 `ALLOW`、`DENY` 或 `ASK` 结果，且按危险命令、路径、用户执行意图、deny、流程前置条件、只读、Plan、allow、模式回退的顺序决定。（验证：运行权限优先级矩阵测试，逐项比对唯一结果及原因分类。）
- [x] 解释、评审或普通提问触发副作用工具时只返回 `task_not_authorized`，不进入权限规则、模式放行或确认流程。（验证：在 default、acceptEdits 和 bypassPermissions 中运行反例，观察目标文件均不变化且不弹出确认卡片。）
- [x] 覆盖或编辑已有文本但尚未读取时只返回 `workflow_precondition`；同一 Agent 补读后重试可进入当前模式对应的 allow 或 ASK 流程。（验证：运行读取前失败、读取后成功的执行器与 Agent 集成测试。）
- [x] acceptEdits、bypassPermissions、会话 allow 和本地 allow 均不能跳过危险命令、路径沙箱、明确 deny 或工具流程前置条件。（验证：对四类放行来源运行同一组硬边界与缺少读取用例，观察全部按固定优先级拒绝。）
- [x] Plan 不向模型公开副作用工具；若模型仍臆造调用已注册的副作用工具，返回 `permission_plan`，不再返回来自另一套门禁的 `tool_not_available` 或 `policy_violation`。（验证：运行 Agent Plan 幻觉工具测试并检查结构化结果。）
- [x] 流程失败、权限拒绝和人工确认在工具活动、确认卡片与模型历史中使用同一个主原因，不出现“权限允许但策略拒绝”等矛盾信息。（验证：运行 TUI 与 Agent 集成测试，对比同一 `ToolResult` 的界面摘要和模型正文。）
- [x] 带 BOM 的 UTF-16 文本仍能成功读取，并在读取后按权限模式覆盖或编辑；真正含 NUL 的非文本文件仍被识别为二进制。（验证：运行 UTF-16 回归测试和二进制反例测试。）
- [x] 旧的第二套权限入口已经消失。（验证：运行 `rg -n "ExecutionPolicy|policy_violation|mewcode\.policy" src tests`，期望无输出；运行编译检查，期望模块导入成功。）
- [x] 统一裁决后的完整测试无失败。（验证：运行 `.venv\Scripts\python.exe -m compileall -q src tests` 和 `.venv\Scripts\python.exe -m pytest -q -rA`。）
- [x] 在 tmux 中走完整流程：AcceptEdits 覆盖已有临时文本时先收到单一 `workflow_precondition`，Agent 补读后完成；随后验证 Plan、危险命令和 deny 各自只显示一个稳定原因，Agent Loop 继续。（验证：观察真实 TUI 工具活动和最终文件内容，结束后只清理本章专用临时文件。）
