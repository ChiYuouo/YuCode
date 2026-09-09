# YuCode 结构化系统提示与缓存策略 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/prompting.py` | 提示模块、稳定/运行期构建、模式轮次和工具描述增强 |
| 修改 | `src/yucode/providers/base.py` | 缓存用量模型与基于请求描述的 Provider 协议 |
| 修改 | `src/yucode/agent.py` | 每轮构建模型请求并累计缓存用量 |
| 修改 | `src/yucode/providers/openai.py` | OpenAI 请求映射与缓存字段解析 |
| 修改 | `src/yucode/providers/anthropic.py` | Claude 请求映射、缓存断点与缓存字段解析 |
| 修改 | `src/yucode/tui/app.py` | 缓存指标的会话累计与状态刷新 |
| 修改 | `src/yucode/tui/widgets.py` | 缓存可用/不可用状态显示 |
| 新建 | `tests/test_prompting.py` | 提示构建器与工具强化的单元测试 |
| 修改 | `tests/test_agent.py` | 请求构建、模式轮次、历史隔离和缓存累计测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 请求结构与缓存观测测试 |
| 修改 | `tests/test_anthropic_provider.py` | Claude 请求结构与缓存观测测试 |
| 修改 | `tests/test_tui.py` | 缓存状态展示与回归测试 |
| 新建 | `doc/ch4/checklist.md` | 自动化、人工对比和 tmux 端到端验收项 |
| 新建 | `src/yucode/policy.py` | 任务授权、工具前置条件、验证追踪与脱敏 |
| 修改 | `src/yucode/prompting.py` | 强制规则、授权与待验证目标的运行期提示 |
| 修改 | `src/yucode/agent.py` | 策略生命周期、未验证停止和文本脱敏 |
| 修改 | `src/yucode/tools/executor.py` | 执行前策略检查与执行结果记录 |
| 新建 | `tests/test_policy.py` | 授权、前置读取、验证追踪和脱敏测试 |

## T1: 建立提示层的数据模型和固定模块构建

**文件：** `src/yucode/prompting.py`、`tests/test_prompting.py`

**依赖：** 无

**步骤：**

1. 定义 `PromptModule`、`RuntimeContext`、`RuntimeMessage` 与 `ModelRequest`，使稳定指令、运行期补充、历史、工具和缓存键有独立字段。
2. 实现 `SystemPromptBuilder` 的七个固定模块拼装，保证优先级、单空行分隔和内容确定性。
3. 为自定义指令、已激活 Skill、长期记忆预留可选模块输入，并确保缺失内容不生成空白占位。
4. 编写测试，覆盖固定顺序、环境不混入稳定文本、可选模块顺序和相同输入的稳定构建结果。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q`；期望模块顺序、空行、可选模块和稳定性断言全部通过。

## T2: 实现运行期补充、模式轮次和工具规则强化

**文件：** `src/yucode/prompting.py`、`tests/test_prompting.py`

**依赖：** T1

**步骤：**

1. 生成只包含运行期信息的 `<system-reminder>`，其中包含规范化工作目录与当前轮次。
2. 实现模式强化策略：第 1、5、10……轮使用完整版本，其余轮使用语义等价的精简版本。
3. 实现 `enhance_tools`，以复制方式为工具描述添加“优先使用适用专用工具”和“编辑前先读取”的相关规则，不修改名称和 schema。
4. 为普通和规划模式分别测试标签包装、环境变化、1～11 轮完整/精简分布、编辑工具前置规则和工具定义不变性。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q`；期望标签、每五轮策略、环境隔离和工具规则测试全部通过。

## T3: 扩展共享 Provider 协议与用量模型

**文件：** `src/yucode/providers/base.py`、`tests/test_agent.py`

**依赖：** T1

**步骤：**

