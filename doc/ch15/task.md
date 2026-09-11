# Hook 自动化系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/hooks/__init__.py` | 导出 Hook 子系统公共入口。 |
| 新建 | `src/yucode/hooks/models.py` | 事件、规则、动作、条件、上下文和拒绝异常。 |
| 新建 | `src/yucode/hooks/conditions.py` | 条件解析、字段读取与条件求值。 |
| 新建 | `src/yucode/hooks/template.py` | `$` 上下文变量替换。 |
| 新建 | `src/yucode/hooks/loader.py` | YAML Hook 规则的加载和校验。 |
| 新建 | `src/yucode/hooks/executors.py` | command、prompt、http、agent stub 四类执行器。 |
| 新建 | `src/yucode/hooks/engine.py` | 顺序调度、once、async、提示缓冲与拒绝控制。 |
| 修改 | `src/yucode/config.py` | 将顶层 `hooks` 解析结果放入应用配置。 |
| 修改 | `src/yucode/tools/executor.py` | 接入工具前后、权限、命令和文件生命周期事件。 |
| 修改 | `src/yucode/agent.py` | 接入会话、轮次、消息、错误、压缩及提示注入事件。 |
| 修改 | `src/yucode/cli.py` | 创建唯一 Engine 并处理启动、会话和关闭事件。 |
| 修改 | `yucode.yaml.example` | 添加最小可用 Hook 配置和字段说明。 |
| 新建 | `tests/test_hooks_conditions.py` | 条件语法、四种操作符、字段读取测试。 |
| 新建 | `tests/test_hooks_template.py` | 模板变量与缺失变量测试。 |
| 新建 | `tests/test_hooks_loader.py` | 有效规则与全部加载期校验测试。 |
| 新建 | `tests/test_hooks_executors.py` | 四类动作、HTTP、命令超时测试。 |
| 新建 | `tests/test_hooks_engine.py` | 排序、once、async、提示和拒绝测试。 |
| 新建 | `tests/test_hooks_integration.py` | Agent/工具生命周期与拦截回填测试。 |
| 修改 | `tests/test_config.py` | 配置层 Hook 成功与失败测试。 |
| 修改 | `tests/test_agent.py`、`tests/test_tools.py`、`tests/test_cli.py` | 现有入口的 Hook 集成回归测试。 |

## T1: 建立 Hook 数据模型

**文件：** `src/yucode/hooks/__init__.py`、`src/yucode/hooks/models.py`

**依赖：** 无

**步骤：**

1. 创建 `hooks` 包和 15 项 `HookEvent` 枚举。
2. 定义动作类型、动作、规则、条件、条件组及不可变上下文结构。
3. 定义提示执行结果和仅供工具拦截边界使用的 `ToolRejectedError`。
4. 为公开数据结构补充中文注释，并从包入口导出后续模块所需类型。

**验证：** 运行 `pytest -q tests/test_hooks_conditions.py`，期望模型构造测试和 15 个事件枚举断言通过。

## T2: 实现条件字段读取、解析与求值

**文件：** `src/yucode/hooks/conditions.py`、`tests/test_hooks_conditions.py`

**依赖：** T1

**步骤：**

1. 实现固定字段和 `$TOOL_ARGS.<键路径>` 的读取；缺失值统一返回空文本。
2. 解析唯一的 `if.all` 或 `if.any` 条件组，拒绝空组、未知字段、混合模式及字段类型错误。
3. 实现 `==`、`!=`、`=~`、`~=` 的求值，并在加载期验证正则。
4. 编写精确、不等、正则、glob、all/any、嵌套参数和无效表达式的单元测试。

**验证：** 运行 `pytest -q tests/test_hooks_conditions.py`，期望所有四种比较、两种组合及错误路径均通过。

## T3: 实现上下文模板替换

**文件：** `src/yucode/hooks/template.py`、`tests/test_hooks_template.py`

**依赖：** T1、T2

**步骤：**

