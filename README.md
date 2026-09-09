# YuCode

YuCode 是一个支持 OpenAI 与 Anthropic Claude 流式对话的终端 AI 助手。

安装依赖后，在包含 `yucode.yaml` 的目录运行：

```powershell
yucode
```

## 上下文管理

YuCode 会在每次模型请求前先外置过大的工具输出，再在接近上下文窗口时生成结构化摘要。默认窗口为 128000 Token，可在 `yucode.yaml` 中配置：

```yaml
context:
  window_tokens: 128000
```

自动压缩会保留 13K Token 安全余量；输入 `/compact` 可手动压缩，并使用 3K 余量。外置工具结果保存在项目的 `.yucode/context/`，模型需要细节时会用 `read_file` 重新读取；该缓存仅保留到本次 YuCode 会话结束。若服务端报告上下文超限，YuCode 会紧急压缩并仅重试原请求一次。

## MCP 工具

可在项目级 `yucode.yaml` 的 `mcp_servers` 中声明 stdio 或 Streamable HTTP Server；也可在用户级 `%APPDATA%\YuCode\yucode.yaml` 中声明。项目级同名 Server 会覆盖用户级声明。

`env` 和 `headers` 支持 `${VAR}` 环境变量展开。启动时 YuCode 会发现可用工具，并将其命名为 `MCP__服务器名__工具名`。单个 Server 加载失败只显示中文警告，不影响其他工具。每次 MCP 请求最长等待 30 秒，不会自动重连。

MCP 工具会继续经过 YuCode 现有权限流程：默认模式下需要明确执行授权和用户确认，也可选择本会话允许。本期只支持工具，不支持资源、提示词或采样。
