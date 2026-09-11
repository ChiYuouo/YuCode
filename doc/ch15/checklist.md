# Hook 自动化系统 Checklist

> 每一项均以运行代码、测试结果或可观察行为验证；不以检查实现文件为验收依据。

## 规则、条件与变量

- [ ] **15 个事件、无条件触发与声明顺序正确（AC1）。** 验证：加载包含全部事件的规则集，逐一构造对应上下文并运行 `uv run pytest tests/test_hooks_loader.py tests/test_hooks_engine.py -q`。预期：15 个事件均被接受；仅同事件且条件满足的规则执行；同事件多条规则的可观察输出顺序与 YAML 一致；未配置事件没有额外动作。

- [ ] **四种条件操作符和两种组合关系正确（AC2）。** 验证：对精确、不等、正则、glob 及 `all`/`any` 各运行匹配和不匹配案例，执行 `uv run pytest tests/test_hooks_conditions.py -q`。预期：各案例只在预期条件下匹配。

- [ ] **无效条件在加载期以中文拒绝（AC2、AC8）。** 验证：分别提供未知字段、未知操作符、损坏正则、空条件组和同时含 `all`/`any` 的 YAML，运行 `uv run pytest tests/test_hooks_loader.py tests/test_config.py -q`。预期：每项错误含规则位置和原因，应用配置不产生可运行的部分规则。

