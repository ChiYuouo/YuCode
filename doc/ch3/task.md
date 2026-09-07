# MewCode Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mewcode/agent.py` | Agent Loop、模式、事件、进度与停止原因 |
| 新建 | `src/mewcode/cancellation.py` | Provider 与工具共享的异步取消信号 |
| 修改 | `src/mewcode/config.py` | 应用配置、Agent 配置和迭代上限校验 |
| 修改 | `src/mewcode/conversation.py` | 从单工具状态机改为纯历史管理 |
| 修改 | `src/mewcode/cli.py` | 创建并装配 Agent 依赖 |
| 修改 | `src/mewcode/providers/base.py` | 异步 Provider 协议与兼容事件模型 |
| 修改 | `src/mewcode/providers/sse.py` | 异步 SSE 解码 |
| 修改 | `src/mewcode/providers/openai.py` | OpenAI 异步流和模式指令 |
| 修改 | `src/mewcode/providers/anthropic.py` | Claude 异步流和模式指令 |
| 修改 | `src/mewcode/tools/base.py` | 工具安全分类与异步协议 |
| 修改 | `src/mewcode/tools/registry.py` | 按安全分类筛选工具 |
| 修改 | `src/mewcode/tools/executor.py` | 顺序屏障、并发批次、确认与异常包装 |
| 修改 | `src/mewcode/tools/filesystem.py` | 文件工具异步包装与取消边界 |
| 修改 | `src/mewcode/tools/command.py` | 可取消、可超时的异步命令进程 |
| 修改 | `src/mewcode/tools/__init__.py` | 导出新增工具类型 |
| 修改 | `src/mewcode/tui/app.py` | 模式解析、异步事件消费、确认与取消 |
| 修改 | `src/mewcode/tui/widgets.py` | Agent 进度和停止状态展示 |
| 新建 | `tests/test_agent.py` | 循环、事件、模式和停止条件测试 |
| 修改 | `tests/test_provider_base.py` | 异步取消和 Provider 基础类型测试 |
| 修改 | `tests/test_sse.py` | 异步 SSE 测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 异步协议测试 |
| 修改 | `tests/test_anthropic_provider.py` | Claude 异步协议测试 |
| 修改 | `tests/test_tools.py` | 安全分类、并发、取消和命令测试 |
| 修改 | `tests/test_conversation.py` | 历史提交和异常状态测试 |
| 修改 | `tests/test_config.py` | Agent 配置测试 |
| 修改 | `tests/test_tui.py` | 模式入口、事件消费、确认和取消测试 |
| 修改 | `tests/test_cli.py` | Agent 装配测试 |

## T1：建立共享异步取消信号

**文件：** `src/mewcode/cancellation.py`、`src/mewcode/providers/base.py`、`tests/test_provider_base.py`

**依赖：** 无

**步骤：**
1. 定义可重复调用的取消对象，提供同步 `cancel()`、取消状态和异步 `wait()`。
2. 从 Provider 基础模块导入并暂时重导出该类型，保持迁移期间现有导入可用。
3. 测试取消前等待、取消后立即唤醒和重复取消。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_provider_base.py -q`，期望取消相关测试全部通过。

## T2：加入 Agent 迭代配置

**文件：** `src/mewcode/config.py`、`tests/test_config.py`

**依赖：** 无

**步骤：**
1. 定义 `AgentConfig` 与根 `AppConfig`，让加载结果同时包含 Provider 与 Agent 配置。
2. 解析可选的 `agent.max_iterations`，缺失时使用 10。
3. 拒绝布尔值、非整数、零、负数和错误的 `agent` 结构，同时保留现有顶层 Provider YAML 兼容性。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_config.py -q`，期望默认值、覆盖值、旧配置和所有无效值测试通过。

## T3：增加异步 SSE 解码器

**文件：** `src/mewcode/providers/sse.py`、`tests/test_sse.py`

**依赖：** 无

**步骤：**
1. 让 SSE 解码器接收异步文本行迭代器。
2. 保持多行 `data` 合并、事件名、注释忽略和未完成帧丢弃语义。
3. 使用 `asyncio.run` 构造分段异步输入测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_sse.py -q`，期望原有 SSE 行为在异步输入下全部通过。

## T4：定义异步 Provider 协议

**文件：** `src/mewcode/providers/base.py`、`tests/test_provider_base.py`

**依赖：** T1

**步骤：**
1. 将 Provider 的 `stream` 改为异步迭代接口，并加入必需取消信号和可选模式指令。
2. 保留 Message、内容块、StreamEvent 和 Usage 的供应商无关语义。
3. 更新基础协议构造测试，确认 thinking、文本、工具调用和用量仍可表达。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_provider_base.py -q`，期望协议与事件测试通过。

