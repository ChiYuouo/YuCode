# 斜杠命令注册与分发 Tasks

## 状态与执行约定

`spec.md`、`plan.md` 已获用户明确批准。本文件包含 42 项待执行任务；本文件和后续 `checklist.md` 都获批后才开始实施。当前未执行任何任务，也没有测试通过结论。

实现任务按约 2—5 分钟的主动工作单元拆分，自动测试、构建与真实模型等待时间另计；实际遇到超过该粒度的未决设计时先说明并拆分，不以预估代替验证。每项完成后运行列出的验证、查看实际输出，再勾选任务。失败先修复并重跑相关验证，不能把仅完成代码写入标成完成。

所有命令默认在项目根目录运行。新测试使用 `test_ch13_tNN_` 名称前缀，任务中的 `-k ch13_tNN` 必须选中至少一个真实行为测试，退出码为零且无相关失败才算通过。测试内容在对应任务中创建；本文件不是要求现在运行尚不存在的测试。

测试中的项目、用户记忆、配置与存档均放在独立临时目录。保护当前工作区已有的 `YUCODE.md`、`.workbuddy/` 和 `.yucode/` 改动，不自动提交代码。实施时以实时工作区状态为准，不能覆盖用户后续修改。

## 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `src/yucode/commands/__init__.py` | 命令包公开入口 |
| 新建 | `src/yucode/commands/models.py` | 命令定义、控制协议、状态和执行上下文 |
| 新建 | `src/yucode/commands/registry.py` | 登记校验、查找、公开清单与补全 |
| 新建 | `src/yucode/commands/parser.py` | 空白、聊天、斜杠输入解析 |
| 新建 | `src/yucode/commands/dispatcher.py` | 分发、用法错误和服务异常反馈 |
| 新建 | `src/yucode/commands/builtins.py` | 内置定义与全部处理器 |
| 新建 | `src/yucode/session_controller.py` | 会话新建、恢复提交与回退 |
| 修改 | `src/yucode/sessions.py` | 空会话、概要、路径校验和删除 |
| 修改 | `src/yucode/context.py` | 估算查询及上下文状态快照、复位 |
| 修改 | `src/yucode/agent.py` | Agent 会话状态与完整恢复保护 |
| 新建 | `src/yucode/tui/command_ui.py` | Textual 界面控制适配 |
| 新建 | `src/yucode/tui/command_widgets.py` | 命令候选、参数提示、删除确认 |
| 修改 | `src/yucode/tui/app.py` | 输入分流、统一 Worker、状态和取消 |
| 修改 | `src/yucode/tui/widgets.py` | 状态标记、输入按键和旧菜单移除 |
| 修改 | `src/yucode/tui/app.tcss` | 新命令菜单与删除弹窗布局 |
| 修改 | `src/yucode/cli.py` | 启动时构建并注入命令表 |
| 新建/测试 | `tests/test_commands.py` | 命令逻辑、假界面和参数边界 |
| 新建/测试 | `tests/test_session_controller.py` | 会话切换成功、失败与后台任务隔离 |
| 修改/测试 | `tests/test_sessions.py` | 空存档、目标边界和删除 |
| 修改/测试 | `tests/test_context.py` | 状态复位及外置文件保留 |
| 修改/测试 | `tests/test_agent.py` | 恢复、取消、提醒和历史完整性 |
| 修改/测试 | `tests/test_cli.py` | 冲突启动失败与正常装配 |
| 修改/测试 | `tests/test_tui.py` | 临时目录隔离、键盘交互与界面集成 |
| 修改 | `README.md` | 十条公开命令、别名和迁移用法 |
| 后续记录 | `doc/ch13/verification.md` | 实际测试、构建和 tmux 验收证据 |
| 后续更新 | `doc/ch13/task.md`、`doc/ch13/checklist.md` | 只在验证后记录完成状态 |

`conversation.py`、`memory.py`、`permissions.py` 使用已有接口，不预先重构；若实施发现必须改变已批准的设计边界，先说明问题。

## T01：隔离测试目录并记录基线

- [ ] 完成并验证

