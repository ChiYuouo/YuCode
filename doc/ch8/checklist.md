# MCP 外部工具接入 Checklist

> 每项均以运行结果或可见行为为准。开发完成后在本文件中勾选，并记录实际命令和结果。

## 配置与发现

- [ ] AC1：用户级与项目级 MCP 配置正确合并（验证：运行 uv run pytest tests/test_config.py -k "mcp and merge"；期望非重名 Server 均保留，项目级同名 Server 整体覆盖用户级 Server）。
- [ ] AC2：stdio 环境变量与 HTTP 请求头正确展开（验证：运行 uv run pytest tests/test_config.py tests/test_mcp_transport.py -k "environment or header"；期望测试 Server 收到 ${VAR} 展开后的值）。
- [ ] AC2 / N1：单个 Server 无效配置显示中文原因且不泄露值（验证：运行 uv run pytest tests/test_config.py -k "invalid or missing or environment"；期望每条问题含 Server 名称与原因，不含展开后的敏感值）。
- [ ] AC3：stdio Server 完成初始化、通知和工具发现（验证：运行 uv run pytest tests/test_mcp_transport.py tests/test_mcp_session.py -k stdio；期望收到 initialize、notifications/initialized、tools/list 的正确顺序与结果）。
- [ ] AC3：Streamable HTTP 同时兼容 JSON 和 SSE 响应（验证：运行 uv run pytest tests/test_mcp_transport.py -k http；期望两种响应均被解析，后续请求附带协商协议版本和 Mcp-Session-Id）。
- [ ] AC3：并发 MCP 请求按 JSON-RPC id 返回各自结果（验证：运行 uv run pytest tests/test_mcp_session.py -k pairing；期望并发请求结果不串包）。
- [ ] AC3：工具列表完整处理分页（验证：运行 uv run pytest tests/test_mcp_session.py -k pagination；期望 nextCursor 后的全部工具均被发现）。
- [ ] 范围边界：本期不请求非工具 MCP 能力（验证：运行 uv run pytest tests/test_mcp_session.py -k "unsupported or lifecycle"；期望测试记录中仅出现初始化、初始化通知、工具列表和工具调用方法）。

## 工具中心与调用

- [ ] AC4：远端工具统一命名且不冲突（验证：运行 uv run pytest tests/test_mcp_manager.py tests/test_tools.py -k "name or conflict"；期望公开名为 MCP__服务器名__远端工具名，同名远端工具和内置工具均可共存）。
- [ ] AC5：调用参数和远端结果正确往返（验证：运行 uv run pytest tests/test_mcp_session.py tests/test_tools.py -k "call or result"；期望远端收到原始 arguments，文本结果返回 Agent，远端 isError 与 JSON-RPC error 成为失败 ToolResult）。
- [ ] AC5：同一 Server 每会话只建立一次且退出释放资源（验证：运行 uv run pytest tests/test_mcp_manager.py tests/test_mcp_transport.py -k "cache or close"；期望启动次数为一次，stdio 子进程退出，HTTP 会话被关闭或 DELETE）。
- [ ] AC6 / N4：单个启动失败不影响其他 Server（验证：运行 uv run pytest tests/test_mcp_manager.py tests/test_tui.py -k "failure or warning"；期望中文警告包含失败 Server 与原因，其他 Server 工具和内置工具仍可用）。
- [ ] AC7 / N3：单次远端工具失败不影响后续调用（验证：运行 uv run pytest tests/test_mcp_manager.py tests/test_tools.py -k "error or isolation"；期望该次返回失败 ToolResult，内置工具与另一 Server 后续调用成功）。
- [ ] AC8：每次 MCP 初始化和工具调用固定 30 秒上限（验证：运行 uv run pytest tests/test_mcp_session.py -k timeout；期望测试替换等待常量后得到 timeout 错误码，生产常量断言为 30 秒，且无重试或自动重连）。
- [x] AC9 / N3：MCP 工具沿用默认权限与会话确认（验证：运行 uv run pytest tests/test_permissions.py tests/test_tools.py tests/test_tui.py -k "permission or mcp"；期望“查看文档”等只读表达也在 default 模式显示确认卡片，选择“本会话允许”后同一调用在本会话可继续执行，bypassPermissions 直接执行）。
- [ ] N2：新增用户提示和配置说明使用清晰中文（验证：运行 rg -n "MCP|服务器|超时|确认" src/yucode yucode.yaml.example README.md；期望新增面向用户文案均为清晰中文，无占位文本）。

## 集成、回归与说明

- [ ] 启动加载完成前不可提交输入，完成后恢复（验证：运行 uv run pytest tests/test_tui.py -k "mcp and startup"；期望加载中输入框禁用，完成或部分失败后恢复焦点与输入）。
- [ ] 无 MCP 配置时原有入口和会话不回归（验证：运行 uv run pytest tests/test_cli.py tests/test_tui.py -k "not mcp"；期望现有 CLI 构造、欢迎页和普通聊天测试通过）。
- [ ] 示例完整说明两层配置、两种传输、变量展开、命名、30 秒上限和权限行为（验证：运行 rg -n "mcp_servers|APPDATA|MCP__|30 秒|确认" yucode.yaml.example README.md；期望每项均能在示例或说明中找到，且没有真实凭据）。
- [x] MCP 专项自动化测试通过（验证：运行 uv run pytest tests/test_mcp_transport.py tests/test_mcp_session.py tests/test_mcp_manager.py；期望退出码为 0）。
- [x] 全量测试通过（验证：运行 uv run pytest；期望退出码为 0）。
- [x] 修改后的源码可编译（验证：运行 uv run python -m compileall -q src；期望退出码为 0）。

## 端到端场景

- [ ] E2E-1：可用 stdio MCP 工具的真实对话（验证：在 tmux 中用含可用 stdio Server 的 yucode.yaml 启动 yucode，输入“执行 MCP 工具并返回结果”；期望加载后出现 MCP__Server__工具名，Agent 请求执行时出现默认权限确认，选择“本会话允许”后工具活动显示成功，最终生成基于工具结果的回复）。
- [ ] E2E-2：故障隔离（验证：在 tmux 中同时配置一个可用 stdio Server 与一个不存在命令的 Server 后启动 yucode；期望看到含失败 Server 名称和原因的中文警告，仍可提交请求并成功调用可用 MCP 工具）。
- [ ] E2E-3：退出清理（验证：在 E2E-1 成功调用后输入 /exit，并检查测试 Server 的关闭记录或子进程状态；期望 stdio Server 已停止，没有残留子进程）。

## 执行记录

| 日期 | 执行人 | 通过项 | 实际命令或场景 | 结果摘要 |
|---|---|---|---|---|
| 2026-09-09 | Codex | 自动化与编译 4/4 | uv run pytest；uv run python -m compileall -q src | 134 通过，2 跳过；编译成功；权限矩阵通过。 |
