# Git Worktree 隔离与子 Agent 集成 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/worktrees/models.py` | Worktree 记录、状态、检查及展示数据。 |
| 新建 | `src/yucode/worktrees/slug.py` | 安全 slug 与受控路径验证。 |
| 新建 | `src/yucode/worktrees/session.py` | `session.json` 的原子存取与恢复。 |
| 新建 | `src/yucode/worktrees/git.py` | 受限 Git Worktree 操作与变更检查。 |
| 新建 | `src/yucode/worktrees/setup.py` | 复制、hooks 与依赖软链接初始化。 |
| 新建 | `src/yucode/worktrees/manager.py` | 生命周期与变更保护的统一入口。 |
| 新建 | `src/yucode/worktrees/runtime.py` | 绝对路径键的项目运行时资源。 |
| 新建 | `src/yucode/worktrees/cleanup.py` | 后台清理任务的启动、停止与周期执行。 |
| 新建 | `src/yucode/worktrees/__init__.py` | 对外导出。 |
| 修改 | `src/yucode/config.py` | `WorktreeConfig` 与 YAML 解析。 |
| 修改 | `yucode.yaml.example`、`.gitignore` | 配置示例与 `.yucode` 忽略约定。 |
| 修改 | `src/yucode/tools/registry.py` | 显式根目录的工具视图。 |
| 修改 | `src/yucode/agent.py` | 每回合固定工作目录资源并建立请求。 |
| 修改 | `src/yucode/cli.py` | `--resume` 与运行时组件组合。 |
| 修改 | `src/yucode/commands/builtins.py` | `/worktree` 五个子命令。 |
| 修改 | `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py` | 显示当前目录并托管清理任务。 |
| 修改 | `src/yucode/subagents/models.py`、`loader.py` | `isolation: worktree` 定义和校验。 |
| 修改 | `src/yucode/subagents/factory.py`、`service.py`、`tasks.py` | 隔离 Agent 创建、通知和结束收尾。 |
| 新建/修改测试 | `tests/test_worktree_*.py` | Worktree 各层自动化测试。 |
| 修改测试 | `tests/test_config.py`、`tests/test_cli.py`、`tests/test_commands.py`、`tests/test_subagent_*.py`、`tests/test_tui.py` | 集成与回归测试。 |

## T1: Worktree 配置模型与解析

**文件：** `src/yucode/config.py`、`yucode.yaml.example`、`tests/test_config.py`

**依赖：** 无

**步骤：**

1. 定义 `WorktreeConfig`，包含复制规则、依赖软链接规则、保留期与清理间隔的安全默认值。
2. 为 `worktrees` YAML 节新增严格解析和中文错误，拒绝错误类型、非法相对路径和非正时间值。
3. 在示例配置中写出 `worktreeinclude`、依赖软链接及清理周期的最小可用示例。

**验证：** 运行 `pytest -q tests/test_config.py`，期望默认配置、有效 Worktree 配置与各类非法配置均按预期通过或以中文拒绝。

## T2: 安全 slug 与受控目录边界

**文件：** `src/yucode/worktrees/models.py`、`src/yucode/worktrees/slug.py`、`src/yucode/worktrees/__init__.py`、`tests/test_worktree_slug.py`

**依赖：** T1

**步骤：**

1. 定义 Worktree 状态、记录和变更保护值对象。
2. 实现受限字符、总长度、单段长度与嵌套段的 slug 解析。
3. 实现 `.yucode/worktrees` 下的路径解析及二次后代验证。
4. 覆盖合法嵌套名称与所有路径遍历、绝对路径、反斜杠、空段和非法字符场景。

**验证：** 运行 `pytest -q tests/test_worktree_slug.py`，期望合法 slug 仅解析到受控根内，所有非法值在无文件系统修改的前提下失败。

## T3: 会话持久化与安全恢复记录

**文件：** `src/yucode/worktrees/session.py`、`tests/test_worktree_session.py`

**依赖：** T2

**步骤：**