## T5：迁移 OpenAI 异步网络流

**文件：** `src/mewcode/providers/openai.py`、`tests/test_openai_provider.py`

**依赖：** T1、T3、T4

**步骤：**
1. 使用 `httpx.AsyncClient` 和异步 SSE 解码发送 Responses 请求。
2. 在取消时关闭活动响应，并把取消与网络错误区分为既有异常类型。
3. 更新文本增量、HTTP 错误、SSE 错误和流中取消测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_openai_provider.py -q -k "text or error or cancel"`，期望异步文本和错误路径测试通过。

## T6：完成 OpenAI 工具、指令与用量适配

**文件：** `src/mewcode/providers/openai.py`、`tests/test_openai_provider.py`

**依赖：** T5

**步骤：**
1. 保留工具参数碎片拼接、工具 Schema 和富历史序列化。
2. 将 Agent 模式指令写入 Responses 请求的 `instructions`。
3. 验证单次模型调用用量仍由完成事件产生。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_openai_provider.py -q`，期望 OpenAI 全部协议测试通过。

## T7：迁移 Claude 异步网络流

**文件：** `src/mewcode/providers/anthropic.py`、`tests/test_anthropic_provider.py`

**依赖：** T1、T3、T4

**步骤：**
1. 使用 `httpx.AsyncClient` 和异步 SSE 解码发送 Messages 请求。
2. 在取消时关闭活动响应，并保持取消、网络错误和供应商错误的区分。
3. 更新文本、thinking、HTTP 错误、SSE 错误和流中取消测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_anthropic_provider.py -q -k "text or thinking or error or cancel"`，期望异步基础流测试通过。

## T8：完成 Claude 工具、指令与用量适配

**文件：** `src/mewcode/providers/anthropic.py`、`tests/test_anthropic_provider.py`

**依赖：** T7

**步骤：**
1. 保留按内容块索引拼接工具参数及工具结果序列化。
2. 将 Agent 模式指令写入 Messages 请求的顶层 `system`。
3. 保持输入、输出与 thinking Token 用量解析。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_anthropic_provider.py -q`，期望 Claude 全部协议测试通过。

## T9：标记工具安全分类

**文件：** `src/mewcode/tools/base.py`、`src/mewcode/tools/registry.py`、`src/mewcode/tools/__init__.py`、`tests/test_tools.py`

**依赖：** T1

**步骤：**
1. 定义 `READ_ONLY` 与 `SIDE_EFFECT`，并让每项工具明确声明分类。
2. 让注册中心可导出全部定义或仅导出只读定义。
3. 验证读取、查找、搜索属于只读，写入、修改、命令属于副作用。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "registry or safety"`，期望六项工具分类和筛选结果正确。

## T10：异步包装文件工具

**文件：** `src/mewcode/tools/base.py`、`src/mewcode/tools/filesystem.py`、`tests/test_tools.py`

**依赖：** T1、T9

**步骤：**
1. 将统一工具协议改为异步执行并接收取消信号。
2. 用工作线程包装五项现有文件操作；开始前若已取消则不执行，开始后等待操作安全完成。
3. 保持路径边界、唯一替换、原子写入、结果截断和错误码不变。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "file or read or write or edit or find or search"`，期望文件工具原有行为和取消前拒绝测试通过。

## T11：实现可取消的异步命令

**文件：** `src/mewcode/tools/command.py`、`tests/test_tools.py`

**依赖：** T1、T9

**步骤：**
1. 使用异步 PowerShell 子进程收集标准输出、错误输出和退出码。
2. 同时等待进程完成、30 秒超时与取消信号。
3. 超时或取消时终止并等待子进程回收，返回对应结构化失败结果。
4. 保持非零退出和输出截断行为。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "command"`，期望成功、非零退出、超时、取消和进程回收测试通过。

## T12：实现顺序屏障与并发批次

**文件：** `src/mewcode/tools/executor.py`、`tests/test_tools.py`

**依赖：** T9、T10、T11

**步骤：**
1. 将命令确认回调改为异步等待，并统一包装拒绝、参数错误和工具异常。
2. 将相邻只读调用组成并发批次，每个副作用或未知调用形成单项屏障。
3. 使用可控测试工具证明只读调用重叠执行、副作用不重叠，结果始终恢复原顺序。
4. 取消后不启动后续批次，并为未启动调用生成 `cancelled` 结果。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q -k "executor or batch or order or cancel"`，期望分批、顺序、失败隔离与取消测试通过。

