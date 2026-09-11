# Skill 能力包 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `src/yucode/skills/__init__.py` | 导出 Skill 公共装配入口。 |
| 新建 | `src/yucode/skills/models.py` | Skill、来源、诊断、历史范围、激活快照数据结构。 |
| 新建 | `src/yucode/skills/loader.py` | 三层目录发现、frontmatter、目录包和工具清单解析。 |
| 新建 | `src/yucode/skills/runtime.py` | 启动校验、热更新、激活状态和快照。 |
| 新建 | `src/yucode/skills/tools.py` | 工具视图、脚本工具、资源工具、LoadSkill 与 InstallSkill。 |
| 新建 | `src/yucode/skills/install.py` | URL 下载、zip/Markdown 校验和原子安装。 |
| 新建 | `src/yucode/skills/prompt.py` | 可用摘要、激活 SOP 和建议工具提示状态。 |
| 新建 | `src/yucode/skills/execution.py` | 独立 Agent、历史选择和回流摘要。 |
| 新建 | `src/yucode/skills/commands.py` | 动态 Skill 命令目录和控制器。 |
| 新建 | `src/yucode/skills/builtin/commit/SKILL.md` | 内置共享提交样板。 |
| 新建 | `src/yucode/skills/builtin/review/SKILL.md` | 内置独立审查样板。 |
| 新建 | `src/yucode/skills/builtin/test/SKILL.md` | 内置独立测试样板。 |
| 修改 | `src/yucode/tools/base.py` | 全局工具目录、工具视图和每次执行上下文协议。 |
| 修改 | `src/yucode/tools/registry.py` | 提供全量工具查询，兼容现有 MCP 注册。 |
| 修改 | `src/yucode/tools/executor.py` | 对冻结工具视图执行调用并传递审批上下文。 |
| 修改 | `src/yucode/prompting.py` | 注入 Skill 提示状态、建议工具与缓存键。 |
| 修改 | `src/yucode/agent.py` | 每轮冻结 Skill 快照、清除状态和隔离执行接入。 |
| 修改 | `src/yucode/commands/models.py` | 命令目录与 Skill 控制器协议。 |
| 修改 | `src/yucode/commands/registry.py` | 兼容静态命令目录公共接口。 |
| 修改 | `src/yucode/commands/dispatcher.py` | 未激活 `skill:` 命令的中文引导。 |
| 修改 | `src/yucode/commands/builtins.py` | `/skill`、`/clear` 和会话切换清除激活状态。 |
| 修改 | `src/yucode/commands/__init__.py` | 导出新的命令目录类型。 |
| 修改 | `src/yucode/tui/app.py` | MCP 后启动校验、动态补全、独立执行与权限展示。 |
| 修改 | `src/yucode/cli.py` | 装配 Skill 运行时、动态命令目录和 Provider 工厂。 |
| 修改 | `pyproject.toml` | 将内置 Markdown 能力包包含进 wheel。 |
| 修改 | `README.md` | 文档化 Skill 格式、目录、命令、热更新和 URL 安装。 |
| 新建 | `tests/test_skills_loader.py` | 格式、目录、覆盖、诊断和路径边界测试。 |
| 新建 | `tests/test_skills_runtime.py` | 激活、启动白名单校验、热更新和快照测试。 |
| 新建 | `tests/test_skills_tools.py` | 工具视图、脚本、资源、LoadSkill/InstallSkill 测试。 |
| 新建 | `tests/test_skills_install.py` | URL 下载、zip 安全和原子安装测试。 |
| 新建 | `tests/test_skills_prompt.py` | 初始摘要、激活指令、建议工具与缓存键测试。 |
| 新建 | `tests/test_skills_execution.py` | 历史选择、fork 隔离、模型选择和摘要测试。 |
| 新建 | `tests/test_skills_commands.py` | `/skill`、动态命令、清除和会话行为测试。 |
| 修改 | `tests/test_tools.py` | 工具视图、执行上下文和旧工具行为回归。 |
| 修改 | `tests/test_prompting.py` | Skill 模块提示顺序与缓存键回归。 |
| 修改 | `tests/test_agent.py` | 每轮快照、白名单与系统工具后重建回归。 |
| 修改 | `tests/test_commands.py` | 静态命令与动态命令目录兼容回归。 |
| 修改 | `tests/test_cli.py` | MCP 后白名单启动失败和内置资源装配测试。 |
| 修改 | `tests/test_tui.py` | 启动失败、Tab 与独立执行界面回归。 |

