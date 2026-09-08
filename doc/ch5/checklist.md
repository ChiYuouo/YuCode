# YuCode 五层权限系统 Checklist

> 每项均通过测试、终端观察或 tmux 中的真实对话验证。完成后记录实际命令、观察结果和通过/失败状态；真实模型服务不可用时，明确标为未执行，不得将模拟结果记为端到端通过。

## 五层硬边界与规则

- [x] AC1 / 危险命令黑名单：针对大范围删除、格式化或分区、关机或重启、系统目录删除和破坏性 Git 清理分别构造调用；在四种权限模式、匹配 `allow` 规则和人工选择允许的条件下重复。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_tools.py -q -k "dangerous or blacklist"`；期望每个调用都返回 `dangerous_command`、真实工具未启动，且结果说明该保护不可配置放开。

- [ ] AC2 / 专用文件工具路径沙箱：对读取、写入、编辑、搜索与查找分别传入项目外绝对路径、`..`、项目外符号链接和合法项目内路径。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_permissions.py -q -k "path or workspace or symlink or glob"`；期望前三类被拒绝且没有项目外副作用，项目内路径成功；命令测试同时确认其工作目录为项目目录，但不把它当作操作系统级路径沙箱。

- [x] AC3 / 精确与 glob 规则：分别用命令参数、文件路径和不匹配样本配置精确/`*` glob 的 allow/deny 规则。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "exact or glob or match"`；期望仅目标工具和参数/路径受影响，每条匹配结果可观察到命中来源与规则效果。

- [x] AC4 / 四层优先级和同层拒绝优先：为同一调用依次在用户、项目、本地、会话规则中设置相反结果，再在同层设置同时匹配的 allow/deny。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "priority or source or layer or deny"`；期望依次由会话、本地、项目、用户的最高命中决定，同层始终拒绝。

- [x] AC9 / 坏配置安全失败：分别提供格式错误 YAML、无效动作、无效规则文本和不可解析路径。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_config.py -q -k "invalid or yaml or config or path"`；期望不发生默认放行，结果包含可操作错误说明，且更高优先级有效规则仍按优先级生效。

## 四档模式与人在回路

- [x] AC5 / 四档权限模式：对无匹配的文件写入、文件编辑和命令调用，分别以 `default`、`acceptEdits`、`plan`、`bypassPermissions` 执行；同时加入明确 allow 和 deny 样本。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_agent.py -q -k "mode or accept or plan or bypass"`；期望 default 请求确认，acceptEdits 自动允许文件写入/编辑但确认命令，plan 仅给只读工具，bypassPermissions 允许未命中副作用；显式 deny、黑名单与路径沙箱始终优先。

- [x] AC6 / 本次、会话、永久和拒绝：在需要确认的模式下，对同一未覆盖副作用调用依次选择四个选项，再测试同范围和不同范围的后续调用。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_tools.py -q -k "approval or once or session or permanent or rejected"`；期望本次允许不复用，会话规则仅命中同一工具和规范化目标，永久允许原子写入项目本地规则，拒绝不执行工具。

- [x] N3 / 规则作用域与 Git 忽略：检查用户、项目、本地和会话规则不会互相写入；选择永久允许后检查生成文件。验证：运行 `git check-ignore yucode.permissions.local.yaml`，并运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py -q -k "local or persistent or scope"`；期望项目本地规则被 Git 忽略且不写入用户或项目共享规则，项目共享示例仍可提交。

- [x] F7、N5 / 确认范围与脱敏：用包含模拟 token、密码或 Cookie 的命令和写入参数触发确认，并尝试把确认用于黑名单或项目外路径。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_permissions.py tests/test_tui.py -q -k "redact or summary or approval or dangerous or outside"`；期望弹窗和模型回灌不包含模拟敏感值，任何确认均不能改变黑名单、沙箱或其他调用的决定。

## 拒绝回灌与既有行为回归

- [x] AC7 / 拒绝后继续 Agent Loop：模拟模型先调用被拒绝工具、收到结构化结果后改用项目内路径、只读工具或经确认允许的替代调用。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tools.py -q -k "permission or denied or rejected or recovery"`；期望首次调用没有真实副作用，Conversation 含错误码、原因和下一步，Agent 发起下一轮且替代调用可完成。

