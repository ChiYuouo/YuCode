# YuCode

YuCode 是一个终端 AI 编程助手：支持 OpenAI 与 Anthropic Claude 流式对话，能自主调用工具、执行多轮任务，并带有权限控制、上下文管理、持久记忆、MCP 外部工具、Skill 能力包、Hook 自动化、子 Agent、Git Worktree 隔离与 Agent Team 协作。

## 安装

需要 Python 3.12 或更高版本。

```powershell
pip install -e .
```

安装后会在当前目录寻找 `yucode.yaml` 并启动：

```powershell
yucode
```

`--resume` 会在启动时恢复此前进入的 Worktree 工作目录：

```powershell
yucode --resume
```

### 配置

把 `yucode.yaml.example` 复制为 `yucode.yaml`，填写真实的 `api_key`。**不要提交真实密钥**（`yucode.yaml` 已在 `.gitignore` 中）。

```yaml
protocol: anthropic      # 或 openai
model: claude-sonnet-4-6
base_url: https://api.anthropic.com
api_key: your-api-key
thinking:
  enabled: true          # 仅 anthropic 支持
```

完整可选项见 `yucode.yaml.example`，或下文的「配置项参考」。

## 斜杠命令

输入以 `/` 开头的命令会直接由 YuCode 处理，不会作为普通聊天交给模型。输入命令名前缀后按 Tab：单个匹配会直接补全，多个匹配会显示候选；`/?` 是 `/help` 的别名。

| 命令 | 作用 |
| --- | --- |
| `/help [命令]` | 查看全部命令或某条命令的用法。 |
| `/compact` | 手动压缩较早的对话上下文，会使用摘要模型并消耗 Token。 |
| `/clear` | 清空聊天显示，保留当前对话上下文和会话；会清除已激活的 Skill。 |
| `/plan`、`/do` | 进入计划模式，或回到默认模式。 |
| `/session` | 查看当前会话 ID 和消息数。 |
| `/memory [要求]` | 请 AI 查看并梳理当前项目记忆。 |
| `/permission [模式]` | 查看或切换权限模式。 |
| `/status` | 查看模型、模式、会话、上下文估算及最近一轮 Token。 |
| `/review [重点]` | 请 AI 只读审查当前工作区改动。 |
| `/skill` | 查看已激活与可用的 Skill。 |
| `/tasks` | 列出子 Agent 任务。 |
| `/task` | 查看或取消子 Agent 任务。 |
| `/worktree` | 管理隔离工作目录。 |
| `/team` | 管理 Agent Team。 |

会话管理使用以下子命令：

```text
/session list
/session new
/session resume <会话 ID>
/session delete <会话 ID>
```

`/session new` 才会创建干净对话；`/clear` 只清屏。删除非当前会话时会显示二次确认，当前会话需要先切换。此前的 `/resume` 已迁移为 `/session resume <会话 ID>`。`/exit` 与 `/quit` 仍可退出，但不会出现在帮助或补全中。

## 权限模式

YuCode 用五层相互补充的机制控制工具调用：不可配置放开的危险操作黑名单、项目范围路径沙箱、可配置规则、整体权限模式和人在回路确认。被拒绝的调用会把原因回灌给模型，让 Agent 调整策略继续运行，而不是直接终止任务。

四档模式既可在配置文件设置，也可在会话中用 Shift+Tab 循环切换：

| 模式 | 行为 |
| --- | --- |
| `default` | 未命中的有副作用操作请求用户确认。 |
| `acceptEdits` | 自动接受项目内的文件编辑，命令仍需确认。 |
| `plan` | 只读，拒绝写入、编辑与命令执行。 |
| `bypassPermissions` | 跳过未命中的确认（黑名单与路径沙箱仍然生效）。 |

> 配置文件 `permissions.mode` 的规范写法是 camelCase（`acceptEdits`、`bypassPermissions`）；会话内 `/permission <模式>` 命令两种拼写都接受，`acceptEdits` 与 `accept_edits` 效果相同。

选择「永久允许」产生的规则写入 `yucode.permissions.local.yaml`，该文件已被 `.gitignore` 忽略。

## 上下文管理

YuCode 会在每次模型请求前先外置过大的工具输出，再在接近上下文窗口时生成结构化摘要。默认窗口为 128000 Token，可在 `yucode.yaml` 中配置：

```yaml
context:
  window_tokens: 128000
```