1. 实现 `.yucode/worktrees/session.json` 的版本化编解码与原子替换写入。
2. 对读入的每条记录重新校验 slug、绝对路径、时间、分支、基准提交与状态。
3. 将损坏或不安全单项作为中文诊断跳过，不阻塞其余可信记录。

**验证：** 运行 `pytest -q tests/test_worktree_session.py`，期望会话可往返保存；篡改路径、格式或单项时不会产生受控目录外访问。

## T4: Git Worktree 客户端与失败关闭变更检查

**文件：** `src/yucode/worktrees/git.py`、`tests/test_worktree_git.py`

**依赖：** T2

**步骤：**

1. 封装仓库确认、机器可读 Worktree 列表、创建、移除和基准提交读取。
2. 实现未提交修改、已有上游分支的未推送提交、无上游分支相对基准提交的检查。
3. 将任一 Git 命令失败转为不可安全删除的检查结果。

**验证：** 运行 `pytest -q tests/test_worktree_git.py`，期望可在临时 Git 仓库创建与枚举 Worktree，并正确区分干净、未提交、未推送及检查失败。

## T5: 创建后环境初始化

**文件：** `src/yucode/worktrees/setup.py`、`tests/test_worktree_setup.py`

**依赖：** T1、T2

**步骤：**

1. 对每条 `worktreeinclude` 规则验证主目录源与目标均在其受控目录内，支持文件和目录复制。
2. 将主仓库 hooks 配置复制或链接为目标 Worktree 可用的 hooks 路径。
3. 按配置为存在的依赖目录创建软链接，并对缺失、冲突、不支持软链接和越界规则产生可定位结果。
4. 将每个初始化结果返回给 Manager，避免将部分失败目录标记为就绪。

**验证：** 运行 `pytest -q tests/test_worktree_setup.py`，期望复制、hooks 与链接正确；非法或失败规则有中文诊断且不越界。

## T6: Worktree 生命周期与快速恢复

**文件：** `src/yucode/worktrees/manager.py`、`tests/test_worktree_manager.py`

**依赖：** T3、T4、T5

**步骤：**

1. 实现 Create、Enter、Exit、List、Remove 和 `current_root`，并统一走 slug、Git 与会话层。
2. 新建时按“创建 → 初始化中记录 → 初始化 → 就绪记录”更新状态；失败时保留可诊断记录而不进入。
3. 对已存在目录实现只读快速恢复，测试其不会调用 Git 创建或其他状态修改操作。
4. 实现默认变更保护、显式 `force` 删除、删除当前目录前退出和成功后会话更新。
5. 将 Worktree 目录删除与分支删除分开；默认结果中报告保留分支，只有 `delete_branch=True` 才删除分支和其提交。
6. 删除成功后仅在 `.yucode/worktrees/` 受控范围内向上清理空父目录，并保留受控根及 `session.json`。

**验证：** 运行 `pytest -q tests/test_worktree_manager.py`，期望完整生命周期可用、快速恢复只读、默认保护拒绝删除、强制删除只移除目录并报告保留分支；显式删除分支才丢弃对应分支；空父目录被清理而 `session.json` 保留。

## T7: 过期临时目录自动清理

**文件：** `src/yucode/worktrees/cleanup.py`、`src/yucode/worktrees/manager.py`、`tests/test_worktree_cleanup.py`

**依赖：** T6

**步骤：**

1. 实现仅筛选超过保留期的临时记录的清理入口与可追溯结果。
2. 复用 Manager 的自动删除路径，确保登记、受控路径、Git Worktree 一致性和变更状态四项验证全部成功才删除。
3. 启动周期协程、支持取消与一次性启动清理；任何检查异常均记录跳过原因。

**验证：** 运行 `pytest -q tests/test_worktree_cleanup.py`，期望仅干净、登记正确且过期的临时目录被删；受保护、伪造、检查失败和手动目录均保留。

## T8: 绝对路径键的 WorkspaceRuntime

**文件：** `src/yucode/worktrees/runtime.py`、`tests/test_worktree_runtime.py`

