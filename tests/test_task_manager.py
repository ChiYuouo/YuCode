import asyncio

from yucode.subagents.models import SubagentKind, TaskOutcome, TaskStatus
from yucode.subagents.tasks import TaskManager


def test_background_task_completes_and_notifies() -> None:
    async def scenario() -> None:
        manager = TaskManager()
        async def work(_): return TaskOutcome(TaskStatus.COMPLETED, "完成")
        task = manager.start(SubagentKind.DEFINITION, "Explore", work, background=True)
        await asyncio.sleep(0)
        assert manager.info(task.id).status is TaskStatus.COMPLETED
        assert manager.drain_notifications()[0].summary == "完成"
    asyncio.run(scenario())


def test_foreground_can_promote_without_cancelling() -> None:
    async def scenario() -> None:
        manager = TaskManager()
        gate = asyncio.Event()
        async def work(_):
            await gate.wait(); return TaskOutcome(TaskStatus.COMPLETED, "完成")
        task = manager.start(SubagentKind.DEFINITION, "Explore", work)
        await asyncio.sleep(0)
        assert manager.promote().id == task.id
        gate.set(); await asyncio.sleep(0)
        assert manager.info(task.id).status is TaskStatus.COMPLETED
    asyncio.run(scenario())


def test_completion_notifies_listener_without_draining_queue() -> None:
    async def scenario() -> None:
        manager = TaskManager(); received = []
        async def listener(notification): received.append(notification)
        manager.set_notification_listener(listener)
        async def work(_): return TaskOutcome(TaskStatus.COMPLETED, "结果")
        task = manager.start(SubagentKind.FORK, None, work, background=True)
        await asyncio.sleep(0)
        assert received[0].task_id == task.id
        assert manager.drain_notifications()[0] == received[0]
    asyncio.run(scenario())