自动压缩会保留 13K Token 安全余量；输入 `/compact` 可手动压缩，并使用 3K 余量。外置工具结果保存在项目的 `.yucode/context/`，模型需要细节时会用 `read_file` 重新读取；该缓存仅保留到本次 YuCode 会话结束。若服务端报告上下文超限，YuCode 会紧急压缩并仅重试原请求一次。

内置工具不再截断自身返回文本，上下文管理器是控制上下文大小的唯一机制。

## 项目指令与记忆

### 分层指令

启动时 YuCode 会按优先级加载项目指令并注入首个请求，可用 `@include <相对路径>` 引入更多内容（越界引用和超过最大嵌套深度会被跳过并给出中文警告）：

```text
<项目根>/YUCODE.md              # 优先级最高
<项目根>/.yucode/YUCODE.md
<用户主目录>/.yucode/YUCODE.md
```

### 持久记忆

记忆按作用域存放在 `<项目>/.yucode/memory/` 与 `~/.yucode/memory/`。每次启动在处理请求前载入记忆索引，让新会话自动获得用户偏好、项目规则和已有项目知识；工具调用中断后也可安全恢复。用 `/memory` 可请 AI 梳理当前记忆。

## MCP 工具

可在项目级 `yucode.yaml` 的 `mcp_servers` 中声明 stdio 或 Streamable HTTP Server；也可在用户级 `~/.yucode/yucode.yaml` 中声明。项目级同名 Server 会覆盖用户级声明。

`env` 和 `headers` 支持 `${VAR}` 环境变量展开。启动时 YuCode 会发现可用工具，并将其命名为 `MCP__服务器名__工具名`。单个 Server 加载失败只显示中文警告，不影响其他工具。每次 MCP 请求最长等待 30 秒，不会自动重连。

MCP 工具会继续经过 YuCode 现有权限流程：默认模式下需要明确执行授权和用户确认，也可选择本会话允许。本期只支持工具，不支持资源、提示词或采样。

## Skill 能力包

Skill 用于保存可重复的 AI 操作。YuCode 会从项目 `.yucode/skills/`、用户 `~/.yucode/skills/` 和内置目录发现它们，优先级依次为项目、用户、内置；修改后下一次对话或命令会自动读取新定义。

单文件 Skill 使用 `*.md`；目录 Skill 以 `SKILL.md` 为入口，并可带 `prompt.md`、`tool.json`、实现脚本与 `references/`：

```markdown
---
name: example
description: 一句话说明用途
allowedTools: [read_file, run_command]
mode: inline # 或 fork
history: none # fork 可用 none、all 或正整数
model: optional-model-name
---
按步骤完成任务。用户参数：$ARGUMENTS
```

模型起初只看到名称和说明，调用 `LoadSkill` 后才获得完整 SOP。激活后 `allowedTools` 会收窄模型可见工具；`LoadSkill` 与 `InstallSkill` 不受白名单影响，但仍受计划模式和授权限制。`inline` 在主对话执行，`fork` 在隔离对话执行并回流摘要。

`/skill` 显示已激活 Skill；加载后可用 `/skill:<名称> [参数]` 执行，例如 `/skill:test 单元测试`。它不会覆盖已有 `/review`。`/clear` 会清除激活 Skill，但保留原有聊天显示/历史语义。

内置 `commit`、`review`、`test` 三个样板。`InstallSkill` 仅从用户明确提供或授权的直接 HTTP(S) URL 安装单个 Markdown 或 zip 能力包，校验失败不会留下半成品且不会覆盖已有用户 Skill。YuCode 不提供 Skill 市场、搜索、发布、版本管理或自动更新。

## Hook 自动化

Hook 让用户以「事件 + 可选条件 + 动作」声明确定性自动化，例如格式化、审计、通知和工具调用前的安全限制。Hook 自身失败不会中断 Agent 对话。

```yaml
hooks:
  - id: block-json-write        # 可选，用于诊断定位，需唯一
    event: pre_tool_use
    if:
      all:
        - field: TOOL_NAME
          operator: "=="
          value: write_file
        - field: TOOL_ARGS.path
          operator: "=~"
          value: '\.json$'
    action:
      type: command
      command: 'Write-Output "禁止直接写入 JSON 文件，请使用专用工具"'
      reject: true
      reason: "禁止直接写入 JSON 文件，请使用专用工具。"
```

