# Agent Team 与 Coordinator Mode Checklist

> 每项均以运行测试、执行命令或观察实际行为验证。实现完成后在方框中记录通过状态和实际结果。

## 团队与成员持久化

- [x] 创建团队 `demo` 后重启 YuCode，再查询团队；预期 `.yucode/teams/demo` 中的负责人、成员名称/角色、`writable`、审批标记、后端、工作目录与状态仍可见，团队名未被任务描述改写。（验证：运行 `pytest -q tests/test_team_repository.py tests/test_team_lifecycle.py`，并重启后的 `/team info demo` 观察输出。）
- [x] 未声明 `writable` 的成员使用主目录且没有 Worktree；显式可写成员使用不同 Git Worktree；不同项目和团队的元数据、邮箱与 transcript 互不可读。（验证：运行 `pytest -q tests/test_team_identity.py tests/test_team_lifecycle.py`，比较项目级团队根、成员目录和跨团队读取结果。）
- [x] 非法团队名、成员名、agent ID、篡改的 `writable`/工作目录/Worktree 组合和损坏元数据均被中文拒绝，且 `.yucode/teams` 外无访问或删除。（验证：运行 `pytest -q tests/test_team_identity.py tests/test_team_repository.py tests/test_team_lifecycle.py`。）

## 后端选择与成员运行

- [x] 在模拟 tmux、iTerm2、仅 in-process 及无可用后端环境创建成员，预期选择符合配置优先级，并显示每个候选的实际可用性原因。（验证：运行 `pytest -q tests/test_team_backends.py`。）
- [x] 明确指定不可用后端或自动选择没有合格候选时，预期创建失败、显示中文原因且没有成员实例或资源被启动。（验证：运行 `pytest -q tests/test_team_backends.py tests/test_team_lifecycle.py`。）
- [x] 在原生 Windows 环境，预期 iTerm2 显示不可用，tmux 仅在运行进程真实可调用 tmux 时可用，默认可选择 in-process。（验证：运行 `pytest -q tests/test_team_backends.py`，并在当前系统查看 `/team info` 的后端诊断。）
- [x] tmux 和 iTerm2 驱动分别创建隔离成员实例且可向目标 pane 发送唤醒；in-process 驱动可启动、唤醒和停止独立 asyncio 成员任务。（验证：运行 `pytest -q tests/test_team_backends.py`。）

## 共享任务、工具范围与审批

- [x] Team Lead 与 Team 成员可创建、查询、更新和完成共享任务；依赖全部完成前任务为阻塞，完成后自动变为可执行。（验证：运行 `pytest -q tests/test_team_tasks.py tests/test_team_tools.py`。）
- [x] 缺失依赖、自依赖、重复依赖和循环依赖均被拒绝；失败或取消的前置任务不会错误解锁后续任务。（验证：运行 `pytest -q tests/test_team_tasks.py`。）
- [x] 共享 Task 工具与 `SendMessage` 仅存在于该 Team 的 Lead/成员工具池；普通主入口、普通定义式子 Agent 和 Fork 看不到且无法调用。（验证：运行 `pytest -q tests/test_team_tools.py tests/test_agent.py`。）
- [x] 需要审批的成员在未批准、已驳回、批准过期或请求 ID 不匹配时不能执行写入工作；收到匹配的结构化批准消息后才可执行。（验证：运行 `pytest -q tests/test_team_tools.py`。）

## 邮箱与协作消息

- [x] 单播与广播消息会写入目标成员邮箱，包含发件人、正文、时间戳、未读状态、摘要和结构化类型；读取后已读状态正确。（验证：运行 `pytest -q tests/test_team_mailbox.py`。）
- [x] 未知收件人、未登记名称和跨团队收件人被拒绝，且不会生成额外邮箱文件。（验证：运行 `pytest -q tests/test_team_mailbox.py tests/test_team_tools.py`。）
- [x] 多个发送者并发写邮箱时消息无丢失或截断；暂时锁会重试，过期锁能安全接管，释放旧锁不会删除新锁。（验证：运行 `pytest -q tests/test_team_mailbox.py`。）
- [x] 独立终端成员收到消息后实际被唤醒；in-process 成员收到消息后得到本地唤醒并处理未读邮件。（验证：运行 `pytest -q tests/test_team_backends.py tests/test_team_member_runtime.py`。）

## 生命周期、续写和清理

- [x] 成员完成当前工作后进入 idle 并向 Lead 发送状态通知；Lead 即使处于空闲也会在界面看到提醒。（验证：运行 `pytest -q tests/test_team_member_runtime.py tests/test_tui.py`。）
- [x] 向 idle 或 stopped 成员发送后续工作时，预期从该成员的磁盘 transcript 恢复历史并续写，而不是创建新成员或丢失上下文。（验证：运行 `pytest -q tests/test_team_member_runtime.py tests/test_team_lifecycle.py tests/test_sessions.py`。）
- [x] transcript 损坏或恢复失败时，预期保留未读邮件和原 transcript、标记成员失败并通知 Lead。（验证：运行 `pytest -q tests/test_team_member_runtime.py`。）
- [x] 停止成员或删除团队时，安全的运行资源和 Worktree 会清理；含未收敛变更、检查失败或异常的资源会保留并报告原因。（验证：运行 `pytest -q tests/test_team_lifecycle.py`。）