1. 添加 `CacheUsage`，明确区分缓存字段不可用与已知为零。
2. 将缓存读/写用量纳入 `Usage`，并提供可复用的、不会伪造可用状态的累计逻辑。
3. 将 Provider 协议替换为接收单个 `ModelRequest` 的形式，保留流事件、富历史和取消语义。
4. 更新测试中的 Fake Provider 接口和最小用量断言，确保既有文本、工具和用量行为未变。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q`；期望现有 Agent 场景和新增缓存用量模型断言通过。

## T4: 将 Agent 接入结构化提示请求

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`

**依赖：** T2、T3

**步骤：**

1. 让 Agent 构造时接收或创建 `SystemPromptBuilder`，并在每轮选定模式工具后创建 `RuntimeContext` 与 `ModelRequest`。
2. 将 Provider 调用改为传递请求描述，保持 `/plan` 的只读工具筛选、停止条件、历史写入和事件顺序不变。
3. 在累计函数与 `UsageUpdated` 中合并缓存观测，且不把运行期补充写入 Conversation。
4. 添加多轮测试：稳定提示保持不变、环境在下一轮更新、规划模式 11 轮始终只有只读工具、缓存读写正确累计。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q`；期望 Agent 多轮、Plan、安全边界、历史隔离和缓存累计断言通过。

## T5: 实现 OpenAI 请求映射与缓存观测

**文件：** `src/yucode/providers/openai.py`、`tests/test_openai_provider.py`

**依赖：** T3、T4

**步骤：**

1. 将 `ModelRequest.stable_instructions` 映射为 Responses 的稳定指令，将运行期提醒以 developer 级输入置于历史之前。
2. 发送增强后的工具定义和确定性缓存键，保持工具调用、工具结果和流式取消的既有序列化行为。
3. 从完成事件的用量详情中解析缓存读 token 和可选缓存写 token；缺失详情时标记为不可用。
4. 使用 HTTP mock 测试请求字段位置、运行期内容不以 user 角色出现、工具 schema 不变、有字段/无字段两类缓存用量以及既有 SSE 行为。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_openai_provider.py -q`；期望请求结构、缓存解析、工具序列化和取消测试全部通过。

## T6: 实现 Claude 请求映射、缓存边界与观测

**文件：** `src/yucode/providers/anthropic.py`、`tests/test_anthropic_provider.py`

**依赖：** T3、T4

**步骤：**

1. 将稳定提示组织为顶层 system 内容块，将运行期提醒作为不写入 Conversation 的前置消息内容，与首个用户消息合并。
2. 对稳定系统/工具前缀设置 ephemeral 缓存断点，确保稳定工具描述可参与缓存而动态提醒不改变稳定前缀。
3. 从流的开始和结束用量中读取缓存读/创建 token，保留既有输入、输出和 thinking token 解析。
4. 使用 HTTP mock 测试 system 块顺序、缓存断点、工具定义、历史隔离、有字段/无字段用量、工具流和取消回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_anthropic_provider.py -q`；期望请求结构、缓存解析、thinking、工具流和取消测试全部通过。

## T7: 在终端显示缓存观测结果

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T3、T4

**步骤：**

1. 扩展会话用量累计状态，记录缓存读/写 token 和数据可用标记，且不改变输入/输出 token 的已有计算。
2. 扩展状态栏显示：有数据时显示缓存读/写；本次请求完全没有缓存字段时显示“缓存数据不可用”。
3. 让新状态在多轮请求、完成、取消与流错误后不会遗留为下一次请求的活动数据。
4. 添加测试，覆盖有数据、无数据、多轮累计和既有 TUI 生成/取消/状态显示回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望缓存状态和既有界面行为断言全部通过。

## T8: 执行自动化回归并修复集成问题

**文件：** 仅修改 T1～T7 中因失败而必须调整的相关文件

**依赖：** T5、T6、T7

**步骤：**

1. 运行提示层、Agent、两个 Provider 和 TUI 的相关测试，定位协议改造造成的序列化、类型或状态回归。
2. 修复失败项，确保不通过删除既有安全、取消或模式测试来获得通过。
3. 运行全量测试与源码编译，记录实际结果。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q` 和 `.venv\Scripts\python.exe -m compileall -q src`；期望两项退出码均为 0，测试无失败、错误或悬挂。

