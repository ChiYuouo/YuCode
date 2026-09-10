# 持久记忆与会话恢复 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/instructions.py` | 三层指令和安全 `@include` 加载 |
| 新建 | `src/yucode/sessions.py` | JSONL 会话创建、记录、扫描、恢复与清理 |
| 新建 | `src/yucode/memory.py` | 自动笔记、两级 Markdown 存储和索引 |
| 修改 | `src/yucode/conversation.py` | 原始追加事件记录与无回写恢复 |
| 修改 | `src/yucode/context.py` | 恢复会话的一次压缩保护 |
| 修改 | `src/yucode/prompting.py` | 指令、记忆索引和时间跨度提醒注入 |
| 修改 | `src/yucode/agent.py` | 恢复流程、恢复超限保护和笔记触发 |
| 修改 | `src/yucode/cli.py` | 启动装配、创建会话和清理 |
| 修改 | `src/yucode/tui/app.py` | `/resume`、恢复与诊断展示 |
| 修改 | `src/yucode/tui/widgets.py` | 命令菜单与会话选择菜单 |
| 新建 | `tests/test_instructions.py` | 项目指令加载边界测试 |
| 新建 | `tests/test_sessions.py` | 会话持久化与恢复测试 |
| 新建 | `tests/test_memory.py` | 笔记与索引测试 |
| 修改 | `tests/test_conversation.py` | 追加事件和恢复不回写测试 |
| 修改 | `tests/test_agent.py` | 恢复压缩、提醒、笔记调度测试 |
| 修改 | `tests/test_prompting.py` | 提示顺序与提醒测试 |
| 修改 | `tests/test_cli.py` | 启动装配与过期清理测试 |
| 修改 | `tests/test_tui.py` | `/resume` 交互和恢复测试 |

## T1：实现三层项目指令加载

**文件：** `src/yucode/instructions.py`、`tests/test_instructions.py`  
**依赖：** 无

**步骤：**

1. 定义加载结果与加载器，定位项目根、项目 `.yucode` 和用户 `.yucode` 的三个 `YUCODE.md` 入口。
2. 按项目根、项目配置、用户配置顺序拼接已读取文本，缺失入口直接跳过。
3. 解析独占一行的 `@include`，相对包含文件解析路径；以入口对应允许根目录、最大深度 5 与 `visited` 集合限制访问。
4. 将循环、越界、缺失和读取失败转换为中文 warning，不中断其他内容加载。
5. 测试优先级、相对嵌套、五层限制、环路、项目越界、用户目录越界和缺失入口。

**验证：** 运行 `pytest tests/test_instructions.py -q`，期望所有测试通过，且无效引用只产生 warning。

## T2：为 Conversation 增加原始追加事件

**文件：** `src/yucode/conversation.py`、`tests/test_conversation.py`  
**依赖：** 无

**步骤：**

1. 定义 `ConversationEvent` 和可选记录器协议，事件分别描述用户文本、助手文本/调用、部分助手文本和工具结果。
2. 在既有四种追加方法成功更新内存后发出事件；保留所有现有消息合并语义。
3. 增加不触发记录器的恢复替换入口。
4. 测试每种追加只产生一次正确事件；工具结果与下一用户消息合并时不重复记录工具结果；恢复替换不产生事件。

**验证：** 运行 `pytest tests/test_conversation.py -q`，期望原有和新增测试全部通过。

## T3：实现 JSONL 消息编码与单会话追加

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`  
**依赖：** T2

**步骤：**

1. 定义会话 ID、会话概要和恢复结果数据结构；生成 `YYYYMMDD-HHMMSS-xxxx` 形式的唯一 ID。
2. 将 `ConversationEvent` 完整编码为带版本、时间、类型和载荷的一行 JSON，并实现对应解码校验。
3. 在 `.yucode/sessions/` 创建会话文件；对每个事件以追加、刷新、关闭方式写入一行。
4. 测试连续创建 ID 不冲突、每个事件的完整往返编码、文件为多行 JSONL，以及截断末行不影响此前有效行读取。

**验证：** 运行 `pytest tests/test_sessions.py -q -k "id or encode or append or truncated"`，期望全部通过。

## T4：实现会话扫描、可信恢复与派生列表信息

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`  
**依赖：** T3

**步骤：**

