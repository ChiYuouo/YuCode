# MCP 外部工具接入 Plan

## 架构概览

新增 mcp 子包，作为 YuCode 与外部 MCP Server 之间的边界。配置层负责从两层 YAML 得到可用 Server 声明；传输层负责 stdio 与 Streamable HTTP 的字节收发；会话层负责 JSON-RPC 请求编号、响应配对和 MCP 生命周期；工具适配层把远端定义和调用结果转换为现有 Tool 协议。

MCPManager 是启动和关闭的唯一入口。它为每个 Server 独立创建会话，依次初始化、发送 initialized 通知、分页获取 tools/list，再把成功发现的工具注册进已有 ToolRegistry。单个 Server 产生的配置或连接异常被收集为警告，不中断其他 Server。

终端界面启动后暂时禁用输入，异步完成 MCP 发现；发现结束后显示失败警告、启用输入。这样子进程和 HTTP 客户端始终绑定 Textual 的运行事件循环，退出界面时能在同一循环中完成关闭。

## 核心数据结构

### MCPServerConfig

字段：name: str、transport: Literal["stdio", "http"]、command: str | None、args: tuple[str, ...]、env: Mapping[str, str]、url: str | None、headers: Mapping[str, str]。

表示一个已校验、变量已展开的 Server 声明。stdio 仅使用 command、args、env；HTTP 仅使用 url、headers。

### MCPConfigIssue

字段：server_name: str、reason: str。

表示仅影响一个 Server 的配置问题。启动层把它转成中文警告而不是终止整个应用。

### JsonRpcError 与 JsonRpcResponse

JsonRpcError 包含 code、message、可选 data；JsonRpcResponse 包含 request_id、result 和可选 error。响应必须关联其整数请求标识，避免并发调用串到错误的等待者。

### MCPTransport

定义异步接口：

    async open() -> None
    async send(message: Mapping[str, Any]) -> None
    async receive() -> AsyncIterator[Mapping[str, Any]]
    async close() -> None

stdio 与 HTTP 都只负责传输 JSON-RPC 对象，不包含工具或生命周期语义。

### MCPClientSession

定义异步接口：

    async start() -> tuple[RemoteToolDefinition, ...]
    async call_tool(name: str, arguments: Mapping[str, Any]) -> RemoteToolResult
    async close() -> None

维护单个 Server 的请求序号、未完成请求表、接收任务、协商后的协议版本及 HTTP 会话标识。start 固定执行 initialize、notifications/initialized、tools/list；call_tool 发送 tools/call。

### MCPTool

保存公开名称、原始远端名称与所属会话。公开名称为 MCP__{server_name}__{remote_name}；调用时使用原始远端名称。实现已有 Tool 协议：

    definition -> ToolDefinition
    safety -> ToolSafety
    async execute(arguments, context, call_id, cancellation) -> ToolResult

所有外部工具固定为 SIDE_EFFECT，不信任远端声明的安全标注，继续经过已有权限确认。

### MCPManager

定义异步接口：

    async start(registry: ToolRegistry) -> tuple[MCPStartupWarning, ...]
    async close() -> None

持有可用会话，以 Server 名称索引。启动时逐个建立并注册，失败则关闭该 Server 已分配资源并记录警告；关闭时独立关闭所有仍存活会话，即使一个关闭失败也继续处理其余会话。

## 模块设计

### yucode.config

职责：保持现有模型、Agent 和权限配置校验；增加 mcp_servers 配置读取。项目级 yucode.yaml 仍是启动所需的主配置，用户级 %APPDATA%\YuCode\yucode.yaml 是可选的 MCP Server 来源。

对外接口：load_config() 返回含 mcp_servers 和 mcp_issues 的 AppConfig；提供可替换的用户配置路径，以便测试。

规则：两份文件只合并 mcp_servers map；项目级同名条目整体替换用户级条目。逐个校验 Server，将无效条目放入 mcp_issues。stdio 的 env 与 HTTP 的 headers 中只允许字符串值，`${VAR}` 必须存在于进程环境中。密钥值不进入异常文本。