## T13：将 Conversation 收敛为历史存储

**文件：** `src/mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** T4

**步骤：**
1. 移除固定两次 Provider 调用和工具执行职责。
2. 提供追加用户文本、完整助手响应、成组工具结果和部分助手文本的操作。
3. 保证工具调用与结果成对、结果同序；相邻用户内容在需要时形成两种 Provider 都能序列化的合法历史。
4. 测试新会话隔离、thinking 不入历史、部分文本保留和用量不入历史。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_conversation.py -q`，期望所有历史不变量测试通过。

## T14：定义 Agent 事件与双路流收集

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T4、T13

**步骤：**
1. 定义运行模式、停止原因和七类 Agent 事件联合类型。
2. 实现单轮异步收集器：文本与 thinking 到达即发事件，同时累积正式文本、工具调用与用量。
3. 发出模型阶段进度、每轮与累计用量，并保证 `AgentFinished` 为最后事件。
4. 测试分段文本实时顺序、完整文本、thinking 排除和正常无工具结束。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "event or stream or completed or usage"`，期望双路收集与事件顺序测试通过。

## T15：实现多轮工具循环与两种模式

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T6、T8、T12、T14

**步骤：**
1. 工具响应完成后写入助手历史、执行全部调用、回灌同序结果并进入下一轮。
2. `FULL` 传入全部工具和执行指令；`PLAN` 仅传只读定义和规划指令。
3. 累计多轮文本和 Token，并让普通失败工具结果继续回灌。
4. 测试多轮自主闭环、批量调用、Plan 工具过滤和两种 Provider 共用行为。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "loop or tool or mode or plan"`，期望多轮循环与模式隔离测试通过。

## T16：实现全部停止条件

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T15

**步骤：**
1. 实现迭代上限：最后一次响应仍请求工具时不执行，以失败结果补齐历史后停止。
2. 实现连续两轮全部未知工具停止，已注册工具出现时清零。
3. 实现模型流开始前、进行中、结束时错误的部分历史保存与 `STREAM_ERROR`。
4. 实现模型阶段和工具阶段取消，确保不产生后续模型或工具调用。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q -k "limit or unknown or error or cancel"`，期望五种停止原因、历史和调用次数断言通过。

## T17：接入 TUI 模式入口与 Agent 事件

**文件：** `src/mewcode/tui/app.py`、`src/mewcode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T14、T15、T16

**步骤：**
1. 解析大小写不敏感的 `/plan` 与 `/do` 前缀；普通消息映射到完整模式，空命令显示用法且不写历史。
2. 保留原始用户输入显示，将剥离前缀后的正文交给 Agent。
3. 用异步 Textual Worker 遍历 Agent 事件，更新文本、thinking、工具摘要、进度、错误和 Token。
4. 根据唯一 `AgentFinished` 恢复输入，保持自动滚动和多轮历史。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "plan or do or stream or progress or token or history"`，期望三种入口和事件展示测试通过。

## T18：异步化命令确认与 TUI 取消

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui.py`

**依赖：** T11、T12、T17

**步骤：**
1. 将命令批准请求改为异步等待的单次结果，不阻塞 UI 事件循环。
2. 保持每条命令逐次展示确认框；拒绝、关闭弹窗和 Ctrl+C 都能解除等待。
3. Ctrl+C 同时覆盖活动 Provider 流、命令进程和批次调度，结束后输入恢复。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "command or reject or cancel"`，期望确认、拒绝、关闭和取消测试通过且无悬挂 Worker。

## T19：完成 CLI 对象装配与兼容迁移

**文件：** `src/mewcode/cli.py`、`src/mewcode/providers/__init__.py`、`src/mewcode/tools/__init__.py`、`tests/test_cli.py`

**依赖：** T2、T6、T8、T12、T13、T16、T18

**步骤：**
1. 使用 `AppConfig.provider` 创建对应异步 Provider。
2. 创建 Registry、Conversation 和 Agent，并把 Agent 配置的上限传入。
3. 将 Agent 注入 TUI，清理迁移期兼容导出和不再使用的同步入口。
4. 更新两种协议的启动装配测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_provider_base.py -q`，期望 OpenAI、Claude 和默认 Agent 配置装配测试通过。

