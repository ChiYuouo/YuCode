# Agent Team 与 Coordinator Mode Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/teams/models.py`、`identity.py`、`repository.py`、`__init__.py` | 团队模型、身份/路径安全及元数据持久化。 |
| 新建 | `src/yucode/teams/tasks.py`、`mailbox.py` | 共享任务、依赖和并发安全邮箱。 |
| 新建 | `src/yucode/teams/backends.py`、`member_runtime.py` | 后端检测、独立成员运行与 transcript 恢复。 |
| 新建 | `src/yucode/teams/runtime.py`、`tools.py`、`lifecycle.py`、`service.py` | 专用工具池、审批门和团队生命周期。 |
| 新建 | `src/yucode/teams/merge.py`、`coordinator.py` | Worktree 收敛与 Coordinator 双锁。 |
| 修改 | `src/yucode/config.py`、`src/yucode/cli.py`、`src/yucode/agent.py` | 配置、运行时装配和团队通知。 |
| 修改 | `src/yucode/sessions.py`、`src/yucode/subagents/factory.py`、`src/yucode/subagents/tasks.py` | 成员 transcript、工具视图和成员任务通知。 |
| 修改 | `src/yucode/commands/builtins.py`、`src/yucode/tui/app.py` | `/team` 命令和 idle 提醒。 |
| 新建/修改测试 | `tests/test_team_*.py`、`tests/test_config.py`、`tests/test_agent.py`、`tests/test_cli.py`、`tests/test_commands.py`、`tests/test_tui.py` | 单元、集成和回归测试。 |

## T1：团队配置模型与 YAML 解析

**文件：** `src/yucode/config.py`、`tests/test_config.py`

**依赖：** 无

**步骤：**

1. 添加 `TeamConfig`，包含能力开关、Coordinator 开关、后端优先级和邮箱锁超时的安全默认值。
2. 为 `teams` 配置块实现严格解析，拒绝未知字段、重复/未知后端、非布尔开关及非法超时，并提供中文错误。
3. 覆盖默认、有效和非法配置。

**验证：** 运行 `pytest -q tests/test_config.py`，期望 Team 默认值与有效配置可读取，非法配置均被中文拒绝。

## T2：团队身份、目录边界与领域模型

**文件：** `src/yucode/teams/models.py`、`src/yucode/teams/identity.py`、`src/yucode/teams/__init__.py`、`tests/test_team_identity.py`

**依赖：** T1

**步骤：**

1. 定义团队、成员、任务、消息、后端和生命周期状态值对象；成员显式保存 `writable`。
2. 实现团队名、成员名、agent ID 的受限格式校验与跨团队路径二次验证。
3. 从当前项目根计算 `.yucode/teams`，团队名作为其直属子目录；允许测试注入临时根目录，不再使用用户级团队目录或旧 `.mewcode/teams`。

**验证：** 运行 `pytest -q tests/test_team_identity.py`，期望合法身份仅解析到团队根内，空名、重复名、路径分隔符、`.`、`..`、绝对路径和篡改路径被拒绝。

## T3：团队元数据的原子持久化

**文件：** `src/yucode/teams/repository.py`、`tests/test_team_repository.py`

**依赖：** T2

**步骤：**

1. 实现 `team.json` 的版本化编码、临时文件替换保存、读取、列出和删除。
2. 读取时重新校验每个成员的 `writable`、路径、时间、后端和状态；只读成员不得登记 Worktree，可写成员必须指向受控 Worktree，损坏或不安全记录不产生目录外访问。
3. 删除只移除目标团队受控目录，且对运行资源仍存在的情况返回给生命周期层处理。

**验证：** 运行 `pytest -q tests/test_team_repository.py`，期望保存/恢复往返正确，坏记录得到中文诊断，删除不影响相邻团队。

## T4：共享任务创建与依赖校验

**文件：** `src/yucode/teams/tasks.py`、`tests/test_team_tasks.py`