**文件：** `tests/test_tui.py`、`tests/test_cli.py`、`doc/ch13/verification.md`。
**依赖：** 四份文档均获批。
**步骤：**
1. 记录 `git status --short`，检查界面测试辅助构造器和 CLI 测试是否会访问真实项目缓存、用户配置或记忆。
2. 使用临时项目和临时用户目录隔离相关测试；保留显式传入的测试目录，不修改生产代码。增加测试，确认默认辅助构造器的缓存位于临时目录。
3. 在隔离生效后运行现有相关测试，记录既有失败和实际结果，作为后续对比基线。
**验证：** 运行 `uv run pytest tests/test_tui.py tests/test_cli.py tests/test_sessions.py tests/test_context.py tests/test_agent.py`；期望收集成功、无测试访问真实 `.yucode` 或用户记忆。测试失败须定位，不能当作本章已通过。

## T02：建立命令契约与假界面

- [ ] 完成并验证

**文件：** `src/yucode/commands/__init__.py`、`src/yucode/commands/models.py`、`tests/test_commands.py`。
**依赖：** T01。
**步骤：**
1. 按 plan 定义 `CommandKind`、不可变 `CommandDefinition`、错误类型、`CommandStatus`、`CommandUI` 和 `CommandContext`，注解依赖不产生循环导入。
2. 在测试中建立记录消息、模式变化、发送内容和确认结果的假界面，以及不会访问真实模型的最小 Agent 测试对象。
3. 验证用同一契约可展示通知、查询状态和发送一次消息，不需要创建 Textual 应用。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t02`；期望假界面收到正确数据，定义不能被原位修改。

## T03：实现启动登记校验

- [ ] 完成并验证

**文件：** `src/yucode/commands/registry.py`、`tests/test_commands.py`。
**依赖：** T02。
**步骤：**
1. 校验名称、别名、说明、用法及处理器；先构建临时索引，完整成功后保存只读结果。
2. 名称和别名转小写后进入同一索引，任何重复键立即报错。
3. 覆盖名称重复、名称撞别名、别名撞别名、同一条内部重复、大小写冲突及非法空白名称。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t03`；期望每种冲突均含冲突词和相关主名称，有效登记正常完成。

## T04：实现查找、公开清单与候选筛选

- [ ] 完成并验证

**文件：** `src/yucode/commands/registry.py`、`tests/test_commands.py`。
**依赖：** T03。
**步骤：**
1. 实现 `get`、`visible`、`complete`，保持登记顺序。
2. 补全同时匹配名称和别名，以主名称去重；隐藏项只允许精确执行查找。
3. 测试空前缀、大小写混合、同一命令多次命中和无匹配。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t04`；期望候选顺序、去重和隐藏规则均正确。

## T05：实现输入解析

- [ ] 完成并验证

**文件：** `src/yucode/commands/parser.py`、`tests/test_commands.py`。
**依赖：** T02。
**步骤：**
1. 定义 `InputKind` 和 `ParsedInput`，实现空白、普通文本和命令三分流。
2. 命令名在首个空白处分隔并转小写，参数仅去除首尾空白；不解释引号和转义。
3. 覆盖 `/`、`/ 参数`、制表符、换行、多空格及参数中的混合大小写。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t05`；期望输入类别正确，参数内部字符未被改写。

## T06：实现分发及错误转换

- [ ] 完成并验证

**文件：** `src/yucode/commands/dispatcher.py`、`tests/test_commands.py`。
**依赖：** T04、T05。
**步骤：**
1. 使用注册表查找并等待对应处理器，不接收普通聊天作为命令执行。
2. 未知命令显示 `/help` 引导；参数错误显示命中命令用法；服务异常显示中文原因。
3. 测试失败命令不触发消息发送，取消和退出信号能传给外层清理。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t06`；期望有效命令仅执行一次，异常有正确反馈且不误发 AI。

## T07：增加上下文估算与状态快照

- [ ] 完成并验证

**文件：** `src/yucode/context.py`、`tests/test_context.py`。
**依赖：** T01。
**步骤：**
1. 实现 `ContextState` 及估算查询、快照、恢复、复位接口，覆盖估算锚点和自动压缩熔断状态。
2. 复位时保留外置存储实例和文件序号，不调用清理目录的初始化路径。
3. 准备外置文件与已熔断状态，验证复位后旧文件仍可读取、新外置结果不会覆盖它。
**验证：** 运行 `uv run pytest tests/test_context.py`；期望新增状态测试与既有压缩、外置回归均通过。

## T08：增加 Agent 会话状态接口

- [ ] 完成并验证

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`。
**依赖：** T07。
**步骤：**
1. 实现 `AgentSessionState` 和快照、恢复、复位、估算查询接口。
2. 复位只清空对话及恢复相关状态，保留权限、Provider、项目指令和记忆组件。
3. 验证历史替换不调用存档追加，恢复快照后所有对话状态一致。
**验证：** 运行 `uv run pytest tests/test_agent.py -k ch13_t08`；期望复位得到空历史，恢复得到原历史和提醒，持久组件引用未改变。