- [ ] **上下文变量替换完整且无跨事件泄露（AC3）。** 验证：在工具、文件、消息和错误上下文中渲染 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$TOOL_ARGS.xxx`，再渲染缺失字段，运行 `uv run pytest tests/test_hooks_template.py tests/test_hooks_conditions.py -q`。预期：已提供字段准确替换；缺失字段为空文本；不会带入上一事件的值。

## 动作与执行控制

- [ ] **命令、提示词、HTTP 与 Agent stub 的行为正确（AC4）。** 验证：以临时目录和本地 HTTP 测试服务分别触发四类动作，运行 `uv run pytest tests/test_hooks_executors.py -q`。预期：命令在工作目录执行；提示词可由 Engine 交给后续模型请求；HTTP 收到预期方法、地址和替换内容；Agent 动作不创建任务、不运行 Agent、不额外注入提示，并有占位日志。

- [ ] **动作失败不打断其他 Hook 或主流程（AC4、N3）。** 验证：让命令、HTTP 和 Agent stub 分别失败或返回空结果，并在其后放置可观察的提示或记录动作，运行 `uv run pytest tests/test_hooks_executors.py tests/test_hooks_engine.py -q`。预期：失败有中文诊断或日志，后续规则仍执行，调用方继续运行。

- [ ] **once、async 和命令超时受控（AC5）。** 验证：连续触发同一 once 规则后重建 Engine；触发一个延迟异步规则和一个超时命令，运行 `uv run pytest tests/test_hooks_engine.py tests/test_hooks_executors.py -q`。预期：一次运行内 once 仅调度一次、重建后可再次调度；async 不阻塞调用方且异常已记录；超时命令被停止并报告超时。

- [ ] **异步拦截规则在加载期拒绝（AC5、AC8）。** 验证：提供同时含 `event: pre_tool_use`、`reject: true`、`async: true` 的 YAML，执行 `uv run pytest tests/test_hooks_loader.py tests/test_config.py -q`。预期：配置加载失败，中文错误明确指出 reject/async 约束。

## 拦截与生命周期集成

- [ ] **工具前拦截会阻止工具执行并让模型获得原因（AC6）。** 验证：为 `pre_tool_use` 配置按工具名和参数拒绝的规则，使用记录工具和两轮模拟 Provider 运行 `uv run pytest tests/test_hooks_integration.py tests/test_tools.py tests/test_agent.py -q`。预期：首次工具本体与权限确认均未发生；模型历史收到错误码 `hook_rejected` 和拒绝原因；第二轮能改用允许方案；不匹配调用仍遵循原权限和执行流程。

- [ ] **会话、轮次、消息、工具与系统事件在正确边界发出（AC7）。** 验证：运行含启动/关闭、一次对话、一次工具调用、一次权限请求、一次写文件、一次命令和一次压缩的集成测试，执行 `uv run pytest tests/test_hooks_integration.py tests/test_agent.py tests/test_tools.py tests/test_cli.py -q`。预期：`startup`、`shutdown`、`session_start`、`session_end`、`turn_start`、`turn_end`、`pre_send`、`post_receive`、`pre_tool_use`、`post_tool_use`、`permission_request`、`file_change`、`command_execute`、`compact` 均得到一次正确上下文；Agent 普通异常会产生 `error`；Hook 失败不会递归触发 `error` 或终止对话。

- [ ] **提示词动作只影响后续模型请求，不污染会话历史（AC4、AC7）。** 验证：触发提示词 Hook 后捕获下一次模型请求和 `Conversation` 内容，执行 `uv run pytest tests/test_hooks_integration.py tests/test_agent.py -q`。预期：下一请求包含提示文本；历史消息中没有伪造的用户或助手消息；取出后不会重复注入。

## 配置、兼容性与构建

- [ ] **YAML 配置集中校验并可直接运行（AC8）。** 验证：分别提供未知事件、未知动作、各动作缺少必填字段、非法 URL/超时、非法变量引用、非法条件和非法拒绝组合，再提供一份有效配置，执行 `uv run pytest tests/test_config.py tests/test_hooks_loader.py -q`。预期：每份无效配置均在加载时给出含规则索引的中文错误；有效配置只解析一次并能直接传给运行时。

- [ ] **示例配置可安全复制和理解。** 验证：将 `yucode.yaml.example` 的 Hook 片段复制到临时有效基础配置，执行 `uv run pytest tests/test_config.py tests/test_hooks_loader.py -q`。预期：示例不含真实密钥、真实外部地址或破坏性命令，且能通过解析并说明条件、变量、动作、once、async、超时和拦截。

- [ ] **既有权限、工具、Agent、CLI 与 TUI 行为无回归（N5）。** 验证：执行 `uv run pytest tests/test_permissions.py tests/test_tools.py tests/test_agent.py tests/test_cli.py tests/test_tui.py -q`。预期：退出码为 0；没有 HookEngine 时旧行为不变；Hook 不绕过 Plan 模式、权限确认、危险命令黑名单或工作区边界。

- [ ] **专项测试、编译检查和全量测试通过。** 验证：依次执行 `uv run python -m compileall -q src/yucode`、`uv run pytest tests/test_hooks_*.py -q`、`uv run pytest -q`。预期：全部退出码为 0；Hook 专项测试覆盖条件、模板、加载器、动作、Engine 和集成；全量测试无新增失败。

## 端到端场景

- [ ] **在 tmux 中完成“拒绝 → 模型调整 → 允许执行”的真实 Hook 流程（AC9）。** 验证：

  1. 在独立临时工作目录准备真实 Provider 配置和安全 Hook：拒绝包含指定危险片段的 `run_command`，并为允许命令或文件写入配置一个可观察的提示词或命令动作。
  2. 在 tmux 中启动 YuCode，输入会促使模型请求被拒绝命令的真实对话；捕获窗格，确认工具结果显示中文拒绝原因。
  3. 继续观察模型下一轮，确认其改用安全的读文件、解释或其他允许方案；不能以只出现拒绝提示替代“模型调整”。
  4. 再输入一个明确授权的安全写文件或命令请求，按既有权限流程完成确认，确认工具实际执行、对应 Hook 产生可观察效果，且对话正常结束。
  5. 记录 tmux 会话名、两段输入、每步实际输出、Hook 可观察结果、通过/失败状态；逐项对照本清单，不以模拟 Provider 或单元测试替代此场景。

  **预期：** 应用正常启动；危险调用未执行且原因反馈给模型；模型确实调整后续策略；允许调用仍受原权限控制并成功执行；提示或命令 Hook 的结果可观察；没有未处理后台异常或对话中断。