## 收敛与 Git 安全

- [x] 两名成员在独立 Worktree 完成可合并变更后，Lead 依赖顺序收敛，主分支包含两人的结果且报告逐成员状态。（验证：运行 `pytest -q tests/test_team_merge.py`，并运行 `git status --short` 与 `git log --oneline -n 3`。）
- [x] Git 可自动合并的改动正常收敛；可证明安全的不同连续块追加冲突被自动解决并通过 Git 状态检查。（验证：运行 `pytest -q tests/test_team_merge.py`。）
- [x] 复杂或无法安全判断的冲突会中止该成员合并、保留其 Worktree/分支并报告冲突文件；Lead 目录没有半完成合并状态。（验证：运行 `pytest -q tests/test_team_merge.py`，随后运行 `git status --short`。）

## 顶层入口、Coordinator 与集成

- [x] `TeamCreate` 将 `members[].writable` 缺省为 `false`，创建结果显示 `.yucode/teams` 下的团队路径、读写权限、工作目录和 Worktree 状态；其余团队、任务和消息能力仍仅向 Lead 暴露。（验证：运行 `pytest -q tests/test_team_tools.py tests/test_agent.py`。）
- [x] `/team list`、`info`、`delete`、`kill` 正确完成对应操作，且命令文本不进入模型对话历史；缺参数、非法名称、未知目标和能力未启用时均有中文反馈。（验证：运行 `pytest -q tests/test_team_commands.py tests/test_commands.py`。）
- [x] 只开启配置、只设置 `YUCODE_COORDINATOR=1`、同时开启和都关闭的四种情况中，只有双锁同时开启才进入 Coordinator Mode。（验证：运行 `pytest -q tests/test_team_coordinator.py`。）
- [x] Coordinator Mode 下 Lead 不可见 `WriteFile`/`EditFile`，但保留读类工具、Shell、团队、消息、终止和收敛能力；成员仍具有完成已委派写入工作的权限。（验证：运行 `pytest -q tests/test_team_coordinator.py tests/test_team_tools.py`。）
- [x] Coordinator Mode 每回合都获得 Research → Synthesis → Implementation → Verification 的工作流指导。（验证：运行 `pytest -q tests/test_team_coordinator.py tests/test_agent.py`，检查模型请求中的运行时提示。）
- [x] CLI 使用当前项目 `.yucode/teams` 装配 Team 服务，不扫描旧用户级团队目录或项目内旧 `.mewcode/teams`；配置禁用时不注册 Team 工具，受控成员入口的篡改参数安全失败。（验证：运行 `pytest -q tests/test_cli.py`。）

## 自动化回归

- [x] 团队领域的全部单元与集成测试在 `.yucode/teams` 项目级路径和默认只读变更后通过。（验证：运行全部 `tests/test_team_*.py`，预期退出码 0。）
- [x] 受影响的配置、会话、工具、Agent、子 Agent、命令、CLI 与 TUI 回归测试通过。（验证：运行相关测试文件，预期退出码 0。）
- [x] 完整测试套件通过。（验证：运行 `pytest -q`，预期退出码 0。）

## 本次变更目标场景

- [x] 启动 YuCode 并输入“帮我创建一个团队 demo，派一个队员 alice，让它读 README.md 并总结主要章节”；预期 Lead 创建并启动 alice，最终返回 README 章节摘要。
- [x] 项目内出现 `.yucode/teams/demo/team.json`、alice/Lead 邮箱和 alice transcript；用户级旧团队目录和项目内 `.mewcode/teams` 均不产生本次 `demo` 数据。
- [x] `team.json` 中 alice 的 `writable` 为 `false`，工作目录为当前项目，`worktree_slug` 为空；项目的 Worktree 列表没有新增 alice Worktree。
- [x] TeamCreate 和 `/team info demo` 的反馈明确显示 `.yucode/teams/demo`、alice 为只读成员、未创建 Worktree及实际后端。
- [x] 确认 `.yucode/teams/demo` 已正确生成后，仅移除本轮误生成的 `.mewcode/teams/demo`，并验证 `.mewcode` 下其他内容未被改动。
- [x] 对照执行一个显式 `writable: true` 的成员用例，确认仍会创建隔离 Worktree，避免默认值变更破坏开发型成员。

## 端到端场景：tmux 中的多成员协作