## T09：支持合法空会话与概要

- [ ] 完成并验证

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`。
**依赖：** T01。
**步骤：**
1. 空文件及仅含空白的合法存档返回空历史、零消息数和 ID 对应时间，加入列表。
2. 实现 `get_summary`；有坏记录且没有有效消息的文件继续报错。
3. 更新原“空文件不可恢复”断言，保留坏行、工具配对和过期清理测试。
**验证：** 运行 `uv run pytest tests/test_sessions.py`；期望空会话可恢复且坏存档不会伪装为空会话。

## T10：统一会话目标边界校验

- [ ] 完成并验证

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`。
**依赖：** T09。
**步骤：**
1. 将 ID、存档目录和解析后文件目标的校验复用于读取、激活、追加、列表及清理路径。
2. 拒绝任意路径、目录越界和指向项目外的符号链接或目录联接。
3. 使用两个临时项目构造边界测试；测试系统不支持某种链接时记录原因，并用支持的链接方式验证实际边界，不能把未运行用例报为通过。
**验证：** 运行 `uv run pytest tests/test_sessions.py -k ch13_t10`；期望非法目标被拒绝，外部临时文件内容和存在状态不变。

## T11：实现单会话删除服务

- [ ] 完成并验证

**文件：** `src/yucode/sessions.py`、`tests/test_sessions.py`。
**依赖：** T10。
**步骤：**
1. 实现 `delete_session`，重复检查目标合法、存在且非活动会话。
2. 只删除指定存档，文件消失或权限错误时抛出真实操作错误。
3. 验证删除成功后不再列出或恢复，删除当前会话、不存在目标及失败操作不影响其他文件。
**验证：** 运行 `uv run pytest tests/test_sessions.py -k ch13_t11`；期望只有目标非活动存档被删除，失败无成功反馈。

## T12：完善 Agent 恢复成功路径

- [ ] 完成并验证

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`。
**依赖：** T08、T09。
**步骤：**
1. 恢复前保存完整状态，使用新的估算锚点准备目标历史，沿用既有一次必要压缩。
2. 成功后设置时间提醒和恢复保护；空会话不触发摘要或时间提醒。
3. 验证长时间间隔提醒只进入下一请求，恢复历史不重复写入存档。
**验证：** 运行 `uv run pytest tests/test_agent.py -k 'ch13_t12 or restores_history'`；期望普通、空白和长时间间隔恢复均符合 spec。

## T13：完善 Agent 恢复失败和取消路径

- [ ] 完成并验证

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`。
**依赖：** T12。
**步骤：**
1. 在摘要失败、仍超限、异常或取消时恢复消息、估算锚点、熔断和原提醒。
2. 恢复准备前和结束前检查取消信号，不把已取消的准备报告为成功。
3. 注入不同阶段失败，比较前后快照，并在回退后继续一轮普通对话。
**验证：** 运行 `uv run pytest tests/test_agent.py -k ch13_t13`；期望所有失败均保留原完整状态，后续对话仍能使用原历史。

## T14：实现新建会话协调

- [ ] 完成并验证

**文件：** `src/yucode/session_controller.py`、`tests/test_session_controller.py`。
**依赖：** T08、T10。
**步骤：**
1. 创建协调层，组合 Agent 与存档服务，并公开只读存档引用供处理器查询。
2. 实现“创建成功后同步复位”，创建失败不改变原状态；复位意外失败恢复原活动 ID 和快照。
3. 验证新 ID、空历史、旧存档保留和当前权限不变。
**验证：** 运行 `uv run pytest tests/test_session_controller.py -k ch13_t14`；期望正常新建与两种失败结果均符合提交顺序。

## T15：实现恢复会话的最终提交

- [ ] 完成并验证