## T9: 编写验收清单与人工对比场景

**文件：** `doc/ch4/checklist.md`

**依赖：** T8

**步骤：**

1. 将 `spec.md` 的 AC1～AC10 逐项转为可观察的清单项，并补充构建、全量测试和安全回归。
2. 写明四种人工对比场景：读取后编辑、`/plan` 只读探索、环境变化的多轮任务、重复请求的缓存复用。
3. 为每个场景给出启动方式、真实输入、应观察的工具顺序/状态栏/最终回复和记录位置。
4. 加入 OpenAI 与 Claude 各一次 tmux 端到端闭环，以及无有效服务配置时如何如实记录未执行项。

**验证：** 人工核对 `doc/ch4/checklist.md`：AC1～AC10 均至少对应一项；四类场景均包含输入、观察重点和预期结果；没有引入项目指令加载、记忆、MCP 或自动化评估。

## T10: 使用 tmux 执行真实端到端和人工对比验收

**文件：** `doc/ch4/checklist.md`（仅填写实际验收记录）

**依赖：** T8、T9

**步骤：**

1. 在 tmux 启动 YuCode，确认欢迎界面正常显示。
2. 在有效 OpenAI 和 Claude 配置下，分别执行“读取项目文件 → 编辑临时文件 → 再读取验证 → 最终总结”的真实请求，观察先读后改、工具优先与缓存状态。
3. 执行 `/plan` 只读任务、环境变化多轮任务和重复任务，按清单记录工具调用、模式限制、环境更新与缓存读/写字段。
4. 如果某供应商未配置或服务不可用，记录实际阻碍和已通过的离线验证，不伪称端到端成功。

**验证：** 依 `doc/ch4/checklist.md` 的 tmux 场景逐项执行；期望界面、工具顺序、最终回复和缓存状态均与清单一致，或有明确、可复现的未执行记录。

## 执行顺序

```text
T1 → T2 ─┐
         ├→ T4 ─┬→ T5 ─┐
T1 → T3 ─┘      ├→ T6 ─┼→ T8 → T9 → T10
                └→ T7 ─┘
```

## 修订任务：严格提示与执行层门禁

### T11: 强化稳定提示与运行期授权信息

**文件：** `src/yucode/prompting.py`、`tests/test_prompting.py`

**依赖：** 无

**步骤：**

1. 将系统约束、动作执行、工具使用和文本输出模块改写为无歧义的“必须/不得/仅当”规则，覆盖专用工具、先读后改、修改后验证、事实陈述和敏感信息。
2. 扩展运行期上下文与 `<system-reminder>`，携带当前授权等级和待验证目标；保持这些动态内容不影响稳定提示和缓存键。
3. 为相关工具描述追加与全局规则一致的硬性前置条件，避免出现例外或冲突措辞。
4. 添加测试，断言强制措辞、授权/验证运行期信息、模块稳定性及工具描述一致性。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_prompting.py -q`；期望强制规则、运行期标签、缓存键稳定性和工具描述断言全部通过。

### T12: 实现任务授权、流程证据与脱敏策略

**文件：** `src/yucode/policy.py`、`tests/test_policy.py`

**依赖：** T11

**步骤：**

1. 根据用户原文与模式实现仅回答、只读、允许执行三种授权；覆盖明确执行、解释/评审、规划和意图不清输入。
2. 实现工具执行前检查：仅回答/只读授权拒绝副作用；编辑已有文件要求同目标成功读取；覆盖已有文件要求先读取。
3. 实现结果记录：副作用成功后登记待验证目标，成功读取同目标后解除；策略拒绝返回结构化失败且不触发真实工具。
4. 实现常见密钥、令牌、密码、Cookie 与 Bearer 凭据的固定占位符脱敏。
5. 添加单元测试，覆盖授权分类、前置条件、结果状态转换、拒绝结果和输入/输出脱敏。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_policy.py -q`；期望每种授权、读取/验证状态和敏感值替换断言通过。

