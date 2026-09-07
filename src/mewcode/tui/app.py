"""MewCode 的完整聊天 Textual 应用。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import OptionList, TextArea

from mewcode.agent import (
    Agent, AgentFinished, ProgressPhase, ProgressUpdated, RunMode, TextDelta,
    ThinkingDelta, ToolCallStarted, ToolResultReady, UsageUpdated,
)
from mewcode.cancellation import Cancellation
from mewcode.config import ProviderConfig
from mewcode.tools.base import ToolCall
from mewcode.tui.widgets import (
    AssistantMessage,
    ChatStatus,
    CommandConfirmation,
    Composer,
    ErrorMessage,
    ModeMenu,
    PendingToolActivity,
    SPINNER_FRAMES,
    ToolActivity,
    UserMessage,
    WelcomePanel,
)


@dataclass
class TokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens


class ChatApp(App[None]):
    """单会话、单异步 Agent Worker 的聊天界面。"""

    CSS_PATH = "app.tcss"
    BINDINGS = [("ctrl+c", "cancel_generation", "停止生成")]

    def __init__(self, agent: Agent, config: ProviderConfig) -> None:
        super().__init__()
        self._agent = agent
        self._config = config
        self._totals = TokenTotals()
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._cancellation: Cancellation | None = None
        self._assistant_message: AssistantMessage | None = None
        self._generating = False
        self._has_started_chat = False
        self._pending_approval: asyncio.Future[bool] | None = None
        self._mode = RunMode.FULL
        self._activity_timer: Timer | None = None
        self._activity_frame = 0
        self._pending_tools: dict[str, PendingToolActivity] = {}

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            WelcomePanel(self._config.protocol, self._config.model), id="chat-view"
        )
        yield ModeMenu()
        yield Composer(
            placeholder="输入消息…  Enter 发送 · Shift+Enter 换行", id="prompt", soft_wrap=True
        )
        yield ChatStatus(self._config.protocol, self._config.model)

    def on_mount(self) -> None:
        self.query_one(Composer).focus()
        self.query_one(ChatStatus).set_mode("Do")
        self._refresh_status("准备就绪")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """仅在输入首字符为 `/` 时展示模式菜单。"""
        if not isinstance(event.text_area, Composer):
            return
        self._update_mode_menu(event.text_area.text)

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        if self._generating:
            return
        raw_prompt = event.composer.text.strip()
        if not raw_prompt:
            return
        event.composer.clear()
        if raw_prompt.lower() in {"/exit", "/quit"}:
            self.exit()
            return

        if raw_prompt.startswith("/"):
            self._show_error("请选择 /plan 或 /do 切换模式后，再输入任务。")
            return
        mode, prompt = self._mode, raw_prompt

        chat = self.query_one("#chat-view", VerticalScroll)
        if not self._has_started_chat:
            self.query_one(WelcomePanel).remove()
            self._has_started_chat = True
        assistant_message = AssistantMessage()
        chat.mount(UserMessage(raw_prompt), assistant_message)
        self._assistant_message = assistant_message
        self._generating = True
        self._cancellation = Cancellation()
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._pending_tools = {}
        event.composer.disabled = True
        self._start_activity_clock()
        self._refresh_status("正在启动 Agent · Ctrl+C 停止")
        self._scroll_to_latest(chat)
        self.call_after_refresh(self.generate, prompt, mode, self._cancellation)

    def handle_composer_key(self, composer: Composer, event) -> bool:
        """在菜单显示期间由输入框接管导航键，避免失去输入焦点。"""
        if not self._is_mode_query(composer.text):
            return False
        menu = self.query_one(ModeMenu)
        if event.key == "down":
            event.stop()
            event.prevent_default()
            self._update_mode_menu(composer.text)
            if menu.display:
                menu.action_cursor_down()
            return True
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self._update_mode_menu(composer.text)
            if menu.display:
                menu.action_cursor_up()
            return True
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            composer.clear()
            menu.hide()
            return True
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._update_mode_menu(composer.text)
            if not self._select_highlighted_mode():
                self._show_error("请选择 /plan 或 /do。")
                composer.clear()
            return True
        return False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """接受鼠标选择的模式菜单项。"""
        if event.option_list.id != "mode-menu":
            return
        event.stop()
        if event.option_id is not None:
            self._activate_mode(event.option_id)

    def _update_mode_menu(self, text: str) -> None:
        menu = self.query_one(ModeMenu)
        if not self._is_mode_query(text):
            menu.hide()
            return
        query = text[1:].split(maxsplit=1)[0] if text[1:] else ""
        menu.show_matches(query)

    @staticmethod
    def _is_mode_query(text: str) -> bool:
        stripped = text.strip().lower()
        return stripped.startswith("/") and stripped not in {"/exit", "/quit"}

    def _select_highlighted_mode(self) -> bool:
        menu = self.query_one(ModeMenu)
        if not menu.display or menu.option_count == 0:
            return False
        index = menu.highlighted if menu.highlighted is not None else 0
        option = menu.get_option_at_index(index)
        if option.id is None:
            return False
        self._activate_mode(option.id)
        return True

    def _activate_mode(self, option_id: str) -> None:
        if option_id == "plan":
            self._mode = RunMode.PLAN
            label = "Plan"
        elif option_id == "do":
            self._mode = RunMode.FULL
            label = "Do"
        else:
            return
        self.query_one(ModeMenu).hide()
        composer = self.query_one(Composer)
        composer.clear()
        composer.focus()
        self.query_one(ChatStatus).set_mode(label)
        self._refresh_status("准备就绪")

    @work(group="generation", exclusive=True, exit_on_error=False)
    async def generate(self, prompt: str, mode: RunMode, cancellation: Cancellation) -> None:
        """异步消费 Agent 事件，界面不参与循环判断。"""
        async for event in self._agent.run(
            prompt, mode, cancellation, self._request_command_approval
        ):
            if isinstance(event, TextDelta):
                await self._append_text(event.content)
            elif isinstance(event, ThinkingDelta):
                await self._append_thinking(event.content)
            elif isinstance(event, ToolCallStarted):
                await self._show_tool_pending(event)
            elif isinstance(event, ToolResultReady):
                await self._show_tool_result(event)
            elif isinstance(event, UsageUpdated):
                self._active_input_tokens = event.total.input_tokens
                self._active_output_tokens = event.total.output_tokens
                self._refresh_status(f"第 {event.iteration} 轮 · 正在生成")
            elif isinstance(event, ProgressUpdated):
                if event.phase is ProgressPhase.MODEL and self._assistant_message is not None:
                    self._assistant_message.start_waiting("正在请求模型…")
                elif event.phase is ProgressPhase.TOOLS and self._assistant_message is not None:
                    self._assistant_message.finish()
                self._refresh_status(event.detail)
            elif isinstance(event, AgentFinished):
                self._finish_generation(event)

    async def _append_text(self, content: str) -> None:
        if self._assistant_message is None:
            return
        await self._assistant_message.append_text(content)
        self._scroll_to_latest(self.query_one("#chat-view", VerticalScroll))

    async def _append_thinking(self, content: str) -> None:
        if self._assistant_message is None:
            return
        await self._assistant_message.append_thinking(content)

    async def _show_tool_result(self, event: ToolResultReady) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._assistant_message is not None:
            self._assistant_message.finish()
        pending = self._pending_tools.pop(event.result.call_id, None)
        if pending is not None:
            pending.finish(event.result)
        else:
            await chat.mount(ToolActivity(event.result))
        next_assistant = AssistantMessage()
        await chat.mount(next_assistant)
        self._assistant_message = next_assistant
        self._scroll_to_latest(chat)

    async def _show_tool_pending(self, event: ToolCallStarted) -> None:
        """将工具请求立即显示为活动行，并等待结果原位更新。"""
        if event.call.id in self._pending_tools:
            return
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._assistant_message is not None:
            self._assistant_message.finish()
        pending = PendingToolActivity(event.call)
        self._pending_tools[event.call.id] = pending
        await chat.mount(pending)
        self._scroll_to_latest(chat)

    def _finish_generation(self, event: AgentFinished) -> None:
        self._stop_activity_clock()
        self._totals.add(event.usage)
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        if self._assistant_message is not None:
            self._assistant_message.finish()
        if event.error:
            self._show_error(event.error)
        self._generating = False
        self._cancellation = None
        self._assistant_message = None
        self._pending_approval = None
        prompt = self.query_one("#prompt", Composer)
        prompt.disabled = False
        prompt.focus()
        self._refresh_status("准备就绪")

    def _start_activity_clock(self) -> None:
        """每个请求只创建一个低频时钟，统一驱动所有活动状态。"""
        if self._activity_timer is not None:
            self._activity_timer.stop()
        self._activity_frame = 0
        self._activity_timer = self.set_interval(0.12, self._advance_activity_frame)

    def _advance_activity_frame(self) -> None:
        if not self._generating:
            return
        frame = SPINNER_FRAMES[self._activity_frame % len(SPINNER_FRAMES)]
        self._activity_frame += 1
        if self._assistant_message is not None:
            self._assistant_message.advance_activity(frame)
        for pending in self._pending_tools.values():
            pending.advance(frame)

    def _stop_activity_clock(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        if self._assistant_message is not None:
            self._assistant_message.finish()
        for pending in self._pending_tools.values():
            pending.stop()
        self._pending_tools.clear()

    def action_cancel_generation(self) -> None:
        if self._generating and self._cancellation is not None:
            self._cancellation.cancel()
            if self._pending_approval is not None and not self._pending_approval.done():
                self._pending_approval.set_result(False)
            if isinstance(self.screen, CommandConfirmation):
                self.screen.dismiss(False)

    async def _request_command_approval(self, call: ToolCall) -> bool:
        loop = asyncio.get_running_loop()
        pending: asyncio.Future[bool] = loop.create_future()
        self._pending_approval = pending
        command = call.arguments.get("command", "")

        def resolved(approved: bool | None) -> None:
            if not pending.done():
                pending.set_result(bool(approved))

        self.push_screen(
            CommandConfirmation(command if isinstance(command, str) else "<无效命令>"), resolved
        )
        try:
            return await pending
        finally:
            if self._pending_approval is pending:
                self._pending_approval = None

    def _show_error(self, content: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(ErrorMessage(content))
        self._scroll_to_latest(chat)

    def _refresh_status(self, state: str) -> None:
        self.query_one(ChatStatus).set_values(
            state,
            len(self._agent.conversation.messages),
            self._totals.input_tokens + self._active_input_tokens,
            self._totals.output_tokens + self._active_output_tokens,
        )

    def _scroll_to_latest(self, chat: VerticalScroll) -> None:
        self.call_after_refresh(chat.scroll_end, animate=False)
