"""Claude Code 风格聊天 TUI 的可复用显示组件。"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from yucode.permissions import ApprovalChoice, PermissionRequest
from yucode.tools.base import ToolCall, ToolResult
from yucode.providers.base import CacheUsage


SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


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
        if self.app.handle_permission_key(event):
            return
        if self.app.handle_composer_key(self, event):
            return
        if event.key == "shift+tab":
            event.stop()
            event.prevent_default()
            self.app.action_cycle_permission_mode()
            return
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

    _WORDMARK = (
        "██╗   ██╗ ██╗   ██╗  ██████╗  ██████╗  ██████╗  ███████╗\n"
        "╚██╗ ██╔╝ ██║   ██║ ██╔════╝ ██╔═══██╗ ██╔══██╗ ██╔════╝\n"
        " ╚████╔╝  ██║   ██║ ██║      ██║   ██║ ██║  ██║ █████╗\n"
        "  ╚██╔╝   ██║   ██║ ██║      ██║   ██║ ██║  ██║ ██╔══╝\n"
        "   ██║    ╚██████╔╝ ╚██████╗ ╚██████╔╝ ██████╔╝ ███████╗\n"
        "   ╚═╝     ╚═════╝   ╚═════╝  ╚═════╝  ╚═════╝  ╚══════╝"
    )

    def __init__(self, provider: str, model: str) -> None:
        content = Text(justify="center")
        content.append(self._WORDMARK, style="bold #f4a261")
        content.append("\n◆ ─────────── ◇ ─────────── ◆", style="#63d8ef")
        content.append(f"\n目录  {Path.cwd()}", style="#b8c4c7")
        content.append(f"\n模型  {provider} / {model}", style="#b8c4c7")
        super().__init__(content, id="welcome-panel")


class ChatStatus(Static):
    """输入框下方的紧凑会话状态行。"""

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(id="status-bar", markup=False)
        self._provider = provider
        self._model = model
        self._mode = "Do"
        self.set_values("准备就绪", 0, 0, 0, CacheUsage())

    def set_mode(self, mode: str) -> None:
        """更新会话级 Agent 模式提示。"""
        self._mode = mode

    def set_values(
        self,
        state: str,
        messages: int,
        input_tokens: int,
        output_tokens: int,
        cache: CacheUsage,
    ) -> None:
        cache_text = (
            f"  ·  缓存读 {cache.read_input_tokens} / 写 {cache.write_input_tokens}"
            if cache.available
            else "  ·  缓存数据不可用"
        )
        self.update(
            f"● {state}  ·  {self._provider} / {self._model}"
            f"  ·  模式:{self._mode}  ·  M:{messages}  ·  I:{input_tokens} O:{output_tokens}{cache_text}"
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
        super().__init__(
            Markdown("", id="thinking-content"),
            title="正在思考…",
            collapsed=True,
            classes="thinking-box",
        )
        self.display = False
        self._content = ""

    def append(self, content: str):
        self.start_thinking()
        self._content += content
        return self.query_one("#thinking-content", Markdown).update(self._content)

    def start_thinking(self) -> None:
        """显示折叠思考区，并等待活动时钟提供旋转帧。"""
        self.display = True
        self.title = "◐ 正在思考…"

    def advance(self, frame: str) -> None:
        if self.display:
            self.title = f"{frame} 正在思考…"

    def finish(self) -> None:
        """结束思考阶段后，将标题改为可回看的思考记录。"""
        if self.display:
            self.title = "已思考"


class ModeMenu(OptionList):
    """输入 `/` 时展示的会话模式选择菜单。"""

    _OPTIONS = {
        "plan": "Plan  ·  只读分析与计划",
        "do": "Do    ·  完整工具执行",
    }

    def __init__(self) -> None:
        super().__init__(id="mode-menu", classes="mode-menu", compact=True)
        self.display = False

    def show_matches(self, query: str) -> bool:
        """根据 `/` 后的输入筛选 Plan 和 Do。"""
        normalized = query.strip().lower()
        options = [
            Option(label, id=name)
            for name, label in self._OPTIONS.items()
            if not normalized or name.startswith(normalized)
        ]
        if [option.id for option in self.options] != [option.id for option in options]:
            self.set_options(options)
            self.highlighted = 0 if options else None
        self.display = bool(options)
        return bool(options)

    def hide(self) -> None:
        self.display = False


class GenerationIndicator(Static):
    """显示可由请求级时钟驱动的模型等待提示。"""

    def __init__(self) -> None:
        super().__init__(classes="generation-indicator", markup=False)
        self.display = False
        self._label = ""

    def start(self, label: str) -> None:
        self.display = True
        self._label = label
        self.advance(SPINNER_FRAMES[0])

    def advance(self, frame: str) -> None:
        if self.display:
            self.update(f"{frame} {self._label}")

    def stop(self) -> None:
        self.display = False


class AssistantMessage(Vertical):
    """一条可增量更新、无气泡边框的 Markdown 回复。"""

    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")
        self._text = ""

    def compose(self) -> ComposeResult:
        yield ThinkingBox()
        yield GenerationIndicator()
        yield Markdown("", id="assistant-content")

    def append_text(self, content: str):
        self._text += content
        self.query_one(GenerationIndicator).stop()
        self.query_one(ThinkingBox).finish()
        return self.query_one("#assistant-content", Markdown).update(self._text)

    def append_thinking(self, content: str):
        self.query_one(GenerationIndicator).stop()
        return self.query_one(ThinkingBox).append(content)

    def start_waiting(self, label: str) -> None:
        self.query_one(GenerationIndicator).start(label)

    def advance_activity(self, frame: str) -> None:
        self.query_one(GenerationIndicator).advance(frame)
        self.query_one(ThinkingBox).advance(frame)

    def finish(self) -> None:
        """隐藏活动提示，并固定 thinking 区域的完成状态。"""
        self.query_one(GenerationIndicator).stop()
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
        super().__init__(classes="message tool-activity")
        self.show_result(result)

    def show_result(self, result: ToolResult) -> None:
        marker = "✓" if result.success else "!"
        color = "#7ee787" if result.success else "#ffb4a9"
        text = Text(f"{marker} 工具 {result.name}：", style=f"bold {color}")
        if result.target:
            text.append(f"{result.target} · ", style="#9ba6aa")
        text.append(result.summary, style="#c8d2d5")
        self.update(text)


class PendingToolActivity(ToolActivity):
    """工具请求到达后、结果返回前显示的动态状态行。"""

    def __init__(self, call: ToolCall) -> None:
        Static.__init__(self, classes="message tool-activity tool-pending")
        self.call_id = call.id
        self.tool_name = call.name
        self._active = True
        self._waiting_permission = False
        self.advance(SPINNER_FRAMES[0])

    def advance(self, frame: str) -> None:
        if self._active and not self._waiting_permission:
            self.update(f"{frame} 正在执行工具 {self.tool_name}…")

    def wait_for_permission(self) -> None:
        """明确表示工具仍未执行，正在等待用户决定。"""
        if self._active:
            self._waiting_permission = True
            self.update(f"? 工具 {self.tool_name}：正在等待权限确认，尚未执行")

    def resume_execution(self) -> None:
        """用户允许后恢复工具活动提示。"""
        if self._active:
            self._waiting_permission = False
            self.update(f"◐ 工具 {self.tool_name}：已确认，正在执行…")

    def finish(self, result: ToolResult) -> None:
        self._active = False
        self.remove_class("tool-pending")
        self.show_result(result)

    def stop(self) -> None:
        if self._active:
            self._active = False
            self.remove_class("tool-pending")
            self.update(f"! 工具 {self.tool_name}：已停止")


class InlinePermissionCard(Static):
    """显示在聊天流内、由键盘完成选择的权限确认卡片。"""

    can_focus = True
    _CHOICES = (
        (ApprovalChoice.ONCE, "仅本次允许"),
        (ApprovalChoice.SESSION, "本会话允许"),
        (ApprovalChoice.PERMANENT, "永久允许"),
        (ApprovalChoice.REJECT, "拒绝"),
    )

    class Selected(Message):
        """卡片完成一次明确选择。"""

        def __init__(self, card: InlinePermissionCard, choice: ApprovalChoice) -> None:
            super().__init__()
            self.card = card
            self.choice = choice

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__(classes="message permission-card", markup=False)
        self._request = request
        self.call_id = request.call.id
        self._highlighted = 0
        self._choice: ApprovalChoice | None = None
        self._result: ToolResult | None = None
        self._update_display()

    def handle_key(self, key: str) -> ApprovalChoice | None:
        """处理确认专用按键；未消费的按键交还给普通界面。"""
        if self._choice is not None or self._result is not None:
            return None
        if key in {"1", "2", "3", "4"}:
            self._highlighted = int(key) - 1
            return self._CHOICES[self._highlighted][0]
        if key == "up":
            self._highlighted = (self._highlighted - 1) % len(self._CHOICES)
            self._update_display()
            return None
        if key == "down":
            self._highlighted = (self._highlighted + 1) % len(self._CHOICES)
            self._update_display()
            return None
        if key == "enter":
            return self._CHOICES[self._highlighted][0]
        if key == "escape":
            return ApprovalChoice.REJECT
        return None

    def mark_selected(self, choice: ApprovalChoice) -> None:
        """原地显示用户已选择，工具仍等待执行结果。"""
        if self._choice is None:
            self._choice = choice
            self._update_display()

    def finish(self, result: ToolResult) -> None:
        """工具完成或被拒绝后原地显示最终结果。"""
        self._result = result
        self._update_display()

    def _on_key(self, event: events.Key) -> None:
        choice = self.handle_key(event.key)
        if choice is not None:
            event.stop()
            event.prevent_default()
            self.post_message(self.Selected(self, choice))
            return
        if event.key in {"up", "down"} and self._choice is None and self._result is None:
            event.stop()
            event.prevent_default()

    def _update_display(self) -> None:
        title = (
            "权限确认结果"
            if self._result is not None
            else "权限已确认（正在继续）"
            if self._choice is not None
            else "需要权限确认（操作尚未执行）"
        )
        content = Text(title, style="bold #f3c969")
        content.append(f"\n{self._request.summary}", style="#edf4f5")
        content.append(f"\n{self._request.impact}", style="#9ba6aa")
        if self._result is not None:
            marker = "✓" if self._result.success else "!"
            color = "#7ee787" if self._result.success else "#ffb4a9"
            content.append(f"\n{marker} {self._result.summary}", style=color)
        elif self._choice is not None:
            label = dict(self._CHOICES)[self._choice]
            content.append(f"\n已选择：{label}，正在继续…", style="#63d8ef")
        else:
            content.append("\n使用数字 1–4，或 ↑/↓ 后 Enter 选择；Esc 拒绝。", style="#778386")
            for index, (_, label) in enumerate(self._CHOICES, start=1):
                pointer = "›" if index - 1 == self._highlighted else " "
                color = "#63d8ef" if index - 1 == self._highlighted else "#c8d2d5"
                content.append(f"\n{pointer} {index}. {label}", style=color)
        self.update(content)
