# MewCode 首个对话版本 Plan

## 架构概览

MewCode 分为配置、会话、Provider 和终端 UI 四层。

- 配置层读取并校验当前目录的 `mewcode.yaml`。
- 会话层维护本次运行的用户与助手文本历史，并协调一次请求的开始、流式输出、完成或中断。
- Provider 层将统一消息历史转为对应厂商的 HTTP/SSE 请求，再把厂商事件转换为统一流事件。
- 终端 UI 负责提示输入、区分显示思考与正式回答、显示错误，以及处理退出和中断。

## 核心数据结构

### `ProviderConfig`

```python
protocol: Literal["openai", "anthropic"]
model: str
base_url: str
api_key: str
thinking_enabled: bool = False
```

- YAML 的四个核心字段必须非空。
- `thinking.enabled` 是仅 Claude 可用的可选字段；OpenAI 配置启用它时启动前报配置错误。
- `max_tokens` 不开放配置，两个后端统一固定为 4096。

### `Message`

```python
role: Literal["user", "assistant"]
content: str
```

只保存用户输入和正式回答文本；Claude 思考摘要仅供显示，不写入后续请求历史。

### `StreamEvent`

```python
kind: Literal["thinking", "text"]
content: str
```

所有 Provider 都只向会话层产出这两类增量事件。

### `Provider`

```python
stream(messages: Sequence[Message]) -> Iterator[StreamEvent]
```

- 负责同步发起 HTTP POST、读取 SSE、转换事件及归一化错误。
- 不负责终端输出、历史保存或用户输入。

## 模块设计

- `config`：解析 YAML，校验字段、协议值、URL 和思考配置；启动失败时给出可理解错误，且绝不打印 API Key。
- `providers.sse`：实现通用 SSE 帧解析，正确合并多行 `data`、以空行分帧，并向各 Provider 提供事件名和 JSON 数据。
- `providers.openai`：调用 `{base_url}/responses`，发送完整本地历史、`stream: true`、`store: false`；只将 `response.output_text.delta` 转换为文本事件。
- `providers.anthropic`：调用 `{base_url}/v1/messages`，发送完整本地历史与 Anthropic 认证头；启用思考时发送 adaptive、summarized thinking 配置；将 `thinking_delta` 和 `text_delta` 分别映射为统一事件。
- `conversation`：提交用户消息后消费 Provider 流，将文本实时交给 UI 并累积。正常完成时保存正式回答；中断或失败时，若已输出正式文本则保存该不完整文本，否则撤销本轮用户消息。
- `cli`：使用 Rich 做简洁终端显示。收到首个思考或文本片段时分别打印“思考：”或“MewCode：”标签，后续片段原样追加。`/exit`、`/quit`、输入阶段的 `Ctrl+C` 或 `Ctrl+D` 退出；输出阶段的 `Ctrl+C` 关闭本轮流并返回输入提示。

## 模块交互

```text
用户输入
  → 会话层追加 user Message
  → Provider.stream(完整历史)
  → SSE 事件转换为 StreamEvent
  → CLI 逐片段输出并累积
  → 会话层提交 assistant Message
```

## 文件组织

```text
project/
├── pyproject.toml
├── mewcode.yaml.example
├── src/mewcode/
│   ├── cli.py
│   ├── config.py
│   ├── conversation.py
│   └── providers/
│       ├── base.py
│       ├── sse.py
│       ├── openai.py
│       └── anthropic.py
└── tests/
    ├── test_config.py
    ├── test_conversation.py
    └── test_providers.py
```

`pyproject.toml` 定义 Python 3.12、`mewcode` 命令行入口，以及运行依赖 `httpx`、`PyYAML`、`rich` 和测试依赖 `pytest`。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| HTTP 与流解析 | `httpx` + 自有 SSE 解析器 | 明确满足 SSE 要求，不将 UI 与厂商 SDK 绑定。 |
| OpenAI 接口 | Responses API | 使用当前的流式接口，并为未来能力扩展保留空间。 |
| Claude 思考 | 可选 adaptive + summarized | 对新 Claude 模型友好，能实时展示可见摘要，且不暴露预算配置。 |
| Provider 抽象 | 同步迭代器输出统一事件 | CLI 可边消费边渲染；新增后端只需实现转换层。 |
| 对话上下文 | 客户端内存全量历史 | 保证本次运行内多轮对话，退出即清空；不依赖服务端会话状态。 |
| 失败与中断 | 保留已显示正式文本，继续交互 | 用户可看见已获得的结果，并能立即继续提问。 |