**规则字段**：`id`（可选，诊断用）、`event`（必填）、`if`（可选条件）、`action`（必填）、`once`、`async`。规则、`action` 与 `if` 条件中的未知字段会直接报中文错误，不会被静默忽略——写错字段名等于规则悄悄失效，这里必须失败关闭。

**诊断指称**：配置错误与运行失败都用规则指称定位——有 `id` 时显示 `Hook「block-json-write」`，未命名时退回声明顺序 `Hook 第 3 条`。`id` 重复会在启动时被拒绝。

**事件**：`startup`、`shutdown`、`session_start`、`session_end`、`turn_start`、`turn_end`、`pre_send`、`post_receive`、`pre_tool_use`、`post_tool_use`、`permission_request`、`file_change`、`command_execute`、`compact`、`error`、`task_start`、`task_stop`、`task_complete`、`send_message`。

**条件**：用 `if.all` 或 `if.any` 组合，`field` 可取 `EVENT`、`TOOL_NAME`、`FILE_PATH`、`MESSAGE`、`ERROR`、`TASK_ID`、`PARENT_TASK_ID`、`TASK_STATUS`，以及 `TOOL_ARGS.xxx`（取工具参数）；`operator` 支持 `==`、`!=`、`=~`（正则）、`~=`（glob）。

**动作**：`type` 为 `command`、`prompt`、`http` 或 `agent`，配合各自的 `command`/`prompt`/`url`、`method`、`body`。可用字段还有 `once`（只执行一次）、`async`（异步执行）、`timeout_seconds`。`reject: true` 只在 `pre_tool_use` 上有效，用于拦截工具调用，且必须提供 `reason`。

**模板变量**：`$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$TOOL_ARGS.xxx` 会在动作执行前替换。

## 子 Agent 与后台任务

主 Agent 通过统一的 `Agent` 工具把子任务委派出去，从而隔离中间过程。子 Agent 分两类：**定义式**从预设角色与空白对话开始；**Fork** 继承发起者完整对话历史，以复用上下文与请求缓存。

角色用 Markdown + YAML frontmatter 定义，项目、用户、内置三层按优先级加载并覆盖同名角色：

```markdown
---
name: Explore
description: 只读探索项目并返回事实与相关位置。
tools: [read_file, find_files, search_code]
disallowedTools: []
model: haiku            # inherit / haiku / sonnet / opus
maxIterations: 12
permissionMode: plan    # default / acceptEdits / plan / bypassPermissions
isolation: none         # none / worktree
---

你是 Explore 子 Agent。只使用只读工具探索项目……
```

存放位置：`<项目>/.yucode/agents/`、`~/.yucode/agents/`、内置目录。内置角色有 `Explore`、`Plan`、`general-purpose`。定义缺少 frontmatter、含未知字段、模型或权限非法、轮次非法或正文为空时不会进入目录，并给出含文件路径与原因的中文诊断。

后台任务状态可查看和取消：

```text
/tasks
/task info <任务标识>
/task cancel <任务标识>
```

相关工具还有 `TaskCreate`、`TaskGet`、`TaskUpdate`、`TaskList`。用 `subagents` 配置段可对所有子 Agent 收敛工具范围：

```yaml
subagents:
  global_disallowed: []                                  # 永久禁用
  background_allowed: [read_file, find_files, search_code] # 后台任务只允许这些
  execution_timeout_seconds: 600
```

这些规则只能移除工具，不能增加权限。

## Git Worktree 隔离

需要隔离的任务会在独立工作目录中运行，避免多个任务同时改动同一仓库时互相覆盖。目录固定在仓库的 `.yucode/worktrees/`，分支名固定为 `yucode/worktree/{名称}`。

```text
/worktree list
/worktree create <名称>
/worktree enter <名称>
/worktree exit
/worktree remove [--force] [--delete-branch] <名称>
```

名称由 LLM 参与生成，因此被视为不可信输入：slug 会在触发任何 Git 或文件系统写入前完成校验，非法名称（空段、`.`、`..`、绝对路径、反斜杠、超长值、非法字符）一律拒绝，并保证解析结果不越出受控根目录。

```yaml
worktrees:
  worktreeinclude: [".env.local", "config/local"]   # 复制进 Worktree 的文件
  symlink_directories: ["node_modules", ".venv", "vendor"]  # 软链接共享的目录
  cleanup_after_hours: 168
  cleanup_interval_seconds: 3600
```