1. 扫描当前项目会话目录，拒绝不符合 ID 格式或不在目录内的请求。
2. 按 JSONL 事件重放出消息；标题从首条有效用户文本派生，最近活动时间从最后有效事件派生，消息数从恢复消息派生。
3. 遇到无效 JSON、未知版本或非法载荷时跳过该行并收集 warning。
4. 暂存含工具调用的助手事件，只在下一有效工具结果的调用 ID 完全匹配时提交；不匹配或缺失时从该调用开始丢弃后续记录。
5. 测试列表按最近活动排序、标题/时间/计数派生、坏行跳过、孤立调用截断、工具 ID 错配和含工具结果后继续用户消息的恢复。

**验证：** 运行 `pytest tests/test_sessions.py -q`，期望所有恢复边界测试通过。

## T5：实现会话保留期清理

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`  
**依赖：** T4

**步骤：**

1. 根据每个会话的有效最近活动时间判断是否超过 30 天；无有效记录时按安全保守方式保留。
2. 排除当前活动会话，仅删除已经解析为会话目录内 JSONL 文件的过期会话。
3. 测试超过 30 天的会话被删除、恰好或不足 30 天的会话保留、当前会话永不删除、非法文件不被删除。

**验证：** 运行 `pytest tests/test_sessions.py -q -k cleanup`，期望全部通过。

## T6：实现两级笔记文件和索引读取

**文件：** `src/yucode/memory.py`、`tests/test_memory.py`  
**依赖：** 无

**步骤：**

1. 定义用户/项目范围、四种笔记类别、笔记动作和索引数据结构。
2. 创建项目 `.yucode/memory/` 与用户 `.yucode/memory/` 的路径管理，读写带 YAML frontmatter 的独立笔记文件。
3. 生成或重建两个 `index.md`，包含按类别组织、可追溯到笔记 ID 的紧凑摘要。
4. 测试用户/项目笔记写入正确目录、frontmatter 完整、索引读取可用于提示，以及不存在笔记时返回空索引。

**验证：** 运行 `pytest tests/test_memory.py -q -k "storage or index"`，期望全部通过。

## T7：实现笔记模型响应校验、去重更新和索引上限

**文件：** `src/yucode/memory.py`、`tests/test_memory.py`  
**依赖：** T6

**步骤：**

1. 构造无工具的专用记忆提取请求，输入本轮历史和现有两个索引，要求模型只返回受限 JSON 动作。
2. 校验动作的类别、范围、标题、正文和更新目标；非法 JSON 或非法目标只写诊断，不写文件。
3. 执行创建、更新、忽略动作，使模型判定重复时更新现有笔记或忽略，而不是新增重复文件。
4. 重建索引；超出 200 行或 25 KB 时调用受限索引压缩请求，失败时按确定规则保留有限条目并记录诊断。
5. 测试四类笔记、用户/项目分流、重复更新、无效模型输出、200 行边界、25 KB 边界和压缩失败降级。

**验证：** 运行 `pytest tests/test_memory.py -q`，期望所有笔记与索引测试通过。

## T8：实现后台笔记调度与退出收尾

**文件：** `src/yucode/memory.py`、`tests/test_memory.py`  
**依赖：** T7

**步骤：**

1. 用 `asyncio.create_task` 调度记忆提取，并保存/回收任务引用，避免未观察到的异常。
2. 提供非阻塞的诊断队列读取，以及退出时有时间上限的等待和取消方法。
3. 测试调度立即返回、后台完成后写入笔记、Provider 失败/超时只形成诊断、退出收尾不会让任务泄漏。

**验证：** 运行 `pytest tests/test_memory.py -q -k "background or failure or shutdown"`，期望全部通过。

## T9：扩展稳定提示与恢复时间提醒

**文件：** `src/yucode/prompting.py`、`tests/test_prompting.py`  
**依赖：** T1、T6

**步骤：**

1. 保持现有系统约束排序不变，将已排序项目指令和用户/项目记忆索引加入稳定提示的可选模块。
2. 扩展运行期上下文，支持一条可选的会话时间跨度提醒，并放入独立系统级 runtime message。
3. 测试指令高优先级内容先于低优先级内容、两个索引均被注入、空内容不产生占位、缓存键随稳定内容变化，时间提醒不写入稳定提示或历史。

**验证：** 运行 `pytest tests/test_prompting.py -q`，期望全部通过。

## T10：实现恢复专用的一次压缩保护

**文件：** `src/yucode/context.py`、`tests/test_context.py`  
**依赖：** T2

**步骤：**

1. 增加恢复历史准备接口：仅在超过既有自动安全线时压缩一次，并返回压缩后估算状态。
2. 保留普通请求的外置、自动压缩和紧急重试行为，不使恢复专用逻辑影响新会话。
3. 测试恢复历史超线时只调用一次摘要、压缩后仍超线返回可展示失败、未超线不压缩，以及普通请求原有路径不回归。

**验证：** 运行 `pytest tests/test_context.py -q`，期望全部通过。

## T11：在 Agent 接入恢复与自动笔记

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`  
**依赖：** T8、T9、T10