## T1: 定义 Skill 领域模型

**文件：** `src/yucode/skills/__init__.py`、`src/yucode/skills/models.py`、`tests/test_skills_loader.py`

**依赖：** 无

**步骤：**
1. 建立 `skills` 包及不可变数据结构：来源层级、执行模式、历史范围、工具清单、定义、诊断、激活项和快照。
2. 定义 `$ARGUMENTS` 的替换规则和 Skill 名称校验规则，拒绝空名称、保留命令前缀冲突字符与不安全名称。
3. 编写模型级测试，覆盖三种历史范围、合法/非法名称、空参数替换和不可变快照。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py -q`，期望模型校验测试全部通过。

## T2: 解析单文件 Markdown Skill

**文件：** `src/yucode/skills/loader.py`、`tests/test_skills_loader.py`

**依赖：** T1

**步骤：**
1. 用安全 YAML 解析顶层 `*.md` 的 frontmatter 与正文，提取必填字段和可选模型字段。
2. 校验 `allowedTools`、模式和历史范围的字段类型与取值，正文为空或 frontmatter 损坏时生成单项中文诊断。
3. 解析成功时保留入口路径和内容指纹；失败时不抛出整体发现异常。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py -q`，期望合法单文件被发现，缺字段、坏 YAML 与空正文仅产生对应警告。

## T3: 解析目录能力包与安全资源边界

**文件：** `src/yucode/skills/loader.py`、`tests/test_skills_loader.py`

**依赖：** T2

**步骤：**
1. 支持一级目录中的 `SKILL.md` 作为入口，并将可选 `prompt.md` 追加到 SOP。
2. 识别可选 `tool.json` 与 `references/`，拒绝入口、脚本或资源经绝对路径、父目录或符号链接越出包根。
3. 确保目录中辅助 Markdown、脚本和资源不会被误发现为额外 Skill。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py -q`，期望目录包只有一个定义，补充 prompt 生效，越界包被跳过且其他包仍可用。

## T4: 实现三层发现、覆盖与热更新指纹

**文件：** `src/yucode/skills/loader.py`、`tests/test_skills_loader.py`

**依赖：** T2、T3

**步骤：**
1. 实现项目、用户、内置三层入口路径，并允许测试注入各层根目录。
2. 按内置→用户→项目的顺序合并同名定义，只保留最高优先级项；同层重复名称以诊断跳过。
3. 为文件和目录包计算稳定指纹，使修改、新增、删除和覆盖均可被后续刷新识别。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py -q`，期望覆盖顺序、回退和同层重复诊断均符合预期。

## T5: 定义目录专属工具清单

**文件：** `src/yucode/skills/loader.py`、`src/yucode/skills/models.py`、`tests/test_skills_loader.py`

**依赖：** T1、T3

