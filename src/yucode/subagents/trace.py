"""当前应用会话内的父子任务调用链。"""

from __future__ import annotations

from dataclasses import replace

from yucode.providers.base import CacheUsage, Usage
from yucode.subagents.models import TaskSnapshot, TraceNode


class TraceRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}

    def register(self, task: TaskSnapshot) -> None:
        self._nodes[task.id] = TraceNode(task.id, task.parent_task_id, task.kind, task.definition_name, task.status, task.usage, task.started_at, task.ended_at)

    def update(self, task: TaskSnapshot) -> None:
        if task.id not in self._nodes:
            self.register(task)
            return
        node = self._nodes[task.id]
        self._nodes[task.id] = replace(node, status=task.status, usage=task.usage, started_at=task.started_at, ended_at=task.ended_at)

    def get(self, task_id: str) -> TraceNode | None:
        return self._nodes.get(task_id)

    def tree(self, task_id: str) -> tuple[TraceNode, ...]:
        root = self._nodes.get(task_id)
        if root is None:
            return ()
        result: list[TraceNode] = []

        def visit(node: TraceNode) -> None:
            result.append(node)
            for child in self._nodes.values():
                if child.parent_task_id == node.task_id:
                    visit(child)

        visit(root)
        return tuple(result)

    def aggregate_usage(self, task_id: str) -> Usage:
        total = Usage()
        for node in self.tree(task_id):
            total = _add_usage(total, node.usage)
        return total


def _add_usage(first: Usage, second: Usage) -> Usage:
    cache = first.cache
    if second.cache.available:
        cache = CacheUsage(
            available=True,
            read_input_tokens=first.cache.read_input_tokens + second.cache.read_input_tokens,
            write_input_tokens=first.cache.write_input_tokens + second.cache.write_input_tokens,
        )
    return Usage(first.input_tokens + second.input_tokens, first.output_tokens + second.output_tokens, first.thinking_tokens + second.thinking_tokens, cache)