## T20：完整回归与 tmux 端到端验收

**文件：** `tests/`、`doc/ch3/checklist.md`

**依赖：** T19

**步骤：**
1. 运行全部自动化测试并修复回归，确认没有遗留同步 Provider 或旧单工具状态机引用。
2. 在 tmux 中启动 MewCode，执行一个需要“读取 → 修改 → 再读取验证 → 最终回复”的真实普通请求。
3. 在 tmux 中分别验证 `/plan` 只读规划、带内容 `/do` 执行、空 `/do` 用法提示和 Ctrl+C 取消。
4. 对 OpenAI 与 Claude 各完成一次真实 Agent Loop；记录迭代、工具顺序、停止原因和最终结果。
5. 逐项执行 `checklist.md` 并记录实际观察结果与通过状态。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q`，期望全部测试通过；tmux 中五类用户场景与 checklist 的预期行为一致。

## 执行顺序

```text
T1 ─┬─→ T4 ─→ T5 ─→ T6 ─┐
    │                      ├─→ T15 ─→ T16 ─→ T17 ─→ T18 ─→ T19 ─→ T20
    ├─→ T9 ─→ T10 ─┐      │
    │        └→ T11 ├─→ T12
    │               │
    └───────────────┘
T3 ─────────→ T5 / T7
T4 ─→ T7 ─→ T8 ───────────┘
T4 ─→ T13 ─→ T14 ─────────┘
T2 ───────────────────────────────→ T19
```

---

# TUI 活动动效补充 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/mewcode/tui/widgets.py` | 活动指示器、思考标题帧和进行中工具行 |
| 修改 | `src/mewcode/tui/app.py` | 请求级动画时钟与 Agent 事件映射 |
| 修改 | `src/mewcode/tui/app.tcss` | 活动与完成状态的终端样式 |
| 修改 | `tests/test_tui.py` | 动效、工具替换和终止状态测试 |
| 修改 | `doc/ch3/checklist.md` | 记录本次验收结果 |

## T21：实现可停止的活动显示组件

**文件：** `src/mewcode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** 无

**步骤：**
1. 定义统一帧序列和可启动、更新、停止的活动提示组件。
2. 让思考折叠标题在活动时接收帧，结束后稳定为静态标题。
3. 增加显示工具名称的进行中工具行，并保留现有静态工具结果组件。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "activity or thinking"`；期望活动提示可推进、思考仍折叠、停止后不再显示动态标记。

## T22：接入请求级动画时钟与工具事件

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui.py`

**依赖：** T21

**步骤：**
1. 在请求启动时创建单个低频时钟，在最终事件时停止并释放。
2. 将模型进度、思考事件和工具调用事件映射到对应活动组件。
3. 将工具结果原位替换对应进行中行；确保多工具调用按原顺序显示。
4. 在正常完成、取消、流错误与触顶时关闭时钟和全部活动组件。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q -k "activity or tool or cancel or error"`；期望工具等待可见、结果替换正确、终止后时钟不再刷新且输入恢复。

## T23：完成终端视觉样式与回归测试

**文件：** `src/mewcode/tui/app.tcss`、`tests/test_tui.py`

**依赖：** T21、T22

**步骤：**
1. 为活动标记、思考标题、进行中工具行和静态结果设置层次清晰的低对比度样式。
2. 补充模型等待、思考、工具成功、工具失败和取消的 UI 回归测试。
3. 确认原有模式菜单、流式文本、Token 与命令确认测试不退化。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望 TUI 全部测试通过。

## T24：全量与 tmux 验收

**文件：** `doc/ch3/checklist.md`

**依赖：** T23

**步骤：**
1. 运行完整测试和源码编译检查。
2. 在 tmux 启动 MewCode，分别观察模型等待、思考、工具执行和最终回复。
3. 在活动期间按 Ctrl+C，确认圆圈停止且输入恢复；记录实际结果。

**验证：** `.venv\Scripts\python.exe -m pytest -q` 与 `.venv\Scripts\python.exe -m compileall -q src` 均退出码 0；tmux 中活动标记可见、完成后静止。

## 执行顺序

```text
T21 → T22 → T23 → T24
```