1. 识别 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR` 与 `$TOOL_ARGS.xxx`。
2. 复用条件模块的字段读取，保持嵌套参数和缺失值语义一致。
3. 拒绝配置中不受支持的 `$` 变量引用，避免动作文本拼写错误在运行时静默失效。
4. 编写全部变量、嵌套参数、多变量文本、缺失值和非法变量的测试。

**验证：** 运行 `pytest -q tests/test_hooks_template.py tests/test_hooks_conditions.py`，期望变量替换与字段读取结果完全一致。

## T4: 实现 Hook YAML 加载与校验

**文件：** `src/yucode/hooks/loader.py`、`tests/test_hooks_loader.py`

**依赖：** T1、T2、T3

**步骤：**

1. 解析顶层 `hooks` 列表和规则序号，保留声明顺序。
2. 校验事件名、动作类型、各动作必填字段、HTTP 方法与 URL、非负命令超时、条件和模板变量。
3. 校验 `reject` 仅出现在 `pre_tool_use`、必须有原因且不得与 `async` 并用。
4. 为有效的四类动作、所有必填字段缺失、非法事件/动作/条件/变量、非法 HTTP 与非法拦截组合编写测试。

**验证：** 运行 `pytest -q tests/test_hooks_loader.py`，期望有效 YAML 得到冻结规则，所有无效规则报出规则索引与中文字段原因。

## T5: 实现四类动作执行器

**文件：** `src/yucode/hooks/executors.py`、`tests/test_hooks_executors.py`

**依赖：** T1、T3

**步骤：**

1. 实现命令动作：在工作目录启动 PowerShell，支持每条动作超时并把超时与失败转为可记录异常。
2. 实现提示词动作：返回渲染后的提示，而不改写会话历史。
3. 使用 `httpx` 实现 HTTP 动作，覆盖方法、地址和可选正文的变量替换。
4. 实现只记日志、返回空结果的 Agent stub。
5. 用本地异步 HTTP 测试服务或模拟传输验证请求内容，并测试命令输出、超时、动作失败与 stub 无副作用。

**验证：** 运行 `pytest -q tests/test_hooks_executors.py`，期望四类动作均可观察，失败和超时不会冒泡为未处理异常。

## T6: 实现 HookEngine 调度与拦截

**文件：** `src/yucode/hooks/engine.py`、`tests/test_hooks_engine.py`

**依赖：** T1、T2、T5

**步骤：**

1. 依配置顺序筛选事件与条件匹配规则，并逐条调用执行器。
2. 实现运行时一次性标记、提示词缓冲与 `drain_prompts` 的取出即清空行为。
3. 实现异步后台调度、任务引用保留与异常日志捕获。
4. 实现 `run_pre_tool_hooks` 的同步专用路径，以及拒绝后停止当前工具调用的行为。
5. 测试顺序、条件跳过、once 重建重置、async 非阻塞且异常被消费、提示累积、拒绝与普通失败继续执行。

**验证：** 运行 `pytest -q tests/test_hooks_engine.py`，期望所有调度控制与拒绝边界断言通过。

## T7: 将 Hook 规则接入应用配置

**文件：** `src/yucode/config.py`、`tests/test_config.py`

**依赖：** T4

**步骤：**

1. 为 `AppConfig` 增加已校验 Hook 规则字段及无配置时的空默认值。
2. 在 `load_config` 中解析顶层 `hooks`，将加载器异常转换为现有中文 `ConfigError`。
3. 保持既有 provider、agent、context、permissions 与 MCP 配置行为不变。
4. 增加有效 Hook 配置、非法 Hook 配置和无 hooks 配置的回归测试。

**验证：** 运行 `pytest -q tests/test_config.py tests/test_hooks_loader.py`，期望 Hook 配置成功进入应用配置，错误阻止加载且不影响现有测试。

## T8: 在工具执行器接入 Hook 边界

**文件：** `src/yucode/tools/executor.py`、`tests/test_tools.py`、`tests/test_hooks_integration.py`

**依赖：** T1、T6

**步骤：**

1. 为工具执行器注入可选 HookEngine，未传入时保持现有执行路径不变。
2. 在权限判断前调用 `pre_tool_use`；将 `ToolRejectedError` 转换为 `hook_rejected` 失败结果，且不调用权限或工具本体。
3. 在等待用户确认前发出 `permission_request`，在允许执行命令前发出 `command_execute`，在写入或编辑成功后发出 `file_change`。
4. 无论拒绝、权限拒绝、工具成功或工具失败，确保恰好发出一次 `post_tool_use`。
5. 测试工具未执行、拒绝原因回填、权限顺序、命令/文件上下文及既有批次执行回归。

**验证：** 运行 `pytest -q tests/test_tools.py tests/test_hooks_integration.py`，期望 Hook 拦截和所有工具事件正确，既有工具测试保持通过。

## T9: 在 Agent 接入轮次、消息、错误、压缩与提示词

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`、`tests/test_hooks_integration.py`