**依赖：** T2、T3

**步骤：**

1. 建立 `tasks.json` 原子存储，支持创建、读取、列表和更新任务。
2. 在创建和更新时验证任务 ID、负责人、依赖存在性、自依赖、重复依赖和有向环。
3. 将无依赖任务标记为 `READY`，其他任务标记为 `BLOCKED`。

**验证：** 运行 `pytest -q tests/test_team_tasks.py`，期望任务持久化正确，所有非法依赖在写入前被拒绝。

## T5：任务状态重算与成员完成关联

**文件：** `src/yucode/teams/tasks.py`、`tests/test_team_tasks.py`

**依赖：** T4

**步骤：**

1. 实现 `IN_PROGRESS`、`COMPLETED`、`CANCELLED` 的合法状态转换。
2. 每次前置任务结束后重算依赖任务，只有全部前置完成才从 `BLOCKED` 变为 `READY`。
3. 将成员正常完成、失败和取消事件转换为可追溯的任务更新，不能自动把失败任务视为依赖满足。

**验证：** 运行 `pytest -q tests/test_team_tasks.py`，期望多前置任务、失败和取消时后续任务状态符合规则。

## T6：邮箱格式、单播/广播和未读状态

**文件：** `src/yucode/teams/mailbox.py`、`tests/test_team_mailbox.py`

**依赖：** T2、T3

**步骤：**

1. 实现每成员 JSONL 邮箱，写入时自动补齐消息 ID、UTC 时间、摘要与未读状态。
2. 根据注册表解析收件人，拒绝未知、跨团队和未登记目标；广播展开为每个可达成员的单播记录。
3. 实现未读读取与已读更新，保留消息审计字段。

**验证：** 运行 `pytest -q tests/test_team_mailbox.py`，期望单播、广播、结构化类型和读状态均准确，未知目标不会创建邮箱文件。

## T7：邮箱锁、竞争重试与过期锁恢复

**文件：** `src/yucode/teams/mailbox.py`、`tests/test_team_mailbox.py`

**依赖：** T6

**步骤：**

1. 用独占创建的锁文件实现同进程和跨进程写入互斥，锁内保存随机持有者令牌和时间。
2. 在短暂竞争时按配置重试；锁超过阈值后仅在可验证过期时接管。
3. 释放时校验持有者令牌，异常时不删除其他调用者的锁。

**验证：** 运行 `pytest -q tests/test_team_mailbox.py`，期望并发消息无丢失或截断、暂时锁会重试、过期锁可恢复且新锁不会被误删。

## T8：后端可用性探测与选择

**文件：** `src/yucode/teams/backends.py`、`tests/test_team_backends.py`

**依赖：** T1、T2

**步骤：**

1. 抽象 tmux、iTerm2、in-process 的可用性探测和启动/唤醒/停止接口。
2. 以配置优先级选择后端并记录每个候选的可用性原因；显式指定不可用后端及无候选均失败。
3. 将原生 Windows iTerm2 标为不可用；tmux 只在当前 YuCode 进程可调用它时可用；in-process 始终作为受配置允许的本地候选。

**验证：** 运行 `pytest -q tests/test_team_backends.py`，期望不同模拟平台得到正确候选、实际选择和中文原因，且不发生静默替换。

## T9：tmux/iTerm2/in-process 驱动的启动与唤醒

**文件：** `src/yucode/teams/backends.py`、`tests/test_team_backends.py`

**依赖：** T8

**步骤：**

1. 以受控参数构造 tmux pane 创建、成员入口启动、`send-keys` 唤醒和停止调用。
2. 在 macOS 可用时以受控 AppleScript 调用 iTerm2 pane 的启动、写入和停止；其他系统不执行该分支。
3. 实现 in-process asyncio Task 的启动、唤醒和取消，统一返回后端句柄与失败结果。

**验证：** 运行 `pytest -q tests/test_team_backends.py`，期望三类驱动使用模拟执行器得到正确命令/事件，任何失败均不留下伪运行成员。

