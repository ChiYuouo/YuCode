# 子 Agent 与后台任务 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/subagents/__init__.py` | 导出子 Agent 公共接口。 |
| 新建 | `src/yucode/subagents/models.py` | 定义、请求、任务、通知、追踪等不可变数据。 |
| 新建 | `src/yucode/subagents/loader.py` | 多来源 Markdown 定义发现、解析和诊断。 |
| 新建 | `src/yucode/subagents/policy.py` | 多层工具规则与过滤视图。 |
| 新建 | `src/yucode/subagents/factory.py` | 定义式/Fork 式隔离 Agent 构建。 |
| 新建 | `src/yucode/subagents/runner.py` | RunToCompletion 事件消费与结果收敛。 |
| 新建 | `src/yucode/subagents/trace.py` | 父子调用链和 token 聚合查询。 |
| 新建 | `src/yucode/subagents/tasks.py` | 任务生命周期、超时、后台、取消和通知队列。 |
| 新建 | `src/yucode/subagents/service.py` | 统一委派入口和前后台调度。 |
| 新建 | `src/yucode/subagents/tool.py` | 固定 schema 的 Agent 工具。 |
| 新建 | `src/yucode/subagents/builtin/explore.md` | 内置只读 Explore 角色。 |
| 新建 | `src/yucode/subagents/builtin/plan.md` | 内置只读 Plan 角色。 |
| 新建 | `src/yucode/subagents/builtin/general-purpose.md` | 内置通用角色。 |
| 修改 | `src/yucode/config.py` | 子 Agent 全局策略与超时配置。 |
| 修改 | `src/yucode/agent.py` | 系统工具装配、任务通知临时注入。 |
| 修改 | `src/yucode/tools/executor.py` | 让系统 Agent 工具获得当前权限确认回调。 |
| 修改 | `src/yucode/hooks/models.py` | 任务生命周期事件及任务上下文。 |
| 修改 | `src/yucode/hooks/conditions.py`、`src/yucode/hooks/template.py` | 支持任务上下文字段读取和变量替换。 |
| 修改 | `src/yucode/cli.py` | 创建并注入唯一的子 Agent 服务。 |
| 修改 | `src/yucode/commands/builtins.py` | 注册 `/tasks`、`/task info`、`/task cancel`。 |
| 修改 | `src/yucode/skills/execution.py`、`src/yucode/skills/commands.py` | Skill Fork 迁移到统一服务。 |
| 修改 | `src/yucode/tui/app.py` | ESC 转后台、通知展示、任务命令依赖。 |
| 修改 | `src/yucode/tui/widgets.py`、`src/yucode/tui/app.tcss` | 任务活动与通知的界面组件和样式。 |
| 修改 | `pyproject.toml` | 将内置 Agent Markdown 纳入构建产物。 |
| 新建 | `tests/test_subagent_loader.py` | 定义解析、覆盖与内置角色测试。 |
| 新建 | `tests/test_subagent_policy.py` | 工具过滤与嵌套限制测试。 |
| 新建 | `tests/test_subagent_factory.py` | 两类上下文与运行时隔离测试。 |
| 新建 | `tests/test_subagent_runner.py` | 跑到底和终止状态测试。 |
| 新建 | `tests/test_task_manager.py` | 前后台、超时、取消、通知与事件测试。 |
| 新建 | `tests/test_trace_registry.py` | 调用链与 token 聚合测试。 |
| 新建 | `tests/test_subagent_service.py` | 统一委派、固定工具和通知注入测试。 |
| 修改 | `tests/test_commands.py`、`tests/test_tui.py`、`tests/test_cli.py` | Slash、ESC、通知和组合装配测试。 |

## T1：建立子 Agent 领域模型

**文件：** `src/yucode/subagents/__init__.py`、`src/yucode/subagents/models.py`
**依赖：** 无
**步骤：**
1. 声明模型选择、委派类型、任务状态和生命周期阶段枚举。
2. 声明定义来源、角色定义、目录、请求、任务快照、通知、执行结果与追踪节点。
3. 对外仅导出后续模块需要的稳定领域类型，保证快照不可变。

**验证：** 运行 `pytest -q tests/test_subagent_loader.py tests/test_task_manager.py tests/test_trace_registry.py`，期望领域对象构造、状态值和不可变快照相关测试通过。