**依赖：** T1、T6

**步骤：**

1. 为每个规范化绝对目录延迟构造项目指令、上下文、记忆和 Skill 资源，并缓存资源。
2. 将当前目录查询委托给 Manager；为子 Agent 提供按指定根目录取得资源的接口。
3. 测试两个 Worktree 的同名文件、指令和项目记忆不会共享缓存，且主目录资源保持独立。

**验证：** 运行 `pytest -q tests/test_worktree_runtime.py`，期望相对路径相同但绝对目录不同的资源相互隔离，重复获取同一绝对目录会复用对应资源。

## T9: 显式根目录工具视图

**文件：** `src/yucode/tools/registry.py`、`tests/test_tools.py`、`tests/test_worktree_runtime.py`

**依赖：** T8

**步骤：**

1. 增加按调用方提供的已规范化根目录创建 `ToolView` 的接口，不修改 Registry 的全局上下文。
2. 保持现有默认视图语义，确保文件工具与命令工具只使用该视图的 `ToolContext.root`。
3. 添加主目录和 Worktree 交替工具视图测试。

**验证：** 运行 `pytest -q tests/test_tools.py tests/test_worktree_runtime.py`，期望已有工具测试不回归，命令和文件操作分别使用传入的明确目录。

## T10: 主 Agent 的工作目录快照与缓存隔离

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`、`tests/test_worktree_runtime.py`

**依赖：** T8、T9

**步骤：**

1. 让 Agent 在每轮用户请求开始时取得固定的 `WorkspaceResources`，并用其工具视图、上下文、提示和记忆完成整轮执行。
2. 保持子 Agent 通知、权限、Hook、Skill 和对话历史的现有行为。
3. 在模型请求中反映当前目录；切换目录后下一轮自动使用新资源，不通过清空缓存实现隔离。

**验证：** 运行 `pytest -q tests/test_agent.py tests/test_worktree_runtime.py`，期望既有 Agent 回归通过，交替目录时请求和工具根目录正确且同一轮不漂移。

## T11: CLI 组合、`--resume` 与清理生命周期

**文件：** `src/yucode/cli.py`、`src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_cli.py`、`tests/test_tui.py`

**依赖：** T7、T8、T10

**步骤：**

1. 解析 `--resume`，在构造主 Agent 前读取并验证 Worktree 会话；失败时展示中文诊断并保持主目录。
2. 组合 Manager、WorkspaceRuntime、主 Agent 和子 Agent 服务，启动一次清理并在 TUI 生命周期内托管周期清理任务。
3. 将界面当前目录显示改为 Agent 的当前根目录，退出时取消清理任务。

**验证：** 运行 `pytest -q tests/test_cli.py tests/test_tui.py`，期望有效恢复进入保存目录，无效恢复安全回退；TUI 不再依赖进程当前目录显示状态。

## T12: `/worktree` 本地命令

**文件：** `src/yucode/commands/builtins.py`、`tests/test_worktree_commands.py`、`tests/test_commands.py`

**依赖：** T6、T10

**步骤：**

1. 注册 `LOCAL` 类型的 `/worktree`，实现 list、create、enter、exit、remove 五个子命令和 `remove --force [--delete-branch] <名称>` 语法。
2. 以中文输出列表、当前标识、分支、状态、保护失败与初始化失败原因；目录删除后明确显示“分支已保留”或“分支已删除”。
3. 处理缺少参数、未知操作、非法 slug、目标不存在、非 Git 仓库和不支持强制清理等错误。

**验证：** 运行 `pytest -q tests/test_worktree_commands.py tests/test_commands.py`，期望五个子命令正确委托 Manager，且命令文本不进入模型历史。

## T13: Agent 定义的隔离字段

**文件：** `src/yucode/subagents/models.py`、`src/yucode/subagents/loader.py`、`src/yucode/subagents/__init__.py`、`tests/test_subagent_loader.py`

**依赖：** T1

**步骤：**

1. 新增 `AgentIsolation`，扩展 `AgentDefinition` 并保持省略字段时为 `none`。
2. 将 `isolation` 加入 frontmatter 白名单，仅接受 `worktree`，其他值、类型和未知字段均报中文可定位错误。
3. 更新内置角色解析回归测试。

**验证：** 运行 `pytest -q tests/test_subagent_loader.py`，期望 `isolation: worktree` 被正确加载，非法值不会进入角色目录。

## T14: 隔离子 Agent 工具参数、工厂与运行时通知

**文件：** `src/yucode/subagents/factory.py`、`src/yucode/agent.py`、`tests/test_subagent_factory.py`、`tests/test_subagent_service.py`

**依赖：** T8、T9、T13

**步骤：**

1. 扩展定义式 Factory，使其可接收显式 Worktree 根目录并从 `WorkspaceRuntime` 构造子 Agent 的独立资源。
2. 扩展固定 `Agent` 工具 schema 和 `SubagentRequest`，支持可选的 `isolation: none|worktree`；未知、空或非字符串值在创建任务前以中文拒绝。
3. 在服务层按“调用参数优先、角色定义为默认值”计算最终隔离模式，并在工具描述/系统提示中说明用户要求隔离时必须传该字段。
4. 为最终隔离任务在首轮请求追加不写入 Conversation 的 `worktree_notice`，含绝对目录、分支及“所有项目工具必须使用该目录”的说明。
5. 保持未指定隔离的定义式与 Fork 式的原有上下文隔离语义。

**验证：** 运行 `pytest -q tests/test_subagent_factory.py tests/test_subagent_service.py tests/test_subagent_tool.py`，期望默认不隔离的角色在调用参数为 `worktree` 时获得隔离目录；未指定时沿用角色默认；非法值被中文拒绝且未创建任务，隔离 Agent 的工具、指令和 notice 均指向其 Worktree。

## T15: 子 Agent 自动创建、保留与清理

**文件：** `src/yucode/subagents/service.py`、`src/yucode/subagents/tasks.py`、`tests/test_subagent_service.py`、`tests/test_task_manager.py`

**依赖：** T6、T7、T14

**步骤：**

1. 定义式角色声明 `worktree` 时，以任务标识生成安全临时 slug 并在运行前创建或恢复目录。
2. 将 Worktree 信息关联到任务；任务完成、失败、取消或超时时均执行自动收尾。
3. 无保护变更时自动清理；有未提交、未推送或检查失败时保留，并把目录、分支和原因附入任务结果通知。
4. 确保 Worktree 创建或收尾失败不会丢失任务的已有结果，也不影响主会话。

**验证：** 运行 `pytest -q tests/test_subagent_service.py tests/test_task_manager.py`，期望隔离任务完整运行，三种变更状态分别产生清理或可定位保留通知。

## T16: 全量自动化回归与端到端验收记录

**文件：** `doc/ch17/checklist.md`（在获批后创建）、相关测试文件

**依赖：** T1–T15

**步骤：**

1. 运行全部新增 Worktree 测试及受影响的配置、工具、Agent、子 Agent、命令和 TUI 测试，修复发现的回归。
2. 运行完整 `pytest -q`，记录实际退出状态。
3. 在 tmux 启动 YuCode，执行真实隔离子 Agent 请求、`/worktree` 五个命令和 `--resume` 场景，记录可观察结果。

**验证：** 运行 `pytest -q`，期望退出码为 0；tmux 场景的记录与随后获批的 checklist 全部一致。

## 执行顺序

```text
T1 ─┬─► T2 ─► T3 ─┬─► T6 ─► T7 ─┬─► T11 ─► T12 ─┐
    │            │               │                │
    ├─► T5 ──────┘               └─► T15 ─────────┼─► T16
    │                                              │
    └─► T13 ─────────────────────────► T14 ───────┘

T4 ─────────────────► T6
T8 ─► T9 ─► T10 ─────────────────────► T11
                     └───────────────► T14
```