### yucode.mcp.transport

职责：提供 StdioTransport 和 StreamableHttpTransport，均实现 MCPTransport。

依赖：asyncio、json、httpx。

stdio 行为：通过 asyncio.create_subprocess_exec 启动命令；标准输入写入一行 UTF-8 JSON-RPC，标准输出逐行解析 JSON，标准错误仅排空以防子进程阻塞；关闭时依次关闭标准输入、等待退出、终止仍未退出的进程。

HTTP 行为：每条 JSON-RPC 消息以 HTTP POST 发送，携带 Accept: application/json, text/event-stream。响应同时支持一个 JSON 对象和 SSE 中的 JSON 消息；初始化后保存 Mcp-Session-Id，后续请求携带它及协商的 MCP-Protocol-Version。关闭时如存在会话标识则尽力 DELETE，再关闭 HTTP 客户端。不会实现旧版 HTTP+SSE 回退、断线恢复或自动重连。

### yucode.mcp.session

职责：完成 JSON-RPC 2.0 构造、响应路由和 MCP 工具生命周期。

对外接口：MCPClientSession.start()、call_tool()、close()。

规则：每个请求分配递增 id，并在发送前登记 Future；接收循环依 id 唤醒对应等待者。未知 id、通知与本期不支持的服务端请求不会影响其他等待请求。每个请求固定在 30 秒后超时，超时后移除等待项并转换为可处理错误。初始化请求宣告 YuCode 的实现信息和空能力，校验 Server 返回协议版本与 tools 能力，随后发送 notifications/initialized。工具列表按 nextCursor 分页直到结束。

### yucode.mcp.tool

职责：将远端 name、description、inputSchema 映射为 ToolDefinition，并转发执行。

规则：远端返回 isError: true 或 JSON-RPC 错误时生成失败 ToolResult；文本内容合并为模型可读文本，其他 MCP 内容项以 JSON 形式保留。连接、协议、30 秒超时和取消异常都转换为带稳定错误码的失败结果，绝不从工具边界泄漏到 Agent。工具固定为 SIDE_EFFECT，因此现有默认授权判断、确认卡片及“本会话允许”规则无需分支即可复用。

### yucode.mcp.manager

职责：负责每个 Server 的独立启动、缓存、注册和关闭。

规则：同一配置 Server 每个 YuCode 会话只创建一个 MCPClientSession。同名远端工具或不能生成合法公开名称时，该 Server 视为发现失败并不注册部分工具，防止工具中心处于不完整状态。

### yucode.tools.registry

职责：在保留内置工具默认集合的基础上，支持启动后追加已验证的工具。

对外接口：新增显式注册操作；拒绝任何与现有名称冲突的工具。

### yucode.tui.app 与 yucode.cli

职责：将已解析的 MCP 配置交给 MCPManager，控制启动加载态和关闭。

规则：UI 挂载后启动 MCP 发现 Worker，发现前输入不可用、状态显示“正在加载 MCP 工具”。所有启动警告在聊天区显示中文 ErrorMessage，完成后恢复“准备就绪”和输入焦点。应用退出或启动中断时调用 manager 关闭会话。没有 MCP Server 时不改变现有启动和交互。

## 模块交互

    用户级 yucode.yaml ─┐
                        ├─> load_config ─> AppConfig(mcp_servers, mcp_issues)
    项目级 yucode.yaml ─┘                         │
                                                  ▼
    cli ─> ToolRegistry(内置工具) ─> ChatApp ─> MCPManager.start
                                                 │
                           stdio / HTTP Transport ─> MCPClientSession
                                                         │
                                                         ▼
                                                MCPTool 注册到 ToolRegistry
                                                         │
    模型工具调用 ─> ToolExecutor ─> MCPTool.execute ─> session.call_tool
                                                         │
                                                         ▼
                                             JSON-RPC tools/call 回包
                                                         │
                                                         ▼
                                              ToolResult ─> Agent / 模型

