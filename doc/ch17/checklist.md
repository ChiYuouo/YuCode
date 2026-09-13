# Git Worktree 隔离与子 Agent 集成 Checklist

> 每一项均以自动化测试、终端输出或 tmux 中的可观察行为验证；实施时记录实际结果与通过状态。

## 安全名称与路径边界

- [ ] 合法嵌套 slug 只能解析到 `.yucode/worktrees/` 内（验证：运行 `pytest -q tests/test_worktree_slug.py`；以 `feature/api`、`agent_1` 创建或解析，观察得到的绝对路径均位于当前仓库受控根目录）。
- [ ] 非法 slug 在触发 Git 或文件系统写入前被拒绝（验证：运行 `pytest -q tests/test_worktree_slug.py tests/test_worktree_manager.py`；分别输入空段、`.`、`..`、绝对路径、反斜杠、超长值和非法字符，观察中文错误且受控根目录外无新增或删除）。
- [ ] 会话记录和初始化规则不能绕开路径边界（验证：运行 `pytest -q tests/test_worktree_session.py tests/test_worktree_setup.py`；篡改记录路径或配置越界的复制/链接规则，观察项目外目标从未被读取、写入、链接或删除）。

## 生命周期、Git 与变更保护

- [ ] 创建、进入、退出与列表正确维护独立 Worktree（验证：运行 `pytest -q tests/test_worktree_manager.py tests/test_worktree_git.py`；观察创建的目录位于 `.yucode/worktrees/`、关联独立分支，进入后当前根目录变为目标，退出后回到主仓库，列表显示名称、目录、分支、当前状态和最后使用时间）。
- [ ] 重复创建走只读快速恢复（验证：运行 `pytest -q tests/test_worktree_manager.py`；预先创建并登记目标后再次创建，观察返回已有记录，Git 创建调用次数为零且分支/目录未变化）。
- [ ] 非 Git 仓库和 Git 操作失败有中文且不破坏状态的反馈（验证：运行 `pytest -q tests/test_worktree_git.py tests/test_worktree_manager.py`；在非仓库和模拟 Git 失败时操作，观察失败原因，当前目录与原有会话记录仍可用）。
- [ ] 默认删除保护未提交修改（验证：运行 `pytest -q tests/test_worktree_manager.py`；向 Worktree 写入未提交文件后执行普通移除，观察拒绝删除、目录及记录保留）。
- [ ] 默认删除保护未推送提交和无法检查状态（验证：运行 `pytest -q tests/test_worktree_git.py tests/test_worktree_manager.py`；分别制造有上游和无上游的本地新增提交、模拟状态检查失败，观察均拒绝删除）。
- [ ] 显式强制删除仅移除用户指定目标（验证：运行 `pytest -q tests/test_worktree_manager.py`；对受保护目录使用强制选项，观察指定目录与记录被移除，主目录和其他 Worktree 不受影响）。
- [ ] Worktree 目录删除默认保留本地分支（验证：运行 `pytest -q tests/test_worktree_manager.py tests/test_worktree_commands.py`；执行普通或强制移除后，观察 Worktree 叶目录与其空父目录被删除、`session.json` 仍存在，结果显示保留的分支名，且该分支仍可由 `git branch --list` 查到）。
- [ ] 只有明确删除分支选项才丢弃提交（验证：运行 `pytest -q tests/test_worktree_manager.py tests/test_worktree_commands.py`；执行 `remove --force --delete-branch <名称>`，观察对应 Worktree 目录、空父目录和本地分支均消失，其他分支与 `session.json` 不受影响）。

## 创建后初始化

- [ ] `worktreeinclude` 可安全复制运行所需文件和目录（验证：运行 `pytest -q tests/test_worktree_setup.py tests/test_config.py`；配置文件及目录规则后创建 Worktree，观察目标内容可用，非法规则被配置加载阶段中文拒绝）。
- [ ] hooks 与大型依赖目录初始化符合配置（验证：运行 `pytest -q tests/test_worktree_setup.py`；观察 hooks 在目标中可用，`node_modules`、`.venv`、`vendor` 等已存在配置目录被软链接而非复制）。
- [ ] 初始化缺失、冲突或链接失败不会伪装为就绪（验证：运行 `pytest -q tests/test_worktree_setup.py tests/test_worktree_manager.py`；观察中文的失败项目与原因，目标记录状态不可进入，用户仍可诊断或移除）。

## 会话恢复与显式工作目录

- [ ] Worktree 会话状态可安全持久化（验证：运行 `pytest -q tests/test_worktree_session.py`；观察 `session.json` 能记录当前目录、分支、临时属性及时间，原子保存后可完整读回）。
- [ ] `--resume` 仅恢复有效登记的目标（验证：运行 `pytest -q tests/test_cli.py`；启动时恢复有效 Worktree，观察当前根目录正确；删除目录或篡改记录后恢复，观察回退主目录和中文原因）。
- [ ] 工具、系统提示、项目指令和项目记忆按绝对目录隔离（验证：运行 `pytest -q tests/test_worktree_runtime.py tests/test_agent.py`；在主目录和两个 Worktree 的同名路径放置不同内容，交替执行请求，观察每次工具结果与模型请求只使用当前目录的数据）。
- [ ] 切换目录不改变进程当前目录且同一回合不漂移（验证：运行 `pytest -q tests/test_worktree_runtime.py tests/test_tools.py`；比较进入/退出前后的进程目录，观察不变；同一 Agent 回合内工具调用始终使用开始时的显式根目录）。

