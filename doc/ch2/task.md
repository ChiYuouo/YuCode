# MewCode 工具系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mewcode/tools/base.py` | 工具协议、定义、调用、结果与上下文 |
| 新建 | `src/mewcode/tools/filesystem.py` | 五项文件与搜索工具 |
| 新建 | `src/mewcode/tools/command.py` | PowerShell 命令工具 |
| 新建 | `src/mewcode/tools/registry.py` | 工具注册与 Schema 导出 |
| 新建 | `src/mewcode/tools/executor.py` | 分派、参数校验、异常与确认处理 |
| 修改 | `src/mewcode/providers/base.py` | 富消息、工具调用流事件与 Provider 接口 |
| 修改 | `src/mewcode/providers/openai.py` | OpenAI 工具请求、流解析与历史序列化 |
| 修改 | `src/mewcode/providers/anthropic.py` | Claude 工具请求、流解析与历史序列化 |
| 修改 | `src/mewcode/conversation.py` | 单工具会话状态机 |
| 修改 | `src/mewcode/cli.py` | 创建工作目录范围内的工具注册中心 |
| 修改 | `src/mewcode/tui/widgets.py` | 工具摘要行与命令确认弹窗 |
| 修改 | `src/mewcode/tui/app.py` | 后台确认等待、工具事件与状态恢复 |
| 新建/修改 | `tests/test_tools.py` | 工具与执行器测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 工具协议测试 |
| 修改 | `tests/test_anthropic_provider.py` | Claude 工具协议测试 |
| 修改 | `tests/test_conversation.py` | 单工具流程与历史测试 |
| 修改 | `tests/test_tui.py` | 工具摘要和确认弹窗测试 |

## T1: 定义工具领域模型

**文件：** `src/mewcode/tools/base.py`、`src/mewcode/providers/base.py`

**依赖：** 无

**步骤：**
1. 定义工具描述、调用、结果、上下文和统一工具协议。
2. 将会话消息改为可表达文本、工具调用和工具结果的内容块。
3. 扩展流事件以携带完整工具调用，并保持文本、thinking、usage 事件兼容。
4. 扩展 Provider 协议以接收工具定义。

**验证：** 运行 `pytest tests/test_provider_base.py -q`，期望现有取消与用量测试继续通过，并新增模型构造测试通过。

## T2: 实现受限文件工具

**文件：** `src/mewcode/tools/filesystem.py`、`tests/test_tools.py`

**依赖：** T1

**步骤：**
1. 实现解析真实路径并校验工作目录边界的共享逻辑。
2. 实现 UTF-8 文本读取、原子写入和原文唯一替换。
3. 实现带默认排除目录、200 项上限的 glob 查找与正则搜索。
4. 实现 1 MiB 读取限制、12,000 字符结果限制、二进制和无效正则错误结果。

**验证：** 运行 `pytest tests/test_tools.py -q`，期望覆盖读写、唯一替换、越界、截断、搜索和排除目录的断言全部通过。

## T3: 实现命令工具

**文件：** `src/mewcode/tools/command.py`、`tests/test_tools.py`

**依赖：** T1

**步骤：**
1. 使用 `powershell -NoProfile -NonInteractive -Command` 在工作目录启动命令。
2. 合并标准输出和错误输出，返回退出码和截断标记。
3. 实现 30 秒超时终止及超时错误结果。
4. 为成功、非零退出、超时和输出截断编写测试。

**验证：** 运行 `pytest tests/test_tools.py -q`，期望命令的成功、失败和超时场景均返回结构化结果且测试通过。

## T4: 完成注册与执行器

**文件：** `src/mewcode/tools/registry.py`、`src/mewcode/tools/executor.py`、`tests/test_tools.py`

**依赖：** T2、T3

**步骤：**
1. 注册六项工具并导出统一 JSON Schema 描述。
2. 按工具名称分派调用并验证对象参数与必填字段。
3. 统一转换未知工具、参数错误、异常、取消和用户拒绝为 `ToolResult`。
4. 为命令调用接入由上层提供的批准回调。

**验证：** 运行 `pytest tests/test_tools.py -q`，期望注册表导出六项工具，所有失败场景均不会抛出未处理异常。

## T5: 适配 OpenAI 工具调用

**文件：** `src/mewcode/providers/openai.py`、`tests/test_openai_provider.py`

**依赖：** T1、T4

**步骤：**
1. 将工具定义写入 Responses 请求，并序列化富消息历史与工具结果。
2. 按输出项累积 `response.function_call_arguments.delta`。
3. 在参数完成事件创建统一工具调用；无效 JSON 转为 Provider 错误。
4. 保留既有文本、用量、HTTP 错误和取消行为。

**验证：** 运行 `pytest tests/test_openai_provider.py -q`，期望现有测试通过，且新增的碎片参数、工具 Schema、调用 ID 与回灌历史断言通过。

## T6: 适配 Claude 工具调用

**文件：** `src/mewcode/providers/anthropic.py`、`tests/test_anthropic_provider.py`

**依赖：** T1、T4

**步骤：**
1. 将工具定义转为 Claude 工具声明并序列化富消息历史。
2. 在 `content_block_start` 记录 `tool_use` 的块索引、名称和调用 ID。
3. 累积同一索引的 `input_json_delta`，在块停止时解析并发出工具调用。
4. 保留 thinking、文本、用量、错误和取消兼容性。

**验证：** 运行 `pytest tests/test_anthropic_provider.py -q`，期望碎片参数、工具结果回灌和既有流式行为均通过。

## T7: 实现单工具会话状态机

**文件：** `src/mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** T4、T5、T6

**步骤：**
1. 将用户消息、首次助手文本与工具调用按正确顺序写入历史。
2. 执行首次响应的第一项工具，给额外工具调用写入上限失败结果。
3. 回灌执行结果并发起第二次流式请求，累计两次用量。
4. 第二次工具调用仅记录失败结果，不执行且不发起第三次请求。
5. 保持无工具轮次、Provider 错误和取消时的历史一致性。

**验证：** 运行 `pytest tests/test_conversation.py -q`，期望无工具、单工具、多工具、二次工具限制、错误和取消的历史断言通过。

## T8: 接入命令确认与工具摘要界面

**文件：** `src/mewcode/tui/widgets.py`、`src/mewcode/tui/app.py`、`src/mewcode/cli.py`、`tests/test_tui.py`

**依赖：** T7

**步骤：**
1. 在 CLI 以当前工作目录创建注册表、执行器和会话依赖。
2. 新增工具活动摘要组件，展示名称、目标和成功/失败状态。
3. 新增命令确认模态窗，展示完整命令和执行、拒绝操作。
4. 让后台生成线程在命令批准期间安全等待，并将决定交还执行器。
5. 将拒绝、关闭、`Ctrl+C` 和工具错误恢复为可继续输入的 UI 状态。

**验证：** 运行 `pytest tests/test_tui.py tests/test_cli.py -q`，期望工具摘要、确认、拒绝、取消及原有界面测试均通过。

## T9: 完整回归与人工端到端验证

**文件：** `tests/`、`doc/ch2/checklist.md`

**依赖：** T8

**步骤：**
1. 运行全部单元测试，修复任何回归。
2. 在 tmux 启动 MewCode，使用真实配置发送“读取 README.md 的第一行并说明项目用途”。
3. 观察工具摘要、模型的最终回复及输入恢复；另发送命令请求，验证确认弹窗和拒绝路径。
4. 按 checklist 记录每项实际结果。

**验证：** 运行 `pytest -q`，期望全绿；tmux 中两个场景均按 checklist 的可观察结果完成。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9
```