### T13: 将策略门禁接入工具执行器

**文件：** `src/yucode/tools/executor.py`、`tests/test_tools.py`、`tests/test_policy.py`

**依赖：** T12

**步骤：**

1. 为执行器增加可选策略参数，在每项调用真正执行前检查策略并返回同序的拒绝结果。
2. 保留相邻只读并发、副作用顺序屏障、未知工具处理、命令确认和取消行为；策略拒绝不应启动线程、命令或确认弹窗。
3. 每项真实工具结果和策略拒绝结果均按原顺序记录，以便后续调用获得正确读取/验证证据。
4. 添加集成测试，验证未读编辑、无授权写入和只读模式命令均不会改变工作区；合法“读→改→读”仍可完成。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_policy.py -q`；期望顺序、并发、确认、取消和新增拒绝/合法闭环断言全部通过。

### T14: 将授权、验证和脱敏接入 Agent 生命周期

**文件：** `src/yucode/agent.py`、`src/yucode/prompting.py`、`tests/test_agent.py`

**依赖：** T11、T13

**步骤：**

1. 在请求开始时创建本次运行唯一的授权和执行策略，并传给每轮工具执行及运行期提示构建。
2. 对文本增量、部分助手文本、工具回灌内容和最终可见文本应用脱敏，保持历史与界面一致。
3. 有待验证目标而模型未请求工具时，不以成功结束；发起带验证提醒的后续轮次。再次未验证或达到现有上限时，产生明确失败状态且不声称任务完成。
4. 添加 Fake Provider 场景，验证解释请求的写入被拒绝、未读编辑被纠正、修改后必须读取、验证失败不成功结束、敏感值不进入可见文本或历史。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py -q`；期望既有停止条件不退化，新增授权、验证和脱敏场景全部通过。

### T15: 让 TUI 清晰呈现策略拒绝与未验证停止

**文件：** `src/yucode/tui/app.py`、`src/yucode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T14

**步骤：**

1. 为策略拒绝和未验证停止提供用户可读的工具结果/错误说明，不暴露内部策略对象或敏感原文。
2. 确认流式脱敏后的文本、现有工具活动、Token/缓存状态、取消和输入恢复仍正确显示。
3. 添加界面测试，覆盖被拒绝的写入、未验证停止和敏感占位符显示。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望策略反馈可读，既有流式、取消、确认和状态栏测试全部通过。

### T16: 执行严格规则的全量回归

**文件：** 仅修改 T11～T15 中因失败而必须调整的相关文件

**依赖：** T15

**步骤：**

1. 运行提示层、策略、Agent、工具、Provider 与 TUI 的全量测试，定位新门禁对既有行为的影响。
2. 修复失败项；不得删除原有安全、取消、模式或工具顺序测试来获得通过。
3. 运行源码编译和全量测试，记录实际结果。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q` 和 `.venv\Scripts\python.exe -m compileall -q src`；期望两项退出码均为 0，测试无失败、错误或悬挂。

### T17: 更新验收清单并在 tmux 复测

**文件：** `doc/ch4/checklist.md`

**依赖：** T16

**步骤：**

1. 为 AC11～AC13 添加可观察清单项，并保留原有缓存与跨 Provider 验收记录。
2. 在 tmux 中输入真实解释请求、未读编辑请求、合法读改读请求和包含模拟敏感输出的请求。
3. 记录实际工具顺序、拒绝结果、最终回复和工作区状态；真实配置不可用时如实记录未执行项。

**验证：** 逐项执行更新后的 `doc/ch4/checklist.md`；期望解释请求无副作用，非法编辑被拒绝，合法读改读完成并验证，敏感值不在最终显示中。

## 修订执行顺序

```text
T11 → T12 → T13 → T14 → T15 → T16 → T17
```