初始化的复制与软链接产物属于运行环境脚手架，不会被当成成员的改动参与「是否有未提交改动」判断与提交范围。

## Agent Team

对需要并行实施与审查的复杂目标，主 Agent 可以成为 Team Lead，创建可持久化的 Agent Team，把文件改动交给成员完成，自己只承担编排与决策。团队数据保存在 `<项目>/.yucode/teams/<团队名称>/`。

- 成员默认 `writable=false`：只读、使用项目目录、没有独立 Worktree，工具视图里也没有写能力。只有需要修改文件的成员才由 Lead 指定 `writable: true`，YuCode 才会为其创建 Worktree。
- 成员通过共享任务和点对点邮箱协作，减少所有信息都经由 Lead 转发。
- 后端优先级默认 `tmux → iterm2 → in_process`，视当前环境可用性选择。

```text
/team list
/team info <团队>
/team kill <团队> <成员>
/team delete <团队>
```

相关工具：`TeamCreate`、`TeamSpawn`、`TeamStop`、`TeamDelete`、`TeamMerge`、`SendMessage`。

```yaml
teams:
  enabled: true
  backend_priority: [tmux, iterm2, in_process]
  coordinator_enabled: false     # 开启后 Lead 只编排、沟通与合并
  mailbox_lock_timeout_seconds: 5
```

启用协调者模式还可设置环境变量 `YUCODE_COORDINATOR=1`。

## 配置项参考

| 段 | 关键字段 | 说明 |
| --- | --- | --- |
| （顶层） | `protocol`、`model`、`base_url`、`api_key` | 必填；`protocol` 为 `openai` 或 `anthropic`。 |
| `thinking` | `enabled` | 仅 anthropic 支持。 |
| `agent` | `max_iterations` | 每个用户请求的模型调用预算，默认 10。 |
| `context` | `window_tokens` | 上下文窗口，默认 128000，必须大于 13000。 |
| `permissions` | `mode` | 启动时的权限模式（camelCase）。 |
| `hooks` | 规则列表 | 见上文「Hook 自动化」。 |
| `mcp_servers` | stdio / http 声明 | 用户级同名 Server 会被项目级完整覆盖。 |
| `subagents` | `global_disallowed`、`background_allowed`、`execution_timeout_seconds` | 子 Agent 工具收敛。 |
| `worktrees` | `worktreeinclude`、`symlink_directories`、`cleanup_after_hours`、`cleanup_interval_seconds` | Worktree 初始化与清理。 |
| `teams` | `enabled`、`backend_priority`、`coordinator_enabled`、`mailbox_lock_timeout_seconds` | Agent Team。 |

配置错误（缺少字段、类型不对、未知子字段、非法模式）会在启动时给出中文错误并停止；单个 MCP Server 或单个 Hook 的问题只影响该项。

## 运行目录

项目状态统一放在仓库的 `.yucode/` 下（已在 `.gitignore` 中）：

```text
.yucode/
├── sessions/     # 会话存档
├── memory/       # 项目记忆
├── agents/       # 子 Agent 定义
├── skills/       # 项目 Skill
├── context/      # 工具结果外置缓存（仅本次会话有效）
├── teams/        # Agent Team 数据
└── worktrees/    # 隔离工作目录
```

用户级数据统一放在 `~/.yucode/`，Windows、macOS 与 Linux 行为一致：

```text
~/.yucode/
├── YUCODE.md         # 用户级项目指令
├── yucode.yaml       # 可选，只用于补充 MCP Server
├── skills/           # 用户级 Skill
├── agents/           # 用户级子 Agent 定义
├── memory/           # 用户级记忆
└── permissions.yaml  # 用户级权限规则
```

早期版本在 Windows 上把 `yucode.yaml` 与 `skills/` 放在 `%APPDATA%\YuCode\`。该位置仍作为只读回退保留：新位置缺失对应项时会继续从旧位置读取，启动时若检测到旧目录仍有内容，会给出一次中文迁移提示。

## 开发与测试

```powershell
# 运行全部测试（使用项目自带 venv）
.venv\Scripts\python.exe -m pytest -q
```

测试不依赖真实模型服务，用假 Provider 与假驱动模拟。功能验收按 `doc/chXX/` 下的 `spec.md` → `plan.md` → `task.md` → `checklist.md` 流程推进，端到端场景按 `AGENTS.md` 用 tmux 在真实终端中验证。