**文件：** `src/yucode/session_controller.py`、`tests/test_session_controller.py`。
**依赖：** T13、T14。
**步骤：**
1. 异步读取目标历史，转发恢复进度；读取线程只返回数据，不改变活动 ID。
2. 截留 Agent 恢复成功事件，最终检查取消并激活目标存档后再报告成功。
3. 覆盖当前 ID 的无操作反馈、正常恢复后追加写入目标、存档激活失败及读取完成后取消的完整回退。
**验证：** 运行 `uv run pytest tests/test_session_controller.py -k ch13_t15`；期望消息与写入目标始终一致，失败及取消不出现成功事件。

## T16：验证后台记忆跨会话隔离

- [ ] 完成并验证

**文件：** `tests/test_session_controller.py`。
**依赖：** T11、T15。
**步骤：**
1. 用可控等待的假 Provider 启动真实 `MemoryManager` 的旧会话记忆任务，项目和用户记忆目录均隔离。
2. 在任务未结束时新建或恢复其他会话，再删除旧非活动存档。
3. 释放等待并检查新存档、被删除存档和记忆输出，不为该测试重写记忆机制。
**验证：** 运行 `uv run pytest tests/test_session_controller.py -k ch13_t16`；期望记忆能正常完成，新会话没有旧消息，删除的存档没有重建。

## T17：实现帮助、清屏、压缩与隐藏退出处理器

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T06、T08。
**步骤：**
1. 帮助从注册表生成，支持指定公开名称和别名；未知或隐藏查询不展示隐藏定义。
2. 为清屏、压缩和退出分别验证无参数，再调用 plan 对应的界面或 Agent 接口。
3. 假界面验证清屏不改历史，压缩事件被展示，退出仅走退出清理，错误参数均无副作用。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t17`；期望帮助信息准确，本地操作不触发普通消息发送。

## T18：实现模式与权限处理器

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T06。
**步骤：**
1. 实现 `/plan`、`/do` 及无参数 `/permission` 展示。
2. 建立四种公开权限参数与现有枚举的映射，子参数大小写不敏感；重复模式给出反馈。
3. 验证 `/do` 从任意模式回到默认，非法或多余参数不改变权限。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t18`；期望模式对象和刷新事件一致，配置内部值未被重定义。

## T19：实现固定提示词处理器

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T06。
**步骤：**
1. 将批准的 memory 和 review 提示词保存为固定文本。
2. 非空参数以补充要求原样附加，每次仅调用一次用户消息发送接口。
3. 验证无参数、内部多空格、换行和大小写内容；不切换权限或新增动态模板。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t19`；期望假界面仅收到一条完整展开消息，参数没有被拆散。

## T20：实现状态查询处理器

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T06。
**步骤：**
1. 格式化 `CommandStatus`，显示模型、模式、会话概要、上下文估算量与最近一轮报告量。
2. 区分 `None`、真实零值和缓存不可用，不把字符估算当作历史实际用量。
3. 覆盖尚未对话、已有用量、无持久会话及非法参数。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t20`；期望估算和实际标签清晰，缺失数据正确显示不可用。

## T21：实现会话概要、清单和子操作校验

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T06、T15。
**步骤：**
1. 实现无参数概要与 `list` 清单，区别当前上下文消息数和可恢复存档消息数。
2. 在执行任何子操作前统一检查名称和参数数量，保留 ID 原值；未启用会话服务时给出明确提示。
3. 清单包含空会话和当前标记，异步读取前展示状态；取消后不继续提交后续操作。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t21`；期望字段、空清单和错误用法正确，无模型请求或消息追加。

## T22：实现会话新建和恢复处理器

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T21。
**步骤：**
1. `new` 调用协调层，成功后清空显示、复位用量、显示新 ID。
2. `resume` 消费协调层事件，仅在成功提交后替换历史并复位用量；当前 ID 无操作。
3. 失败保留旧显示和用量；提交后渲染异常明确区分“已切换”与“未切换”，使用已提交历史重绘。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t22`；期望成功、失败和当前 ID 分支的显示、用量与活动会话一致。

## T23：实现会话删除确认流程

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`tests/test_commands.py`。
**依赖：** T11、T21。
**步骤：**
1. 读取并验证目标概要，当前会话直接提示先切换，合法非当前目标才请求确认。
2. 仅明确确认且未取消时调用存档删除服务，确认后再次依赖服务校验目标。
3. 覆盖取消、确认期间目标消失、删除失败与成功后的刷新。
**验证：** 运行 `uv run pytest tests/test_commands.py -k ch13_t23`；期望取消不删除，成功才报告成功，其他会话不受影响。

