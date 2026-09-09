# YuCode 结构化系统提示与缓存策略 Checklist

> 每一项均通过测试、请求捕获、终端观察或 tmux 中的真实对话验证。完成后记录实际命令、观察结果和通过/失败状态；未提供缓存字段与未配置真实服务必须如实标为不可用或未执行。

## 提示结构与稳定性

- [x] AC1：完整请求的稳定系统提示严格按身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出的顺序组成；每个模块间仅有一个空行，环境内容位于其后。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q -k "order or separator or stable"`；期望相同输入的稳定部分完全相同，顺序和分隔断言通过。
- [x] AC2：无、部分和全部可选内容时，可选模块只在环境之后按自定义指令、已激活 Skill、长期记忆的顺序出现，且没有空白占位。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q -k "optional"`；期望三种输入组合的模块清单断言通过。
- [x] AC3：稳定规则和工具不变、环境或历史变化的连续请求中，稳定提示和缓存键不变；环境、历史、补充规则为独立请求部分。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py tests/test_agent.py -q -k "stable or environment or history or cache_key"`；期望请求捕获显示动态内容未进入稳定文本。

## 工具规则、补充指令和模式

- [ ] AC4：全局规则和相关工具描述都表达“优先使用适用专用工具”“编辑已有内容前先读取”。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q -k "tool or edit or read"`；期望工具名称/schema 不变，编辑工具描述及全局工具规则均包含前置读取约定。随后在 tmux 场景 1 观察真实模型先调用读取工具再编辑。
- [x] AC5：运行期补充始终由 `<system-reminder>` 包裹，模型将其作为系统约束而不当成用户问题回复。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q -k "reminder or runtime"`；期望捕获到标签与供应商系统/developer 映射。随后在 tmux 场景 3 确认最终回复不复述标签且遵守模式约束。
- [x] AC6：同一 Agent 多轮运行中，环境信息变化后下一轮请求反映新值，稳定提示不重建。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "environment"`；期望第一轮与下一轮捕获的环境文本不同，而稳定部分相同。
- [x] AC7：11 轮规划模式中，第 1、5、10 轮有完整模式提醒，其他轮有精简提醒；所有轮仅能获得只读工具。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py tests/test_agent.py -q -k "iteration or plan"`；期望轮次分布和工具集合断言通过，且没有文件或命令副作用。

## Provider 缓存映射与观测

- [x] AC8 / OpenAI：Responses 请求包含稳定 `instructions`、增强后的工具、位于历史之前的 developer 补充消息和确定性缓存键；完成事件有缓存字段时被解析为缓存读/写 token。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_openai_provider.py -q`；期望请求结构和有字段/无字段用量断言全部通过，缺失字段显示不可用而非零命中。
- [x] AC8 / Claude：Messages 请求包含稳定系统/工具缓存前缀，以及位于该前缀之后、不写入 Conversation 的 `<system-reminder>` 消息内容；流用量的缓存读/创建 token 被解析。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_anthropic_provider.py -q`；期望请求结构和有字段/无字段用量断言全部通过。
- [x] AC8 / 终端显示：缓存字段可用时状态栏显示累计“缓存读 N / 写 M”；本次请求完全没有缓存字段时显示“缓存数据不可用”。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "cache"`；期望有数据、无数据、多轮累计、取消和错误后的状态断言通过。

## 人工对比场景

- [ ] AC9 / 场景 1：在有效模型配置下请求“先读取 README.md，向临时文件写入一段摘要，再读取该临时文件确认内容，最后总结”。验证：记录输入、工具调用顺序、最终回复和缓存状态；期望先出现 `read_file`，使用文件专用工具而非命令完成读写，最终回复说明已验证。
- [ ] AC9 / 场景 2：在同一会话切换 `/plan`，输入“分析 README 的安装说明并给出改进计划，不要修改任何文件”。验证：记录工作区状态、可见工具活动和最终回复；期望只出现读取、查找或搜索，工作区无变化，最终仅给计划。
- [ ] AC9 / 场景 3：发起会产生至少两轮工具调用的请求，并让第一轮写入或生成可供下一轮读取的临时内容。验证：记录每轮请求的环境信息和最终回复；期望第二轮携带更新环境/上下文，稳定提示不变，最终回复不直接回应 `<system-reminder>` 标签。
- [ ] AC9 / 场景 4：在相同工作目录、模式和工具集合下连续执行两次等价读取请求。验证：记录两次状态栏缓存读/写数值或供应商返回字段；期望能够据实际字段判断是否发生缓存复用。若服务未返回字段，记录“缓存数据不可用”，不得将此场景判为命中。

