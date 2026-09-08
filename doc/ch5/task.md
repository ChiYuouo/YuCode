# YuCode 五层权限系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/yucode/permissions.py` | 权限枚举、规则层、黑名单、模式判断、会话授权与本地规则存储 |
| 修改 | `src/yucode/tools/filesystem.py` | 共享、符号链接安全的项目路径解析与 glob 输入校验 |
| 修改 | `src/yucode/tools/executor.py` | 真实工具执行前统一接入权限判断与通用确认 |
| 修改 | `src/yucode/config.py` | 解析 `permissions.mode` |
| 修改 | `src/yucode/cli.py` | 创建并注入会话级权限管理器 |
| 修改 | `src/yucode/policy.py` | 保留流程门禁，去除“拒绝即终止”的状态 |
| 修改 | `src/yucode/agent.py` | 回灌权限拒绝并继续 Agent Loop |
| 修改 | `src/yucode/tui/widgets.py` | 通用四选项权限确认弹窗 |
| 修改 | `src/yucode/tui/app.py` | 把弹窗选择作为通用权限回调返回 |
| 修改 | `tests/test_config.py` | 权限模式配置的单元测试 |
| 新建 | `tests/test_permissions.py` | 五层判断、规则解析、持久化与脱敏测试 |
| 修改 | `tests/test_tools.py` | 路径解析和执行器拒绝时不执行的集成测试 |
| 修改 | `tests/test_policy.py` | 既有流程门禁回归测试 |
| 修改 | `tests/test_agent.py` | 权限拒绝回灌后继续循环的测试 |
| 修改 | `tests/test_tui.py` | 四选项权限确认与取消测试 |
| 修改 | `yucode.yaml.example` | 权限模式使用示例 |
| 新建 | `yucode.permissions.yaml.example` | 项目共享规则格式示例 |
| 修改 | `.gitignore` | 忽略项目本地规则文件 |
| 新建 | `doc/ch5/checklist.md` | 规格验收与端到端验证清单 |

## T1: 统一专用文件工具的路径沙箱

**文件：** `src/yucode/tools/filesystem.py`、`tests/test_tools.py`

**依赖：** 无

**步骤：**
1. 抽出一个由所有路径型文件工具复用的解析函数：先解析真实项目根目录与候选路径中的已有符号链接，再确认候选结果属于项目根目录。
2. 让读取、写入、编辑和代码搜索的路径参数使用该函数；让文件查找在枚举前拒绝绝对 glob 与包含 `..` 路径段的 glob。
3. 保持现有普通项目内路径、UTF-8、原子写入、结果摘要和错误码行为不变。
4. 增加项目外绝对路径、`..`、指向项目外的符号链接、项目内路径和非法查找 glob 的断言。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py -q`；期望原有文件工具测试和新增逃逸测试全部通过，且没有项目外文件被读取、写入或枚举。

## T2: 实现规则模型、YAML 加载和优先级匹配

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** T1

**步骤：**
1. 定义权限模式、规则效果、规则来源、规则层和权限决定等不可变数据结构。
2. 解析 `工具名(模式)` 规则字符串和 YAML `rules` 列表，校验工具名、模式、动作与数据形状；区分精确匹配和 glob 匹配。
3. 固定加载用户全局、项目共享和项目本地规则文件；缺失文件作为空层，格式或读取错误作为该层的安全拒绝信息。
4. 实现会话、本地、项目、用户四层优先级，以及同层 `deny` 优先的确定性匹配。
5. 为精确/glob、各层覆盖、同层冲突、无效 YAML/规则、缺失文件和规范化文件路径匹配分别编写测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "rule or layer or yaml or glob"`；期望精确与 glob 匹配、优先级、同层拒绝优先及坏配置的保守拒绝断言全部通过。

## T3: 实现硬边界与权限模式决策

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** T1、T2