## T24：完成内置命令登记

- [ ] 完成并验证

**文件：** `src/yucode/commands/builtins.py`、`src/yucode/commands/__init__.py`、`tests/test_commands.py`。
**依赖：** T17、T18、T19、T20、T22、T23。
**步骤：**
1. `build_builtin_registry` 一次性登记十条公开命令和隐藏 exit；help 别名为 `?`，exit 别名为 `quit`。
2. 补齐每条定义的类型、描述、用法、参数提示，不登记旧 `/resume`。
3. 用同一注册表验证公开帮助、补全和完整名称执行范围。
**验证：** 运行 `uv run pytest tests/test_commands.py`；期望公开清单恰好十项，隐藏退出可执行但不展示，全部命令逻辑测试通过。

## T25：实现消息、清屏与历史显示适配

- [ ] 完成并验证

**文件：** `src/yucode/tui/command_ui.py`、`src/yucode/tui/app.py`、`tests/test_tui.py`。
**依赖：** T02、T08。
**步骤：**
1. 建立 `TextualCommandUI`，实现通知、错误、清屏、历史替换及上下文进度显示。
2. 将已有历史绘制改为可等待的“清空后重绘”，正确处理空历史、欢迎组件和消息引用。
3. 从适配器调用清屏和历史重绘，验证存档字节不变、通知不进入模型历史。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t25`；期望聊天区正确替换或清空，没有历史重复追加。

## T26：接入统一模式与 Token 状态

- [ ] 完成并验证

**文件：** `src/yucode/tui/command_ui.py`、`src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`。
**依赖：** T25。
**步骤：**
1. 适配模式设置、状态查询、用量复位和状态刷新，模式标签使用同一权限对象。
2. 增加当前轮及最近一轮 `Usage | None`，只根据实际用量事件更新，完成或取消保存本轮报告量。
3. 验证四种标记、无用量事件、真实零值和缓存不可用；清屏不复位用量，显式会话复位同时清除累计及最近一轮。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t26`；期望标记与查询一致，无报告量显示不可用，不沿用上一会话统计。

## T27：实现删除确认弹窗

- [ ] 完成并验证

**文件：** `src/yucode/tui/command_widgets.py`、`src/yucode/tui/command_ui.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py`。
**依赖：** T25。
**步骤：**
1. 实现 `SessionDeleteConfirm`，展示概要及不可恢复说明，默认选择取消。
2. 界面适配以可等待结果返回确认；Esc、Ctrl+C 或关闭均返回否，只有显式确认返回是。
3. 验证键盘和鼠标选择，并在窄终端尺寸检查确认文字和按钮可见。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t27`；期望默认 Enter 不删除，取消和确认分别返回正确结果。

## T28：提取可等待的对话、压缩与退出流程

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/command_ui.py`、`tests/test_tui.py`。
**依赖：** T26、T27。
**步骤：**
1. 从现有独立 Worker 提取可直接等待的生成、压缩和退出执行体，保留流式消息、工具卡片、审批与退出资源清理。
2. `send_user_message` 直接调用统一执行体，不再通过回车解析或创建嵌套 Worker。
3. 在输入迁移前保留旧入口的薄调用，避免中间阶段失去可运行入口；由 T29 统一收口。
**验证：** 运行 `uv run pytest tests/test_tui.py -k 'ch13_t28 or enter_sends or compact_is_local or shows_tool_summary'`；期望一条消息只启动一次 Agent，流式与工具显示不退化。

## T29：接入唯一输入分流与忙碌控制

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/command_ui.py`、`tests/test_tui.py`。
**依赖：** T24、T28。
**步骤：**
1. 应用接收可选注册表，未提供时构建默认表；创建协调层和界面适配。
2. 回车同步检查忙碌、解析、占用输入并启动唯一 Worker；为每次提交创建独立取消信号和执行上下文。
3. 普通文本发送，命令分发，空白返回；移除回车中的旧命令硬编码及旧菜单对提交的截获，Worker 的 `finally` 统一解锁。
4. 调整 Agent 完成事件，不能在 Worker 返回前释放输入。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t29`；期望普通消息一次发送、空白无操作、未知斜杠不发模型、连续回车不重复或排队执行。

