# 后台任务可见性与执行一致性 Plan

## 模块总览

改动横跨四层，数据流向：`Agent/RunToCompletion`（执行事件）→ `SubagentService/TaskManager`（任务层转发）→ TUI（进度与命令展示）；Team 侧由 `TeamService.work()` 改走 `TaskManager` 登记与超时。

```
RunToCompletion(事件) ──progress 回调──> TaskManager(进度快照) ──listener──> TUI ForkActivity
                                                            └──/tasks 命令读取快照
skills/commands.py(inline) ──SOP 随消息──> 主会话
TUI InlinePermissionCard <──ToolResultReady/任务结束──收尾
```

## 模块设计

### M1：进度事件转发（F1）

**现状**：`RunToCompletion.run` 只消费事件统计 effects，`SubagentService.delegate` 中 `work()` 闭包调用 `runner.run(child, ...)`，`progress` 参数在 `run_fork` 中被丢弃。

**设计**：
- `RunToCompletion.run` 增加可选 `on_event` 回调，每收到 `ToolResultReady` 或迭代轮次推进时调用，传入 `f"第 N 轮 · 最近工具：{name}{'成功' if ok else '失败'}"` 风格的短文本。
- `SubagentService.delegate` 的 `work()` 闭包把该回调接入 `TaskManager`：`TaskSnapshot` 增加可变进度字段（`progress: str = ""`，dataclass 用 `replace` 更新），`_run` 中每次回调更新快照。
- `TaskManager.set_notification_listener` 旁新增 `set_progress_listener(listener)`；TUI 在 `on_mount`（现有 `app.py:124` 附近）注册 `show_skill_progress`。listener 异常一律吞掉（N1）。
- `run_fork` 的 `progress` 参数真正接线：删除该参数或改为透传说明，最终以 TaskManager 进度监听为准（避免两条进度通道），`skills/commands.py` 调用点同步清理。
- 前台子 Agent 不注册进度监听，行为不变。

### M2：/tasks 命令（F2）

- 新增 `commands/builtins.py` 或独立 `commands/tasks.py` 中的 `TasksCommand`（CommandKind.LOCAL，不发送模型）：
  - 无参：遍历 `agent._subagents.tasks.list()`，输出任务标识、类型（FORK/DEFINITION/TEAM_MEMBER）、名称、状态、开始时间、用量摘要。
  - `/tasks <id>`：`tasks.info(id)` 输出详情（含 progress、summary、error）。
  - `/tasks cancel <id>`：`tasks.cancel(id)`，返回中文确认或拒绝原因；成员取消经由 M3 的统一入口。
- 状态文本复用 `_task_status_text` 的中文映射，抽到共享位置供命令与 TUI 使用。

### M3：Team 成员纳入 TaskManager（F3）

**现状**：`TeamService.spawn` 中 `work()` 直接 `asyncio.create_task`，超时、Hook、统一视图均不覆盖。

**设计**：
- `SubagentKind` 增加 `TEAM_MEMBER`；`TeamService` 持有与 Lead 相同的 `TaskManager` 引用（构造时注入，`bind_parent` 时接线）。
- `spawn` 的成员运行改为 `tasks.start(SubagentKind.TEAM_MEMBER, member.role, work_wrapper, parent_task_id=None, background=True)`；`work_wrapper` 内部保留现有 `work()` 逻辑不变（审批、邮箱、Worktree 提交、通知回流全部原样）。
- `execution_timeout_seconds` 由此自动覆盖成员（`TaskManager._run` 的 `wait_for` 路径）；超时后成员状态标记 FAILED、Lead 收到失败报告——需在 `work()` 的异常分支确认 `CancelledError`/超时路径的现有 `except Exception` 语义仍然成立，必要时补 `TimeoutError` 分支。
- Hook 事件由 TaskManager 自动发出，无需 TeamService 额外处理。
- `wait_member`、`stop` 改为同时调用 `tasks.cancel`，保证 `/tasks cancel` 与 `TeamStop` 行为一致。

### M4：inline Skill SOP 随消息下发（F4）

- `skills/commands.py` 的 inline 分支（`_definition.execute` 第 49 行）：消息模板改为
  `f"用户已通过 /skill:{name} 命令装载 inline Skill「{name}」，以下为其完整指令，请严格按其执行：\n\n===== Skill 指令开始 =====\n{current.rendered_sop}\n===== Skill 指令结束 =====\n\n用户参数：{arguments or '（无）'}"`。
- `runtime.load` 激活行为保留（工具范围收敛仍依赖激活状态），但消息不再声称"已激活"却让模型等下一轮。
- 同步检查 `skills/prompt.py` 的激活状态注入说明，避免模型看到两份相互矛盾的指令来源描述。

### M5：后台权限卡结果收尾（F5）

**现状**：后台任务的 `ToolResultReady` 不经过主 Agent 事件流，`app.py:_show_tool_result` 无从触发，`_permission_cards[call_id]` 永不 `finish`。

**设计**：
- `TaskManager` 进度回调（M1）已携带工具结果事件；在其中识别"该 call_id 存在等待收尾的权限卡"这一信息不可行（TaskManager 不知道 UI），因此采用反向通知：`SubagentService` 在 `work()` 结束后无法拿到逐工具结果——改为在 M1 的 `on_event` 回调里同时转发 `ToolResultReady` 原始结果对象，TUI 的进度 listener 收到后调用与 `_show_tool_result` 相同的卡片收尾逻辑（`card.finish(result)`、清理 `_permission_cards` 条目），但不创建 `ToolActivity` 行、不滚动视图。
- 权限卡收尾对前台路径不产生重复处理：前台卡片由 `_show_tool_result` 收尾，M5 listener 需按 `call_id` 幂等（`_permission_cards.pop` 本身幂等）。

## 测试策略

- 单元：`RunToCompletion.on_event` 回调触发条件与文本；`TaskManager` 进度字段更新与 listener 异常吞噬；`/tasks` 三种形态的输出与取消语义；inline 消息模板内容；M5 卡片收尾幂等。
- 集成（不依赖真实模型）：`TeamService.spawn` 改造后，用假 Provider 验证成员任务登记进 TaskManager、超时触发 TIMED_OUT、Hook 事件顺序、Lead 通知回流不变。
- TUI：`app.run_test` 验证进度文本上屏、`/tasks` 本地执行、权限卡收尾。
- 端到端：tmux 真实场景按 spec AC6。

## 风险与对策

- **风险**：M3 改动 TeamService 生命周期，可能破坏 ch18 的成员恢复/审批语义。对策：`work()` 内部逻辑零改动，只在外层包 TaskManager；ch18 既有 team 测试全部保留并通过。
- **风险**：进度 listener 高频更新导致 TUI 卡顿。对策：进度文本仅在轮次或工具结果变化时推送（事件驱动，非定时器），文本极短。
- **风险**：`SubagentKind.TEAM_MEMBER` 影响 `allowed_names` 等既有分支。对策：检查所有 `SubagentKind` 的 match/枚举使用点，补充默认分支处理。