**步骤：**
1. 增加不可配置的跨 Shell 危险命令正则组，覆盖大范围删除、格式化/分区、关机/重启、系统目录删除和破坏性 Git 清理。
2. 在权限决定中固定执行黑名单、专用文件工具路径沙箱、规则匹配、未命中模式回退的顺序；任何前置拒绝不得被后续层覆盖。
3. 让未命中的只读调用继续允许；让未命中的副作用调用在 default、acceptEdits、plan、bypassPermissions 四档中分别按定义返回待确认、编辑允许/命令待确认、拒绝、允许。
4. 测试每个黑名单类别在 bypassPermissions 模式和匹配 `allow` 规则下仍拒绝，测试路径逃逸仍拒绝，测试四档模式与显式 allow/deny 的组合。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "dangerous or sandbox or mode or hard"`；期望危险命令与项目外文件访问始终被拒绝，且四档模式只影响未命中的副作用调用。

## T4: 实现一次、会话和永久授权

**文件：** `src/yucode/permissions.py`、`tests/test_permissions.py`

**依赖：** T2、T3

**步骤：**
1. 定义通用确认请求与四种用户选择，并生成经过脱敏的工具摘要和影响说明。
2. 实现本次允许仅放行当前调用；实现本会话允许为当前工具和规范化主参数追加精确会话规则。
3. 实现永久允许：只向项目本地规则文件原子追加精确 `allow`，成功后更新内存规则层；写入失败时拒绝当前调用。
4. 测试拒绝/取消、一次授权不复用、会话授权只作用于同范围调用、永久授权文件内容与读取后的命中、写入失败和确认摘要脱敏。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "approval or session or permanent or redact"`；期望四种选择的范围精确，永久文件只包含本地精确 allow，敏感模拟值不出现在请求摘要中。

## T5: 加入主配置和规则配置示例

**文件：** `src/yucode/config.py`、`tests/test_config.py`、`yucode.yaml.example`、`yucode.permissions.yaml.example`、`.gitignore`

**依赖：** T2

**步骤：**
1. 在应用配置中加入权限模式，缺省为 `default`，并明确拒绝非 `default/acceptEdits/plan/bypassPermissions` 值。
2. 更新主配置示例，说明权限模式的四个可选值及其确认行为，以及配置只决定启动模式。
3. 新建项目共享规则示例，展示规范工具名的精确和 glob 规则，不生成真实规则文件。
4. 将 `yucode.permissions.local.yaml` 加入 Git 忽略规则。
5. 测试默认、四种合法模式和非法模式的配置加载，并检查本地规则文件可被 Git 忽略。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_config.py -q`，随后运行 `git check-ignore yucode.permissions.local.yaml`；期望配置测试通过，第二个命令返回该本地规则文件的忽略匹配。

## T6: 将权限判断接入工具执行器

**文件：** `src/yucode/tools/executor.py`、`tests/test_tools.py`

**依赖：** T1、T2、T3、T4、T5

**步骤：**
1. 用通用权限确认回调替换仅命令使用的布尔确认回调，并让执行器持有或接收权限管理器。
2. 在既有未知工具、模式过滤、参数对象检查和流程门禁之后、真实工具调用之前执行权限决定；待确认时等待四选项回调后再决定是否调用工具。
3. 将所有拒绝转为带明确错误码、原因与下一步建议的 `ToolResult`，并保证拒绝调用不会进入工具实现。
4. 保持并发只读批次、顺序副作用屏障、取消和结果顺序不变。
5. 使用会记录是否被调用的假工具测试黑名单、沙箱、规则、严格模式与人工拒绝均不执行；测试 once/session/permanent 允许后恰好执行一次。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_permissions.py -q`；期望既有批处理与取消回归通过，所有权限拒绝均无真实工具副作用。

## T7: 让 Agent 在拒绝后继续并保留既有流程门禁

**文件：** `src/yucode/agent.py`、`src/yucode/policy.py`、`src/yucode/cli.py`、`tests/test_agent.py`、`tests/test_policy.py`

**依赖：** T5、T6

