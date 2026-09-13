# 后台任务可见性与执行一致性 Task

按依赖顺序推进；每个任务完成后在 `checklist.md` 勾选并附验证证据（命令与输出）。

## T1：RunToCompletion 事件回调与任务进度快照

- `RunToCompletion.run` 增加可选 `on_event` 回调，迭代轮次推进与 `ToolResultReady` 时推送短进度文本与原始结果对象。
- `TaskSnapshot` 增加 `progress` 字段；`TaskManager._run` 接收回调更新快照，新增 `set_progress_listener` 且 listener 异常吞噬。
- `SubagentService.delegate` 的 `work()` 接线上述回调。
- 验证：单元测试覆盖回调触发条件、进度文本格式、listener 异常不影响任务完成。

## T2：进度上屏与 run_fork 清理

- TUI 注册进度监听，复用 `ForkActivity` + `show_skill_progress` 展示；任务结束收尾进度区。
- 删除 `run_fork` 的死参数 `progress`，`skills/commands.py` 调用点同步清理。
- 验证：`app.run_test` 集成测试验证进度文本上屏与收尾；现有 TUI 测试全绿。

## T3：/tasks 命令

- 新增 `/tasks`（列表）、`/tasks <id>`（详情）、`/tasks cancel <id>`（取消）三个形态，CommandKind.LOCAL 不发送模型。
- 状态中文映射抽为共享函数供命令与 TUI 复用。
- 验证：单元测试覆盖三种形态输出、取消运行中/已结束任务的中文反馈。

## T4：SubagentKind.TEAM_MEMBER 与 TeamService 接入 TaskManager

- `SubagentKind` 增加 `TEAM_MEMBER`，检查全部枚举使用点的默认分支。
- `TeamService` 注入 TaskManager；`spawn` 成员运行改经 `tasks.start(background=True)`，`work()` 内部逻辑零改动。
- `wait_member`/`stop` 与 `tasks.cancel` 打通；`TeamStop` 行为保持不变。
- 验证：ch18 全部 team 测试通过；新增测试断言成员任务出现在 `tasks.list()`。

## T5：成员超时与 Hook 覆盖

- 验证 `execution_timeout_seconds` 对成员生效：超时取消、状态 TIMED_OUT、Lead 收到失败报告、Worktree 无半完成提交。
- 验证 TASK_START/TASK_STOP/TASK_COMPLETE 对成员触发且顺序正确。
- 验证：假 Provider 集成测试覆盖上述三点。

## T6：inline Skill SOP 随消息下发

- `skills/commands.py` inline 分支消息模板改为携带 `rendered_sop` 与用户参数（plan M4 文案）。
- 核对 `skills/prompt.py` 激活状态注入描述，消除矛盾来源说明。
- 验证：单元测试断言消息包含 SOP 正文、参数与边界说明；不含"已激活"误导表述。

## T7：后台权限卡结果收尾

- TUI 进度监听中对带权限卡的 `call_id` 执行幂等收尾（`card.finish(result)`、清理 `_permission_cards`）。
- 验证：TUI 测试验证后台路径应答后卡片最终显示成功/失败摘要；前台路径无重复收尾。

## T8：端到端验收与文档收尾

- tmux 真实场景按 spec AC6 执行：后台 review Skill + 后台定义式子 Agent + Team 成员任务并行，全程用进度与 `/tasks` 观察。
- 对照 checklist 逐项记录结果与命令输出；更新 README 的命令与配置说明（如涉及）。