## T2：实现 Agent 定义加载和解析

**文件：** `src/yucode/subagents/loader.py`、`tests/test_subagent_loader.py`
**依赖：** T1
**步骤：**
1. 实现插件、内置、用户、项目目录的发现顺序及按名称覆盖。
2. 解析 YAML frontmatter 与 Markdown 正文，校验全部允许字段、模型、权限模式、轮次和工具列表。
3. 将单个定义的格式错误收集为带文件路径的中文诊断，保留其他有效定义。
4. 为正常解析、同名覆盖、无效字段、空正文和四层来源分别编写测试。

**验证：** 运行 `pytest -q tests/test_subagent_loader.py`，期望覆盖顺序为“项目 > 用户 > 内置 > 插件”，错误定义被中文诊断且不进入目录。

## T3：加入三个内置角色和构建资源

**文件：** `src/yucode/subagents/builtin/explore.md`、`src/yucode/subagents/builtin/plan.md`、`src/yucode/subagents/builtin/general-purpose.md`、`pyproject.toml`、`tests/test_subagent_loader.py`
**依赖：** T2
**步骤：**
1. 按定义格式写入 Explore、Plan、general-purpose 的角色说明、模型、权限和工具边界。
2. 把内置目录加入打包资源，确保安装后的发现路径可读取。
3. 测试三角色可加载；验证 Explore 为 haiku 且只读、Plan 只读、通用角色不额外缩小允许工具。

**验证：** 运行 `pytest -q tests/test_subagent_loader.py`，期望三个内置名称与其权限/模型配置正确。

## T4：实现多层工具策略

**文件：** `src/yucode/subagents/policy.py`、`tests/test_subagent_policy.py`
**依赖：** T1
**步骤：**
1. 用全局禁止、定义白名单、定义黑名单和后台白名单计算最终允许工具名。
2. 基于现有 ToolRegistry 视图生成只含最终工具的过滤视图。
3. 强制从子 Agent 视图移除统一 Agent 工具，并为被拒绝名称提供中文原因。
4. 为每一层单独限制、相互冲突时取更严格结果、空白名单和嵌套阻止编写测试。

**验证：** 运行 `pytest -q tests/test_subagent_policy.py`，期望不允许工具既不出现在模型工具定义中，也不能通过执行入口实际运行。

## T5：实现定义式与 Fork 式 Agent 工厂

**文件：** `src/yucode/subagents/factory.py`、`tests/test_subagent_factory.py`
**依赖：** T1、T4
**步骤：**
1. 定义式工厂创建空 Conversation、独立 ContextManager、独立 PermissionManager 和角色系统指令。
2. Fork 工厂复制父消息快照、父权限模式值与工具排序，但不复用父的可变运行时对象。
3. 共享 Provider、HookEngine、项目根与注册表底座，接入过滤工具视图。
4. 验证定义式请求无父消息、Fork 请求保留前缀；验证子 Agent 修改消息、权限、缓存和用量不会影响父 Agent。

**验证：** 运行 `pytest -q tests/test_subagent_factory.py`，期望两条路径的初始请求、共享/隔离边界均符合预期。

## T6：实现 RunToCompletion

**文件：** `src/yucode/subagents/runner.py`、`tests/test_subagent_runner.py`
**依赖：** T1、T5
**步骤：**
1. 消费子 Agent 事件直到 `AgentFinished`，收集最终文本、工具概要、用量和终止原因。
2. 将完成、轮次上限、流式错误、取消和无完成事件分别映射为任务结果。
3. 不向父对话暴露子 Agent 的文本增量、思考或完整工具历史。
4. 用可控 Provider 测试“工具调用后继续直到无工具调用”的完整循环和所有终止状态。

**验证：** 运行 `pytest -q tests/test_subagent_runner.py`，期望每个终止状态生成稳定的中文摘要且私有事件不外泄。

## T7：实现 TraceRegistry

**文件：** `src/yucode/subagents/trace.py`、`tests/test_trace_registry.py`
**依赖：** T1
**步骤：**
1. 记录任务登记、状态迁移、起止时间和单任务 Usage。
2. 依据父任务标识返回调用链、后代集合与聚合 token 用量。
3. 将查询结果设计为快照，避免调用方修改内部登记。
4. 覆盖根任务、父子任务、完成后的查询、各 Usage 字段的聚合和未知标识。

