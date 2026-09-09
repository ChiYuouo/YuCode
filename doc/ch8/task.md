# MCP 外部工具接入 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | src/yucode/config.py | 两层 MCP 配置、校验、变量展开和问题收集 |
| 新建 | src/yucode/mcp/__init__.py | MCP 子包导出 |
| 新建 | src/yucode/mcp/transport.py | stdio、Streamable HTTP、SSE 传输和关闭 |
| 新建 | src/yucode/mcp/session.py | JSON-RPC 配对、初始化、发现和调用 |
| 新建 | src/yucode/mcp/tool.py | 远端工具到 Tool 的适配 |
| 新建 | src/yucode/mcp/manager.py | 多 Server 启动、缓存、隔离和清理 |
| 修改 | src/yucode/tools/registry.py | 运行时追加工具并拒绝冲突 |
| 修改 | src/yucode/cli.py | 构造并注入 MCPManager |
| 修改 | src/yucode/tui/app.py | MCP 加载态、警告显示和退出关闭 |
| 修改 | tests/test_config.py | 两层配置、覆盖、变量与错误测试 |
| 新建 | tests/mcp_stdio_server.py | 可由子进程启动的 MCP 测试 Server |
| 新建 | tests/test_mcp_transport.py | 两种传输的收发、SSE、会话头和关闭测试 |
| 新建 | tests/test_mcp_session.py | 生命周期、分页、id 配对和协议错误测试 |
| 新建 | tests/test_mcp_manager.py | 注册、隔离、缓存和关闭测试 |
| 修改 | tests/test_tools.py | 适配工具、结果转换、权限和重名测试 |
| 修改 | tests/test_tui.py | 启动加载、警告、关闭以及普通交互回归 |
| 修改 | tests/test_cli.py | 入口注入与无 MCP 回归 |
| 修改 | yucode.yaml.example | MCP 的 stdio、HTTP 与两层覆盖示例 |
| 修改 | README.md | 配置位置、格式、范围与安全行为说明 |

## T1: 增加 MCP 配置模型和基础解析

**文件：** src/yucode/config.py、tests/test_config.py  
**依赖：** 无  
**步骤：**

1. 定义 MCP Server 配置、单项配置问题和 AppConfig 的 MCP 字段。
2. 保留项目级主配置的现有模型校验，同时读取顶层 mcp_servers。
3. 允许 mcp_servers 缺省，确保没有 MCP 配置时行为兼容。
4. 为空配置、stdio 和 HTTP 的合法最小声明添加测试。

**验证：** 运行 uv run pytest tests/test_config.py；期望全部通过，既有 provider、Agent、权限配置测试不变。

## T2: 实现用户级合并、变量展开和单项错误隔离

**文件：** src/yucode/config.py、tests/test_config.py  
**依赖：** T1  
**步骤：**

1. 定位用户级 %APPDATA%\YuCode\yucode.yaml；不存在时视为没有用户级 MCP 配置。
2. 合并两层 mcp_servers，项目级同名 Server 整体覆盖用户级 Server。
3. 展开 env 和 headers 字符串中的 ${VAR}，并校验 transport 所需字段、值类型和 HTTP URL。
4. 将单个 Server 的字段错误、缺失变量和不支持传输记录为含 Server 名称的 MCPConfigIssue。
5. 覆盖合并、变量展开和错误隔离测试。

**验证：** 运行 uv run pytest tests/test_config.py；期望覆盖规则和变量展开断言通过，无效 Server 只产生对应问题。

## T3: 建立可复用的 MCP 测试 Server

**文件：** tests/mcp_stdio_server.py  
**依赖：** 无  
**步骤：**

1. 编写仅供测试启动的 stdio MCP Server，逐行读取并写出 JSON-RPC。
2. 实现 initialize、notifications/initialized、tools/list（含分页）和 tools/call 的确定性响应。
3. 记录启动次数、收到环境变量、调用参数和关闭状态。
4. 保证标准输出只输出合法 JSON-RPC，调试信息仅输出到标准错误。

**验证：** 通过 python tests/mcp_stdio_server.py 启动后写入一条 initialize JSON-RPC；期望标准输出返回同 id 的初始化响应。

## T4: 实现 stdio 传输和关闭