## T30：打通会话界面与删除流程

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/command_ui.py`、`tests/test_tui.py`。
**依赖：** T29。
**步骤：**
1. 从真实回车入口串起概要、清单、新建、恢复及删除确认，用临时存档观察真实落盘结果。
2. 验证成功切换时替换显示和用量；失败时保留原显示、状态和追加目标。
3. 验证恢复当前会话无重复内容，确认取消后目标仍可恢复。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t30`；期望用户可见会话、实际历史、用量和存档目标一致。

## T31：补齐忙碌期间取消与异常恢复

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/command_ui.py`、`tests/test_tui.py`。
**依赖：** T30。
**步骤：**
1. 将启动加载、生成、审批、压缩、恢复和删除确认纳入同一忙碌检查；Shift+Tab 不能在忙碌时改模式。
2. Ctrl+C 对当前取消信号生效，并处理未完成审批或删除确认；所有失败分支最终恢复可输入状态。
3. 用可控暂停的服务测试重复提交、取消读盘后丢弃结果、命令异常和下一条正常输入。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t31`；期望无延迟执行、审批和停止有效，异常后可继续对话。

## T32：实现注册表驱动的候选与参数提示组件

- [ ] 完成并验证

**文件：** `src/yucode/tui/command_widgets.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py`。
**依赖：** T04、T27。
**步骤：**
1. 实现 `CommandMenu` 接收候选定义并显示名称、描述，选择结果仅返回主名称。
2. 实现 `CommandHint`，从命中定义显示参数说明，无命中或无提示时隐藏。
3. 独立挂载组件验证多候选滚动、上下键、鼠标、取消和参数提示，不在组件中执行命令。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t32`；期望组件按传入定义展示并返回选择，不产生业务副作用。

## T33：接入 Tab 与菜单按键分流

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`。
**依赖：** T29、T32。
**步骤：**
1. 仅命令名片段末尾允许 Tab；单匹配补主名称加空格，多匹配打开菜单，无匹配保留文本。
2. 菜单 Enter 和鼠标只补全，Esc 保留原输入；再次回车才执行。文本变化更新已打开菜单，进入参数区改显示提示。
3. 覆盖 `/`、混合大小写、`/?`、`/session `、光标在中间、隐藏命令及 Shift+Tab。
**验证：** 运行 `uv run pytest tests/test_tui.py -k ch13_t33`；期望补全不发送请求，参数空格不会被误判为命令名。

## T34：移除旧模式与恢复菜单并迁移回归测试

- [ ] 完成并验证

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`src/yucode/tui/app.tcss`、`tests/test_tui.py`。
**依赖：** T31、T33。
**步骤：**
1. 删除无调用方的 `ModeMenu`、`SessionPicker`、旧 `/resume` 流程、对应样式和临时入口，保留新版按键行为。
2. 将旧“选中即执行”“/do 恢复上一档”“/resume 弹窗”测试改成已批准的补全、默认模式及按 ID 恢复断言；保留仍适用的流式、滚动和审批测试。
3. 检查旧符号只在历史章节文档中出现，当前源码不残留执行入口。
**验证：** 运行 `uv run pytest tests/test_tui.py`；期望新旧适用交互全部通过。运行 `rg -n 'ModeMenu|SessionPicker|_start_resume_selection' src/yucode`，期望无匹配。

## T35：接入 CLI 启动校验与依赖装配

- [ ] 完成并验证

**文件：** `src/yucode/cli.py`、`src/yucode/tui/app.py`、`tests/test_cli.py`。
**依赖：** T24、T29。
**步骤：**
1. CLI 最先构建命令表，冲突输出中文原因并以状态 1 退出，早于配置加载、会话创建、Provider 和 MCP 启动。
2. 正常启动将同一注册表传给应用；更新测试 FakeApp 接口，避免测试替身阻碍正常装配。
3. 用冲突登记替换构建入口，验证失败后没有新存档，也未进入交互。
**验证：** 运行 `uv run pytest tests/test_cli.py`；期望冲突启动退出码为 1，正常启动仍创建一个新会话并传入正确注册表。

## T36：验证命令不污染模型历史及用量

- [ ] 完成并验证