## 跨 Provider 集成与安全回归

- [ ] AC10 / OpenAI 闭环：使用有效 OpenAI 配置完成“读取项目信息 → 修改临时文件 → 再读取验证 → 最终回复”。验证：在 tmux 中观察工具活动、命令确认行为（如触发）、最终回复与文件内容；期望 Agent 自动完成，无需额外催促，流式显示、工具调用和安全边界正常。
- [x] AC10 / Claude 闭环：使用有效 Claude 配置重复上述流程。验证：在 tmux 中观察与 OpenAI 相同的用户可见行为；期望模式、安全边界、停止与最终结果一致，缓存状态按供应商实际字段显示。
- [ ] AC10 / Plan 安全：分别对两家 Provider 发起 `/plan` 只读探索。验证：执行前后记录 `git status --short` 与临时目录状态；期望没有写入、编辑或命令工具活动，工作区无新增变更。
- [x] N4：既有 Agent 多轮、取消、未知工具、命令确认、工作目录边界、流式显示和 Token 统计均未退化。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tools.py tests/test_tui.py -q`；期望全部通过。
- [x] N5：本章没有项目指令文件加载、自动记忆、真实 MCP 接入或自动化评估依赖。验证：按上述测试和 tmux 场景运行，并检查请求只使用本地内置工具；期望无需这些能力仍可完成全部已配置验证。

## 严格规则与执行层门禁

- [x] AC11 / 先读后改：模拟模型在未读取时请求 `edit_file`，再模拟先 `read_file` 后请求同一编辑。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py tests/test_tools.py tests/test_agent.py -q -k "read or edit or policy"`；期望首次请求返回 `policy_violation` 且文件不变，第二条流程可执行。
- [x] AC11 / 专用工具与验证：对已有文件修改后让模型直接给最终文本，再让模型读取修改目标。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_prompting.py -q -k "verification or tool"`；期望未验证时不产生成功结束，读取成功后才允许完成；工具描述和全局提示均包含强制验证规则。
- [x] AC12 / 解释和评审：分别输入“解释这段代码作用”和“评审这段代码并给建议”，并模拟模型请求写入或命令。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py tests/test_agent.py -q -k "answer_only or review or authorization"`；期望副作用工具被拒绝，工作区不变，模型获得可读的授权说明。
- [x] AC12 / 明确执行与意图不清：分别输入“创建文件并验证内容”和“处理这个文件”。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py tests/test_agent.py -q -k "execute or ambiguous"`；期望前者可在既有安全边界内闭环，后者仅可只读探索且最终要求用户明确副作用授权。
- [x] AC13 / 事实与失败：模拟工具失败、验证失败和 Plan 模式。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "failure or verification or plan"`；期望最终回复不包含“已完成”式伪造结论，明确说明失败或尚未验证的事实。
- [x] AC13 / 敏感信息：将模拟 API key、Bearer token、Cookie 和密码作为工具结果或模型文本输入。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py tests/test_agent.py tests/test_tui.py -q -k "redact or secret or sensitive"`；期望历史、工具回灌、流式文本和最终显示只包含固定脱敏占位符，没有原始值。
- [x] N6：检查稳定提示与增强工具描述中的流程、授权、事实和安全规则。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q -k "strict or mandatory or authorization"`；期望关键规则均为“必须/不得/仅当”形式，且不存在相互冲突的例外。

## 构建与全量自动化

