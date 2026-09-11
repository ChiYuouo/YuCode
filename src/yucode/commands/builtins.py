"""YuCode 的内置斜杠命令。"""

from __future__ import annotations

from yucode.agent import ContextUpdated, SessionRestoreFinished
from yucode.commands.models import CommandContext, CommandDefinition, CommandKind, CommandUsageError
from yucode.commands.registry import CommandRegistry
from yucode.permissions import PermissionMode


def _no_arguments(arguments: str) -> None:
    if arguments:
        raise CommandUsageError("该命令不接受参数。")


def _parts(arguments: str, count: int) -> list[str]:
    values = arguments.split()
    if len(values) != count:
        raise CommandUsageError("参数数量不正确。")
    return values


async def _help(context: CommandContext, arguments: str) -> None:
    if not arguments:
        lines = ["可用命令："]
        for command in context.registry.visible():
            aliases = f"（别名：{', '.join('/' + alias for alias in command.aliases)}）" if command.aliases else ""
            lines.append(f"/{command.name} {aliases}\n  {command.description}\n  用法：{command.usage}")
        await context.ui.show_message("\n".join(lines))
        return
    if len(arguments.split()) != 1:
        raise CommandUsageError("帮助命令只接受一个命令名。")
    target = context.registry.get(arguments.strip())
    if target is None or target.hidden:
        raise CommandUsageError("找不到公开命令。")
    hint = f"\n参数：{target.argument_hint}" if target.argument_hint else ""
    await context.ui.show_message(f"/{target.name}\n{target.description}\n用法：{target.usage}{hint}")