**文件：** src/yucode/mcp/__init__.py、src/yucode/mcp/transport.py、tests/test_mcp_transport.py  
**依赖：** T3  
**步骤：**

1. 定义 MCPTransport 协议和传输通用异常。
2. 用异步子进程启动 stdio Server，向标准输入写入单行 UTF-8 JSON-RPC。
3. 从标准输出逐行解析对象，持续排空标准错误。
4. 实现关闭标准输入、等待、超时终止的顺序，并确保重复关闭安全。
5. 测试消息往返、环境变量、子进程退出和异常关闭。

**验证：** 运行 uv run pytest tests/test_mcp_transport.py -k stdio；期望全部通过，测试结束没有残留子进程。

## T5: 实现 Streamable HTTP 传输

**文件：** src/yucode/mcp/transport.py、tests/test_mcp_transport.py  
**依赖：** T4  
**步骤：**

1. 使用现有 httpx 创建可复用异步 HTTP 客户端。
2. 为每次 JSON-RPC 消息发送 POST，并附带规定的 Accept 请求头。
3. 支持 application/json 单响应和 text/event-stream 中的 data JSON 消息。
4. 从初始化响应保存 Mcp-Session-Id；后续请求附带会话与协商协议版本头；关闭时尽力 DELETE。
5. 用本地异步测试 HTTP Server 验证 JSON、SSE、请求头、会话标识和关闭路径。

**验证：** 运行 uv run pytest tests/test_mcp_transport.py -k http；期望 JSON 与 SSE 场景都通过，后续请求包含会话和协议版本头。

## T6: 实现 JSON-RPC 路由和 MCP 会话初始化

**文件：** src/yucode/mcp/session.py、tests/test_mcp_session.py  
**依赖：** T4、T5  
**步骤：**

1. 为每个请求生成递增 id，并在发送前登记对应 Future。
2. 启动接收循环，按响应 id 唤醒正确 Future；忽略通知和未知 id。
3. 实现 initialize、协议版本和 tools capability 校验、initialized 通知。
4. 为固定 30 秒超时、JSON-RPC error、版本不兼容、缺少工具能力和传输失败定义稳定异常。
5. 测试正常握手、错误回包、30 秒超时边界和并发请求按 id 正确配对；测试中缩短等待常量，生产配置仍固定为 30 秒。

**验证：** 运行 uv run pytest tests/test_mcp_session.py -k "initialize or pairing"；期望初始化顺序正确，并发结果不串包。

## T7: 实现工具发现和远端调用

**文件：** src/yucode/mcp/session.py、tests/test_mcp_session.py  
**依赖：** T6  
**步骤：**

1. 实现 tools/list 的 nextCursor 分页，合并所有合法远端工具定义。
2. 校验工具名称、描述和 inputSchema 的最低有效性。
3. 实现 tools/call 参数转发和 isError 读取。
4. 测试多页发现、调用参数原样传递、远端业务错误以及固定 30 秒超时。
5. 确认不会发送资源、提示词、采样或订阅请求。

**验证：** 运行 uv run pytest tests/test_mcp_session.py；期望分页工具完整返回，调用与错误测试全部通过。

## T8: 适配远端工具并保护权限边界

**文件：** src/yucode/mcp/tool.py、tests/test_tools.py  
**依赖：** T7  
**步骤：**

1. 实现 MCPTool 的 MCP__服务器名__远端工具名公开名称、远端名称和会话绑定。
2. 将远端描述和输入模式转换成 ToolDefinition。
3. 将文本结果合并，非文本内容编码为 JSON；将远端、协议、超时和取消错误转成 ToolResult。
4. 将 MCPTool 固定标记为 SIDE_EFFECT。
5. 测试公开名称、成功和失败结果、非文本结果，以及计划模式、未授权请求、默认模式确认和“本会话允许”不能绕过现有权限边界。

**验证：** 运行 uv run pytest tests/test_tools.py -k "mcp or external"；期望远端工具经现有权限判断后才会实际调用。

## T9: 扩展注册表并实现多 Server 管理

**文件：** src/yucode/tools/registry.py、src/yucode/mcp/manager.py、tests/test_mcp_manager.py、tests/test_tools.py  
**依赖：** T7、T8  
**步骤：**