**文件：** `tests/test_tui.py`、`tests/test_commands.py`。
**依赖：** T16、T34、T35。
**步骤：**
1. 从界面依次执行帮助、状态、清屏和模式命令，比较 Provider 请求、对话历史、存档及记忆调度次数。
2. 从界面执行 memory 与 review，验证展开消息一次入库、参数保留、沿用当前权限；使用可验证的只读工具响应。
3. 验证最近一轮用量在本地命令后不变，在新建和恢复成功后重置，清屏后仍能追问先前信息。
**验证：** 运行 `uv run pytest tests/test_tui.py tests/test_commands.py -k ch13_t36`；期望本地操作零意外模型调用，提示词命令各一次发送，历史和用量符合语义。

## T37：更新用户命令说明

- [ ] 完成并验证

**文件：** `README.md`。
**依赖：** T36。
**步骤：**
1. 添加十条公开命令与 session 子操作示例，说明 `/?`、Tab 补全和隐藏退出兼容入口。
2. 写清 clear 与 new 区别、压缩会消耗 Token、删除确认、当前会话不可直接删除和 `/resume` 迁移用法。
3. 修正上下文缓存说明：同一进程切换会话保留外置文件，沿用既有启动清理；不宣称每次 `/session new` 都清理缓存。
**验证：** 运行 `rg -n '/help|/compact|/clear|/plan|/do|/session|/memory|/permission|/status|/review|Tab' README.md`，逐条与实际注册表和批准 spec 对照，期望十条用法齐全且无旧行为冲突；再运行 `git diff --check`，期望无补丁空白错误。

## T38：完成自动回归和构建

- [ ] 完成并验证

**文件：** `tests/test_commands.py`、`tests/test_session_controller.py`、相关已有测试、`doc/ch13/verification.md`。
**依赖：** T37。
**步骤：**
1. 运行全部测试，确认没有因删去旧断言遗漏新的用户行为；记录通过、失败和跳过原因。
2. 运行语法编译及打包，确认命令包和界面模块可随当前项目构建；记录构建产物位置。
3. 当前项目没有配置独立 lint 工具，不临时引入新规范；运行现有可适用检查并记录范围。
**验证：** 依次运行 `uv run pytest`、`uv run python -m compileall -q src/yucode`、`uv build`、`git diff --check`；期望全部退出码为零，pytest 实际选中测试。失败后先修复并重跑受影响检查，不能继续报告完成。

## T39：准备并验证 tmux 真实终端环境

- [ ] 完成并验证

**文件：** `doc/ch13/verification.md`；运行时仅使用专用临时测试项目及测试用户目录。
**依赖：** T38。
**步骤：**
1. 为端到端测试创建独立临时项目，准备一份可审查的未提交改动，使用当前批准配置的真实模型连接；通过隔离进程的用户路径避免改写真实用户记忆，不把凭据打印或写入验收记录。
2. 在 WSL Ubuntu 的专用 tmux 会话中，经 Windows PowerShell 使用已安装的项目环境启动 YuCode；记录 tmux 名称、临时项目绝对路径和实际启动命令。
3. 检查键盘、中文渲染与 `/help`，确认不是仅有进程存活。若桥接不可用，记录实际错误并解决终端兼容性，不能拿模拟界面代替真实终端验收。
**验证：** 运行 `wsl -d Ubuntu -- tmux list-sessions`，再以实际会话名运行 `wsl -d Ubuntu -- tmux capture-pane -p -t <本次会话名> -S -120`；期望看到 YuCode 界面及十条命令帮助。尖括号必须替换为本次实际值并记入证据，不能照抄作为有效目标。

## T40：真实对话、模式、补全与预设提示词验收

- [ ] 完成并验证

**文件：** `doc/ch13/verification.md`；T39 的临时项目。
**依赖：** T39。
**步骤：**
1. 在同一 tmux 中输入真实请求：“请读取当前项目的 README 并说明这个示例做什么”，观察实际工具调用和最终回复；执行 `/status` 核对模式及用量。
2. 输入 `/pl` 按 Tab，确认只补全；回车执行后检查 `[PLAN]`，再执行 `/do` 检查 `[DEFAULT]`。执行 `/review` 审查预置改动、`/memory` 查看记忆，观察预设内容与回复。
3. 执行 `/clear` 后追问刚才示例内容，确认仍有上下文；执行 `/compact`，记录其实际结果，历史不足时应显示没有可压缩内容，不能伪称压缩成功。足量历史压缩成功已由 AC8 的对应自动测试提供证据。
**验证：** 每组输入后用 T39 的 `tmux capture-pane` 命令保存输出片段，并核对临时项目文件未被 review 擅自修复；期望真实模型回复、可见只读工具、状态切换和保留上下文全部符合预期。真实请求失败则记录失败，不能以 FakeProvider 替代。

