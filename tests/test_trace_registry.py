from datetime import UTC, datetime

from yucode.providers.base import Usage
from yucode.subagents.models import SubagentKind, TaskSnapshot, TaskStatus
from yucode.subagents.trace import TraceRegistry


def test_trace_tree_and_aggregate_usage() -> None:
    now = datetime.now(UTC)
    parent = TaskSnapshot("parent", None, SubagentKind.DEFINITION, "Explore", TaskStatus.COMPLETED, now, usage=Usage(3, 2))
    child = TaskSnapshot("child", "parent", SubagentKind.FORK, None, TaskStatus.COMPLETED, now, usage=Usage(5, 1))
    trace = TraceRegistry(); trace.register(parent); trace.register(child)
    assert [node.task_id for node in trace.tree("parent")] == ["parent", "child"]
    assert trace.aggregate_usage("parent") == Usage(8, 3)
