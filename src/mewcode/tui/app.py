"""MewCode 的完整聊天 Textual 应用。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message as TextualMessage

from mewcode.config import ProviderConfig
from mewcode.conversation import Conversation, TurnResult
from mewcode.providers.base import Cancellation, Provider, StreamEvent, Usage
from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.registry import ToolRegistry
from mewcode.tui.widgets import (
    AssistantMessage,
    ChatStatus,
    CommandConfirmation,
    Composer,
    ErrorMessage,
    ToolActivity,
    UserMessage,
    WelcomePanel,
)


class StreamChunk(TextualMessage):
    """由后台生成线程投递给 UI 线程的流片段。"""

    def __init__(self, event: StreamEvent) -> None:
        super().__init__()
        self.event = event


class GenerationFinished(TextualMessage):
    """由后台生成线程投递的单轮结束结果。"""

    def __init__(self, result: TurnResult) -> None:
        super().__init__()
        self.result = result


class ToolResultReady(TextualMessage):
    """后台线程通知 UI 显示已完成工具的摘要。"""

    def __init__(self, result: ToolResult) -> None:
        super().__init__()
        self.result = result


class CommandApprovalRequested(TextualMessage):
    """后台线程请求 UI 向用户展示命令确认框。"""

    def __init__(self, pending: "PendingApproval") -> None:
        super().__init__()
        self.pending = pending


class PendingApproval:
    """在后台工具线程与 TUI 线程之间安全传递用户决定。"""

    def __init__(self, call: ToolCall) -> None:
        self.call = call
        self._event = Event()
        self._approved = False

    def resolve(self, approved: bool) -> None:
        self._approved = approved
        self._event.set()

    def wait(self, cancellation: Cancellation) -> bool:
        while not cancellation.is_cancelled:
            if self._event.wait(0.05):
                return self._approved
        return False


@dataclass
class TokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens


class ChatApp(App[None]):
    """单会话、单生成 Worker 的完整聊天界面。"""

    CSS_PATH = "app.tcss"
    BINDINGS = [("ctrl+c", "cancel_generation", "停止生成")]

    def __init__(
        self, provider: Provider, config: ProviderConfig, registry: ToolRegistry | None = None
    ) -> None:
        super().__init__()
        self._conversation = Conversation(provider, registry)
        self._config = config
        self._totals = TokenTotals()
        self._cancellation: Cancellation | None = None
        self._assistant_message: AssistantMessage | None = None
        self._generating = False
        self._has_started_chat = False
        self._pending_approval: PendingApproval | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            WelcomePanel(self._config.protocol, self._config.model), id="chat-view"
        )
        yield Composer(
            placeholder="输入消息…  Enter 发送 · Shift+Enter 换行", id="prompt", soft_wrap=True
        )
        yield ChatStatus(self._config.protocol, self._config.model)

    def on_mount(self) -> None:
        self.query_one(Composer).focus()
        self._refresh_status("准备就绪")

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        if self._generating:
            return
        prompt = event.composer.text.strip()
        if not prompt:
            return
        event.composer.clear()
        if prompt.lower() in {"/exit", "/quit"}:
            self.exit()
            return

        chat = self.query_one("#chat-view", VerticalScroll)
        if not self._has_started_chat:
            self.query_one(WelcomePanel).remove()
            self._has_started_chat = True
        user_message = UserMessage(prompt)
        assistant_message = AssistantMessage()
        chat.mount(user_message, assistant_message)
        self._assistant_message = assistant_message
        self._generating = True
        self._cancellation = Cancellation()
        event.composer.disabled = True
        self._refresh_status("正在生成 · Ctrl+C 停止")
        self._scroll_to_latest(chat)
        self.generate(prompt, self._cancellation)

    @work(thread=True, group="generation", exclusive=True, exit_on_error=False)
    def generate(self, prompt: str, cancellation: Cancellation) -> None:
        """在后台读取同步 Provider 流，避免阻塞 Textual 事件循环。"""
        result = self._conversation.run_turn(
            prompt,
            lambda event: self.post_message(StreamChunk(event)),
            cancellation,
            lambda result: self.post_message(ToolResultReady(result)),
            lambda call: self._request_command_approval(call, cancellation),
        )
        self.post_message(GenerationFinished(result))

    async def on_stream_chunk(self, message: StreamChunk) -> None:
        assistant = self._assistant_message
        if assistant is None:
            return
        chat = self.query_one("#chat-view", VerticalScroll)
        if message.event.kind == "text":
            await assistant.append_text(message.event.content)
        elif message.event.kind == "thinking":
            await assistant.append_thinking(message.event.content)
        else:
            return
        self._scroll_to_latest(chat)

    async def on_tool_result_ready(self, message: ToolResultReady) -> None:
        """将工具摘要插入两段助手流式内容之间。"""
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._assistant_message is not None:
            self._assistant_message.finish()
        next_assistant = AssistantMessage()
        await chat.mount(ToolActivity(message.result), next_assistant)
        self._assistant_message = next_assistant
        self._scroll_to_latest(chat)

    def on_command_approval_requested(self, message: CommandApprovalRequested) -> None:
        self._pending_approval = message.pending
        command = message.pending.call.arguments.get("command", "")
        self.push_screen(
            CommandConfirmation(command if isinstance(command, str) else "<无效命令>"),
            lambda approved: message.pending.resolve(bool(approved)),
        )

    def on_generation_finished(self, message: GenerationFinished) -> None:
        result = message.result
        self._totals.add(result.usage)
        if self._assistant_message is not None:
            self._assistant_message.finish()
        self._generating = False
        self._cancellation = None
        self._assistant_message = None
        prompt = self.query_one("#prompt", Composer)
        prompt.disabled = False
        prompt.focus()

        if result.error:
            chat = self.query_one("#chat-view", VerticalScroll)
            chat.mount(ErrorMessage(result.error))
            self._scroll_to_latest(chat)
        self._refresh_status("准备就绪")

    def action_cancel_generation(self) -> None:
        """仅在生成中响应 Ctrl+C，取消活动 HTTP 流。"""
        if self._generating and self._cancellation is not None:
            self._cancellation.cancel()
            if self._pending_approval is not None:
                self._pending_approval.resolve(False)
                self._pending_approval = None

    def _request_command_approval(self, call: ToolCall, cancellation: Cancellation) -> bool:
        pending = PendingApproval(call)
        self.post_message(CommandApprovalRequested(pending))
        return pending.wait(cancellation)

    def _refresh_status(self, state: str) -> None:
        self.query_one(ChatStatus).set_values(
            state,
            len(self._conversation.messages),
            self._totals.input_tokens,
            self._totals.output_tokens,
        )

    def _scroll_to_latest(self, chat: VerticalScroll) -> None:
        """待新内容完成布局后滚到底部，保证流式输出始终可见。"""
        self.call_after_refresh(chat.scroll_end, animate=False)