**步骤：**

1. 注入可选的 `MemoryManager` 和恢复状态，新增异步恢复入口，先替换历史并执行恢复专用压缩。
2. 恢复失败时维持原有新会话并发出清晰失败事件；成功时按最后活动时间设置仅下一请求有效的 24 小时时间提醒。
3. 为已恢复会话标记上下文超限保护，使下一次 Provider 超限直接报告，不执行现有紧急第二次压缩。
4. 在 `COMPLETED` 的最终助手消息追加后仅调度一次后台笔记；取消、流错误、迭代上限、未知工具和验证未完成均不调度。
5. 测试恢复成功/失败、一次性提醒、超限不重试、自然结束调度及其他停止原因不调度。

**验证：** 运行 `pytest tests/test_agent.py -q`，期望所有 Agent 回归和新增测试通过。

## T12：在启动入口装配持久化组件

**文件：** `src/yucode/cli.py`、`tests/test_cli.py`  
**依赖：** T1、T3、T5、T6、T9、T11

**步骤：**

1. 启动时加载项目指令和两个记忆索引，并将其传给提示构建器。
2. 创建 `SessionManager` 与当前会话，使用其事件记录器构造新 `Conversation`；创建后运行过期清理并排除当前 ID。
3. 创建并注入 `MemoryManager`，把加载/清理 warning 和后台诊断读取接口交给 TUI。
4. 测试默认每次启动创建独立会话、Agent 获得拼接指令和记忆、清理在启动发生、既有 Provider/MCP 装配不回归。

**验证：** 运行 `pytest tests/test_cli.py -q`，期望全部通过。

## T13：实现 `/resume` 的菜单与恢复交互

**文件：** `src/yucode/tui/widgets.py`、`src/yucode/tui/app.py`、`tests/test_tui.py`  
**依赖：** T4、T11、T12

**步骤：**

1. 将 `/resume` 加入现有命令建议，保留 `/plan`、`/do` 的快捷交互和既有按键行为。
2. 执行 `/resume` 后读取当前项目会话概要，显示 ID、标题、最近活动时间和消息数的可选列表；空列表显示中文反馈。
3. 用户选择会话后禁用输入，调用 Agent 恢复入口，显示恢复 warning、压缩进度或失败原因；失败时恢复当前新会话的可用状态。
4. 恢复成功后将有效历史渲染到聊天区域，并允许继续对话；定期显示已完成后台笔记任务的诊断。
5. 测试命令选择、会话概要显示、空列表、成功恢复后下一条消息携带历史、恢复失败可继续当前新会话，以及 `/plan`、`/do` 回归。

**验证：** 运行 `pytest tests/test_tui.py -q`，期望全部通过。

## T14：运行全量自动化验证并修复回归

**文件：** 所有上述生产文件与测试文件  
**依赖：** T1–T13

**步骤：**

1. 运行完整测试套件并修复本章导致的失败。
2. 检查 `git diff --check`，修正空白和补丁格式问题。
3. 以临时项目目录构造三层指令、历史 JSONL 和笔记索引，完成一次无网络的集成验证。

**验证：** 运行 `pytest -q` 与 `git diff --check`，期望测试全部通过且没有格式错误。

## T15：执行终端端到端验收

**文件：** `doc/ch12/checklist.md` 记录的验收环境与结果  
**依赖：** T14

**步骤：**

1. 用 tmux 在包含有效 `yucode.yaml` 和 `YUCODE.md` 的测试项目中启动 YuCode。
2. 输入真实请求，观察首个模型请求使用项目指令和记忆索引，并完成无工具最终回复以触发后台笔记。
3. 退出后重新启动，确认新会话已创建；执行 `/resume`，选择上一会话并继续追问，观察历史被恢复和工具/回复正常展示。
4. 按 `checklist.md` 逐项记录实际观察结果；失败则定位并修复后重跑。

**验证：** tmux 中的真实对话、`/resume` 恢复和 `checklist.md` 全项均通过。

## 执行顺序

```text
T1 ───────────────────────────────┐
T2 → T3 → T4 → T5 ────────┐       │
T6 → T7 → T8 ─────────────┼→ T9 ─┼→ T12 → T13 → T14 → T15
T2 ────────────────────────┴→ T10 → T11 ────────┘
```