## T10：成员 transcript 目录与会话恢复能力

**文件：** `src/yucode/sessions.py`、`src/yucode/teams/member_runtime.py`、`tests/test_team_member_runtime.py`

**依赖：** T2、T3

**步骤：**

1. 扩展会话管理器，使成员能使用受控、独立的 transcript 目录，而主会话行为不变。
2. 为成员创建首次会话、记录 Conversation 事件、寻找最新可信 transcript 并恢复。
3. 对损坏 transcript 保留原文件、给出可定位错误且不伪造已恢复状态。

**验证：** 运行 `pytest -q tests/test_team_member_runtime.py tests/test_sessions.py`，期望成员可跨运行恢复历史，主会话测试不回归。

## T11：成员运行时的邮件注入、idle 与续写

**文件：** `src/yucode/teams/member_runtime.py`、`tests/test_team_member_runtime.py`

**依赖：** T5、T7、T9、T10

**步骤：**

1. 启动成员前读取未读邮件，以 `REMINDER` 运行时通知注入而不篡改用户对话历史。
2. 回合正常结束时保存 transcript、标记 `IDLE` 并投递状态消息给 Lead；异常时标记失败。
3. 对 idle/stopped 成员的新消息先恢复 transcript 再续写；恢复失败保留未读邮件并通知 Lead。

**验证：** 运行 `pytest -q tests/test_team_member_runtime.py`，期望成员完成后进入 idle，后续消息复用此前历史而非新建对话。

## T12：成员专用 Task 与 SendMessage 工具

**文件：** `src/yucode/teams/tools.py`、`src/yucode/teams/runtime.py`、`tests/test_team_tools.py`

**依赖：** T5、T7、T11

**步骤：**

1. 实现 `TaskCreate`、`TaskGet`、`TaskList`、`TaskUpdate` 和 `SendMessage` 的 schema、参数校验和中文结果。
2. 将工具绑定到当前团队和成员身份，不能由模型参数伪造其他团队或发件人。
3. 收件人写入成功后调用对应后端唤醒，唤醒失败时保留已持久化消息并向发件人说明。

**验证：** 运行 `pytest -q tests/test_team_tools.py`，期望工具的任务、消息和唤醒语义正确，非法参数与跨团队尝试安全失败。

## T13：成员工具可见性和 Worktree 创建

**文件：** `src/yucode/subagents/factory.py`、`src/yucode/teams/runtime.py`、`src/yucode/teams/lifecycle.py`、`tests/test_team_tools.py`、`tests/test_team_lifecycle.py`

**依赖：** T9、T12

**步骤：**

1. 复用现有工具过滤逻辑，为 Team 成员在原角色限制后附加协作工具。
2. 确保普通主入口、普通定义式子 Agent 和 Fork 不附加成员工具。
3. 创建团队成员时将缺省 `writable` 解析为 `false`；只读成员使用主目录且没有写工具，只有显式可写成员才调用 `WorktreeManager`；将权限、实际路径和分支持久化到成员记录。

**验证：** 运行 `pytest -q tests/test_team_tools.py tests/test_team_lifecycle.py`，期望可写成员有独立 Worktree 和专用工具，其他 Agent 看不到该工具集。

## T14：审批消息协议与执行硬门

**文件：** `src/yucode/teams/runtime.py`、`src/yucode/teams/tools.py`、`tests/test_team_tools.py`

**依赖：** T6、T12、T13

**步骤：**

1. 实现计划请求与批准/驳回消息的固定字段、请求 ID 关联和收件人验证。
2. 将需审批成员的写入任务置于待决状态，只有关联的批准消息才允许进入实施。
3. 在执行写工具前复核批准状态；驳回、缺失、过期或错配批准均拒绝执行。

**验证：** 运行 `pytest -q tests/test_team_tools.py`，期望未批准不能写，正确批准后可执行，错误批准或驳回不能绕过限制。

