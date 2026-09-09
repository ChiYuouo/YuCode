# 上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/context.py` | 外置缓存、预算估算、摘要、熔断 |
| 修改 | `src/yucode/conversation.py` | 原子替换历史 |
| 修改 | `src/yucode/config.py` | 上下文窗口配置 |
| 修改 | `src/yucode/providers/base.py` | 携带错误码的 ProviderError |
| 修改 | `src/yucode/providers/openai.py` | OpenAI 错误码提取 |
| 修改 | `src/yucode/providers/anthropic.py` | Anthropic 错误码提取 |
| 修改 | `src/yucode/agent.py` | 请求前压缩、紧急重试与事件 |
| 修改 | `src/yucode/cli.py` | 组装上下文管理器 |
| 修改 | `src/yucode/tui/app.py` | `/compact` 本地命令与状态 |
| 修改 | `src/yucode/tui/widgets.py` | 上下文状态显示组件 |
| 新建 | `tests/test_context.py` | 上下文管理单元测试 |
| 修改 | `tests/test_conversation.py` | 历史原子替换测试 |
| 修改 | `tests/test_config.py` | 窗口配置测试 |
| 修改 | `tests/test_agent.py` | 前置压缩、usage 锚点、紧急重试测试 |
| 修改 | `tests/test_tui.py` | `/compact` 交互测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 错误码测试 |
| 修改 | `tests/test_anthropic_provider.py` | Anthropic 错误码测试 |
| 修改 | `yucode.yaml.example` | 上下文配置说明 |
| 修改 | `README.md` | 用户使用说明 |

## T1: 增加会话历史原子替换能力

**文件：** `src/yucode/conversation.py`、`tests/test_conversation.py`

**依赖：** 无

**步骤：**

1. 为 Conversation 添加接收完整消息序列的替换接口。
2. 保持现有追加、撤销和消息只读访问行为不变。
3. 覆盖替换后消息顺序、不可由外部可变序列影响的测试。

**验证：** 运行 `pytest tests/test_conversation.py -q`，期望全部通过。

## T2: 添加跨 Provider 的上下文窗口配置

**文件：** `src/yucode/config.py`、`tests/test_config.py`、`yucode.yaml.example`

**依赖：** 无

**步骤：**

1. 定义默认值为 128000 的上下文配置结构，并纳入应用总配置。
2. 解析 `context.window_tokens`，校验类型、正数和满足安全余量的最小值。
3. 在示例配置中写入中文说明和默认值。
4. 测试默认值、有效覆盖值及无效值的中文错误。

**验证：** 运行 `pytest tests/test_config.py -q`，期望全部通过。

## T3: 统一 Provider 的上下文超限错误码

**文件：** `src/yucode/providers/base.py`、`src/yucode/providers/openai.py`、`src/yucode/providers/anthropic.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`

**依赖：** 无

**步骤：**

1. 让 ProviderError 可选地保存服务端错误码，兼容现有只传错误消息的用法。
2. 从两个 Provider 的 HTTP 错误体和流事件错误中提取 code 与中文错误文字。
3. 添加 `prompt_too_long`、`context_length_exceeded` 的解析测试，并回归普通错误测试。