**依赖：** T1、T6、T8

**步骤：**

1. 为 Agent 注入可选 HookEngine，并与工具执行器共享同一实例。
2. 在每个 `run` 的入口与唯一结束路径触发 `turn_start`/`turn_end`；在用户消息写入前与模型回复收集后触发 `pre_send`/`post_receive`。
3. 在普通异常结束路径触发 `error`，保证 Hook 异常不会触发递归错误 Hook。
4. 在手动、自动、紧急压缩完成时触发 `compact`。
5. 每次构建模型请求前取出提示缓冲并作为本次运行时提示加入请求，不写入 `Conversation`。
6. 测试完整对话生命周期、模型可见提示、错误与压缩事件，以及无 Engine 时的原有行为。

**验证：** 运行 `pytest -q tests/test_agent.py tests/test_hooks_integration.py`，期望生命周期顺序和提示注入正确，全部既有 Agent 测试通过。

## T10: 在 CLI 接入应用、会话生命周期与安全关闭

**文件：** `src/yucode/cli.py`、`tests/test_cli.py`

**依赖：** T7、T9

**步骤：**

1. 从已加载配置创建唯一的 HookEngine，并传入 Agent。
2. 在 UI 启动前依次运行 `startup`、`session_start`；用 `try/finally` 保证退出时运行 `session_end`、`shutdown`。
3. 保持配置错误时不创建 UI 或运行 Hook，避免无效配置产生副作用。
4. 测试启动、关闭、事件顺序和配置失败时的零 Hook 执行。

**验证：** 运行 `pytest -q tests/test_cli.py tests/test_hooks_integration.py`，期望 CLI 生命周期正确且现有启动测试通过。

## T11: 补充示例配置与全量自动化验证

**文件：** `yucode.yaml.example`、`tests/test_hooks_conditions.py`、`tests/test_hooks_template.py`、`tests/test_hooks_loader.py`、`tests/test_hooks_executors.py`、`tests/test_hooks_engine.py`、`tests/test_hooks_integration.py`、`tests/test_config.py`、`tests/test_agent.py`、`tests/test_tools.py`、`tests/test_cli.py`

**依赖：** T2–T10

**步骤：**

1. 在示例配置添加带条件、变量、超时、HTTP、提示、once、async 和拦截说明的安全示例，不包含真实地址、密钥或破坏性命令。
2. 审核单元与集成测试是否覆盖全部 15 个事件、四种动作、四种操作符、两种逻辑组合、执行控制、拦截和错误隔离。
3. 运行 Hook 专项测试和完整测试集，修复本章改动导致的回归。

**验证：** 依次运行 `pytest -q tests/test_hooks_conditions.py tests/test_hooks_template.py tests/test_hooks_loader.py tests/test_hooks_executors.py tests/test_hooks_engine.py tests/test_hooks_integration.py` 与 `pytest -q`，期望均通过。

## T12: 在 tmux 执行端到端验收

**文件：** `doc/ch15/checklist.md`（后续验收记录，如有必要）

**依赖：** T11、已批准的 `checklist.md`

**步骤：**

1. 准备一份安全的真实 Hook 配置：对危险命令拒绝、对允许操作添加提示或记录命令动作。
2. 在 tmux 中启动 YuCode，先输入会触发拒绝的真实对话请求，观察模型读取拒绝结果后调整策略。
3. 再输入允许的读写文件或命令请求，观察工具正常执行及 Hook 的可观察效果。
4. 按 `checklist.md` 逐项记录实际结果；失败时定位、修复并重跑相关项。

**验证：** tmux 会话中完成拒绝→模型调整→允许调用的闭环，且 `checklist.md` 的每一项有通过或失败记录。

## 执行顺序

```text
T1
├── T2 ──► T3 ──► T4 ──► T7 ───────────────► T10 ──┐
├── T5 ─────────► T6 ──► T8 ──► T9 ───────────────┼──► T11 ──► T12
└───────────────────────────────────────────────────┘
```