**步骤：**
1. 在 CLI 创建与当前项目和配置模式绑定的权限管理器，并注入同一会话的 Agent/执行器，确保本会话规则和当前模式跨多条用户消息可用。
2. 调整 Agent 的确认回调类型与结束逻辑：工具拒绝写入 Conversation 后继续下一轮，不以单次权限或流程拒绝产生提前停止原因。
3. 让 Agent 根据当前权限模式选择工具与提示：plan 使用既有只读工具/规划提示，其他三档使用完整工具集合；保持 `/plan` 和 `/do` 对应模式同步。
4. 保留每个用户任务独立的任务授权、先读后改和修改后验证追踪；保留取消、未知工具连续上限、Provider 异常与迭代上限行为。
5. 模拟“首次危险/未授权调用被拒绝，第二轮改用项目内允许路径或只读工具”的模型响应，验证第二轮执行并完成；测试四档的工具暴露，并更新旧的拒绝即停止测试为新的可恢复行为断言。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_policy.py -q`；期望 Agent 只因既有终止条件结束，权限拒绝结果可见于历史且替代调用可执行，plan 仅暴露只读工具。

## T8: 实现四选项权限确认界面

**文件：** `src/yucode/tui/widgets.py`、`src/yucode/tui/app.py`、`tests/test_tui.py`

**依赖：** T4、T6、T7

**步骤：**
1. 用通用权限确认弹窗替换命令专用弹窗，展示权限层给出的脱敏摘要和影响说明。
2. 添加仅本次、本会话、永久允许、拒绝四个可识别按钮，并将每个按钮映射为对应确认选择。
3. 绑定 Shift+Tab：在空闲时按 default、acceptEdits、plan、bypassPermissions 循环更新权限管理器；状态栏显示当前模式，生成期间不允许切换。
4. 让现有 `/plan` 与 `/do` 入口同步更新当前权限模式，避免快捷键与既有模式入口显示不一致。
5. 更新生成取消逻辑，使等待弹窗自动返回拒绝并恢复输入状态；保持工具活动行和普通错误显示行为。
6. 通过 Textual 测试分别点击四种选择，验证回调值；测试 Shift+Tab 循环和状态栏、/plan 与 /do 同步、取消生成与拒绝均不会卡住界面；保留既有工具活动和状态栏回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py -q`；期望四种按钮均可完成一次工具流程，Shift+Tab 循环四档并更新状态栏，取消或拒绝后界面回到可输入状态。

## T9: 执行全量自动化回归

**文件：** `tests/test_permissions.py`、`tests/test_config.py`、`tests/test_tools.py`、`tests/test_policy.py`、`tests/test_agent.py`、`tests/test_tui.py`

**依赖：** T1、T2、T3、T4、T5、T6、T7、T8

**步骤：**
1. 运行全量测试，记录任何失败对应的权限层或既有行为回归。
2. 修复与本章改动直接相关的测试或实现问题，重新运行失败测试直至通过。
3. 确认本地规则示例未被误生成为真实可提交规则，并确认没有测试在项目外留下文件。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q`；期望全部测试通过。

## 执行顺序

```text
T1 ─┬→ T2 ─┬→ T3 → T4 ─┬→ T6 → T7 → T8 → T9
    │      └→ T5 ───────┘
    └────────────────────→ T6
```

## 完成记录

- [x] T1：共享路径解析、`..`/绝对 glob 拒绝与文件工具回归已通过；当前 Windows 环境不允许测试进程创建符号链接，该用例如实跳过。
- [x] T2：YAML 规则、精确/glob 匹配、四层优先级与同层 deny 优先已通过。
- [x] T3：危险命令黑名单、专用文件路径沙箱和四档模式判断已通过。
- [x] T4：本次、会话、永久、拒绝四种确认结果和本地规则原子写入已通过。
- [x] T5：四档启动配置、规则示例与本地规则 Git 忽略已通过。
- [x] T6：执行器在真实工具启动前完成权限判断，拒绝无副作用且保留批处理/取消行为。
- [x] T7：拒绝结果回灌模型后可继续下一轮并恢复为安全调用，Plan 保持只读。
- [x] T8：四选项确认弹窗、Shift+Tab 循环与状态栏同步已通过。
- [x] T9：2026-09-08 运行编译和全量测试；自动化均通过，2 个符号链接用例因环境能力不足跳过。