- [x] Python 源码可编译。验证：运行 `.venv\Scripts\python.exe -m compileall -q src`；期望退出码为 0 且无错误输出。
- [x] 新提示层与 Provider 协议的全量测试通过。验证：运行 `.venv\Scripts\python.exe -m pytest -q`；期望退出码为 0，且无失败、错误或悬挂。
- [ ] 项目未配置独立 lint 门禁。验证：检查 `pyproject.toml`；期望未配置时如实记录。若实现期间新增 lint 配置，则运行对应命令并要求退出码为 0。

## tmux 端到端执行步骤

1. 启动：运行 `wsl.exe tmux new-session -d -s yucode-ch4 "cd <项目根目录> && .venv/Scripts/python.exe -m yucode"`，随后运行 `wsl.exe tmux capture-pane -p -t yucode-ch4`；期望看到 YuCode 欢迎界面。
2. 输入场景 1 的真实请求，使用 `wsl.exe tmux capture-pane -p -t yucode-ch4` 观察读取、编辑、验证和最终回复；保存缓存状态文本。
3. 输入 `/plan` 并执行场景 2，比较执行前后 `git status --short` 与临时目录；期望无副作用。
4. 输入场景 3 与场景 4，记录各轮状态和两次请求的缓存观测。需要切换 Provider 时，停止旧会话后用对应有效配置重新启动。
5. 输入“解释 README 的作用，不要修改文件”。即使模型请求写入或命令，期望看到策略拒绝，工作区不变。
6. 输入“先读取临时文件，再修改一处内容并读取验证”。期望工具顺序为读取、编辑、读取；若跳过首次读取或最终验证，期望策略拒绝或阻止成功结束。
7. 若配置缺失、认证失败或网络不可达，保留终端输出并在验收记录中写明阻碍、未执行场景和已通过的离线测试；不得把 API mock 的结果记为真实端到端通过。

## 验收记录

- 自动化测试：2026-09-07 运行 `.venv\Scripts\python.exe -m pytest -q`，94 项通过，退出码为 0。
- 编译：2026-09-07 运行 `.venv\Scripts\python.exe -m compileall -q src`，退出码为 0。
- tmux 场景 1（读取后编辑）：Claude 实测完成写入、命令确认后的读取验证和最终总结。模型首次 `read_file` 调用缺少参数，随后使用 `write_file` 并以 `run_command` 读取验证；因此专用工具优先与“先读取后编辑”的定性表现尚不通过，验收临时文件已清理。
- tmux 场景 2（Plan 只读）：Claude 实测只执行 `find_files`；模型请求 `run_command` 时被模式工具边界拒绝，最终仅输出改进计划，未观察到写入。
- tmux 场景 3（环境变化多轮）：待执行。
- tmux 场景 4（缓存复用对比）：早期短无工具请求的连续与跨会话复跑均为缓存读 0；严格规则 tmux 请求在后续多轮中显示累计缓存读 16631 / 写 12952，证明当前服务已返回真实缓存命中字段。严格按“相同用户请求两次”的独立对比仍待补做，因此本项保持未通过。
- OpenAI 真实闭环：未执行；当前工作区未提供可用 OpenAI 配置。
- Claude 真实闭环：2026-09-07 通过；执行模式完成“读取/写入/验证/总结”闭环，Plan 模式保持只读工具边界。
- AC11～AC13 严格规则：2026-09-07 的策略、Agent、工具、提示和 TUI 测试均通过。未授权写入被拒绝且不显示伪造完成；合法“读→改→读”在验证前不展示完成文本；常见 API key、Bearer、Cookie 与密码值在历史和最终文本前被替换为 `[已脱敏]`。
- tmux 严格规则：首次请求连接超时，重试后成功写入临时文件，但模型连续发出缺少路径的 `read_file` 调用。系统未报告完成，在第 10 轮显示迭代上限失败；临时文件已清理。状态栏累计显示缓存读 16631 / 写 12952，证明缓存字段的读取与展示在真实服务中生效。