## T15：Team 服务创建、派生与停止

**文件：** `src/yucode/teams/service.py`、`src/yucode/teams/lifecycle.py`、`tests/test_team_lifecycle.py`

**依赖：** T3、T8、T11、T13、T14

**步骤：**

1. 实现团队创建、成员花名册登记、后端选择、成员派生和状态持久化；严格保留用户传入的团队名，并把缺省成员登记为只读。
2. 对已停止/空闲成员调用新工作时复用成员记录和 transcript，不新增无关成员。
3. 实现成员停止，先发送关闭协议、停止后端并处理 Worktree/异常资源保留结果。

**验证：** 运行 `pytest -q tests/test_team_lifecycle.py`，期望创建、派生、idle 续写、停止和失败报告符合生命周期模型。

## T16：Lead 顶层团队工具

**文件：** `src/yucode/teams/tools.py`、`src/yucode/agent.py`、`tests/test_team_tools.py`、`tests/test_agent.py`

**依赖：** T15

**步骤：**

1. 实现 Lead 的 `TeamCreate`、`TeamDelete`、`TeamSpawn`、`TeamStop`、`TeamMerge`、任务与 `SendMessage` 工具；`TeamCreate.members[].writable` 的 schema 默认值为 `false`，结果明确显示成员是否可写以及是否创建 Worktree。
2. 仅在拥有 Team 服务的 Lead 上注册这些工具，保留既有 `Agent` 工具语义。
3. 将成员状态和邮箱事件并入每回合的运行时通知。

**验证：** 运行 `pytest -q tests/test_team_tools.py tests/test_agent.py`，期望 Lead 可调用团队工具、非 Lead 不可见，通知在下一次请求中可见。

## T17：收敛顺序与干净分支合并

**文件：** `src/yucode/teams/merge.py`、`tests/test_team_merge.py`

**依赖：** T5、T13、T15

**步骤：**

1. 根据已完成共享任务的依赖关系确定成员分支的收敛顺序。
2. 在 Lead 目录以无提交试合并验证分支，并在成功后写入可追溯合并提交。
3. 在结果中逐成员记录合并、跳过、保留和失败状态。

**验证：** 运行 `pytest -q tests/test_team_merge.py`，期望两个干净 Worktree 的改动按顺序进入主分支，未完成成员被跳过。

## T18：有限冲突自动处理与回滚

**文件：** `src/yucode/teams/merge.py`、`tests/test_team_merge.py`

**依赖：** T17

**步骤：**

1. 对无冲突 Git 自动合并路径保持成功行为。
2. 为经三方基线证明的不同连续块追加冲突实现确定性合并，并在写入后重新检查 Git 状态。
3. 对其他冲突执行 `merge --abort`，保留成员 Worktree/分支，报告冲突成员与文件，确保主目录没有半合并状态。

**验证：** 运行 `pytest -q tests/test_team_merge.py`，期望可证明安全冲突被自动解决，复杂冲突回滚且分支保留。

## T19：团队删除与资源清理

**文件：** `src/yucode/teams/lifecycle.py`、`src/yucode/teams/service.py`、`tests/test_team_lifecycle.py`

**依赖：** T15、T18

**步骤：**

1. 删除前停止所有成员并等待后端状态确认。
2. 仅自动移除无受保护变更的成员 Worktree；有未收敛变更或检查失败时保留资源并汇总报告。
3. 仅在成员资源安全收尾后删除团队持久化目录，失败时保留可恢复元数据。

**验证：** 运行 `pytest -q tests/test_team_lifecycle.py`，期望可安全删除的团队被清理，受保护分支/目录不被静默删除。

## T20：Coordinator 双锁、工具收窄与提示

**文件：** `src/yucode/teams/coordinator.py`、`src/yucode/agent.py`、`tests/test_team_coordinator.py`、`tests/test_agent.py`

**依赖：** T1、T16