## T41：真实会话新建、恢复与删除验收

- [ ] 完成并验证

**文件：** `doc/ch13/verification.md`；T39 的临时项目。
**依赖：** T40。
**步骤：**
1. `/session` 记下实际旧 ID，执行 `/session new` 后核对新 ID、零消息、用量复位与权限保留；查看 `/session list`，旧会话和空会话均可识别。
2. 用 `/session resume <旧ID>` 恢复后追问先前示例，核对可见历史和新增存档目标；再次切到另一测试会话。
3. 删除旧的非活动测试会话：先取消并确认仍存在，再明确确认并检查列表消失；尝试删除当前会话应被拒绝。只操作本次临时项目中创建的 ID。
**验证：** 保存上述输入的 tmux 输出并读取临时存档清单；期望恢复可继续对话，取消保留目标，确认只删除指定测试存档，当前会话仍可继续使用。

## T42：逐项验收、记录证据并交付

- [ ] 完成并验证

**文件：** `doc/ch13/verification.md`、`doc/ch13/task.md`、后续批准的 `doc/ch13/checklist.md`。
**依赖：** T38、T40、T41。
**步骤：**
1. 按已批准 checklist 逐项检查，关联实际运行命令、预期、实际输出、通过/失败/未执行和证据位置。前序检查已满足同一清单项且代码未再变化时复用本次有效证据；缺少证据的项实际运行。
2. 失败先修复并重跑受影响验证；如有环境阻碍或外部失败，明确记录未通过，不将其勾选为完成。删除测试数据或结束 tmux 仅针对已核实的本次目标，记录保留的验收材料。
3. 检查最终变更只涉及授权功能，按证据更新任务和清单状态；报告“通过（N/M）”“未通过（预期、实际与修复方案）”“端到端结果”。
**验证：** 执行 `git status --short` 和 `git diff --check`；逐条核对清单与 `verification.md`，期望没有无证据勾选或遗漏，源码变更后受影响检查已重跑，用户原有改动保留。

## 执行顺序

默认按 T01 至 T42 顺序执行；该顺序满足所有依赖。下图用于说明模块依赖，不表示自动委派其他代理。

```text
T01 测试隔离
 ├─ T02 → T03 → T04 ─┐
 │    └─ T05 ────────┴→ T06 分发
 ├─ T07 → T08 状态接口 ──────────────┐
 └─ T09 → T10 → T11 删除服务         │
       └─ [T08] → T12 → T13          │
 [T08,T10] → T14 → [T13] → T15 → [T11] → T16 后台隔离

 [T06,T08] → T17
 T06 → T18 / T19 / T20
 [T06,T15] → T21 → T22
 [T11,T21] → T23
 [T17—T20,T22,T23] → T24 完整登记

 [T02,T08] → T25 → T26 ─┐
              └→ T27 ──┴→ T28
 [T24,T28] → T29 → T30 → T31
 [T04,T27] → T32 → [T29] → T33
 [T31,T33] → T34 旧入口移除
 [T24,T29] → T35 CLI 装配
 [T16,T34,T35] → T36 → T37 → T38
 T38 → T39 → T40 → T41 → T42
```

## 设计覆盖与审批范围

| 已批准设计 | 对应任务 |
| --- | --- |
| 定义、校验、解析、分发 | T02—T06、T24、T35 |
| 上下文快照、会话新建/恢复与删除 | T07—T16 |
| 十条公开命令与隐藏退出 | T17—T24 |
| 界面适配、状态、确认与统一执行 | T25—T31 |
| Tab 补全、参数提示与旧交互迁移 | T32—T34 |
| 对话、存档、用量和后台任务隔离 | T16、T30、T36 |
| 文档、回归、构建及 tmux 验收 | T01、T37—T42 |

请审批任务粒度、依赖和是否遗漏。获批后进入 `checklist.md` 设计；本文件获批本身不等于实现阶段已经获准开始。
