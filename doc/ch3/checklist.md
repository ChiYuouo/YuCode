# MewCode Agent Loop Checklist

> 每项均通过运行代码、检查事件或在 WSL tmux 中观察真实交互验证；验收时记录实际结果后再勾选。

## Agent 循环与停止条件

- [x] AC1：模型连续请求两轮及以上工具时，MewCode 自动执行、回灌并继续，直到无工具响应后正常结束。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "loop or completed"`；期望调用次数、历史和最终 `COMPLETED` 事件断言通过。
- [x] AC2：默认上限为 10，可配置覆盖，触顶后不执行最后响应中新请求的工具，也不发起下一次模型调用。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_config.py -q -k "limit or max_iterations"`；期望默认、覆盖、无效配置、失败结果和调用次数断言通过。
- [x] AC3：模型流和命令执行期间取消都会尽快结束，且不再启动后续循环。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tools.py tests/test_tui.py -q -k "cancel"`；期望停止原因为 `CANCELLED`、命令进程已回收、输入恢复且无额外调用。
- [x] AC4：一轮全为未知工具时允许回灌纠正，连续第二轮仍全未知时停止；任一已注册工具出现后计数清零。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "unknown"`；期望三种计数路径和 `UNKNOWN_TOOL_LIMIT` 断言通过。
- [x] AC5：Provider 在首段前、文本中途和结束阶段报错时停止并保留已完成内容。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "error or partial"`；期望 `STREAM_ERROR`、部分文本、历史合法性和可继续对话断言通过。

## 事件流、用量与工具调度

- [x] AC6：单次请求可观察到进度、文本、工具调用、工具结果、用量和最终事件，且 `AgentFinished` 唯一并位于末尾。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "event or progress"`；期望事件类型、迭代序号、阶段和停止原因顺序断言通过。
- [x] AC7：文本增量实时发出，同时完整响应被正确收集用于历史与下一轮。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tui.py -q -k "stream or delta"`；期望分段事件先于完成事件，拼接文本与历史一致，界面逐段更新。
- [x] AC8：相邻只读调用并发，副作用和未知工具形成顺序屏障。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "batch or barrier"`；期望“读 A、读 B、写 C、读 D、命令 E”的开始/结束记录证明仅 A/B 重叠且无人跨越屏障。
- [x] AC9：并发工具乱序完成时，结果仍按原调用顺序发出和回灌；单项失败不影响同批其他项。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_agent.py -q -k "order or failure"`；期望结果 ID 顺序与调用顺序相同，成功和失败均存在于下一轮历史。
- [x] AC13：多轮用量按轮发出并正确累计。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tui.py -q -k "usage or token"`；期望每轮用量、请求累计值和界面会话总量的算术断言通过。

## 模式与安全边界

- [x] AC10：`/plan` 仅获得读取、查找和搜索工具，可多轮探索后输出计划。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tui.py -q -k "plan"`；期望传给 Provider 的工具集合只有三项只读工具，模式指令要求只规划，工作目录无修改。
- [x] AC11：普通消息与带正文 `/do` 使用完整工具集；空 `/plan`、空 `/do` 显示用法且不写历史；三种入口共享已有会话。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "mode or plan or do or history"`；期望解析正文、工具集合、原文显示和消息历史断言通过。
- [x] AC12：命令继续逐次确认；拒绝后不执行并回灌失败，写文件和改文件不新增确认。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_tui.py -q -k "approval or command or reject"`；期望确认调用次数、拒绝结果和文件工具无确认断言通过。
- [x] 上一章安全限制未退化。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "escape or binary or unique or timeout or truncate"`；期望工作目录越界、二进制读取、非唯一替换、超时和截断测试全部通过。

## Provider、历史与界面集成

