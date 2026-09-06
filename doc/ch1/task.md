# MewCode 首个对话版本 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `pyproject.toml` | Python 包元数据、依赖和 `mewcode` 命令行入口。 |
| 新建 | `mewcode.yaml.example` | 不含真实密钥的 Claude 与 OpenAI 配置示例。 |
| 新建 | `src/mewcode/config.py` | YAML 读取、配置结构与校验。 |
| 新建 | `src/mewcode/conversation.py` | 内存会话历史与单轮生命周期。 |
| 新建 | `src/mewcode/cli.py` | 交互输入、流式终端渲染、退出与中断处理。 |
| 新建 | `src/mewcode/providers/` | Provider 契约、SSE 解析器、OpenAI 与 Claude 实现。 |
| 新建 | `tests/` | 配置、SSE、Provider、会话及 CLI 自动化测试。 |

## T1：初始化可安装的 Python 包

**文件：** `pyproject.toml`、`src/mewcode/__init__.py`、`src/mewcode/__main__.py`、`src/mewcode/cli.py`

**依赖：** 无

**步骤：**

1. 声明 Python 3.12、运行依赖 `httpx`、`PyYAML`、`rich` 与开发依赖 `pytest`。
2. 注册 `mewcode` 控制台入口，并预留调用 CLI 主函数的模块入口。
3. 创建最小可导入包与临时 CLI 主函数。

**验证：** 运行 `uv sync`，再运行 `uv run python -c "import mewcode"`；预期均成功且无导入错误。

## T2：实现配置加载与示例配置

**文件：** `src/mewcode/config.py`、`mewcode.yaml.example`、`tests/test_config.py`

**依赖：** T1

**步骤：**

1. 定义当前后端配置与思考开关的数据结构。
2. 从工作目录的 `mewcode.yaml` 加载 YAML，验证四个核心字段、协议取值、URL 和思考配置。
3. 对错误配置生成不暴露 API Key 的中文错误信息。
4. 提供 Claude 和 OpenAI 的无密钥示例，并为配置加载和错误分支编写测试。

**验证：** 运行 `uv run pytest tests/test_config.py`；预期有效配置被正确解析，全部非法配置用例通过。

## T3：定义统一 Provider 契约与 SSE 解码器

**文件：** `src/mewcode/providers/base.py`、`src/mewcode/providers/sse.py`、`tests/test_sse.py`

**依赖：** T1

**步骤：**

1. 定义消息、统一流事件、Provider 协议和可展示的 Provider 错误类型。
2. 实现增量 SSE 帧解码：支持 `event`、多行 `data` 和空行分帧。
3. 编写正常、分块、多行数据和不完整结尾的解码测试。

**验证：** 运行 `uv run pytest tests/test_sse.py`；预期所有解析用例通过，且每帧的事件名和数据完整保留。

## T4：实现 OpenAI Responses 流式 Provider

**文件：** `src/mewcode/providers/openai.py`、`tests/test_openai_provider.py`

**依赖：** T2、T3

**步骤：**

1. 以完整本地消息历史构造 Responses 请求，使用配置地址、Bearer 认证、`stream: true`、`store: false` 和固定 4096 输出上限。
2. 消费 SSE 并仅将 `response.output_text.delta` 转换为统一文本事件。
3. 将 HTTP、SSE 错误事件和无效 JSON 统一转换为 Provider 错误。
4. 以模拟 HTTP 流断言请求路径、头、载荷和增量文本输出。

**验证：** 运行 `uv run pytest tests/test_openai_provider.py`；预期能验证请求格式、两段以上文本增量及错误转换。

## T5：实现 Claude 流式 Provider 与思考事件

**文件：** `src/mewcode/providers/anthropic.py`、`tests/test_anthropic_provider.py`

**依赖：** T2、T3

**步骤：**

