"""YuCode 的内置斜杠命令。"""

from __future__ import annotations

import re

from yucode.agent import ContextUpdated, SessionRestoreFinished
from yucode.commands.models import CommandContext, CommandDefinition, CommandKind, CommandUsageError
from yucode.commands.registry import CommandRegistry
from yucode.permissions import PermissionMode


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _permission_mode_table() -> dict[str, PermissionMode]:
    """命令侧的模式查询表。

    规范值是 camelCase，与配置文件、子 Agent frontmatter 保持一致；这里额外接受
    snake_case 拼写，让用户在配置文件与命令之间切换时不必记住两套写法。
    """
    table: dict[str, PermissionMode] = {}
    for mode in PermissionMode:
        table[mode.value.lower()] = mode
        table[_snake_case(mode.value)] = mode
    return table


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
    table = _permission_mode_table()
    available = ", ".join(mode.value for mode in PermissionMode)
    if not arguments:
        await context.ui.show_message(
            f"当前权限模式：{context.agent.permissions.mode.value}\n可用模式：{available}"
        )
        return
    mode = table.get(_parts(arguments, 1)[0].lower())
    if mode is None:
        raise CommandUsageError(f"未知权限模式。可用模式：{available}。")
    if context.agent.permissions.mode is mode:
        await context.ui.show_message("当前已处于该权限模式。")
        return
    context.agent.permissions.set_mode(mode)
    context.ui.set_mode(mode)
    context.ui.refresh_status()
    await context.ui.show_message(f"已切换权限模式：{mode.value}。")


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


def _subagents(context: CommandContext):
    service = getattr(context.agent, "_subagents", None)
    if service is None:
        raise CommandUsageError("当前没有可用的后台任务服务。")
    return service


async def _tasks(context: CommandContext, arguments: str) -> None:
    _no_arguments(arguments)
    tasks = _subagents(context).tasks.list()
    if not tasks:
        await context.ui.show_message("当前没有子 Agent 任务。")
        return
    await context.ui.show_message("\n".join(f"{item.id} · {item.status.value} · {item.kind.value} · {item.definition_name or 'fork'} · 输入 {item.usage.input_tokens} / 输出 {item.usage.output_tokens}" for item in tasks))


async def _task(context: CommandContext, arguments: str) -> None:
    values = arguments.split()
    if len(values) != 2 or values[0] not in {"info", "cancel"}:
        raise CommandUsageError("用法：/task info <任务标识> 或 /task cancel <任务标识>。")
    service = _subagents(context); task_id = values[1]
    if values[0] == "cancel":
        task = service.tasks.cancel(task_id)
        await context.ui.show_message(f"已请求取消任务：{task_id}。" if task else f"任务不存在或已结束：{task_id}。", error=task is None)
        return
    task = service.tasks.info(task_id)
    if task is None:
        await context.ui.show_message(f"找不到任务：{task_id}。", error=True); return
    usage = service.tasks.aggregate_usage(task_id)
    await context.ui.show_message(f"任务：{task.id}\n状态：{task.status.value}\n类型：{task.kind.value}\n角色：{task.definition_name or 'fork'}\n父任务：{task.parent_task_id or '无'}\n结果：{task.summary or '进行中'}\n用量：输入 {usage.input_tokens} · 输出 {usage.output_tokens}")


def _worktrees(context: CommandContext):
    manager = getattr(context.agent, "_worktrees", None)
    if manager is None:
        raise CommandUsageError("当前没有可用的 Worktree 服务。")
    return manager


