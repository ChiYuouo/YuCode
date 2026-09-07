"""Claude Code 风格聊天 TUI 的可复用显示组件。"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Markdown, Static, TextArea

from mewcode.tools.base import ToolResult


class Composer(TextArea):
    """支持 Enter 发送、Shift+Enter 换行的多行输入框。"""

    class Submitted(Message):
        """用户请求发送当前编辑内容。"""

        def __init__(self, composer: Composer) -> None:
            super().__init__()
            self.composer = composer

        @property
        def control(self) -> Composer:
            """与 Textual 输入事件保持一致的控件别名。"""
            return self.composer

    async def _on_key(self, event: events.Key) -> None:
        """拦截提交键，其余编辑行为沿用 TextArea。"""
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self))
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class WelcomePanel(Static):
    """空会话时展示的品牌、环境和使用提示。"""

    _CAT = " /\\_/\\\\\n( o.o )\n > ^ <"

    def __init__(self, provider: str, model: str) -> None:
        content = Text(justify="center")
        content.append(self._CAT, style="bold #63d8ef")
        content.append("\n\nMewCode", style="bold #f4f7f7")
        content.append("\n你的终端 AI 助手", style="#9ba6aa")
        content.append(f"\n\n目录  {Path.cwd()}", style="#b8c4c7")
        content.append(f"\n模型  {provider} / {model}", style="#b8c4c7")
        super().__init__(content, id="welcome-panel")


class ChatStatus(Static):
    """输入框下方的紧凑会话状态行。"""

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(id="status-bar", markup=False)
        self._provider = provider
        self._model = model
        self.set_values("准备就绪", 0, 0, 0)

    def set_values(self, state: str, messages: int, input_tokens: int, output_tokens: int) -> None:
        self.update(
            f"● {state}  ·  {self._provider} / {self._model}"
            f"  ·  M:{messages}  ·  I:{input_tokens} O:{output_tokens}"
        )


class UserMessage(Static):
    """以终端提示符样式展示的一条用户消息。"""

    def __init__(self, content: str) -> None:
        message = Text("› ", style="bold #63d8ef")
        message.append(content, style="#edf4f5")
        super().__init__(message, classes="message user-message")


class ThinkingBox(Collapsible):
    """默认折叠、无卡片边框的 Claude 思考内容。"""

    def __init__(self) -> None:
        super().__init__(title="▸ 思考过程", collapsed=True, classes="thinking-box")
        self.display = False
        self._content = ""

    def compose(self) -> ComposeResult:
        yield Markdown("")

    def append(self, content: str):
        self.display = True
        self.title = "▸ 正在思考…"
        self._content += content
        return self.query_one(Markdown).update(self._content)

    def finish(self) -> None:
        """结束思考阶段后，将标题改为可回看的思考记录。"""
        if self.display:
            self.title = "▸ 思考过程"


class GenerationIndicator(Static):
    """在尚未收到正式回答时提示用户请求仍在处理中。"""

    def __init__(self) -> None:
        super().__init__("◌ 正在思考…", classes="generation-indicator", markup=False)


class AssistantMessage(Vertical):
    """一条可增量更新、无气泡边框的 Markdown 回复。"""

    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")
        self._text = ""

    def compose(self) -> ComposeResult:
        yield GenerationIndicator()
        yield Markdown("")
        # 放在正式回答后面，自动跟随到底部时仍能看见思考入口。
        yield ThinkingBox()

    def append_text(self, content: str):
        self._text += content
        self.query_one(GenerationIndicator).display = False
        self.query_one(ThinkingBox).finish()
        return self.query_one(Markdown).update(self._text)

    def append_thinking(self, content: str):
        return self.query_one(ThinkingBox).append(content)

    def finish(self) -> None:
        """隐藏活动提示，并固定 thinking 区域的完成状态。"""
        self.query_one(GenerationIndicator).display = False
        self.query_one(ThinkingBox).finish()


class ErrorMessage(Static):
    """以醒目但不打断会话的样式显示可恢复错误。"""

    def __init__(self, content: str) -> None:
        message = Text("! 请求失败：", style="bold #ff8170")
        message.append(content, style="#ffb4a9")
        super().__init__(message, classes="message error-message")


class ToolActivity(Static):
    """仅显示工具调用的可读摘要，不泄露完整工具输出。"""

    def __init__(self, result: ToolResult) -> None:
        marker = "✓" if result.success else "!"
        color = "#7ee787" if result.success else "#ffb4a9"
        text = Text(f"{marker} 工具 {result.name}：", style=f"bold {color}")
        if result.target:
            text.append(f"{result.target} · ", style="#9ba6aa")
        text.append(result.summary, style="#c8d2d5")
        super().__init__(text, classes="message tool-activity")


class CommandConfirmation(ModalScreen[bool]):
    """命令真正启动前展示的明确确认弹窗。"""

    def __init__(self, command: str) -> None:
        super().__init__()
        self._command = command

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("模型请求执行以下 PowerShell 命令：", classes="confirm-title"),
            Static(self._command, classes="confirm-command", markup=False),
            Horizontal(
                Button("执行", variant="success", id="approve-command"),
                Button("拒绝", variant="error", id="reject-command"),
                classes="confirm-actions",
            ),
            id="command-confirmation",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve-command")