## 子 Agent 隔离与自动清理

- [ ] Agent 定义只接受有效的 `isolation: worktree`（验证：运行 `pytest -q tests/test_subagent_loader.py`；观察省略时为默认非隔离、正确值可加载，未知值/类型得到带文件位置的中文诊断）。
- [ ] 单次 Agent 委派可覆盖角色默认隔离模式（验证：运行 `pytest -q tests/test_subagent_tool.py tests/test_subagent_service.py`；对默认不隔离的 `general-purpose` 调用传入 `isolation: "worktree"`，观察创建临时目录并注入 `worktree_notice`；省略参数时观察仍使用角色默认值）。
- [ ] 非法单次隔离值不会静默降级（验证：运行 `pytest -q tests/test_subagent_tool.py`；传入未知值、空值和非字符串值，观察中文参数错误，且未创建子 Agent 任务、Worktree 或主目录文件改动）。
- [ ] 隔离子 Agent 自动使用专属目录与 `worktree_notice`（验证：运行 `pytest -q tests/test_subagent_factory.py tests/test_subagent_service.py`；运行声明隔离的角色，观察开始前创建临时目录，首个模型请求的运行时通知包含绝对目录和分支，文件及命令工具的根目录等于该目录）。
- [ ] 隔离子 Agent 的安全收尾正确（验证：运行 `pytest -q tests/test_subagent_service.py tests/test_task_manager.py`；无变更结束后观察临时目录自动删除；未提交、未推送或状态检查失败时观察目录保留，任务通知包含目录、分支与原因，主会话仍可继续）。
- [ ] 非隔离定义式和 Fork 式 Agent 行为不回归（验证：运行 `pytest -q tests/test_subagent_service.py tests/test_subagent_factory.py`；观察未声明隔离的定义式与 Fork 不创建 Worktree，仍保持原有上下文、权限和后台语义）。

## 过期清理与 Slash 命令

- [ ] 过期清理只删除安全的临时 Worktree（验证：运行 `pytest -q tests/test_worktree_cleanup.py`；建立过期临时、手动、脏、未推送、未登记和伪造路径样本，观察只删除登记一致、干净且在受控根内的临时目录）。
- [ ] 自动清理为失败关闭（验证：运行 `pytest -q tests/test_worktree_cleanup.py`；模拟 Git 状态、上游检查或记录读取失败，观察跳过删除并留下可追溯原因）。
- [ ] `/worktree` 五个子命令均为本地操作（验证：运行 `pytest -q tests/test_worktree_commands.py tests/test_commands.py`；执行 list、create、enter、exit、remove、`--force` 与 `--delete-branch`，观察行为、中文输出和模型历史均正确，命令文本未发给模型）。
- [ ] 命令错误可操作且不影响其他目录（验证：运行 `pytest -q tests/test_worktree_commands.py`；分别缺少参数、未知子命令、非法名称、未知目标、非 Git 仓库、受保护变更及初始化失败，观察中文反馈且其他 Worktree 状态不变）。

## 构建、回归与端到端

- [ ] 配置、工具、Agent、子 Agent、命令和 TUI 的受影响测试均通过（验证：运行 `pytest -q tests/test_config.py tests/test_tools.py tests/test_agent.py tests/test_commands.py tests/test_tui.py tests/test_subagent_loader.py tests/test_subagent_factory.py tests/test_subagent_service.py tests/test_task_manager.py`；期望退出码为 0）。
- [ ] 全部自动化测试通过（验证：运行 `pytest -q`；期望退出码为 0）。
- [ ] 端到端：隔离子 Agent 完成真实文件任务（验证：在 tmux 启动 YuCode，输入“使用一个 isolation: worktree 的子 Agent，在独立目录中查看项目 README 并创建一份简短摘要文件，然后告诉我结果”。观察创建 `.yucode/worktrees/` 下临时目录、子 Agent 的工具调用根目录正确、主会话收到完成通知；使用 `/worktree list` 验证无变更目录已自动清理或列表反映保留状态）。
- [ ] 端到端：手动目录和恢复流程（验证：在 tmux 执行 `/worktree create demo/check`、`/worktree enter demo/check`、执行真实文件读取或写入、`/worktree exit`，重启 YuCode 并使用 `--resume`；观察恢复到最后进入的有效目录。制造未提交修改后执行 `/worktree remove demo/check`，观察保护拒绝；再执行强制移除，观察仅该目录被移除）。

## 本次验收记录

- 通过：`uv run pytest -q tests/test_worktree_slug.py tests/test_worktree_session.py tests/test_worktree_manager.py`，路径校验、会话存取、创建/进入/退出、快速恢复与强制删除保护全部通过。
- 通过：`uv run pytest -q tests/test_subagent_tool.py tests/test_subagent_loader.py tests/test_subagent_policy.py tests/test_worktree_manager.py`，单次 `isolation: "worktree"` 正确传入委派请求，非法值在创建任务前被拒绝。
- 通过：`uv run pytest -q tests/test_worktree_manager.py tests/test_commands.py tests/test_subagent_tool.py`，默认删除保留分支，显式 `--force --delete-branch` 删除分支，空父目录清理且 `session.json` 保留。
- 通过：`uv run pytest -q`，完整自动化测试套件通过。
- 未执行：tmux 端到端场景。本机 PowerShell 环境未安装 `tmux`（执行 `tmux -V` 返回“找不到该命令”），因此无法按本章约定启动交互式 YuCode 并输入真实模型请求；这不影响自动化测试结果。