- [ ] 在可用 tmux 环境（Windows 请在 WSL 内运行 YuCode）启动项目，输入真实的多文件开发请求；创建 Team，派生至少两名可写成员，并分别在其 Worktree 中接到有依赖关系的任务。（验证：在 tmux 中启动 `yucode`，观察 Team 创建反馈、pane、成员 Worktree 和 `/team info <名称>`。）
- [ ] 让其中一名需审批成员先提交计划，Lead 用结构化批准消息回复；预期批准前无写入，批准后成员开始工作并通过邮箱向其他成员或 Lead 发送进展。（验证：观察成员 pane、邮箱状态、任务状态和 Lead 提醒。）
- [x] 等一名成员自然 idle 后，以 `SendMessage` 指派续作；预期成员从原 transcript 继续，处理完成后 Lead 收到通知。（验证：观察成员历史连续性、状态从 idle/stopped 到 running 再到 idle，以及 Lead 提醒。）
- [ ] Lead 执行收敛后，预期成员分支按依赖合并，`git status --short` 无半合并状态，最终回复概括合并结果；再执行 `/team delete <名称>`，预期安全资源清理、受保护资源被明确保留。（验证：观察 Lead 输出、`git log --oneline -n 5`、`git status --short` 和 `/team list`。）
- [ ] 启用 `teams.coordinator_enabled: true` 与 `YUCODE_COORDINATOR=1` 后重复一次小型协作请求；预期 Lead 只编排、沟通与合并，文件改动由成员完成，并遵循四阶段工作流。（验证：观察 Lead 工具调用、成员写入记录及最终 Git 结果。）

## 2026-09-12 旧实现验收记录

- 以下记录仅对应“用户级团队目录、成员默认可写”的旧实现；本次变更完成前不得用于勾选上面的新增验收项。
- 自动化：旧实现完整 `pytest -q` 通过；团队、配置、CLI、权限、Agent、会话、命令和 TUI 回归均通过。
- 真实 tmux 对话：创建 `demo-final`，以 in-process 启动只读成员 `alice` 读取 `README.md`；团队元数据、Lead 邮箱和成员 transcript 均落盘，成员完成后变为 `idle`，Lead 空闲时自动显示结果。
- 续写：Lead 用 `SendMessage` 再次指派同一成员，`agent_id` 与 `transcript_id` 保持不变，第二次结果写回同一 Lead 邮箱；随后 `/team delete demo-final` 自动停止成员并删除团队目录。
- Git 集成：自动化测试已覆盖可写成员后台写入与自动提交、普通合并、安全追加冲突自动处理、复杂冲突 `merge --abort` 和干净状态检查。
- 平台限制：当前是 Windows；iTerm2 只能做受控模拟测试，无法在本机真实运行。WSL 内有 tmux，但 WSL Python 尚未安装本项目依赖；用 Windows Python 嵌入 WSL tmux 的临时两成员场景出现终端编码/按键异常，因此完整多成员、审批、资源清理和 Coordinator UI 等未实机覆盖项仍保留未勾选，不能误报为真实通过。

## 2026-09-12 错误 `.mewcode` 路径的旧验收记录

- 本节仅记录修正前的历史结果，已被 `.yucode/teams` 新要求取代，不用于勾选当前验收项。
- 自动化：当时的完整测试套件通过；默认只读、显式可写 Worktree、项目级隔离、路径安全、CLI 装配和工具反馈均有回归覆盖。
- 真实 tmux 对话：在 WSL tmux 中启动 YuCode，原样输入目标请求，Lead 创建 `demo` 并以 in-process 后端启动 `alice`；alice 读取 `README.md` 后返回五个主要章节的摘要并进入 `idle`，Lead 收到完成提醒。
- 持久化：本次数据写入项目内 `.mewcode/teams/demo`，包含 `team.json`、alice transcript 和 Lead 邮箱；团队名保持为 `demo`。
- 只读行为：`team.json` 中 `alice.writable=false`、`workspace_root` 为当前项目、`worktree_slug=null`；执行前后 `git worktree list` 均仅包含主项目目录。
- 可写回归：显式 `writable: true` 的生命周期用例通过，仍会创建隔离 Worktree；默认值变更未破坏开发型成员。

## 2026-09-12 `.yucode/teams` 路径修正验收记录

- 自动化：团队身份、CLI、持久化、生命周期、工具和 Agent 定向测试通过，完整测试套件通过。
- 真实 tmux 对话：原样输入目标请求，Lead 在 `.yucode/teams/demo` 创建团队并以 in-process 后端启动只读成员 alice；alice 读取 `README.md`、返回五个主要章节摘要并进入 `idle`。
- 磁盘与 Git：`team.json`、alice transcript 和 Lead 邮箱均位于 `.yucode/teams/demo`；alice 的 `writable=false`、`worktree_slug=null`，Git Worktree 列表只包含主项目。
- 重启恢复：关闭并重新启动 YuCode 后执行 `/team info demo`，界面显示 `.yucode/teams/demo`、alice 状态为 `idle`、只读、Worktree 未创建、后端为 in-process。
- 旧路径：`.mewcode/teams/demo` 不存在，测试证明旧 `.mewcode/teams` 即使含数据也不会被 Team 服务扫描；未改动 `.mewcode` 下其他内容。
