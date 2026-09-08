# YuCode Claude Code 风格 TUI Plan

## 架构

Textual `ChatApp` 继续持有 `Conversation`、Token 累计值、取消控制器和单一后台 Worker。Provider 与会话层不变；同步 Provider 流由线程 Worker 消费，`StreamChunk` 再投递回 UI 线程渲染。

## 组件与状态

- `WelcomePanel`：空会话唯一的内容，基于 `Path.cwd()` 渲染小猫和环境信息，不提供固定示例提示。
- `VerticalScroll`：始终作为聊天容器；首轮提交时移除欢迎页，再挂载用户与助手组件。
- `Composer(TextArea)`：拦截 `Enter` 投递提交消息，拦截 `Shift+Enter` 插入换行，其他编辑行为沿用 Textual。
- `UserMessage`、`AssistantMessage`、`ErrorMessage`：均为低边框/无卡片呈现；助手消息先显示“正在思考…”活动提示，Markdown 后方放置 `ThinkingBox`，使思考入口随最新回答保持可见。
- `ChatStatus`：位于输入框下方，显示准备就绪或生成中状态和累计指标。

## 交互与数据流

```text
Composer.Submitted
  → 首轮移除 WelcomePanel，挂载用户提示符和空助手消息，禁用 Composer
  → 后台 Worker 调用 Conversation.run_turn
  → StreamChunk 更新 Markdown 或折叠 thinking 内容
  → GenerationFinished 累计 Token、恢复 Composer、刷新状态行
```

- 输入 `/exit`、`/quit` 时退出，不创建会话轮次。
- `Ctrl+C` 仅在生成时调用 `Cancellation.cancel()`；会话层保留已有部分正式回复的语义。
- 每次流片段或新消息挂载后，通过 `call_after_refresh` 等布局完成再无动画滚到底部；生成期间始终跟随最新输出。

## 视觉约定

- 深灰背景 `#17191a`，输入区为略亮的 `#202425`，焦点与用户提示符使用青蓝 `#63d8ef`。
- 用留白、缩进和细输入边框区分区域，不再使用消息气泡或顶部色块状态栏。
