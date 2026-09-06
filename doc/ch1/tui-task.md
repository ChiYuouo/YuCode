# MewCode Claude Code 风格 TUI Tasks

## T1：替换界面组件

**文件：** `src/mewcode/tui/widgets.py`

- [x] 实现带小猫字符画和环境信息、无固定示例提示的 `WelcomePanel`。
- [x] 实现 `Composer(TextArea)`，支持 `Enter` 发送与 `Shift+Enter` 换行。
- [x] 将用户、助手、错误和 thinking 组件改为低边框终端样式。
- [x] 在正式回答到达前显示“正在思考…”活动提示，并在结束时自动隐藏。
- [x] 将 thinking 折叠入口放在正式回答之后，避免自动滚动时被长回答推离视口。
- [x] 将状态组件改为输入框下方的紧凑状态行。

## T2：调整应用状态流转

**文件：** `src/mewcode/tui/app.py`

- [x] 启动时挂载欢迎页，首轮提交后切换到聊天记录。
- [x] 接入 `Composer.Submitted`，并保留流式 Worker、自动滚动、错误恢复和取消逻辑。
- [x] 保持 `/exit`、`/quit`、多轮历史与 Token 统计的原有语义。

## T3：更新主题与自动化测试

**文件：** `src/mewcode/tui/app.tcss`、`tests/test_tui.py`

- [x] 应用深灰/青蓝主题、无顶部栏消息布局和底部状态行。
- [x] 覆盖欢迎页、首轮切换、Enter、Shift+Enter、thinking、错误、取消与多轮历史。
- [x] 使用布局完成后的滚动回调，覆盖超长流式回复始终跟随最新内容。

## T4：验收

- [x] 运行全量 pytest 与构建。
- [x] 通过 tmux + winpty 完成真实 Claude 两轮对话和退出验证。