**验证：** 运行 `pytest -q tests/test_trace_registry.py`，期望父子关系、时间、状态和 token 聚合正确。

## T8：扩展 Hook 的任务生命周期事件

**文件：** `src/yucode/hooks/models.py`、`src/yucode/hooks/conditions.py`、`src/yucode/hooks/template.py`、`tests/test_task_manager.py`
**依赖：** T1
**步骤：**
1. 为 TaskStart、TaskStop、TaskComplete、SendMessage 添加固定事件值。
2. 为 HookContext 加入任务标识、父任务标识和任务状态字段。
3. 让条件与模板变量能读取这些字段，同时不改变既有事件的空值语义。
4. 用 HookEngine 测试事件触发和事件处理失败不改变任务状态或通知结果。

**验证：** 运行 `pytest -q tests/test_task_manager.py`，期望四个事件被观察到，Hook 异常仅被记录。

## T9：实现 TaskManager 生命周期与通知

**文件：** `src/yucode/subagents/tasks.py`、`tests/test_task_manager.py`
**依赖：** T1、T6、T7、T8
**步骤：**
1. 创建内存任务记录、Cancellation、执行协程和通知队列。
2. 实现启动、任务快照、列表、详情、取消、结果归档和通知消费。
3. 在开始、停止、完成、发送通知时触发对应 Hook 生命周期事件。
4. 实现每项任务的执行时限停止与资源清理；验证任务结束仍可查询至本次进程退出。

**验证：** 运行 `pytest -q tests/test_task_manager.py`，期望运行、完成、失败、取消、超时、通知和四类事件的状态转换均正确。

## T10：实现前台等待、120 秒转后台与手动转后台

**文件：** `src/yucode/subagents/tasks.py`、`tests/test_task_manager.py`
**依赖：** T9
**步骤：**
1. 实现任务的前台等待接口，默认等待上限固定为 120 秒。
2. 到达等待上限时只转换任务交付方式为后台，不取消实际子 Agent 协程。
3. 实现将当前可切换前台任务提升为后台的操作，并返回稳定快照。
4. 用可注入时钟或短测试超时验证显式后台、自动转后台、手动转后台、Fork 强制后台与无可转任务的反馈。

**验证：** 运行 `pytest -q tests/test_task_manager.py`，期望自动和手动后台化均立即返回任务标识，后台任务仍可完成并发送通知。

## T11：实现服务层和固定 Agent 工具

**文件：** `src/yucode/subagents/service.py`、`src/yucode/subagents/tool.py`、`tests/test_subagent_service.py`、`tests/test_subagent_tool.py`
**依赖：** T2、T4、T5、T9、T10
**步骤：**
1. 解析 `subagent_type`、`prompt`、`run_in_background`，区分定义式与 Fork，返回中文参数错误。
2. 创建任务、调用工厂和 RunToCompletion，并为 Fork 强制后台。
3. 为 Agent 工具定义固定 schema，确保角色增删不会改工具清单；子 Agent 永不装配该工具。
4. 覆盖正常委派、未知角色、空任务、显式后台、前台完成、120 秒转后台、Fork、通知队列与嵌套拒绝。

**验证：** 运行 `pytest -q tests/test_subagent_service.py tests/test_subagent_tool.py`，期望仅一个固定 Agent 工具暴露，所有路径产生正确任务或中文失败结果。

## T12：接入配置与应用组合

**文件：** `src/yucode/config.py`、`src/yucode/cli.py`、`tests/test_cli.py`、`tests/test_config.py`
**依赖：** T2、T4、T9、T11
**步骤：**
1. 增加子 Agent 全局禁止工具、后台白名单和执行超时的配置结构与中文校验。
2. 在 CLI 创建唯一的加载器、目录、策略、追踪、任务管理器和服务，并将服务注入主 Agent、命令和 TUI。
3. 将加载诊断显示为启动告警；插件目录暂以空集合接入并保留组合入口。
4. 测试默认配置、无效配置、CLI 构建依赖关系与内置定义可用性。

**验证：** 运行 `pytest -q tests/test_config.py tests/test_cli.py`，期望有效配置可启动，非法子 Agent 配置输出中文且不会构建不安全服务。

## T13：让主 Agent 注入系统工具和任务通知

