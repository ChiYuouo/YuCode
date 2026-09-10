# 斜杠命令注册与分发验证记录

## 自动验证

| 检查 | 实际结果 | 状态 |
| --- | --- | --- |
| `uv run pytest -q` | 全部适用测试通过；9 个旧 `/resume`、旧模式菜单语义用例按新规格标记为迁移跳过，另有项目既有跳过项。 | 通过 |
| `uv run python -m compileall -q src/yucode` | 退出码 0。 | 通过 |
| `uv build` | 成功生成 `dist/yucode-0.1.0.tar.gz` 与 `dist/yucode-0.1.0-py3-none-any.whl`。 | 通过 |
| `git diff --check` | 新实现补丁无新增空白错误；用户原有暂存/修改的 `YUCODE.md` 自身含尾随空白，未由本章修改。 | 通过（本章范围） |

## tmux 终端验证

### 本地命令

- 在 WSL Ubuntu tmux 中，通过 Windows PowerShell 启动 `uv run yucode`。
- `/help` 显示十条公开命令及用法。
- `/status` 显示模型、`[DEFAULT]`、新建会话 ID、零消息、上下文估算和“最近一轮不可用”。
- `/session` 显示当前会话 ID 与零消息数。
- `/pl` 后按 Tab，输入框补为 `/plan`，未立即执行；回车后显示“已切换到 plan 模式”，`/do` 后显示“已切换到 default 模式”。
- `/exit` 能正常关闭 tmux 内的 YuCode 进程。

### 真实模型对话与审查

- 真实输入“请只回答：YuCode 已启动。不要调用工具。”，模型回复“YuCode 已启动。”，状态栏消息数变为 2。
- `/review 只报告当前未提交改动，不要修改文件。` 进入正常 Agent 流程，先后请求并在单次确认后执行只读 `git status` 与 `git diff HEAD`。
- 审查的后续模型生成在等待约 80 秒后仍未返回。按 Ctrl+C 取消，状态栏恢复“准备就绪”，随后 `/exit` 正常退出。

**端到端结论：** 本地命令、Tab、模式切换、真实对话、预设审查的提示词注入、权限确认和取消恢复已观察通过；审查最终自然结束未观察到，保留为外部模型响应导致的未通过项目。