1. 以完整本地历史构造 Messages 请求，使用 Anthropic 认证和 API 版本头、`stream: true` 与固定 4096 输出上限。
2. 配置启用时添加 adaptive、summarized thinking 请求参数。
3. 将 `thinking_delta` 映射为思考事件，将 `text_delta` 映射为文本事件；忽略签名和停止事件。
4. 以模拟 HTTP 流验证普通对话、思考开启、请求格式和错误转换。

**验证：** 运行 `uv run pytest tests/test_anthropic_provider.py`；预期文本与思考事件按原流顺序产生，测试全部通过。

## T6：实现内存会话与中断恢复

**文件：** `src/mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** T3、T4、T5

**步骤：**

1. 在每轮开始追加用户消息，并逐事件回调给终端层和累积正式文本。
2. 正常完成时追加助手正式文本；不保存思考摘要。
3. 对异常或 `KeyboardInterrupt`：已有正式文本则作为不完整助手消息保存，无正式文本则回滚本轮用户消息，然后重新抛出可处理结果。
4. 用假 Provider 覆盖多轮传递、正常完成、部分输出失败与无输出中断。

**验证：** 运行 `uv run pytest tests/test_conversation.py`；预期历史变化与每种完成状态一致。

## T7：完成 CLI 交互与流式渲染

**文件：** `src/mewcode/cli.py`、`src/mewcode/__main__.py`、`tests/test_cli.py`

**依赖：** T2、T6

**步骤：**

1. 启动时加载配置、选择对应 Provider，并在加载失败时显示中文错误后结束。
2. 以 Rich 安全地逐片段输出原始文本；第一次思考和第一次回答分别显示标签。
3. 实现 `/exit`、`/quit`、`Ctrl+D` 与输入阶段 `Ctrl+C` 的退出；流式阶段 `Ctrl+C` 取消本轮并回到提示符。
4. 在假 Provider 与可替换输入输出下测试逐次输出、失败后续聊及命令退出。

**验证：** 运行 `uv run pytest tests/test_cli.py`；预期所有交互分支通过，且没有将流内容当作 Rich 标记解析。

## T8：执行全量自动化验证

**文件：** 全部实现与测试文件（仅在失败时修改）

**依赖：** T1–T7

**步骤：**

1. 运行全部测试并修复发现的问题。
2. 从临时目录复制示例配置，分别替换为 OpenAI 和 Claude 的测试配置，确认配置加载与 Provider 选择正确。
3. 安装本地包后确认 `mewcode` 命令可被终端找到。

**验证：** 运行 `uv run pytest` 和 `uv run mewcode`；预期测试全部通过，后者在有效配置时进入输入提示。

## T9：tmux 真实端到端验收

**文件：** 不新增文件；仅在验证发现问题时修改相关实现

**依赖：** T8

**步骤：**

1. 使用用户提供的有效 `mewcode.yaml`，在 tmux 中启动 `mewcode`。
2. 输入真实问题，再输入一条依赖上一轮回答的追问。
3. 观察回复是否边接收边显示、第二轮是否使用上下文，并使用退出命令关闭会话。
4. 对照 `checklist.md` 记录实际命令和观察结果。

**验证：** 捕获 tmux 窗口输出；预期出现两轮模型回复、第二轮包含前文关联，且会话正常退出。

## 执行顺序

```text
T1 → {T2, T3}
{T2, T3} → {T4, T5}
{T3, T4, T5} → T6 → T7 → T8 → T9
```

## 执行状态

- [x] T1：初始化可安装的 Python 包。
- [x] T2：实现配置加载与示例配置。
- [x] T3：定义统一 Provider 契约与 SSE 解码器。
- [x] T4：实现 OpenAI Responses 流式 Provider。
- [x] T5：实现 Claude 流式 Provider 与思考事件。
- [x] T6：实现内存会话与中断恢复。
- [x] T7：完成 CLI 交互与流式渲染。
- [x] T8：执行全量自动化验证。
- [x] T9：tmux 真实端到端验收（Claude 多轮流式对话与正常退出已验证）。