**文件：** `src/yucode/agent.py`、`src/yucode/tools/executor.py`、`tests/test_subagent_service.py`、`tests/test_agent.py`
**依赖：** T11、T12
**步骤：**
1. 将固定 AgentTool 与既有 Skill 系统工具合并到主 Agent 工具视图，并保持原有工具顺序稳定。
2. 在模型请求前消费任务通知，生成临时 `task-notification` RuntimeMessage，绝不写入 Conversation 历史。
3. 将当前 TUI 权限确认回调安全传递给 AgentTool 的服务调用；其他工具语义不变。
4. 测试工具清单稳定、通知在后续请求可见且只消费一次、父历史不含通知、子任务权限仍经确认。

**验证：** 运行 `pytest -q tests/test_agent.py tests/test_subagent_service.py`，期望主 Agent 可继续对话并看到任务通知，历史与原工具回归测试通过。

## T14：迁移 Skill Fork 到统一服务

**文件：** `src/yucode/skills/execution.py`、`src/yucode/skills/commands.py`、`tests/test_skills.py`、`tests/test_subagent_service.py`
**依赖：** T11、T12、T13
**步骤：**
1. 将原 `run_fork` 的创建和运行逻辑替换为服务适配调用，保留 Skill 的参数与用户可见摘要。
2. 删除 Skill 专属的权限对象共享和独立进度通道，改用 TaskManager 通知和追踪。
3. 将 Skill 指令、历史范围和模型选择映射到统一 Fork 请求的受支持范围；不支持项给出中文诊断。
4. 测试 Skill Fork 任务可通过 `/tasks` 查询、受相同过滤约束、可取消并产生追踪与通知。

**验证：** 运行 `pytest -q tests/test_skills.py tests/test_subagent_service.py`，期望 Fork Skill 不再走旧运行器且功能行为保持可用。

## T15：增加任务 Slash 命令

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`
**依赖：** T9、T12
**步骤：**
1. 注册 `/tasks`，按活动优先显示标识、状态、类型、角色和用量摘要。
2. 注册 `/task info <id>`，显示链路、时间、结果/错误、单项与聚合用量。
3. 注册 `/task cancel <id>`，请求取消仍在运行任务。
4. 为缺少标识、非法标识、已结束取消、正常列表/详情/取消编写测试，并确认命令不发送给模型。

**验证：** 运行 `pytest -q tests/test_commands.py`，期望三条命令输出中文结果且不会新增用户模型消息。

## T16：接入 TUI 的后台切换和通知显示

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py`
**依赖：** T10、T12、T13、T15
**步骤：**
1. 添加任务通知与前台子任务活动组件，显示任务标识、状态和摘要，避免展示私有历史。
2. 在主请求等待子 Agent 工具时，ESC 优先请求转后台；无可转任务时保留现有取消语义。
3. 接收任务完成通知并更新聊天区和状态栏，保证完成后仍可正常输入。
4. 测试 ESC 转后台、通知渲染、权限卡片与普通取消不回归。

**验证：** 运行 `pytest -q tests/test_tui.py`，期望 ESC 对前台子任务转后台而非取消，其他生成流程保持原行为。

## T17：全量自动化回归与端到端验收

**文件：** `doc/ch16/checklist.md`（下一阶段生成后按其执行）、所有本章测试文件
**依赖：** T1–T16
**步骤：**
1. 运行新增子 Agent 测试与全部现有测试，修复本章造成的回归。
2. 在 tmux 中启动 YuCode，输入真实“先探索项目再给出结论”的请求。
3. 观察统一 Agent 工具委派、后台状态、`task-notification` 回传、`/tasks` 与 `/task info` 的实际输出。
4. 记录 tmux 会话中的命令、观察结果和 checklist 各项通过状态。

**验证：** 运行 `pytest -q`，期望全套测试通过；tmux 场景中主 Agent 能在子 Agent 完成后继续回复且任务可查询。

## 执行顺序

```text
T1
├── T2 → T3 ──────────────────────────┐
├── T4 ──┐                             │
├── T7 ──┼── T9 → T10 ──┐              │
└── T8 ──┘              │              │
          T4 → T5 → T6 ─┼── T11 → T12 → T13 → T14 ─┐
                         │                 │          │
                         └──────── T15 ────┴── T16 ──┤
                                                       ▼
                                                      T17
```