**步骤：**
1. 解析 `tool.json` 的单项和列表形式，要求名称、说明、输入 schema、安全级别和命令数组齐全。
2. 校验 schema 为对象、命令非空、脚本参数只解析到包内文件，保存为 `SkillToolManifest`。
3. 覆盖工具名重复、与同包工具冲突、无效安全级别及脚本缺失等失败路径。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py -q`，期望有效工具清单被保留，所有无效项令该包被跳过并给出中文原因。

## T6: 抽取全局工具目录和冻结工具视图协议

**文件：** `src/yucode/tools/base.py`、`src/yucode/tools/registry.py`、`tests/test_tools.py`

**依赖：** T1

**步骤：**
1. 定义全局工具目录、只读/普通工具视图和每次调用的执行上下文协议，同时保留旧工具只依赖工作目录的用法。
2. 让 `ToolRegistry` 暴露全量工具定义及名称查询，仍保持内置和 MCP 的原子注册、冲突拒绝行为。
3. 增加工具视图测试：可见定义、名称解析、只读筛选和全局目录不被视图修改。

**验证：** 运行 `uv run pytest tests/test_tools.py -q`，期望现有六个内置工具测试仍通过，新增视图断言通过。

## T7: 让执行器按工具视图执行

**文件：** `src/yucode/tools/executor.py`、`src/yucode/tools/base.py`、`tests/test_tools.py`

**依赖：** T6

**步骤：**
1. 调整执行器的单次与批量接口，显式接收本轮冻结工具视图和包含审批回调、授权信息的执行上下文。
2. 保持权限判断、只读并发批次、取消和错误包装逻辑不变。
3. 覆盖被视图隐藏工具返回未知工具、可见工具继续获得原有审批回调的行为。

**验证：** 运行 `uv run pytest tests/test_tools.py -q`，期望旧权限/批处理测试与新增隐藏工具测试均通过。

## T8: 实现脚本专属工具与资源读取工具

**文件：** `src/yucode/skills/tools.py`、`tests/test_skills_tools.py`

**依赖：** T5、T6、T7

**步骤：**
1. 将 `SkillToolManifest` 适配为无 shell 子进程工具：输入 JSON、输出 JSON、取消、超时和错误转换均返回结构化中文结果。
2. 实现只读资源工具，限制相对路径在指定已激活包的 `references/` 内，并限制文本类型与大小。
3. 编写临时脚本、无效 JSON、超时、取消、资源越界、二进制资源和正常资源读取测试。

**验证：** 运行 `uv run pytest tests/test_skills_tools.py -q`，期望脚本与资源成功路径、所有安全失败路径均有可识别错误码。

## T9: 实现 URL 下载与原子安装

**文件：** `src/yucode/skills/install.py`、`tests/test_skills_install.py`

**依赖：** T2、T3、T5

**步骤：**
1. 使用现有 HTTP 依赖下载用户授权的 `http/https` URL，并限制响应大小、重定向次数和临时文件数量。
2. 支持单 Markdown 与 zip 目录包；解压时拒绝绝对路径、父目录、符号链接和超限条目。
3. 在用户层临时目录中调用同一解析器完整校验，通过后原子提交；同名用户 Skill 已存在时拒绝覆盖，任意失败均清理临时目录。

**验证：** 运行 `uv run pytest tests/test_skills_install.py -q`，期望 Markdown/zip 安装成功，下载失败、zip slip、损坏包和重名安装均不留下可发现半成品。

## T10: 实现 Skill 运行时启动校验与激活

**文件：** `src/yucode/skills/runtime.py`、`tests/test_skills_runtime.py`

**依赖：** T4、T5、T6

**步骤：**
1. 实现运行时初始化：发现目录、保留损坏项诊断，并在全局工具目录已就绪后验证每个有效 `allowedTools` 和专属工具名称。
2. 有效 Skill 引用未知工具时抛出携带 Skill/工具名的启动错误；损坏 Skill 不参与严格校验。
3. 实现加载、重复加载替换参数、激活列表与清空状态，失败加载必须保持已有激活项不变。

**验证：** 运行 `uv run pytest tests/test_skills_runtime.py -q`，期望启动失败与容错路径区分明确，激活/重复加载/清空行为通过。

## T11: 实现热更新和最后有效快照保留

**文件：** `src/yucode/skills/runtime.py`、`tests/test_skills_runtime.py`

**依赖：** T4、T10

**步骤：**
1. 在刷新时比较目录指纹，处理新增、修改、删除及三层覆盖变化。
2. 已激活 Skill 暂时变为损坏内容时保留最后有效定义并仅报告一次诊断；重新有效后切换到新定义。
3. 已激活 Skill 删除后回退到低层有效定义，或在无替代定义时撤销激活。

**验证：** 运行 `uv run pytest tests/test_skills_runtime.py -q`，期望刷新无需重建运行时，更新、保留、回退和撤销行为均通过。

## T12: 构造白名单工具视图与系统工具保留

**文件：** `src/yucode/skills/tools.py`、`src/yucode/skills/runtime.py`、`tests/test_skills_tools.py`

**依赖：** T7、T8、T10

**步骤：**
1. 未激活时构造包含原有普通工具和系统工具的视图；激活后构造所有白名单并集、激活私有工具、资源工具和系统工具的冻结视图。
2. 对计划模式应用既有只读筛选，保持 `LoadSkill` 可用，令 `InstallSkill` 继续受计划模式与权限约束。
3. 覆盖多个 Skill 白名单并集、未列工具隐藏、私有工具出现/撤销及系统加载工具始终出现。

**验证：** 运行 `uv run pytest tests/test_skills_tools.py -q`，期望每种激活组合只暴露规定工具，工具目录全局内容未被删除。

## T13: 构造 Skill 提示状态并接入系统提示

**文件：** `src/yucode/skills/prompt.py`、`src/yucode/prompting.py`、`tests/test_skills_prompt.py`、`tests/test_prompting.py`

**依赖：** T10、T12

**步骤：**
1. 从运行时快照产生“可用名称和一句说明”“激活完整 SOP 与参数”“建议工具”三种提示状态。
2. 扩展 `SystemPromptBuilder`：无激活时只注入目录摘要和加载引导；激活 SOP 紧跟基础系统约束，并进入稳定提示与缓存键。
3. 提供克隆/注入接口供隔离 Agent 使用，保持无 Skill 时既有自定义指令、记忆和 Provider 请求序列化不变。

**验证：** 运行 `uv run pytest tests/test_skills_prompt.py tests/test_prompting.py -q`，期望不泄露未激活正文，激活 SOP 排序正确，工具或 SOP 变化会改变缓存键。

## T14: 让主 Agent 每轮使用同一 Skill 快照

**文件：** `src/yucode/agent.py`、`tests/test_agent.py`

**依赖：** T7、T10、T12、T13

**步骤：**
1. 在 Agent 请求开始及每次模型迭代前刷新 Skill，冻结同一 `SkillSnapshot` 供上下文准备、提示构建和执行器使用。
2. 系统工具成功加载 Skill 后，仅从下一迭代采用更新后的 SOP 和工具视图；本轮不混用新旧定义。
3. 将新建会话和成功恢复会话接到公开的清除激活接口；恢复失败保持原激活状态。

**验证：** 运行 `uv run pytest tests/test_agent.py -q`，期望普通 Agent 回归通过，新增测试证明白名单收窄与下一轮重建正确。

## T15: 实现隔离历史选择与独立 Agent 创建

**文件：** `src/yucode/skills/execution.py`、`tests/test_skills_execution.py`

**依赖：** T12、T13、T14

**步骤：**
1. 将主历史按完整用户发起单元分组，实现 `none`、最近 N 轮和 `all` 的复制函数。
2. 使用无 recorder 的 `Conversation`、冻结 Skill 提示源和工具视图创建子 Agent；不传入主会话管理器或记忆更新器。
3. 验证子 Agent 获得基础项目约束与被调用 SOP，而不会读取主运行时稍后的激活变更。

**验证：** 运行 `uv run pytest tests/test_skills_execution.py -q`，期望三种历史范围正确，子运行的历史追加不改变主历史或存档记录器。

## T16: 实现独立模型选择与摘要回流

**文件：** `src/yucode/skills/execution.py`、`tests/test_skills_execution.py`

**依赖：** T15

**步骤：**
1. 注入 Provider factory；Skill 声明模型时只替换子 Agent 模型，不声明时使用主模型。
2. 聚合子 Agent 的完成状态、最终文本、工具成功/失败和可见目标，输出固定中文摘要。
3. 将取消、Provider/模型创建失败和工具失败都转为一条失败或取消摘要，不静默回退其他模型。

**验证：** 运行 `uv run pytest tests/test_skills_execution.py -q`，期望指定模型被实际传给 factory，成功/失败/取消各只生成一条符合字段要求的摘要。

## T17: 实现 LoadSkill 与 InstallSkill 系统工具

**文件：** `src/yucode/skills/tools.py`、`tests/test_skills_tools.py`

**依赖：** T9、T12、T16

**步骤：**
1. 实现 `LoadSkill` schema（名称、可选参数），成功加载 inline Skill 后返回状态；fork Skill 调用隔离执行器并只返回其摘要。
2. 实现副作用 `InstallSkill` schema（URL），将每次调用的授权/审批上下文传给安装流程并在成功后刷新目录。
3. 覆盖不存在/损坏 Skill 不改变状态、fork 结果不携带中间消息、安装失败保留旧目录和两个系统工具不受白名单影响。

**验证：** 运行 `uv run pytest tests/test_skills_tools.py tests/test_skills_install.py -q`，期望系统工具在允许视图中可调用，所有状态边界测试通过。

## T18: 实现动态命令目录

**文件：** `src/yucode/skills/commands.py`、`src/yucode/commands/models.py`、`src/yucode/commands/registry.py`、`src/yucode/commands/__init__.py`、`tests/test_skills_commands.py`、`tests/test_commands.py`

**依赖：** T10、T11、T16

**步骤：**
1. 为既有静态注册表抽出 `get`、`visible`、`complete` 的公共命令目录协议。
2. 实现叠加目录：固定 `/skill` 加当前激活 Skill 的 `skill:<名称>`，使帮助、解析和 Tab 查询同一动态数据源。
3. 为未激活但格式正确的 `skill:` 输入提供中文加载引导，不让它走普通聊天；确认 `/skill:review` 与内置 `/review` 同时可查找。

**验证：** 运行 `uv run pytest tests/test_skills_commands.py tests/test_commands.py -q`，期望动态出现/撤销、补全、帮助、冲突隔离和静态命令回归全部通过。

## T19: 接入 `/skill`、`/clear` 与会话命令

**文件：** `src/yucode/commands/builtins.py`、`src/yucode/commands/dispatcher.py`、`src/yucode/commands/models.py`、`tests/test_skills_commands.py`、`tests/test_commands.py`

**依赖：** T14、T18

**步骤：**
1. 在 `CommandContext` 提供 Skill 运行时和执行控制器；实现 `/skill` 的激活列表输出和无项提示。
2. 将动态 inline 命令交给正常主对话生成，将动态 fork 命令交给隔离执行控制器并仅记录/展示摘要。
3. 调整 `/clear`、`/session new` 和成功 `/session resume` 清除激活状态，保留 `/clear` 的既有聊天历史语义。

**验证：** 运行 `uv run pytest tests/test_skills_commands.py tests/test_commands.py -q`，期望列表字段完整、两种命令模式正确，清除和恢复边界符合 Spec。

## T20: 接入 Textual 启动、补全与独立执行展示

**文件：** `src/yucode/tui/app.py`、`tests/test_tui.py`

**依赖：** T17、T18、T19

**步骤：**
1. MCP 工具注册结束后初始化 Skill 运行时；严格白名单错误时保持输入禁用、展示中文错误、关闭 MCP 并退出。
2. 让 Tab 和提交均查询动态命令目录，刷新后能立刻显示/隐藏 `skill:` 候选。
3. 实现独立执行的忙碌文本、完成摘要展示和复用的权限卡片，确保隔离中间工具活动不渲染到主聊天区。

**验证：** 运行 `uv run pytest tests/test_tui.py -q`，期望启动失败不接受输入、动态 Tab 正确、fork 只显示摘要且权限请求仍可完成。

## T21: 在 CLI 装配 Skill 与 Provider factory

**文件：** `src/yucode/cli.py`、`tests/test_cli.py`

**依赖：** T10、T16、T18、T20

**步骤：**
1. 在 CLI 创建全局工具目录后构造 SkillRuntime、Provider factory 和动态命令目录，并注入 Agent/ChatApp。
2. 保持 MCP 在 TUI 启动阶段连接后再进行严格 Skill 工具校验，避免未知 MCP 名称提前误报。
3. 扩展 CLI 测试，覆盖内置 Skill 可被装配、MCP 可用时的白名单通过，以及 MCP 后未知工具导致无交互启动失败。

**验证：** 运行 `uv run pytest tests/test_cli.py -q`，期望既有 Provider/会话装配断言与新增 Skill 启动边界均通过。

## T22: 添加内置样板并确保打包资源可用

**文件：** `src/yucode/skills/builtin/commit/SKILL.md`、`src/yucode/skills/builtin/review/SKILL.md`、`src/yucode/skills/builtin/test/SKILL.md`、`pyproject.toml`、`tests/test_skills_loader.py`

**依赖：** T2、T5、T21

**步骤：**
1. 编写三个带中文说明、`$ARGUMENTS`、正确模式和最小工具白名单的内置 Skill。
2. 让 `commit` 为 inline，`review` 与 `test` 为 fork，且三个名字可按三层优先级被覆盖。
3. 配置 wheel 包含 Markdown 资源，并在测试中从安装/源码资源路径发现三个定义。

**验证：** 运行 `uv run pytest tests/test_skills_loader.py tests/test_cli.py -q`，期望三个内置 Skill 可发现且定义满足模式、工具与覆盖规则。

## T23: 更新使用文档

**文件：** `README.md`

**依赖：** T19、T21、T22

**步骤：**
1. 说明三层目录、单文件/目录包布局、frontmatter 字段、`$ARGUMENTS`、`tool.json` JSON 协议和 `references/` 用法。
2. 说明两阶段加载、白名单、inline/fork、`/skill`、`/skill:<名称>`、`/clear`、热更新与三个样板。
3. 说明 `InstallSkill` 仅接受用户明确提供或授权的直接 URL、支持格式、校验失败行为及不提供的市场/版本能力。

**验证：** 运行 `rg -n "Skill|allowedTools|/skill|InstallSkill|\$ARGUMENTS" README.md`，期望上述主题均有用户可执行的中文说明。

## T24: 运行 Skill 模块与回归测试

**文件：** 本章新增及修改的测试文件

**依赖：** T1—T23

**步骤：**
1. 先运行所有 `tests/test_skills_*.py`，修复 Skill 解析、运行时、脚本工具、安装、提示、fork 与命令集成失败。
2. 再运行受影响的原测试：工具、提示、Agent、命令、CLI 和 TUI；修复兼容性问题，特别关注 MCP、权限、计划模式和会话存档。
3. 最后运行完整 pytest 套件，记录实际测试数和失败项；没有全部通过前不得进行端到端验收。

**验证：** 依次运行 `uv run pytest tests/test_skills_*.py -q`、`uv run pytest tests/test_tools.py tests/test_prompting.py tests/test_agent.py tests/test_commands.py tests/test_cli.py tests/test_tui.py -q`、`uv run pytest -q`，期望三次均以退出码 0 完成。

## T25: 在 tmux 中完成端到端验收

**文件：** `doc/ch14/checklist.md`（验收记录）

**依赖：** T24、已批准的 `checklist.md`

**步骤：**
1. 在 tmux 中启动真实 YuCode，确认能看见内置 Skill 摘要并发起真实对话。
2. 按 AC15 完成目录 Skill 加载、带参数短命令、`/skill` 列表、白名单并集、内置 `/skill:test`、热更新和 `/clear` 的真实流程；观察工具调用、回复和摘要。
3. 对照 checklist 逐项记录命令、预期、实际与通过/失败；若失败，先修复并从对应自动化测试和端到端步骤重新验证。

**验证：** tmux 会话中每个 checklist 项均有实际可观察记录，且端到端流程以真实模型与工具结果通过，不能用模拟 Provider 代替。

## 执行顺序

```text
T1 → T2 → T3 → T4
T1、T3 → T5
T1 → T6 → T7
T4、T5、T6 → T10 → T11
T5、T6、T7 → T8
T2、T3、T5 → T9
T10、T11、T7、T8 → T12 → T13 → T14 → T15 → T16
T9、T12、T16 → T17
T10、T11、T16 → T18 → T19
T17、T18、T19 → T20 → T21 → T22 → T23 → T24 → T25
```

T5、T6 可在 T1 后并行推进；T9 可在 T5 前的入口解析完成后推进。合并前以图中的 T12、T17 和 T18 作为同步点，避免工具协议、运行时与命令层对未稳定接口产生耦合。
