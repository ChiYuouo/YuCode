# 后台任务可见性与执行一致性 Checklist

> 逐项验收后在 `[ ]` 勾选，并附实际结果与命令输出。未执行的项目不得预勾。

## spec 验收标准

- [ ] AC1（F1）：后台 fork Skill 与定义式子 Agent 运行期间界面持续显示轮次与最近工具进度；模拟 progress 回调抛错，任务仍正常完成并回传结果。
  - 自动化覆盖：`test_runner_forwards_progress_and_tool_results`、`test_task_manager_reports_progress_and_survives_listener_errors`、`test_tui_task_progress_updates_activity_and_finishes_permission_card`。真机复核待做。
- [ ] AC2（F2）：`/tasks` 列出全部任务（含状态与用量）；`/task info <id>` 显示详情；`/task cancel <id>` 取消运行中任务并中文确认，已结束任务取消被中文拒绝。
  - 命令为既有能力（`commands/builtins.py`），本章确认其输出覆盖 TEAM_MEMBER 类型；真机复核待做。
- [ ] AC3（F3）：Team 成员任务出现在 `/tasks`；极小 `execution_timeout_seconds` 下成员被超时停止（TIMED_OUT）且 Lead 收到失败报告；成员完成后 Lead 通知回流与 ch18 一致；任务 Hook 事件按序触发。
  - 自动化覆盖：`test_in_process_member_is_registered_in_unified_task_list`、`test_member_timeout_marks_failed_and_reports_lead`、`test_lead_stop_does_not_report_timeout_failure`、`test_member_runs_emit_task_hooks_in_order`。真机复核待做。
- [ ] AC4（F4）：inline `/skill:<名称>` 注入的消息包含完整 SOP 正文与用户参数；模型不再回复"尚未激活"，无须自行调用 LoadSkill。
  - 自动化覆盖：`test_inline_skill_message_carries_sop`。"模型不再回复尚未激活"需真机复核。
- [ ] AC5（F5）：后台权限卡应答后，工具出结果时卡片原地显示最终成功/失败摘要；拒绝路径同样收尾。
  - 自动化覆盖：`test_tui_task_progress_updates_activity_and_finishes_permission_card`（成功路径）。真机复核待做。
- [ ] AC6（整体）：tmux 真实执行"后台 review Skill + 后台定义式子 Agent + Team 成员任务"并行场景，全程可观察，结果通知、Lead 回流与 Git 状态符合预期。
  - 实际结果：待真机执行（本机无 tmux，等池鱼验收）。

## 任务清单

- [x] T1：RunToCompletion 事件回调与任务进度快照（`runner.py` on_event；`tasks.py` report_progress/set_progress_listener/worker_for；`TaskSnapshot.progress`）
- [x] T2：进度上屏与 run_fork 死参数清理（TUI `_on_task_progress`；`execution.py` 移除 progress 形参；`skills/commands.py` 同步）
- [x] T3：/tasks 与 /task 命令确认覆盖（命令已存在，TEAM_MEMBER 类型自动纳入列表；未新增命令代码）
- [x] T4：SubagentKind.TEAM_MEMBER 与 TeamService 接入 TaskManager（`teams/service.py` bind_parent 注入、spawn 改经 tasks.start、stop 走 tasks.cancel 并带 lead_stopped 守卫）
- [x] T5：成员超时与 Hook 覆盖（execution_timeout_seconds 生效 → TIMED_OUT + FAILED + Lead 报告；TASK_START/STOP/COMPLETE/SEND_MESSAGE 按序触发）
- [x] T6：inline Skill SOP 随消息下发（`skills/commands.py` 消息模板携带 rendered_sop 与用户参数）
- [x] T7：后台权限卡结果收尾（进度监听中按 call_id 幂等 finish 并清理 `_permission_cards`）
- [ ] T8：tmux 端到端验收与文档收尾（待真机执行）

## 回归确认

- [x] 全量 pytest 通过（`--basetemp` 独立临时目录；`pytest-current` 默认目录因环境残留被锁，与代码无关）。
- [x] ruff 检查通过（`ruff check src tests` All checks passed）。
- [x] 权限确认多槽位行为（上一章修复）不受影响：`test_concurrent_permission_requests_resolve_independently`、`test_generation_finish_keeps_pending_background_approval` 仍通过。
- [x] fork 委派提示（SOP 随任务下发）不受影响：`test_skills_execution.py` 仍通过。
- [x] ch18 团队生命周期既有测试全绿（成员登记改造未破坏审批、恢复、收敛语义）。
- [x] 新增用户可见文本均为中文。
