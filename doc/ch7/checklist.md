# YuCode 品牌迁移与首页锦鲤 Checklist

> 每项均以实际运行、搜索或终端观察验证；完成后记录所用命令和实际结果。

## 品牌与入口

- [x] AC1 / 发布入口：运行 `uv run yucode`，观察应用启动或（在当前目录缺少配置时）错误信息只要求 `yucode.yaml`；运行 `uv run python -m yucode`，观察结果一致。
- [x] AC1 / 构建元数据：运行 `uv build`，观察 `dist/` 生成的制品名为 `yucode`，且 `pyproject.toml` 中存在 `yucode` 控制台命令。
- [x] AC2 / 新包可用、旧包已移除：运行 `uv run python -c "import yucode; print(yucode.__version__)"`，预期输出版本；再尝试导入旧包，预期导入失败。
- [x] AC2 / 默认配置：在隔离临时目录仅创建合法的 `yucode.yaml` 后运行新入口，预期可进入启动流程；仅创建同内容的旧配置后运行，预期提示找不到 `yucode.yaml`，不会读取旧文件。
- [x] AC2 / 权限路径：运行权限相关自动化测试，观察默认用户、项目和本地规则路径均为 `.yucode` 或 `yucode.permissions*`，不再生成或读取旧名称。

## 文本与欢迎页

- [x] AC3 / 项目文本统一：搜索旧产品标识，预期无匹配；检查 README，预期产品名、配置名和命令均为 YuCode/yucode。
- [x] AC3 / 无旧文件入口：列出受版本控制的文件并搜索旧名称，预期无输出；确认旧源码目录、旧配置示例与旧命令声明不存在。
- [x] AC4 / 锦鲤欢迎页：运行 `uv run pytest tests/test_tui.py -q`，预期通过；在测试渲染和真实启动中观察 YuCode 标题与完整小锦鲤字符画，不出现猫咪图案或 旧名称。

## 回归测试

- [x] AC5 / 配置、权限和 CLI：运行 `uv run pytest tests/test_config.py tests/test_permissions.py tests/test_cli.py -q`，预期全部通过。
- [x] AC5 / 全量自动化测试：运行 `uv run pytest -q`，预期全部通过。
- [x] AC5 / 代码质量：运行 `git diff --check`，预期无空白错误；运行 `uv run python -m compileall -q src/yucode`，预期无编译错误。

## 端到端场景

- [x] 场景 1 / 终端启动：首页：在 tmux 中从项目目录运行 `uv run python -m yucode`，捕获终端；预期显示 YuCode、小锦鲤、当前目录、模型与可用输入框，没有导入或配置错误。
- [x] 场景 2 / 真实对话与工具：在同一 tmux 会话输入“请读取 README.md 的第一行，并用一句中文说明项目名称。”；如出现权限确认，按 `1` 允许本次读取。观察到工具活动、基于读取结果的中文回复和恢复可用的输入框。

## 验收记录

| 项目 | 命令或操作 | 实际结果 | 状态 |
|---|---|---|---|
| 品牌与入口 | `uv build`、控制台入口启动 | 已生成 yucode wheel/source 包，入口显示 YuCode 锦鲤 | 通过 |
| 文本与欢迎页 | 旧标识搜索、TUI 测试 | 源码、测试和文档无旧标识；锦鲤渲染正常 | 通过 |
| 回归测试 | `uv run pytest -q`、编译检查 | 测试通过，2 项按环境跳过；编译成功 | 通过 |
| tmux 端到端 | 启动、读取 README 第一行 | 显示锦鲤；调用 read_file 后回复“项目名称是 YuCode。” | 通过 |