**步骤：**

1. 仅在 `teams.coordinator_enabled: true` 和 `YUCODE_COORDINATOR=1` 同时满足时激活模式。
2. 激活后从 Lead 可见工具移除 `WriteFile` 和 `EditFile`，保留读类、Shell、团队、消息、终止和收敛能力。
3. 向 Lead 每回合请求追加 Research、Synthesis、Implementation、Verification 四阶段提示；成员不继承工具收窄。

**验证：** 运行 `pytest -q tests/test_team_coordinator.py tests/test_agent.py`，期望四种双锁组合、工具可见性和提示注入均正确。

## T21：CLI 组装与独立成员入口

**文件：** `src/yucode/cli.py`、`tests/test_cli.py`

**依赖：** T15、T16、T20

**步骤：**

1. 在主入口加载 Team 配置并以当前项目的 `.yucode/teams` 装配 TeamService、后端驱动和 Lead Agent，不扫描旧用户级团队目录或旧 `.mewcode/teams`。
2. 添加仅供受控后端调用的成员入口参数，重新验证团队和成员身份后构造成员运行时。
3. 确保普通启动不暴露成员入口、配置禁用时不注册 Team 工具、成员入口失败返回中文且不影响主入口。

**验证：** 运行 `pytest -q tests/test_cli.py`，期望主入口和成员入口各自完成正确装配，篡改参数安全失败。

## T22：`/team` 本地命令

**文件：** `src/yucode/commands/builtins.py`、`tests/test_team_commands.py`、`tests/test_commands.py`

**依赖：** T19、T21

**步骤：**

1. 注册 `LOCAL` 类型的 `/team list`、`info`、`delete`、`kill` 命令。
2. 委托 TeamService 并显示成员、后端、状态、目录、待审批和清理结果的中文摘要。
3. 处理缺参数、非法名、未知团队/成员和未启用团队能力。

**验证：** 运行 `pytest -q tests/test_team_commands.py tests/test_commands.py`，期望命令不进入模型历史，且成功与失败均有中文反馈。

## T23：TUI 团队提醒生命周期

**文件：** `src/yucode/tui/app.py`、`tests/test_tui.py`

**依赖：** T16、T21

**步骤：**

1. 在 TUI 运行期启动可取消的 Team 通知监听或轮询。
2. 当 Lead idle 时显示成员消息、审批、完成、失败及资源保留提醒，无须用户额外输入。
3. 退出时取消监听任务，避免遗留 asyncio Task。

**验证：** 运行 `pytest -q tests/test_tui.py`，期望 idle 提醒可显示且关闭后没有未处理后台任务。

## T24：跨模块自动化回归

**文件：** 上述测试文件

**依赖：** T1–T23

**步骤：**

1. 运行团队测试、受影响的 Agent/工具/配置/命令/TUI/会话测试并修复回归。
2. 运行完整测试套件，确保既有子 Agent、Worktree、权限和普通会话行为保持正确。
3. 记录所有失败的实际输出并在进入下一任务前修复。

**验证：** 运行 `pytest -q`，期望退出码为 0。

## T25：端到端验收执行与记录

**文件：** `doc/ch18/checklist.md`（在其获批后更新验收记录）、相关测试文件

**依赖：** T24

**步骤：**

1. 在 tmux 环境启动 YuCode，输入真实多文件开发请求，创建至少两名成员并建立依赖任务。
2. 验证消息、审批、idle 续写和收敛；再检查 Git 状态与 `/team` 输出。
3. 执行 checklist 的所有自动化和手动观察项，记录实际结果。

**验证：** 运行 `pytest -q` 及 checklist 中的 tmux 场景，期望全部通过并有可追溯记录。

## T26：项目级团队根目录迁移

**文件：** `src/yucode/teams/identity.py`、`src/yucode/cli.py`、`tests/test_team_identity.py`、`tests/test_cli.py`

**依赖：** 更新后的 T2、T21

