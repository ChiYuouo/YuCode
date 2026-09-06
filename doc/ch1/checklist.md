# MewCode 首个对话版本 Checklist

> 每项均通过命令、测试结果或终端可观察行为验证；真实密钥只保留在未提交的 `mewcode.yaml` 中。

## 功能验收

- [x] AC1：在含有效 `mewcode.yaml` 的目录运行 `mewcode`，输入一条问题；预期先显示输入提示，再在同一终端持续追加模型回复片段，而非在结束后一次性输出。
- [x] AC2-OpenAI：运行 `uv run pytest tests/test_openai_provider.py`；预期模拟 OpenAI Responses SSE 的请求与增量文本事件测试全部通过。
- [x] AC2-Claude：运行 `uv run pytest tests/test_anthropic_provider.py`；预期模拟 Claude Messages SSE 的请求与增量文本事件测试全部通过。
- [x] AC3：运行 `uv run pytest tests/test_conversation.py -k multi_turn`；预期第二次请求收到第一轮用户消息和助手正式回答，且测试通过。
- [x] AC4：分别使用 `protocol: openai` 与 `protocol: anthropic` 的临时配置启动测试；预期无需改变 CLI 命令，分别选择对应 Provider 并完成模拟流式问答。
- [x] AC5：运行 `uv run pytest tests/test_anthropic_provider.py -k thinking` 与 `uv run pytest tests/test_cli.py -k thinking`；预期思考与正式文本事件顺序正确，终端以可区分标签显示两类内容。
- [x] AC6：运行 `uv run pytest tests/test_config.py`、`uv run pytest tests/test_openai_provider.py -k error`、`uv run pytest tests/test_anthropic_provider.py -k error` 和 `uv run pytest tests/test_cli.py -k error`；预期配置、认证或流错误显示中文错误且 CLI 可继续接收下一条输入，输出中不含 API Key。
- [x] AC7：运行 `uv run pytest tests/test_cli.py -k exit`；预期 `/exit`、`/quit`、`Ctrl+D` 与输入阶段 `Ctrl+C` 都能结束会话；重建会话实例后历史为空。

## 集成与稳健性

- [x] SSE 解码：运行 `uv run pytest tests/test_sse.py`；预期单行、多行 `data`、分块传输与帧边界均被正确还原。
- [x] 流式中断：运行 `uv run pytest tests/test_conversation.py -k interrupt`；预期有已显示文本时保存不完整助手回答，无文本时撤销本轮用户消息，并返回可继续使用的状态。
- [x] CLI 原始输出：运行 `uv run pytest tests/test_cli.py -k render`；预期模型文本中的 Rich 标记字符被原样输出，不会被解释为样式命令。

## 构建与自动化测试

- [x] 依赖与打包：运行 `uv sync`、`uv build`；预期依赖可解析且成功生成 Python 分发包。
- [x] 全量测试：运行 `uv run pytest`；预期全部测试通过、无跳过的核心 Provider 或会话用例。
- [x] 命令入口：在项目外的临时目录运行已安装的 `mewcode`；预期能找到命令，并在缺少配置时显示配置错误而不是 Python 堆栈。

## 端到端场景

- [x] 真实对话：准备一个含有效 OpenAI 或 Claude 凭据的未提交 `mewcode.yaml`，在 tmux 启动 `mewcode`；输入“请记住我的名字是小明，只回复已记住。”，确认回复逐步显示；再输入“我叫什么名字？”，预期回复引用“小明”；最后输入 `/exit`，预期会话正常关闭。
- [ ] Claude 思考（有支持该能力的 Claude 配置时）：将 `thinking.enabled` 设为 `true` 后在 tmux 启动并提问；预期在正式回答前或期间看见独立的“思考：”流式区块和“ MewCode：”正式回答区块。

## 验收记录（2026-09-06）

- 通过：`uv sync`、`uv run python -m compileall -q src`、`uv run pytest`（28 passed）、`uv build`。
- 通过：项目外目录运行本地安装的 `mewcode.exe`，实际显示配置错误且没有 Python 堆栈。
- 通过：通过 WSL tmux 与 `winpty` 启动 Claude 配置，第一轮真实回复为 `remembered.`，第二轮真实回复为 `Xiaoming.`；两轮均回到输入提示，`/exit` 正常退出。
- 通过：真实 tmux 会话中发生 TLS EOF 时，MewCode 显示中文错误并回到输入提示。
- 未通过：Claude thinking 可见输出。预期在思考题中显示“思考：”区块；实际该轮在产生流片段前收到 TLS EOF。修复方案：网络稳定后使用相同配置重跑该场景；Provider 与终端映射的自动化测试已通过。