- [x] AC8、F10、N6 / Shift+Tab 与状态同步：在空闲终端界面连续按四次 `Shift+Tab`，并在每种模式下发起只读、文件编辑和命令请求。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tui.py tests/test_agent.py -q -k "shift or mode or plan or status"`；期望状态区域按 default、acceptEdits、plan、bypassPermissions 循环显示，下一次调用立即按显示模式处理，plan 只暴露只读工具。

- [x] F11 / 既有工具流程回归：运行读取、先读后编辑、编辑后验证、命令确认、取消和多轮工具调用。验证：运行 `.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_policy.py tests/test_agent.py tests/test_tui.py -q`；期望只读调用不触发确认，批处理顺序、取消、工具回灌和验证流程保持正常。

## 构建与全量自动化

- [x] Python 源码可编译。验证：运行 `.venv\Scripts\python.exe -m compileall -q src`；期望退出码为 0 且无错误输出。

- [x] 全量自动化通过。验证：运行 `.venv\Scripts\python.exe -m pytest -q`；期望退出码为 0，且没有失败、错误或悬挂。

- [x] 项目未配置独立 lint 门禁。验证：检查 `pyproject.toml`；未发现 lint 配置，因此无需运行额外命令。

## tmux 端到端场景

> 以下操作仅在有效模型配置存在时执行。高危命令场景只验证系统的拒绝结果，不在系统外实际运行破坏性命令。

- [ ] 场景 1 / 默认模式确认与恢复：在 tmux 启动 YuCode，输入“在项目内创建 permission-e2e.txt，内容为 `ok`，然后读取确认”。当默认模式弹出确认时选择“仅本次允许”。验证：观察工具活动、确认弹窗、文件内容和最终回复；期望写入仅执行一次，随后读取验证，最终回复基于工具结果说明完成。

- [ ] 场景 2 / 会话与永久规则：在同一会话中再次对同一文件编辑，选择“本会话允许”；随后对另一个文件编辑，确认仍会出现。再对一个明确目标选择“永久允许”。验证：观察会话行为和 `yucode.permissions.local.yaml`，并运行 `git check-ignore yucode.permissions.local.yaml`；期望会话规则不扩大到其他目标，永久规则是精确 allow 且文件被忽略。

- [ ] 场景 3 / 四档切换：在输入框空闲时连续使用 Shift+Tab，分别停在 acceptEdits、plan、bypassPermissions。验证：在 acceptEdits 请求创建项目内临时文件、请求运行无害命令；在 plan 请求分析 README 并要求写文件；在 bypassPermissions 请求创建临时文件。期望状态栏和行为分别为编辑免确认/命令确认、只读拒绝写入、未命中写入免确认；结束后删除测试临时文件。

- [ ] 场景 4 / 黑名单与路径逃逸：要求模型尝试格式化磁盘或执行破坏性 Git 清理，并在另一轮请求读取项目外文件或项目外符号链接。验证：观察工具结果和文件系统；期望出现结构化拒绝，系统无命令执行、无项目外读取或写入，Agent 继续并给出安全替代方案或说明。

- [ ] 场景 5 / 真实多轮调整：输入“先尝试读取项目外文件；被拒绝后改为读取 README.md 并总结”。验证：观察至少两轮工具活动、工具结果和最终回复；期望首轮拒绝不终止会话，后续 `read_file` 成功，最终回复只基于 README 内容。

## tmux 执行步骤

1. 启动：运行 `wsl.exe tmux new-session -d -s yucode-ch5 "cd <项目根目录> && .venv/Scripts/python.exe -m yucode"`，随后运行 `wsl.exe tmux capture-pane -p -t yucode-ch5`；期望看到 YuCode 欢迎界面。
2. 用 `wsl.exe tmux send-keys -t yucode-ch5 "<场景输入>" Enter` 输入场景 1、2、4、5；每一步用 `wsl.exe tmux capture-pane -p -t yucode-ch5` 保存观察。
3. 在场景 3 使用 `wsl.exe tmux send-keys -t yucode-ch5 BTab` 发送 Shift+Tab；每次捕获状态区域，确认四档名称与预期顺序一致。需要点击确认按钮时，在 tmux 中使用对应键盘焦点操作，并记录选择。
4. 每个会修改文件的场景前后运行 `git status --short`，并在完成后仅删除本清单创建的明确临时文件；记录清理结果。
5. 若配置缺失、认证失败或网络不可达，保存终端输出并记录为端到端阻碍；离线自动化仍可单独判定，但不得替代真实端到端通过。

## 验收记录

- 编译：2026-09-08 运行 `.venv\Scripts\python.exe -m compileall -q src`，退出码为 0。
- 自动化测试：2026-09-08 运行 `.venv\Scripts\python.exe -m pytest -q -rA`，全部通过，2 个符号链接用例因当前 Windows 环境不允许创建符号链接而跳过。
- AC2 符号链接子场景：当前环境缺少创建符号链接的权限，两个真实符号链接用例被跳过；`..`、绝对路径和 glob 逃逸已自动验证通过。路径解析实现仍在执行前调用符号链接解析，需在允许创建链接的环境补跑该子项。
- tmux 基线读取：2026-09-08 在真实 Anthropic 会话输入“读取 README.md 的第一行，并只用一句话说明项目用途。”；观察到 `read_file README.md` 成功与基于工具结果的回复。
- tmux 模式切换与 Plan：2026-09-08 使用 Shift+Tab 观察状态栏从 Default 依次切到 AcceptEdits、Plan；在 Plan 输入 README 分析请求，观察到只读分析回复，未产生写入工具活动。
- tmux 场景 1～5：除上述基线读取与 Plan 场景外待执行；默认确认、会话/永久规则、bypassPermissions 和真实拒绝恢复仍应在后续具备时间时逐项执行。
