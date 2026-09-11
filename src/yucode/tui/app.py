"""YuCode 的完整聊天 Textual 应用。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import OptionList, TextArea

from yucode.agent import (
    Agent, AgentFinished, ContextUpdated, ProgressPhase, ProgressUpdated, TextDelta,
    ThinkingDelta, ToolCallStarted, ToolResultReady, UsageUpdated, SessionRestoreFinished,
)
from yucode.cancellation import Cancellation
from yucode.commands import CommandDispatcher, InputKind, build_builtin_registry, parse_input
from yucode.commands.models import CommandContext, CommandStatus
from yucode.config import ProviderConfig
from yucode.mcp.manager import MCPManager
from yucode.permissions import ApprovalChoice, PermissionMode, PermissionRequest
from yucode.providers.base import CacheUsage, TextContent, ToolResultContent
from yucode.skills.runtime import SkillConfigurationError
from yucode.tui.widgets import (
    AssistantMessage, ContextActivity, ForkActivity,
    ChatStatus,
    Composer,
    ErrorMessage,
    InlinePermissionCard,
    ModeMenu, SessionPicker, SessionDeleteConfirm,
    NoticeMessage,
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
    cache_available: bool = False
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_available = self.cache_available or usage.cache.available
        self.cache_read_tokens += usage.cache.read_input_tokens
        self.cache_write_tokens += usage.cache.write_input_tokens

    def cache_usage(self) -> CacheUsage:
        return CacheUsage(self.cache_available, self.cache_read_tokens, self.cache_write_tokens)


class ChatApp(App[None]):
    """单会话、单异步 Agent Worker 的聊天界面。"""

    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("ctrl+c", "cancel_generation", "停止生成"),
        ("shift+tab", "cycle_permission_mode", "切换权限模式"),
    ]
    _PERMISSION_MODES = (
        PermissionMode.DEFAULT,
        PermissionMode.ACCEPT_EDITS,
        PermissionMode.PLAN,
        PermissionMode.BYPASS_PERMISSIONS,
    )

    def __init__(self, agent: Agent, config: ProviderConfig, mcp_manager: MCPManager | None = None, command_registry=None) -> None:
        super().__init__()
        self._agent = agent
        self._config = config
        self._mcp_manager = mcp_manager or getattr(agent, "mcp_manager", MCPManager())
        self._totals = TokenTotals()
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._active_cache = CacheUsage()
        self._cancellation: Cancellation | None = None
        self._assistant_message: AssistantMessage | None = None
        self._generating = False
        self._has_started_chat = False
        self._pending_approval: asyncio.Future[ApprovalChoice] | None = None
        self._active_permission_card: InlinePermissionCard | None = None
        self._permission_cards: dict[str, InlinePermissionCard] = {}
        self._activity_timer: Timer | None = None
        self._activity_frame = 0
        self._pending_tools: dict[str, PendingToolActivity] = {}
        self._fork_activity: ForkActivity | None = None
        self._sessions = getattr(agent, "session_manager", None)
        self._skills = getattr(agent, "skill_runtime", None)
        self._memory = getattr(agent, "_memory", None)
        self._startup_warnings = tuple(getattr(agent, "startup_warnings", ()))
        self._command_registry = command_registry or getattr(agent, "command_registry", None) or build_builtin_registry()
        self._last_turn_usage = None
        self._busy = False
        self._command_menu_active = False

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            WelcomePanel(self._config.protocol, self._config.model), id="chat-view"
        )
        yield ModeMenu()
        yield Composer(
            placeholder="输入消息…  Enter 发送 · Shift+Enter 换行 · Shift+Tab 切换权限", id="prompt", soft_wrap=True
        )
        yield ChatStatus(self._config.protocol, self._config.model)

    def on_mount(self) -> None:
        prompt = self.query_one(Composer)
        prompt.disabled = True
        self._show_permission_mode()
        self.set_interval(0.8, self._show_memory_diagnostics)
        self._refresh_status("正在加载 MCP 工具")
        self._load_mcp()

    @work(exclusive=True)
    async def _load_mcp(self) -> None:
        warnings = await self._mcp_manager.start(self._agent._registry)
        skills = getattr(self._agent, "skill_runtime", None)
        if skills is not None:
            try:
                warnings = (*warnings, *skills.initialize())
            except SkillConfigurationError as error:
                self._show_error(f"Skill 配置错误：{error}")
                await self._mcp_manager.close()
                self.exit()
                return
        self.query_one(WelcomePanel).set_mcp_status(
            self._mcp_manager.connected_count, self._mcp_manager.tool_count
        )
        for warning in warnings:
            if hasattr(warning, "server_name"):
                self._show_error(f"MCP Server {warning.server_name} 未加载：{warning.reason}")
            else:
                self._show_error(warning.message)
        for warning in self._startup_warnings:
            self._show_error(warning)
        prompt = self.query_one(Composer)
        prompt.disabled = False
        prompt.focus()
        self._refresh_status("准备就绪")

    @work(exclusive=True)
    async def _shutdown_mcp_and_exit(self) -> None:
        """先释放 MCP 子进程和连接，再结束终端会话。"""
        if self._memory is not None:
            await self._memory.wait_for_pending_updates()
        await self._mcp_manager.close()
        self.exit()


    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """旧模式菜单不再处理命令；命令在提交时统一分发。"""
        if not isinstance(event.text_area, Composer):
            return
        stripped_left = event.text_area.text.lstrip()
        if any(char.isspace() for char in stripped_left[1:]):
            self._command_menu_active = False
        if not self._command_menu_active:
            self.query_one(ModeMenu).hide()

    def on_key(self, event) -> None:
        """即使卡片获得焦点，也让确认按键优先完成等待中的权限请求。"""
        self.handle_permission_key(event)

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        if self._busy:
            return
        parsed = parse_input(event.composer.text)
        if parsed.kind is InputKind.EMPTY:
            return
        event.composer.clear()
        self._busy = True
        event.composer.disabled = True
        self._cancellation = Cancellation()
        self.handle_input(parsed)

    @work(group="generation", exclusive=True, exit_on_error=False)
    async def handle_input(self, parsed) -> None:
        """统一处理普通对话和已解析的斜杠命令。"""
        try:
            if parsed.kind is InputKind.CHAT:
                await self.send_user_message(parsed.text)
            else:
                context = CommandContext(self._command_registry, self, self._agent, self._sessions, self._skills)
                await CommandDispatcher(context).dispatch(parsed)
        finally:
            if self._generating:
                return
            self._busy = False
            self._cancellation = None
            prompt = self.query_one("#prompt", Composer)
            prompt.disabled = False
            prompt.focus()
            self._refresh_status("准备就绪")

    async def send_user_message(self, raw_prompt: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if not self._has_started_chat:
            self.query_one(WelcomePanel).remove()
            self._has_started_chat = True
        assistant_message = AssistantMessage()
        await chat.mount(UserMessage(raw_prompt), assistant_message)
        self._assistant_message = assistant_message
        self._generating = True
        if self._cancellation is None:
            self._cancellation = Cancellation()
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._active_cache = CacheUsage()
        self._pending_tools = {}
        self._start_activity_clock()
        self._refresh_status("正在启动 Agent · Ctrl+C 停止")
        self._scroll_to_latest(chat)
        await self._run_generation(raw_prompt, self._cancellation)

    def handle_composer_key(self, composer: Composer, event) -> bool:
        """Tab 仅补全命令名，其他按键保留 TextArea 的默认行为。"""
        if event.key == "tab":
            text = composer.text
            stripped_left = text.lstrip()
            if not stripped_left.startswith("/") or any(char.isspace() for char in stripped_left[1:]):
                return False
            matches = self._command_registry.complete(stripped_left[1:])
            event.stop()
            event.prevent_default()
            if len(matches) == 1:
                composer.text = f"/{matches[0].name} "
            elif len(matches) > 1:
                self._command_menu_active = True
                self.query_one(ModeMenu).show_commands(matches)
            return True
        menu = self.query_one(ModeMenu)
        if self._command_menu_active and menu.display:
            if event.key in {"up", "down"}:
                event.stop()
                event.prevent_default()
                if event.key == "up":
                    menu.action_cursor_up()
                else:
                    menu.action_cursor_down()
                return True
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self._command_menu_active = False
                menu.hide()
                return True
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                index = menu.highlighted if menu.highlighted is not None else 0
                option = menu.get_option_at_index(index)
                if option.id is not None:
                    composer.text = f"/{option.id} "
                self._command_menu_active = False
                menu.hide()
                return True
        return False
        # 以下旧菜单逻辑保留到后续迁移时删除。
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
        event.stop()
        if event.option_list.id == "mode-menu" and self._command_menu_active and event.option_id is not None:
            composer = self.query_one(Composer)
            composer.text = f"/{event.option_id} "
            composer.focus()
            self._command_menu_active = False
            self.query_one(ModeMenu).hide()
        elif event.option_list.id == "mode-menu" and event.option_id is not None:
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
        return stripped.startswith("/") and stripped not in {"/exit", "/quit", "/compact"}

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
            self._set_permission_mode(PermissionMode.PLAN)
        elif option_id == "do":
            self._agent.permissions.resume_do_mode()
            self._show_permission_mode()
        elif option_id == "resume":
            self._start_resume_selection()
            return
        else:
            return
        self.query_one(ModeMenu).hide()
        composer = self.query_one(Composer)
        composer.clear()
        composer.focus()
        self._refresh_status("准备就绪")

    def _start_resume_selection(self) -> None:
        """读取会话概要并显示选择列表。"""
        self.query_one(ModeMenu).hide()
        composer = self.query_one(Composer)
        composer.clear()
        if self._sessions is None:
            self._show_error("当前会话未启用历史恢复。")
            composer.focus()
            return
        summaries = self._sessions.list_sessions()
        if not summaries:
            self._show_error("当前项目没有可恢复的历史会话。")
            composer.focus()
            return
        composer.disabled = True
        self.push_screen(SessionPicker(summaries), self._on_resume_picker_closed)
        self._refresh_status("选择要恢复的历史会话 · Esc 取消")

    def _on_resume_picker_closed(self, session_id: str | None) -> None:
        """处理历史选择弹窗的确认或取消结果。"""
        composer = self.query_one(Composer)
        if session_id is None:
            composer.disabled = False
            composer.focus()
            self._refresh_status("准备就绪")
            return
        self._begin_restore(session_id)

    def _begin_restore(self, session_id: str) -> None:
        self._refresh_status("正在恢复历史会话")
        self.restore_session(session_id)

    @work(group="generation", exclusive=True, exit_on_error=False)
    async def restore_session(self, session_id: str) -> None:
        composer = self.query_one(Composer)
        try:
            recovered = self._sessions.recover(session_id)
        except Exception as error:
            self._show_error(f"无法恢复会话：{error}")
            composer.disabled = False
            composer.focus()
            self._refresh_status("准备就绪")
            return
        cancellation = Cancellation()
        success = False
        async for event in self._agent.restore_session(recovered, cancellation):
            if isinstance(event, ContextUpdated):
                self._show_context_result(event)
            elif isinstance(event, SessionRestoreFinished):
                success = event.success
                for warning in event.warnings:
                    self._show_error(warning)
                if not event.success:
                    self._show_error(event.detail)
        if success:
            self._sessions.activate_session(session_id)
            await self._render_recovered_history()
            self._show_notice("已恢复历史会话，可继续追问。")
        composer.disabled = False
        composer.focus()
        self._refresh_status("准备就绪")

    async def _render_recovered_history(self) -> None:
        """将恢复的有效历史补绘到聊天区，便于用户追溯。"""
        chat = self.query_one("#chat-view", VerticalScroll)
        if not self._has_started_chat:
            self.query_one(WelcomePanel).remove()
            self._has_started_chat = True
        for message in self._agent.conversation.messages:
            if message.role == "user":
                for block in message.blocks:
                    if isinstance(block, TextContent):
                        await chat.mount(UserMessage(block.text))
                    elif isinstance(block, ToolResultContent):
                        await chat.mount(ToolActivity(block.result))
            else:
                text = "\n".join(block.text for block in message.blocks if isinstance(block, TextContent))
                if text:
                    assistant = AssistantMessage()
                    await chat.mount(assistant)
                    await assistant.append_text(text)
                    assistant.finish()
        self._scroll_to_latest(chat)

    def _show_memory_diagnostics(self) -> None:
        if self._memory is None:
            return
        for message in self._memory.drain_diagnostics():
            self._show_error(message)

    def action_cycle_permission_mode(self) -> None:
        """在空闲时循环四档权限模式。"""
        if self._generating:
            return
        current = self._agent.permissions.mode
        index = self._PERMISSION_MODES.index(current)
        self._set_permission_mode(self._PERMISSION_MODES[(index + 1) % len(self._PERMISSION_MODES)])
        self._refresh_status("准备就绪")

    def _set_permission_mode(self, mode: PermissionMode) -> None:
        self._agent.permissions.set_mode(mode)
        self._show_permission_mode()

    def _show_permission_mode(self) -> None:
        labels = {
            PermissionMode.DEFAULT: "[DEFAULT]",
            PermissionMode.ACCEPT_EDITS: "[ACCEPT_EDITS]",
            PermissionMode.PLAN: "[PLAN]",
            PermissionMode.BYPASS_PERMISSIONS: "[BYPASS_PERMISSIONS]",
        }
        self.query_one(ChatStatus).set_mode(labels[self._agent.permissions.mode])

    @work(group="generation", exclusive=True, exit_on_error=False)
    async def generate(self, prompt: str, cancellation: Cancellation) -> None:
        """异步消费 Agent 事件，界面不参与循环判断。"""
        await self._run_generation(prompt, cancellation)

    async def _run_generation(self, prompt: str, cancellation: Cancellation) -> None:
        async for event in self._agent.run(prompt, cancellation, self._request_permission_approval):
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
                self._active_cache = event.total.cache
                self._refresh_status(f"第 {event.iteration} 轮 · 正在生成")
            elif isinstance(event, ProgressUpdated):
                if event.phase is ProgressPhase.MODEL and self._assistant_message is not None:
                    self._assistant_message.start_waiting("正在请求模型…")
                elif event.phase is ProgressPhase.TOOLS and self._assistant_message is not None:
                    self._assistant_message.finish()
                self._refresh_status(event.detail)
            elif isinstance(event, ContextUpdated):
                self._show_context_result(event)
            elif isinstance(event, AgentFinished):
                self._finish_generation(event)

    def _start_context_compaction(self) -> None:
        self._generating = True
        self._cancellation = Cancellation()
        prompt = self.query_one("#prompt", Composer)
        prompt.disabled = True
        self._refresh_status("正在压缩上下文")
        self.compact_context(self._cancellation)

    @work(group="generation", exclusive=True, exit_on_error=False)
    async def compact_context(self, cancellation: Cancellation) -> None:
        async for event in self._agent.compact(cancellation):
            if isinstance(event, ContextUpdated):
                self._show_context_result(event)
        self._stop_activity_clock()
        self._generating = False
        self._cancellation = None
        prompt = self.query_one("#prompt", Composer)
        prompt.disabled = False
        prompt.focus()
        self._refresh_status("准备就绪")

    def _show_context_result(self, event: ContextUpdated) -> None:
        result = event.result
        if result.action.value == "auto" and result.status == "unchanged":
            return
        failed = result.status in {"failed", "circuit_open"}
        self.query_one("#chat-view", VerticalScroll).mount(ContextActivity(_context_text(result), failed))
        self._scroll_to_latest(self.query_one("#chat-view", VerticalScroll))

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
        card = self._permission_cards.pop(event.result.call_id, None)
        if card is not None:
            card.finish(event.result)
            if self._active_permission_card is card:
                self._active_permission_card = None
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
        self._last_turn_usage = event.usage
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._active_cache = CacheUsage()
        if self._assistant_message is not None:
            self._assistant_message.finish()
        if event.error:
            self._show_error(event.error)
        self._generating = False
        self._cancellation = None
        self._assistant_message = None
        self._pending_approval = None
        self._active_permission_card = None
        self._permission_cards.clear()
        prompt = self.query_one("#prompt", Composer)
        prompt.disabled = False
        prompt.focus()
        self._refresh_status("准备就绪")

    async def show_message(self, text: str, *, error: bool = False) -> None:
        if error:
            self._show_error(text)
        else:
            self._show_notice(text)

    async def clear_chat(self) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        await chat.remove_children()
        self._has_started_chat = True

    async def replace_history(self) -> None:
        await self.clear_chat()
        await self._render_recovered_history()

    async def compact(self) -> None:
        cancellation = self._cancellation or Cancellation()
        self._generating = True
        try:
            async for event in self._agent.compact(cancellation):
                if isinstance(event, ContextUpdated):
                    self._show_context_result(event)
        finally:
            self._generating = False

    async def confirm_delete(self, session_id: str, detail: str) -> bool:
        """等待删除弹窗的明确选择，取消和 Esc 都返回否。"""
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[bool] = loop.create_future()

        def completed(value: bool | None) -> None:
            if not answer.done():
                answer.set_result(bool(value))

        self.push_screen(SessionDeleteConfirm(session_id, detail), completed)
        return await answer

    def set_mode(self, mode: PermissionMode) -> None:
        self._set_permission_mode(mode)

    def status(self) -> CommandStatus:
        return CommandStatus(
            self._config.protocol,
            self._config.model,
            self._agent.permissions.mode,
            self._sessions.active_session_id if self._sessions is not None else None,
            len(self._agent.conversation.messages),
            self._agent.estimated_context_tokens(),
            self._last_turn_usage,
        )

    def reset_usage(self) -> None:
        self._totals = TokenTotals()
        self._active_input_tokens = 0
        self._active_output_tokens = 0
        self._active_cache = CacheUsage()
        self._last_turn_usage = None

    def refresh_status(self, text: str = "准备就绪") -> None:
        self._refresh_status(text)

    async def request_exit(self) -> None:
        if self._memory is not None:
            await self._memory.wait_for_pending_updates()
        await self._mcp_manager.close()
        super().exit()

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
            self._resolve_active_permission(ApprovalChoice.REJECT)

    async def _request_permission_approval(self, request: PermissionRequest) -> ApprovalChoice:
        loop = asyncio.get_running_loop()
        pending: asyncio.Future[ApprovalChoice] = loop.create_future()
        self._pending_approval = pending
        chat = self.query_one("#chat-view", VerticalScroll)
        tool_activity = self._pending_tools.get(request.call.id)
        if tool_activity is not None:
            tool_activity.wait_for_permission()
        card = InlinePermissionCard(request)
        self._active_permission_card = card
        self._permission_cards[request.call.id] = card
        await chat.mount(card)
        card.focus()
        self._refresh_status("等待权限确认 · 选择 1–4 或 Esc")
        self._scroll_to_latest(chat)
        try:
            return await pending
        finally:
            if self._pending_approval is pending:
                self._pending_approval = None
            if self._active_permission_card is card:
                self._active_permission_card = None

    async def request_skill_permission(self, request: PermissionRequest) -> ApprovalChoice:
        """供隔离 Skill 复用当前界面的权限确认卡片。"""
        return await self._request_permission_approval(request)

    async def show_skill_progress(self, text: str) -> None:
        """隔离 Agent 展示阶段进度，不泄露其完整私有对话。"""
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._fork_activity is None:
            self._fork_activity = ForkActivity(text)
            await chat.mount(self._fork_activity)
        else:
            self._fork_activity.set_progress(text)
        self._scroll_to_latest(chat)
        self._refresh_status(text)

    async def show_skill_summary(self, text: str) -> None:
        """将隔离任务的最终摘要作为 Markdown 回复显示。"""
        if self._fork_activity is not None:
            self._fork_activity.finish()
            self._fork_activity = None
        chat = self.query_one("#chat-view", VerticalScroll)
        message = AssistantMessage()
        await chat.mount(message)
        await message.append_text(text)
        message.finish()
        self._scroll_to_latest(chat)

    def handle_permission_key(self, event) -> bool:
        """当输入框仍持有焦点时，也优先把确认键交给活动卡片。"""
        card = self._active_permission_card
        if card is None:
            return False
        choice = card.choose_for_key(event.key)
        if choice is not None:
            event.stop()
            event.prevent_default()
            self._resolve_active_permission(choice)
            return True
        if event.key in {"up", "down"}:
            event.stop()
            event.prevent_default()
            return True
        return False

    def on_inline_permission_card_selected(self, event: InlinePermissionCard.Selected) -> None:
        """接收卡片焦点下的键盘选择。"""
        event.stop()
        if event.card is self._active_permission_card:
            self._resolve_active_permission(event.choice)

    def _resolve_active_permission(self, choice: ApprovalChoice) -> None:
        card = self._active_permission_card
        if card is not None:
            card.mark_selected(choice)
            pending_tool = self._pending_tools.get(card.call_id)
            if pending_tool is not None and choice is not ApprovalChoice.REJECT:
                pending_tool.resume_execution()
        if self._pending_approval is not None and not self._pending_approval.done():
            self._pending_approval.set_result(choice)

    def _show_error(self, content: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(ErrorMessage(content))
        self._scroll_to_latest(chat)

    def _show_notice(self, content: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(NoticeMessage(content))
        self._scroll_to_latest(chat)

    def _refresh_status(self, state: str) -> None:
        totals_cache = self._totals.cache_usage()
        cache = CacheUsage(
            totals_cache.available or self._active_cache.available,
            totals_cache.read_input_tokens + self._active_cache.read_input_tokens,
            totals_cache.write_input_tokens + self._active_cache.write_input_tokens,
        )
        self.query_one(ChatStatus).set_values(
            state,
            len(self._agent.conversation.messages),
            self._totals.input_tokens + self._active_input_tokens,
            self._totals.output_tokens + self._active_output_tokens,
            cache,
        )

    def _scroll_to_latest(self, chat: VerticalScroll) -> None:
        """仅在用户原本停留在底部时跟随新增内容。"""
        if chat.is_vertical_scroll_end:
            self.call_after_refresh(chat.scroll_end, animate=False)


def _context_text(result) -> str:
    """将上下文处理数据渲染为紧凑、稳定的终端状态。"""
    if result.status == "offloaded":
        return f"已外置 {result.offloaded_count} 个工具结果到磁盘 · {result.released_characters} 字符已释放"
    if result.status == "compacting":
        return "正在压缩上下文…"
    if result.status == "compacted":
        return f"已压缩上下文 · {result.before_tokens} → {result.after_tokens} 估算 Token"
    return result.detail