async def _worktree(context: CommandContext, arguments: str) -> None:
    values = arguments.split()
    if not values:
        raise CommandUsageError("用法：/worktree list|create <名称>|enter <名称>|exit|remove [--force] <名称>。")
    action = values[0].lower(); manager = _worktrees(context)
    if action == "list":
        if len(values) != 1: raise CommandUsageError("worktree list 不接受额外参数。")
        items = manager.list()
        if not items:
            await context.ui.show_message("当前没有 Worktree。")
            return
        current = manager.current_root()
        await context.ui.show_message("\n".join(f"{item.slug}{'（当前）' if item.path == current else ''}\n  分支：{item.branch} · 状态：{item.state.value}\n  目录：{item.path}" for item in items))
        return
    if action == "exit":
        if len(values) != 1: raise CommandUsageError("worktree exit 不接受额外参数。")
        manager.exit(); await context.ui.show_message("已返回主工作目录。")
        return
    if action in {"create", "enter"}:
        if len(values) != 2: raise CommandUsageError(f"worktree {action} 需要一个名称。")
        record = manager.create(values[1]) if action == "create" else manager.enter(values[1])
        if action == "create": await context.ui.show_message(f"已创建 Worktree：{record.slug}\n目录：{record.path}\n分支：{record.branch}")
        else: await context.ui.show_message(f"已进入 Worktree：{record.slug}\n目录：{record.path}")
        return
    if action == "remove":
        flags = {item for item in values[1:] if item.startswith("--")}
        names = [item for item in values[1:] if not item.startswith("--")]
        if flags - {"--force", "--delete-branch"} or len(names) != 1:
            raise CommandUsageError("用法：/worktree remove [--force] [--delete-branch] <名称>。")
        force = "--force" in flags; delete_branch = "--delete-branch" in flags
        result = manager.remove(names[0], force=force, delete_branch=delete_branch)
        await context.ui.show_message(result.reason, error=not result.removed)
        return
    raise CommandUsageError("未知 worktree 子命令。")


def _teams(context: CommandContext):
    service = getattr(context.agent, "team_service", None)
    if service is None or not service.config.enabled:
        raise CommandUsageError("当前未启用 Agent Team。")
    return service


async def _team(context: CommandContext, arguments: str) -> None:
    values = arguments.split()
    if not values:
        raise CommandUsageError("用法：/team list|info <团队>|kill <团队> <成员>|delete <团队>。")
    action = values[0].lower()
    service = _teams(context)
    if action == "list":
        if len(values) != 1:
            raise CommandUsageError("team list 不接受额外参数。")
        teams = service.list()
        if not teams:
            await context.ui.show_message("当前项目没有 Team。")
            return
        await context.ui.show_message("\n".join(f"{item.name}：{len(item.members)} 名成员 · 负责人 {item.lead_id}" for item in teams))
        return
    if action == "info":
        if len(values) != 2:
            raise CommandUsageError("用法：/team info <团队>。")
        try:
            team = service.get(values[1])
        except ValueError as error:
            await context.ui.show_message(str(error), error=True)
            return
        lines = [f"团队：{team.name}", f"目录：{team.root}"]
        for item in team.members:
            lines.append(
                f"{item.name} · {item.role} · {item.state.value} · {item.backend.value}\n"
                f"{'可写' if item.writable else '只读'} · Worktree {item.worktree_slug or '未创建'} · "
                f"审批 {'是' if item.requires_approval else '否'} · {item.workspace_root}"
            )
        diagnostics = service.backend_diagnostics()
        lines.append("后端检测：\n" + "\n".join(
            f"{item.backend.value}：{'可用' if item.available else '不可用'}（{item.reason}）" for item in diagnostics
        ))
        await context.ui.show_message("\n".join(lines))
        return
    if action == "kill":
        if len(values) != 3:
            raise CommandUsageError("用法：/team kill <团队> <成员>。")
        try:
            await service.stop(values[1], values[2])
        except ValueError as error:
            await context.ui.show_message(str(error), error=True)
            return
        await context.ui.show_message(f"已停止成员：{values[2]}。")
        return
    if action == "delete":
        if len(values) != 2:
            raise CommandUsageError("用法：/team delete <团队>。")
        try:
            await service.delete_team(values[1])
        except ValueError as error:
            await context.ui.show_message(str(error), error=True)
            return
        await context.ui.show_message(f"已删除团队：{values[1]}。")
        return
    raise CommandUsageError("未知 team 子命令。")


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
        CommandDefinition("tasks", (), "列出子 Agent 任务", "/tasks", CommandKind.LOCAL, _tasks),
        CommandDefinition("task", (), "查看或取消子 Agent 任务", "/task info <任务标识>|cancel <任务标识>", CommandKind.LOCAL, _task),
        CommandDefinition("worktree", (), "管理隔离工作目录", "/worktree list|create <名称>|enter <名称>|exit|remove [--force] [--delete-branch] <名称>", CommandKind.LOCAL, _worktree),
        CommandDefinition("team", (), "管理 Agent Team", "/team list|info <团队>|kill <团队> <成员>|delete <团队>", CommandKind.LOCAL, _team),
        CommandDefinition("exit", ("quit",), "退出 YuCode", "/exit", CommandKind.UI, _exit, hidden=True),
    ))