**步骤：**

1. 将默认团队根目录改为 `<当前项目>/.yucode/teams`，所有主进程与独立成员入口使用同一项目根计算结果。
2. 保留 `storage_root` 测试注入能力；默认路径不再读取 `%APPDATA%/YuCode/teams`、用户主目录或项目内旧 `.mewcode/teams`。
3. 验证团队名不被任务描述改写，`demo` 只解析为 `.yucode/teams/demo`。

**验证：** 运行 `pytest -q tests/test_team_identity.py tests/test_cli.py`，期望两个不同项目得到不同团队根，旧用户级目录和项目内 `.mewcode/teams` 即使存在也不被列出。

## T27：成员写权限显式持久化与默认只读

**文件：** `src/yucode/teams/models.py`、`src/yucode/teams/repository.py`、`src/yucode/teams/service.py`、`src/yucode/subagents/factory.py`、`tests/test_team_repository.py`、`tests/test_team_lifecycle.py`

**依赖：** T26

**步骤：**

1. 给成员模型及 `team.json` 编解码增加 `writable`，新建成员缺省为 `false`。
2. 只读成员固定使用项目根、不创建 Worktree，并从工具视图移除写文件、编辑和命令执行能力。
3. 只有显式 `writable: true` 时创建并登记 Worktree；加载时拒绝权限、目录和 Worktree 登记互相矛盾的数据。

**验证：** 运行 `pytest -q tests/test_team_repository.py tests/test_team_lifecycle.py`，期望缺省成员无 Worktree且不能写，显式可写成员仍有隔离 Worktree并可完成写入。

## T28：TeamCreate 提示、schema 与结果反馈

**文件：** `src/yucode/teams/tools.py`、`src/yucode/agent.py`、`tests/test_team_tools.py`、`tests/test_agent.py`

**依赖：** T27

**步骤：**

1. 在工具 schema 中声明 `writable` 缺省为 `false`，并在 Team Lead 提示中区分只读任务与修改任务。
2. TeamCreate 结果显示团队项目内路径、成员读写权限、实际工作目录和 Worktree 状态。
3. 保证模型遗漏 `writable` 时服务层仍按只读处理，不能只依赖提示词或 schema 默认值。

**验证：** 运行 `pytest -q tests/test_team_tools.py tests/test_agent.py`，期望 README 总结请求生成只读成员，结果明确显示未创建 Worktree。

## T29：变更回归与目标场景验收

**文件：** `doc/ch18/checklist.md`、相关测试文件

**依赖：** T26、T27、T28

**步骤：**

1. 运行团队测试与完整测试套件，修复项目级路径和模型字段变更造成的回归。
2. 在 tmux 中启动 YuCode，输入“创建团队 demo，派 alice 读取 README 并总结主要章节”。
3. 验证 `.yucode/teams/demo` 出现，alice 使用项目目录、没有 Worktree，完成后摘要进入 Lead 邮箱；同时确认 `.mewcode/teams` 没有本次新数据。
4. 核对路径后，仅清理本轮错误实现生成的 `.mewcode/teams/demo`；不得删除 `.mewcode` 下其他内容。

**验证：** 运行 `pytest -q` 并执行上述 tmux 对话；预期测试通过，磁盘结构、成员状态与最终回复符合更新后的 spec。

## 执行顺序

```text
T1 → T2 → T3 ─┬─→ T4 → T5 ──────────────┬─→ T11 → T12 → T13 → T14 → T15 → T16 ─┬─→ T20 → T21 → T22 → T23 ─┐
              │                          │                                      │                                     │
              └─→ T6 → T7 ──────────────┘                                      ├─→ T17 → T18 → T19 ────────────────┤
T1 → T8 → T9 ────────────────────────────┘                                      │                                     │
T2 → T10 ────────────────────────────────→ T11                                │                                     │
                                                                              └──────────────→ T24 → T25
更新增量：T26 → T27 → T28 → T29
```
