# Skill 能力包 Checklist

> 每一项均以可运行测试、终端输出或真实交互观察为准。执行时在方框内标注通过状态，并在失败项下记录实际结果与修复后的复验结果。

## Skill 定义、目录包与发现

- [ ] AC1：单文件和目录型 Skill 都能被发现并正确替换参数。验证：在临时项目分别创建带 frontmatter 正文的 `*.md` 与包含 `SKILL.md`、`prompt.md`、`tool.json`、`references/` 的目录包；运行 `uv run pytest tests/test_skills_loader.py -q`，期望二者各只显示名称和一句说明，带参数时 `$ARGUMENTS` 为原始参数，未带参数时为空，辅助文件不成为额外 Skill。

- [ ] AC2：三层优先级、同层重复和非法名称行为正确。验证：对项目、用户、内置三层提供同名 Skill 后执行加载测试，再依次移除高层定义；期望依次回退到用户和内置定义。同层重复或非法命名只产生中文跳过诊断，其他 Skill 仍可加载。运行 `uv run pytest tests/test_skills_loader.py -q`。

- [ ] AC3：单项解析失败不阻断其他能力。验证：分别制造坏 YAML、缺元信息、空正文、损坏工具描述和不存在脚本的目录包；运行 `uv run pytest tests/test_skills_loader.py -q`，期望每项都有原因明确的中文诊断，应用仍可发现、加载有效 Skill。

- [ ] AC4：有效 Skill 的未知工具白名单在进入交互前阻止启动。验证：分别设置不存在的普通工具和不存在的专属工具名称，模拟 MCP 已完成注册后启动应用；运行 `uv run pytest tests/test_skills_runtime.py tests/test_cli.py tests/test_tui.py -q`，期望错误包含 Skill 名和工具名、输入未开放。修正名称后应用可正常启动。

- [ ] 目录专属脚本与资源不会越出能力包边界。验证：执行 `uv run pytest tests/test_skills_loader.py tests/test_skills_tools.py -q`，期望脚本、入口、资源的绝对路径、`..`、符号链接越界、二进制或超大参考资料均被拒绝，正常包内资源可读取。

## 两阶段加载、提示与工具白名单

- [ ] AC5：初始模型上下文只含可用 Skill 摘要，加载后才出现完整 SOP。验证：使用记录 Provider 捕获首次请求和 `LoadSkill` 成功后的下一次请求，运行 `uv run pytest tests/test_skills_prompt.py tests/test_agent.py -q`；期望首次没有正文/私有工具，之后有替换参数后的完整 SOP。加载不存在或损坏 Skill 后，现有激活项不变化。

- [ ] AC6：多个激活 Skill 的上下文、白名单并集和系统加载工具正确。验证：激活两个白名单不同的 Skill 并记录每轮请求；运行 `uv run pytest tests/test_skills_runtime.py tests/test_skills_tools.py tests/test_skills_prompt.py -q`，期望两个 SOP 均清晰标识并位于高优先级位置，普通工具为并集，未列工具不可调用，`LoadSkill` 始终可见，原有权限测试仍通过。

- [ ] “建议工具”提示与真实可见工具保持一致。验证：为不同激活组合构造请求，运行 `uv run pytest tests/test_skills_prompt.py tests/test_skills_tools.py -q`；期望运行期提示只列当前白名单和专属工具，Provider 收到的工具列表与提示相符，改变 SOP 或工具集合会改变提示缓存键。

- [ ] 计划模式不成为 Skill 白名单的绕过通道。验证：在计划模式加载可读 Skill 和包含副作用工具的 Skill，运行 `uv run pytest tests/test_skills_tools.py tests/test_tools.py tests/test_permissions.py -q`；期望只读工具与 `LoadSkill` 的可见性符合规则，`InstallSkill` 和其他副作用工具仍不能借由 Skill 绕过计划模式或审批。

## 共享与独立执行

- [ ] AC7：共享模式完整留在主历史，独立模式只回流一条摘要。验证：用记录会话和模拟 Provider 分别执行 inline、fork Skill，运行 `uv run pytest tests/test_skills_execution.py tests/test_agent.py -q`；期望 inline 的用户调用、工具和回复可被后续主对话引用，fork 的中间消息/工具细节不在主历史，仅有包含状态、结果、副作用和下一步的一条摘要。失败与取消同样只回流一条摘要且主对话可继续。

- [ ] AC8：独立 Skill 的模型选择严格生效。验证：为 fork Skill 配置可用模型和不可用模型各执行一次，运行 `uv run pytest tests/test_skills_execution.py -q`；期望前者由 Provider factory 使用指定模型，后者只产生“模型不可用”的失败摘要，不静默改用主模型且不污染主历史。

- [ ] 独立执行历史范围与定义快照隔离。验证：准备多轮带工具历史后，分别以 `none`、最近 N 轮、`all` 运行 fork；运行 `uv run pytest tests/test_skills_execution.py -q`。期望子 Agent 接收的历史范围正确，子执行期间修改同一 Skill 文件不会改变该次执行，主会话存档不包含子消息。

- [ ] 独立执行仍复用既有权限确认。验证：让 fork Skill 请求一个需要确认的副作用工具，使用 TUI 测试批准和拒绝各一次；运行 `uv run pytest tests/test_tui.py tests/test_skills_execution.py -q`，期望出现原有权限卡片，批准/拒绝结果正确汇入唯一摘要。

## 短命令、热更新与会话边界