async def _compact(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    await context.ui.compact()


async def _clear(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    await context.ui.clear_chat()
    context.agent.clear_active_skills()
    await context.ui.show_message("已清空聊天显示；当前对话上下文仍保留。")
    context.ui.refresh_status()


async def _set_mode(context: CommandContext, arguments: str, mode: PermissionMode) -> None:
    _no_arguments(arguments)
    if context.agent.permissions.mode is mode:
        await context.ui.show_message(f"当前已处于 {mode.value} 模式。")
        return
    context.agent.permissions.set_mode(mode)
    context.ui.set_mode(mode)
    context.ui.refresh_status()
    await context.ui.show_message(f"已切换到 {mode.value} 模式。")


async def _plan(context: CommandContext, arguments: str) -> None:
    await _set_mode(context, arguments, PermissionMode.PLAN)


async def _do(context: CommandContext, arguments: str) -> None:
    await _set_mode(context, arguments, PermissionMode.DEFAULT)


async def _permission(context: CommandContext, arguments: str) -> None:
    options = {
        "default": PermissionMode.DEFAULT,
        "accept_edits": PermissionMode.ACCEPT_EDITS,
        "plan": PermissionMode.PLAN,
        "bypass_permissions": PermissionMode.BYPASS_PERMISSIONS,
    }
    if not arguments:
        await context.ui.show_message(
            f"当前权限模式：{context.agent.permissions.mode.value}\n可用模式：{', '.join(options)}"
        )
        return
    value = _parts(arguments, 1)[0].lower()
    if value not in options:
        raise CommandUsageError("未知权限模式。")
    if context.agent.permissions.mode is options[value]:
        await context.ui.show_message("当前已处于该权限模式。")
        return
    context.agent.permissions.set_mode(options[value])
    context.ui.set_mode(options[value])
    context.ui.refresh_status()
    await context.ui.show_message(f"已切换权限模式：{value}。")


def _prompt(prefix: str, arguments: str) -> str:
    return prefix if not arguments else f"{prefix}\n\n补充要求：{arguments}"


async def _memory(context: CommandContext, arguments: str) -> None:
    await context.ui.send_user_message(_prompt("查看并梳理当前项目记忆，说明已有内容、重复或待更新之处；没有明确修改要求时仅给出整理建议。", arguments))


async def _review(context: CommandContext, arguments: str) -> None:
    await context.ui.send_user_message(_prompt("审查当前工作区未提交改动，指出可验证的问题、位置、影响和建议；仅审查，不实施修复。", arguments))


async def _status(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    status = context.ui.status()
    usage = status.last_turn_usage
    recent = "不可用" if usage is None else f"输入 {usage.input_tokens} · 输出 {usage.output_tokens}"
    cache = ""
    if usage is not None:
        cache = " · 缓存不可用" if not usage.cache.available else f" · 缓存读 {usage.cache.read_input_tokens} / 写 {usage.cache.write_input_tokens}"
    await context.ui.show_message(
        f"模型：{status.provider} / {status.model}\n模式：{status.mode.value}\n"
        f"会话：{status.session_id or '不可用'} · 消息 {status.message_count}\n"
        f"上下文估算：{status.estimated_context_tokens} Token\n最近一轮：{recent}{cache}"
    )


async def _skill(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    runtime = context.skills
    if runtime is None:
        await context.ui.show_message("当前没有可用的 Skill 运行时。", error=True)
        return
    items = runtime.active_items()
    active_names = {item.definition.name for item in items}
    active_lines = [f"{item.definition.name} · {item.definition.description}\n  模式：{item.definition.mode.value} · 参数：{item.arguments or '无'}" for item in items]
    available = [item for item in runtime.catalog.definitions.values() if item.name not in active_names]
    available_lines = [f"{item.name} · {item.description}\n  可直接执行：/skill:{item.name}" for item in available]
    sections = ["已激活 Skill：\n" + ("\n".join(active_lines) if active_lines else "无")]
    if available_lines:
        sections.append("可用但未激活的 Skill：\n" + "\n".join(available_lines))
    await context.ui.show_message("\n\n".join(sections))


async def _session(context: CommandContext, arguments: str) -> None:
    store = context.sessions
    if store is None:
        await context.ui.show_message("当前未启用会话管理。", error=True)
        return
    values = arguments.split()
    if not values:
        status = context.ui.status()
        await context.ui.show_message(f"当前会话：{store.active_session_id}\n当前上下文消息数：{status.message_count}")
        return
    action = values[0].lower()
    if action == "list":
        if len(values) != 1:
            raise CommandUsageError("session list 不接受额外参数。")
        summaries = store.list_sessions()
        if not summaries:
            await context.ui.show_message("当前项目没有会话。")
            return
        active = store.active_session_id
        lines = ["历史会话："]
        for item in summaries:
            marker = "（当前）" if item.session_id == active else ""
            lines.append(f"{item.session_id} {marker}\n  {item.title} · {item.last_active_at.astimezone():%Y-%m-%d %H:%M} · {item.message_count} 条消息")
        await context.ui.show_message("\n".join(lines))
        return
    if action == "new":
        if len(values) != 1:
            raise CommandUsageError("session new 不接受额外参数。")
        session_id = store.create_session()
        context.agent.reset_session_state()
        await context.ui.clear_chat()
        context.ui.reset_usage()
        context.ui.refresh_status()
        await context.ui.show_message(f"已新建会话：{session_id}")
        return
    if action == "resume":
        session_id = _parts(" ".join(values[1:]), 1)[0]
        if session_id == store.active_session_id:
            await context.ui.show_message("已处于该会话。")
            return
        recovered = store.recover(session_id)
        success = False
        async for event in context.agent.restore_session(recovered, __import__("yucode.cancellation", fromlist=["Cancellation"]).Cancellation()):
            if isinstance(event, ContextUpdated):
                await context.ui.show_message(event.result.detail or "正在准备恢复上下文。")
            elif isinstance(event, SessionRestoreFinished):
                success = event.success
                if not success:
                    raise RuntimeError(event.detail)
        if success:
            store.activate_session(session_id)
            await context.ui.replace_history()
            context.ui.reset_usage()
            context.ui.refresh_status()
            await context.ui.show_message("已恢复历史会话，可继续追问。")
        return
    if action == "delete":
        session_id = _parts(" ".join(values[1:]), 1)[0]
        summary = store.get_summary(session_id)
        if session_id == store.active_session_id:
            raise CommandUsageError("不能删除当前会话，请先新建或恢复其他会话。")
        detail = f"{summary.title} · {summary.message_count} 条消息"
        if await context.ui.confirm_delete(session_id, detail):
            store.delete_session(session_id)
            await context.ui.show_message(f"已删除会话：{session_id}")
        else:
            await context.ui.show_message("已取消删除会话。")
        return
    raise CommandUsageError("未知 session 子操作。")


async def _exit(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    await context.ui.request_exit()


def build_builtin_registry() -> CommandRegistry:
    return CommandRegistry((
        CommandDefinition("help", ("?",), "显示可用命令和用法", "/help [命令]", CommandKind.LOCAL, _help, "可查询名称或别名"),
        CommandDefinition("compact", (), "压缩当前对话上下文", "/compact", CommandKind.LOCAL, _compact),
        CommandDefinition("clear", (), "清空聊天显示", "/clear", CommandKind.UI, _clear),
        CommandDefinition("plan", (), "进入计划模式", "/plan", CommandKind.UI, _plan),
        CommandDefinition("do", (), "回到默认模式", "/do", CommandKind.UI, _do),
        CommandDefinition("session", (), "查看和管理会话", "/session [list|new|resume <ID>|delete <ID>]", CommandKind.UI, _session, "list、new、resume <ID>、delete <ID>"),
        CommandDefinition("memory", (), "查看并梳理项目记忆", "/memory [补充要求]", CommandKind.PROMPT, _memory, "可选补充要求"),
        CommandDefinition("permission", (), "查看或切换权限模式", "/permission [模式]", CommandKind.UI, _permission, "default、accept_edits、plan、bypass_permissions"),
        CommandDefinition("status", (), "显示模型、会话和 Token 状态", "/status", CommandKind.LOCAL, _status),
        CommandDefinition("skill", (), "查看当前已激活的 Skill", "/skill", CommandKind.LOCAL, _skill),
        CommandDefinition("review", (), "审查当前工作区改动", "/review [审查重点]", CommandKind.PROMPT, _review, "可选审查重点"),
        CommandDefinition("exit", ("quit",), "退出 YuCode", "/exit", CommandKind.UI, _exit, hidden=True),
    ))