- [x] OpenAI 使用异步 Responses 流，正确发送 `instructions`、工具声明和富历史，并解析文本、工具参数与用量。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_openai_provider.py -q`；期望全部通过。
- [x] Claude 使用异步 Messages 流，正确发送顶层 `system`、工具声明和富历史，并解析 thinking、文本、工具参数与用量。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_anthropic_provider.py -q`；期望全部通过。
- [x] SSE 的多行数据、注释与未完成帧在异步输入下行为正确。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_sse.py -q`；期望全部通过。
- [x] Conversation 在正常、触顶、取消和流错误后都保持可被两个 Provider 序列化的合法历史。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_conversation.py tests/test_agent.py -q -k "history or partial or limit or cancel"`；期望工具调用与结果成对，无 thinking、用量或孤立调用写入历史。
- [x] TUI 只通过 Agent 事件驱动展示，生成和工具等待期间保持响应，完成后恢复输入与自动滚动。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望布局、流式显示、进度、工具摘要、取消和多轮测试全部通过。
- [x] CLI 能以旧 YAML 和含 Agent 配置的新 YAML 分别创建 OpenAI/Claude Agent。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_config.py -q`；期望四种装配组合和配置错误测试通过。

## 构建与自动化测试

- [x] Python 源码可编译。验证：运行 `.venv\Scripts\python.exe -m compileall -q src`；期望退出码为 0 且无错误输出。
- [x] 不再存在旧的同步 Provider 或单工具轮次入口。验证：运行 `rg -n "def stream\(|run_turn|本轮工具调用上限|首次响应只执行一个" src/mewcode`；期望 Provider 定义均为 `async def stream`，其余旧状态机标记无匹配。
- [x] 全部自动化测试通过。验证：运行 `.venv\Scripts\python.exe -m pytest -q`；期望退出码为 0 且无失败、错误或悬挂。
- [x] 项目未配置 lint 工具，本章不新增独立 lint 门禁。验证：检查 `pyproject.toml`；若后续加入 lint 配置，则运行对应命令并要求退出码为 0。

## tmux 端到端场景

- [x] tmux 会话可启动。验证：运行 `wsl.exe tmux new-session -d -s mewcode-ch3 "cd /mnt/c/develop/Mewcode && .venv/Scripts/python.exe -m mewcode"`，再运行 `wsl.exe tmux capture-pane -p -t mewcode-ch3`；期望看到 MewCode 欢迎界面而非 shell 或启动错误。
- [ ] AC14 / 场景 1（OpenAI 完整 Agent）：使用 OpenAI 配置，在 `mewcode-ch3` 会话请求“在临时测试目录创建一个文本文件，写入指定内容，再读取它验证内容，最后总结执行结果”。期望无需追加催促即可依次看到写入、读取和最终回复，停止原因正常，文件内容正确，输入恢复可用。
- [ ] AC14 / 场景 2（Claude 完整 Agent）：切换到 Claude 配置后重复场景 1。期望工具闭环、事件显示、停止原因和最终文件结果与 OpenAI 行为一致。
- [ ] 场景 3（Plan Mode 只读）：记录工作区状态后，在 tmux 输入 `/plan 分析如何给 README 增加安装示例，只输出计划`。期望可出现读取、查找或搜索活动，不能出现写入、修改或命令活动；结束后工作区状态与测试前一致。
- [ ] 场景 4（独立 `/do`）：在同一会话输入 `/do 按以下内容执行：在临时测试目录创建 plan-do.txt，写入 done 并读回确认`。期望共享此前对话但仅执行本次正文，完成写入和验证；单独输入 `/do` 时只显示用法，历史长度不增加。
- [ ] 场景 5（命令取消）：请求通过命令先等待 30 秒再创建临时文件，批准命令后立即按 Ctrl+C。期望命令被终止、目标文件未创建、没有后续模型或工具调用，界面显示取消并恢复输入。

## 验收记录

- 自动化测试：2026-09-07 运行 `.venv\Scripts\python.exe -m pytest -q`，76 项通过，退出码 0。
- 编译与旧入口检查：`compileall` 退出码 0；旧状态机标记无匹配，三个 Provider 定义均为异步流。
- tmux：`mewcode-ch3` 成功显示欢迎界面；空 `/do` 仅显示用法且消息数保持 0；模型连接阶段按 Ctrl+C 后约 0.5 秒恢复“准备就绪”。
- OpenAI 端到端：当前工作区没有可用的 OpenAI 配置，未执行真实服务验收。
- Claude 端到端：当前配置的 `https://whitepolar.app/v1/messages` 在 POST 连接阶段发生 `ConnectTimeout`；错误能显示且输入可恢复，但无法完成真实工具闭环。
- 未通过项：tmux 场景 1～5 中，完整 OpenAI、Claude、Plan、`/do` 和命令取消闭环尚无真实模型响应证据。修复方案：服务恢复或提供可用 OpenAI/Claude 配置后，按本节五个场景复测；测试期间未创建任何验收临时文件。

---

# TUI 活动动效补充 Checklist

> 每项均通过自动化测试或 tmux 中的实际屏幕观察验证；完成后再勾选并记录结果。

## 活动状态

- [x] AC15 / 模型等待：发送一个延迟响应请求；期望正式回答出现前的等待提示显示循环活动标记，回答开始后该标记停止。
- [x] AC15 / 思考：Provider 发送 thinking 增量时；期望回答上方的折叠标题显示循环活动标记，正文不自动展开或滚动。
- [x] AC16 / 工具：模型请求一个或多个工具时；期望每项工具结果出现前有“正在执行”活动行，完成后原位变为成功或失败的静态结果。
- [x] AC17：在思考后输出正式文本；期望思考标题变为静态“已思考”且保持折叠，正式回答位于其下方。
- [x] AC18：分别模拟正常完成、取消、流错误和迭代上限；期望等待一个动效周期后不再发生帧更新，输入均恢复可用。
- [x] AC19：在活动期间按 Ctrl+C；期望取消立即生效、动效停止、流式文本与工具结果顺序不受影响。

## 自动化与端到端

- [x] TUI 回归：运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望活动帧、工具替换、模式菜单、流式显示、取消与命令确认测试全部通过。
- [x] 全量回归：运行 `.venv\Scripts\python.exe -m pytest -q` 与 `.venv\Scripts\python.exe -m compileall -q src`；期望均退出码 0。
- [ ] tmux 动效场景：启动 MewCode，发起一个只读 Plan 请求；期望依次观察模型等待、思考标题、进行中工具行和静态工具结果；在下一次等待时按 Ctrl+C，期望活动立即停止并恢复输入。

## 补充验收记录

- 自动化测试：2026-09-07 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`，14 项通过；全量 76 项通过，源码编译退出码 0。
- tmux 动效：2026-09-07 观察到模型等待时 `◒ 正在请求模型…` 持续变化；`read_file` 完成后显示静态成功结果，最终回答后动效消失、输入恢复。快速只读工具未能稳定截获进行中帧，已由自动化测试覆盖；当前 tmux 场景保留为待进一步人工观察项。