1. 为 ToolRegistry 增加显式追加注册接口，并在名称与内置或已注册工具冲突时拒绝注册。
2. 实现 MCPManager：独立创建会话、启动、发现、构造 MCP__Server__工具名的 MCPTool 并原子注册。
3. 将配置问题、连接失败、初始化失败和工具名冲突转为含 Server 名称的启动警告。
4. 缓存成功会话；关闭时逐个释放，单个关闭失败不阻塞其余 Server。
5. 测试两台 Server 的同名远端工具、内置同名工具、单台启动失败、会话复用和独立清理。

**验证：** 运行 uv run pytest tests/test_mcp_manager.py tests/test_tools.py；期望可用 Server 工具被注册，失败 Server 只产生警告且其他调用成功。

## T10: 接入命令入口和 TUI 启动生命周期

**文件：** src/yucode/cli.py、src/yucode/tui/app.py、tests/test_cli.py、tests/test_tui.py  
**依赖：** T2、T9  
**步骤：**

1. 在 CLI 组装 MCPManager，并将配置声明和配置问题传给 ChatApp。
2. 在 UI 挂载后启动独占 Worker；加载中禁用 Composer，并更新状态文字。
3. Worker 完成后注册工具、在聊天区显示每条中文警告、恢复输入和焦点；默认模式调用外部工具时沿用既有确认卡片。
4. 为退出和加载中中断接入 manager 关闭；无 MCP Server 时保持原有启动和聊天行为。
5. 测试加载态、警告可见、输入恢复、关闭调用和 CLI 原有构造路径。

**验证：** 运行 uv run pytest tests/test_cli.py tests/test_tui.py；期望既有界面与 CLI 测试通过，新增测试确认警告和资源关闭。

## T11: 补充用户配置示例和说明

**文件：** yucode.yaml.example、README.md  
**依赖：** T2、T10  
**步骤：**

1. 在示例中给出 mcp_servers 的 stdio 与 HTTP 声明。
2. 注释用户级文件位置、项目级覆盖规则、${VAR} 展开、MCP__Server__工具名规则和固定 30 秒超时。
3. 在 README 说明本期只支持工具、外部工具会触发现有权限确认与会话允许、Server 失败隔离，以及不支持的能力。
4. 检查示例不包含真实密钥或本机绝对路径。

**验证：** 运行 rg -n "mcp_servers|APPDATA|MCP__|30 秒" yucode.yaml.example README.md；期望示例与说明均可检索，且没有真实凭据。

## T12: 全量自动化回归与静态检查

**文件：** 全部上述实现与测试文件  
**依赖：** T1 至 T11  
**步骤：**

1. 运行所有 MCP 专项测试，修复失败与资源泄漏。
2. 运行完整 pytest，修复 MCP 改动造成的既有行为回归。
3. 对修改的 Python 文件运行编译检查。
4. 复查配置错误和工具结果中不含环境变量展开后的敏感值，并确认超时测试不依赖重试或自动重连。

**验证：** 依次运行 uv run pytest tests/test_mcp_transport.py tests/test_mcp_session.py tests/test_mcp_manager.py、uv run pytest、uv run python -m compileall -q src；期望全部命令返回成功。

## T13: tmux 端到端验收

**文件：** checklist.md 中对应的验收记录  
**依赖：** T12  
**步骤：**

1. 用测试 stdio MCP Server 配置启动 YuCode。
2. 在 tmux 中输入一段真实请求，要求 Agent 调用 MCP__Server__工具名形式的 MCP 工具。
3. 观察工具活动、默认权限确认、会话允许、工具结果和最终回复。
4. 另以一个故障 Server 与一个可用 Server 同时启动，确认警告和隔离行为。
5. 将实际观察结果逐项填入 checklist 的执行记录。

**验证：** 在 tmux 会话中完成两段真实对话；期望可用远端工具经确认后被调用并生成回复，故障 Server 只显示警告。

## 执行顺序

    T1 ─> T2 ─┐
              ├──────────────────────────────> T10 ─> T11 ─> T12 ─> T13
    T3 ─> T4 ─> T5 ─> T6 ─> T7 ─> T8 ─> T9 ─┘