- [ ] AC9：`/skill`、动态短命令和现有 `/review` 可共存。验证：激活两个带不同参数的 Skill 后执行 `/skill`，再执行 `/skill:<名称> 参数内容`、未激活的 `/skill:<名称>`、`/skill:review` 和 `/review`；运行 `uv run pytest tests/test_skills_commands.py tests/test_commands.py -q`。期望列表字段齐全，动态命令按模式执行，未激活命令给加载引导且不进普通聊天，两个 review 命令互不覆盖。

- [ ] AC10：热更新即时反映，运行中的任务保持旧快照。验证：应用存活时新增、修改、删除和跨层覆盖 Skill，并在一项执行暂停时修改其源文件；运行 `uv run pytest tests/test_skills_runtime.py tests/test_skills_commands.py tests/test_tui.py -q`。期望下一次列表、加载和 Tab 使用新状态，正在执行项用原定义完成；无替代删除会撤销激活、专属工具和短命令。

- [ ] AC11：已激活 Skill 暂时损坏时保留最后有效定义。验证：激活 Skill 后把定义改为非法内容，再修复为新有效内容；运行 `uv run pytest tests/test_skills_runtime.py tests/test_skills_prompt.py -q`。期望损坏期间继续使用旧定义并只显示中文警告，修复后下一轮切换为新 SOP 和新工具范围。

- [ ] AC12：清空和会话切换清除激活状态，不删除 Skill 文件。验证：激活至少两个 Skill，依次执行 `/clear`、`/session new` 和成功的 `/session resume <ID>`；运行 `uv run pytest tests/test_skills_commands.py tests/test_commands.py -q`。期望完整 SOP、白名单收窄和 `/skill:` 补全均被清除，Skill 文件与 `/clear` 原聊天历史语义保持不变，恢复会话不自动激活旧 Skill；恢复失败保持当前激活项。

## 内置样板与 URL 安装

- [ ] AC13：内置 `commit`、`review`、`test` 可发现且遵循模式/权限。验证：在无覆盖定义时发现并分别执行三个样板；运行 `uv run pytest tests/test_skills_loader.py tests/test_skills_commands.py tests/test_cli.py -q`。期望 `commit` 为共享历史，`review` 与 `test` 只回流独立摘要，三者遵循现有工具权限，并可被项目或用户同名定义覆盖。

- [ ] AC14：`InstallSkill` 的直接 URL 安装安全、原子且无需重启。验证：使用本地 HTTP 测试服务提供有效 Markdown、有效 zip、损坏内容、zip slip、下载错误和未知白名单包；运行 `uv run pytest tests/test_skills_install.py tests/test_skills_tools.py -q`。期望有效包安装后立即可发现/加载，项目级同名仍优先；每种失败保留既有 Skill、没有半成品，重名用户安装拒绝覆盖，并经现有副作用授权。

- [ ] 内置能力包被实际包含在发行包中。验证：运行 `uv build`，在构建的 wheel 中检查三个 `SKILL.md`，并执行 `uv run pytest tests/test_skills_loader.py tests/test_cli.py -q`；期望构建成功且从资源路径可发现全部三个内置 Skill。

## 文档、构建与回归

- [ ] README 给出可操作的 Skill 使用说明。验证：运行 `rg -n "Skill|allowedTools|/skill|InstallSkill|\$ARGUMENTS" README.md`，期望结果覆盖三层目录、单文件/目录结构、frontmatter、`tool.json`、引用资源、两种模式、命令、热更新、URL 安装限制和内置样板。

- [ ] 修改后的 Python 代码可编译。验证：运行 `uv run python -m compileall -q src`，期望退出码为 0 且没有语法错误。

- [ ] Skill 自动化测试通过。验证：运行 `uv run pytest tests/test_skills_*.py -q`，期望退出码为 0，覆盖解析、运行时、工具、安装、提示、fork 与命令。

- [ ] 受影响现有模块回归通过。验证：运行 `uv run pytest tests/test_tools.py tests/test_prompting.py tests/test_agent.py tests/test_commands.py tests/test_cli.py tests/test_tui.py tests/test_permissions.py -q`，期望退出码为 0。

- [ ] 全部项目测试通过。验证：运行 `uv run pytest -q`，期望退出码为 0；记录通过数量，不接受跳过 Skill 相关失败或以局部测试替代全量测试。

- [ ] 工作区只包含本章预期改动且没有格式错误。验证：运行 `git diff --check` 和 `git status --short`，期望无空白错误，状态中的每个改动均能对应本章文件清单或验收记录。

## 端到端场景

- [ ] AC15：在 tmux 中完成真实 Skill 生命周期。验证：在 tmux 中启动配置了真实 Provider 的 YuCode，依次完成：
  1. 查看首次对话中的可用 Skill 摘要，并请求加载一个项目级目录 Skill。
  2. 输入 `/skill:<名称> 带有大小写和内部空白的参数`，观察它按声明模式执行；再输入 `/skill`，确认名称、模式和参数可见。
  3. 再加载一个白名单不同的 Skill，提出需要两类白名单工具的真实请求，观察模型只调用当前可见工具并在提示中获得建议工具。
  4. 执行 `/skill:test`，观察独立运行完成后主对话只有结果摘要，不显示其内部工具过程；必要的副作用确认仍由界面请求。
  5. 修改该项目级 Skill 的 SOP 或工具白名单后再次执行，确认无需重启即采用新定义。
  6. 执行 `/clear`，输入 `/skill` 并按 Tab 检查，确认没有激活项和 `/skill:` 候选；Skill 文件仍存在，普通聊天仍可继续。

  期望：每步都有真实模型回复、工具调用或可见状态变化；记录 tmux 会话名、输入、预期、实际、通过/失败。不能用模拟 Provider、手工构造历史或单元测试替代本场景。
