# 子 Agent 与后台任务 Checklist

> 每一项均以可执行测试、终端输出或 tmux 中的可观察行为验证；执行时记录实际结果和通过状态。

## Agent 定义与角色

- [ ] 多来源定义按正确优先级加载并覆盖同名角色（验证：运行 `pytest -q tests/test_subagent_loader.py`；以项目、用户、内置、插件四层同名定义测试，观察最终角色来自项目层，且不同名角色均可用）。
- [ ] 无效 Agent 定义被中文且可定位地报告（验证：运行 `pytest -q tests/test_subagent_loader.py`；观察缺少 frontmatter、未知字段、非法模型、非法权限、非法轮次或空正文不会进入目录，诊断含文件路径与原因）。
- [ ] 三个内置角色的模型与工具边界正确（验证：运行 `pytest -q tests/test_subagent_loader.py`；观察 Explore 为 haiku 且只读、Plan 只读、general-purpose 使用允许的完整能力；以更高优先级同名定义覆盖后观察覆盖生效）。

## 统一委派与运行隔离

- [ ] 主 Agent 始终只暴露一个稳定的 Agent 委派工具（验证：运行 `pytest -q tests/test_subagent_tool.py`；在角色增删前后比较工具定义，观察工具名和 schema 不变，并含 `subagent_type`、`prompt`、`run_in_background`）。
- [ ] 定义式与 Fork 式请求正确分流并拒绝无效调用（验证：运行 `pytest -q tests/test_subagent_service.py tests/test_subagent_tool.py`；观察已存在角色和 `fork` 分别进入对应路径，未知角色、空任务和嵌套委派返回中文失败且不创建任务）。
- [ ] 定义式与 Fork 式的上下文边界正确（验证：运行 `pytest -q tests/test_subagent_factory.py`；观察定义式首次请求不含父消息而含角色指令，Fork 首次请求保留父历史和工具前缀，仅在末尾追加任务文本）。
- [ ] 子 Agent 运行时状态不会污染父 Agent（验证：运行 `pytest -q tests/test_subagent_factory.py`；在子 Agent 中改变消息、权限、读缓存和用量后，观察父 Agent 的对应状态仍不变）。

## 运行至完成、工具限制与权限

- [ ] 子 Agent 能连续执行工具直至模型不再调用工具（验证：运行 `pytest -q tests/test_subagent_runner.py`；使用先请求工具、后返回文本的可控模型，观察最终状态为完成、摘要和用量正确，私有事件未回流）。
- [ ] 轮次上限、模型错误、取消、超时及缺失完成事件都有明确结果（验证：运行 `pytest -q tests/test_subagent_runner.py tests/test_task_manager.py`；观察每种终止都有中文摘要、状态和用量，且主 Agent 仍能继续运行）。
- [ ] 多层工具限制采用最严格结果并实际阻止调用（验证：运行 `pytest -q tests/test_subagent_policy.py`；分别验证全局禁止、定义白名单/黑名单、后台白名单、权限模式和嵌套限制，观察被禁止工具既不暴露给模型也未被执行）。
- [ ] 允许但需授权的工具仍走现有 TUI 权限确认（验证：运行 `pytest -q tests/test_subagent_service.py tests/test_tui.py`；观察未被策略阻止的受控调用显示确认卡，批准后执行，拒绝后返回结果）。

## 后台任务、通知与追踪

- [ ] 显式后台、120 秒自动后台、ESC 手动后台和 Fork 强制后台均正确（验证：运行 `pytest -q tests/test_task_manager.py tests/test_subagent_service.py tests/test_tui.py`；观察每种进入后台的方式返回可查询任务标识，任务继续运行，Fork 不进行前台等待）。
- [ ] 任务超时和取消不会影响其他任务（验证：运行 `pytest -q tests/test_task_manager.py`；让一个任务超时或取消、另一个完成，观察前者对应终止状态与通知，后者不受影响；取消不存在或已结束任务得到中文说明）。
- [ ] 任务生命周期事件可被 Hook 观察且 Hook 失败不改变任务结果（验证：运行 `pytest -q tests/test_task_manager.py`；观察 TaskStart、TaskStop、TaskComplete、SendMessage 在相应时点触发，故意失败的 Hook 只记录问题）。
- [ ] 完成通知以 `task-notification` 进入主 Agent 后续上下文而不写入历史（验证：运行 `pytest -q tests/test_subagent_service.py tests/test_agent.py`；观察任务完成后下一次模型请求可见通知、只消费一次，Conversation 历史不含该通知）。
- [ ] 当前运行内可查询完整父子链和 token 用量（验证：运行 `pytest -q tests/test_trace_registry.py tests/test_task_manager.py`；观察详情包含父任务、子任务、调用类型、角色、状态、时间、单项与聚合用量，完成后仍可查）。

## 命令与 Skill 集成

- [ ] `/tasks`、`/task info <id>`、`/task cancel <id>` 行为正确且不发送给模型（验证：运行 `pytest -q tests/test_commands.py`；观察列表、详情、取消均为中文，缺少/错误标识及不可取消状态均有明确反馈，模型历史没有命令文本）。
- [ ] Skill Fork 复用统一子 Agent 通道（验证：运行 `pytest -q tests/test_skills.py tests/test_subagent_service.py`；从 Fork 模式 Skill 发起任务，观察它出现在任务列表、受同一工具策略约束、可取消、可追踪并产生任务通知）。

## 构建、回归与端到端场景

- [ ] 配置和应用组合正确（验证：运行 `pytest -q tests/test_config.py tests/test_cli.py`；观察默认配置可构建服务，非法子 Agent 配置以中文拒绝，加载诊断以启动告警呈现）。
- [ ] 全部自动化测试通过（验证：运行 `pytest -q`；期望退出码为 0）。
- [ ] 端到端：主 Agent 委派探索并接收异步结论（验证：在 tmux 启动 YuCode，输入“先探索这个项目中 Agent Loop 的实现位置，再简要告诉我结论”。观察主 Agent 调用固定 Agent 工具，Explore 在后台完成，主对话获得任务通知并给出后续回复；执行 `/tasks` 与 `/task info <任务标识>`，观察状态、调用链和用量；逐项记录本清单结果）。
- [ ] 端到端：用户手动让前台子任务转后台（验证：在 tmux 启动一个会持续多轮的定义式任务，执行中按 ESC。观察主界面立即回到可输入状态、任务标识可通过 `/tasks` 查询、任务之后完成并显示通知；普通无子任务生成时 ESC 仍执行原取消行为）。