**验证：** 运行 `pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望全部通过。

## T4: 实现会话缓存与工具结果外置

**文件：** `src/yucode/context.py`、`tests/test_context.py`

**依赖：** T1

**步骤：**

1. 实现启动时清理并重新创建 `.yucode/context/` 的会话缓存存储。
2. 将完整工具输出写入唯一文件，并生成带预览、路径与重新读取提示的替换结果。
3. 实现单项大于 50K 字符的外置，以及单消息合计大于 200K 字符时按大小依次外置。
4. 确保写入失败时消息历史不被部分替换。
5. 测试存盘原文、重读路径、清理行为、单项阈值、总量排序和失败回滚。

**验证：** 运行 `pytest tests/test_context.py -q`，期望外置与缓存相关测试全部通过。

## T5: 实现 Token 预算锚点与近期消息选择

**文件：** `src/yucode/context.py`、`tests/test_context.py`

**依赖：** T1

**步骤：**

1. 实现以实际 input usage 和当时字符总数为锚点的预算跟踪器。
2. 实现无 usage 时的全量字符估算，以及历史新增、删除和替换后的差量估算。
3. 实现从末尾选取至少 10K Token 且至少 5 条消息的保留区。
4. 测试默认 128K 窗口的自动阈值、手动目标、无锚点和保留边界。

**验证：** 运行 `pytest tests/test_context.py -q`，期望预算与保留区测试全部通过。

## T6: 实现无工具的结构化摘要与历史替换

**文件：** `src/yucode/context.py`、`tests/test_context.py`

**依赖：** T1、T4、T5

**步骤：**

1. 构造不携带工具的摘要模型请求，并写入禁止工具调用、草稿与正式摘要分段、九个固定标题的中文提示。
2. 收集摘要流式文本，只解析并保留 `<structured-summary>` 内容，立即丢弃草稿。
3. 将早期历史替换为正式摘要和边界提示，同时保留所选的近期原文。
4. 测试请求工具列表为空、九段摘要格式、草稿不入历史、用户原文保留、边界提示及格式错误回滚。

**验证：** 运行 `pytest tests/test_context.py -q`，期望摘要与历史替换测试全部通过。

## T7: 实现自动/手动/紧急压缩及熔断

**文件：** `src/yucode/context.py`、`tests/test_context.py`

**依赖：** T4、T5、T6

**步骤：**

1. 将前置处理固定为“轻量外置后再判断自动重量压缩”。
2. 实现手动压缩始终发起、无早期历史时正常跳过的逻辑。
3. 实现自动及紧急摘要连续三次失败后的会话级熔断；成功自动摘要重置计数。
4. 保证手动失败不计数，自动熔断后手动仍可执行。
5. 覆盖三种触发方式、状态文本、连续失败、成功重置与手动绕过熔断的测试。

**验证：** 运行 `pytest tests/test_context.py -q`，期望触发与熔断相关测试全部通过。

## T8: 将上下文管理接入 Agent 请求生命周期

**文件：** `src/yucode/agent.py`、`src/yucode/cli.py`、`tests/test_agent.py`

**依赖：** T2、T3、T7

**步骤：**

1. 在 CLI 组装共享 Conversation、Provider、工作区和 ContextConfig 对应的 ContextManager，并注入 Agent。
2. 增加上下文状态 Agent 事件，在每轮请求前发出轻量和重量处理结果。
3. 在收到实际 usage 后记录输入 Token 锚点，不影响既有累计展示。
4. 识别上下文超限 ProviderError：丢弃该次未完成响应、紧急压缩、仅重试原请求一次；第二次失败按普通失败结束。
5. 测试请求前顺序、usage 锚点、摘要请求无工具、一次重试和非上下文错误不重试。

**验证：** 运行 `pytest tests/test_agent.py -q`，期望全部通过。

## T9: 增加 `/compact` 终端交互与上下文状态展示

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T8

**步骤：**

1. 增加不显示内部内容的上下文活动消息组件，展示中文开始、完成、跳过、失败和熔断状态。
2. 将 `/compact` 从模式命令判断中排除，空闲时启动独占手动压缩 worker。
3. 在压缩期间禁用输入，完成后恢复焦点；命令不创建用户消息，也不请求普通 Agent 对话。
4. 在普通生成中消费 ContextUpdated 事件并显示状态。
5. 测试命令不会进入会话、会调用压缩、输入恢复和模式菜单不会拦截它。

**验证：** 运行 `pytest tests/test_tui.py -q`，期望全部通过。

## T10: 完成说明与全量回归

**文件：** `README.md`、`yucode.yaml.example`

**依赖：** T2、T8、T9

**步骤：**

1. 说明窗口配置、`/compact`、缓存目录、自动安全余量、超限重试与不跨会话保留的限制。
2. 复核示例配置与实际字段一致。
3. 运行全部自动化测试，修复本功能导致的回归。

**验证：** 运行 `pytest -q`，期望全部通过。

## T11: 终端端到端验收

**文件：** 无新增文件

**依赖：** T10

**步骤：**

1. 在 tmux 中启动 YuCode，并使用可控 Provider 或测试配置输入真实对话请求。
2. 输入 `/compact`，观察中文压缩状态、输入框恢复和命令不作为普通对话发送。
3. 继续输入一条真实任务，观察工具调用、正常回复和上下文状态不会破坏会话。
4. 按 `checklist.md` 的最终验收项记录实际结果。

**验证：** 在 tmux 中完成上述场景，期望 YuCode 正确处理命令、工具调用与回复，无崩溃或无响应。

## 执行顺序

```text
T1 ─┬─→ T4 ─┬─→ T6 ─→ T7 ─┐
    └─→ T5 ─┘              │
T2 ─────────────────────────┼─→ T8 ─→ T9 ─→ T10 ─→ T11
T3 ─────────────────────────┘
```