1. 配置加载合并两层 mcp_servers，产生可用声明和独立问题列表。
2. TUI 启动 Worker 启动每个 Server 会话：打开传输、初始化、通知、分页发现工具。
3. Manager 为成功会话生成带前缀的 MCPTool 并一次性注册；失败仅产生警告。
4. Agent 后续从同一 Registry 取得工具定义；执行器照常先完成权限判断，再调用适配工具。
5. 适配工具通过所属会话发送 tools/call，JSON-RPC 路由器用 id 将结果交还给该调用。
6. 退出 TUI 时关闭 Manager，Manager 独立释放每条连接或子进程。

## 文件组织

    src/yucode/
    ├── cli.py                         # 注入 MCPManager
    ├── config.py                      # 两层 MCP 配置、变量展开与问题收集
    ├── tools/registry.py              # 追加注册并检测重名
    ├── tui/app.py                     # 启动加载态、警告显示与会话关闭
    └── mcp/
        ├── __init__.py
        ├── manager.py                 # 多 Server 生命周期与工具注册
        ├── session.py                 # JSON-RPC 配对与 MCP 生命周期
        ├── tool.py                    # Tool 协议适配
        └── transport.py               # stdio、Streamable HTTP 与 SSE

    tests/
    ├── test_config.py                 # 两层配置、覆盖和变量展开
    ├── test_mcp_transport.py          # stdio/HTTP 消息收发与关闭
    ├── test_mcp_session.py            # 初始化、分页、id 配对和错误
    ├── test_mcp_manager.py            # 隔离、缓存、注册和清理
    ├── test_tools.py                  # 外部工具权限与注册冲突
    ├── test_tui.py                    # 加载态、警告和退出关闭
    └── test_cli.py                    # 注入 MCPManager

    yucode.yaml.example                # 用户级/项目级 MCP 配置示例
    README.md                          # MCP 配置与范围说明
    doc/ch8/spec.md
    doc/ch8/plan.md

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| MCP 协议版本 | 实现 2025-06-18 的工具子集 | 该版本定义 stdio 与 Streamable HTTP；本期目标限定为工具能力。 |
| 协议实现 | 基于 asyncio、json 与现有 httpx 自行实现最小客户端 | 避免新增 MCP SDK 依赖，并将范围限制在本期三步工具生命周期。 |
| HTTP 响应 | 支持 JSON 与 SSE | Streamable HTTP 允许两种响应形式，缺一会导致部分标准 Server 不兼容。 |
| HTTP 会话 | 保存 Mcp-Session-Id，后续携带版本与会话请求头 | 遵循有状态会话协商，确保同一 YuCode 会话复用连接。 |
| 配置 key | 使用顶层 mcp_servers map | 与现有顶层 agent、permissions 风格一致，表达多个命名 Server。 |
| 两层合并范围 | 只合并 mcp_servers；模型配置仍仅由项目配置决定 | 不改变既有模型配置语义，也允许用户级配置只保存可复用 MCP Server。 |
| 无效单项配置 | 记录警告并跳过该 Server | 满足单 Server 隔离；主模型配置错误仍维持现有启动失败行为。 |
| 外部工具安全级别 | 一律 SIDE_EFFECT | MCP 工具标注不可信，保守地复用默认授权、确认卡片与会话允许边界。 |
| 远端结果转换 | 文本合并，非文本 JSON 序列化 | ToolResult 当前只接收文本，同时避免静默丢弃 MCP 返回内容。 |
| 超时与重连 | 每个 MCP 请求固定 30 秒超时；不重连、不重试 | 防止连接永久阻塞，同时遵守本期不做自动重连的范围。 |
| 启动位置 | 在 TUI 所属事件循环的 Worker 中发现 | 子进程、HTTP 客户端与关闭逻辑位于同一事件循环，避免跨事件循环资源错误。 |
