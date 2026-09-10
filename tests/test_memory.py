import asyncio
from pathlib import Path

from yucode.memory import INDEX_MAX_BYTES, INDEX_MAX_LINES, MemoryAction, MemoryKind, MemoryManager, MemoryScope
from yucode.providers.base import Message, StreamEvent


class FakeProvider:
    def __init__(self, outputs: list[str] | None = None, error: Exception | None = None) -> None:
        self.outputs = outputs or []
        self.error = error
        self.requests = []

    async def stream(self, request, _cancellation):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        yield StreamEvent("text", self.outputs.pop(0) if self.outputs else '{"actions":[]}')


def actions() -> tuple[MemoryAction, ...]:
    return (
        MemoryAction("create", MemoryScope.USER, MemoryKind.USER_PREFERENCE, "回答语言", "用户偏好简短中文回答。"),
        MemoryAction("create", MemoryScope.PROJECT, MemoryKind.PROJECT_KNOWLEDGE, "项目名", "项目名称是 YuCode。"),
    )


def test_stores_separate_markdown_notes_and_indexes(tmp_path: Path) -> None:
    manager = MemoryManager(FakeProvider(), tmp_path, tmp_path / "user")
    manager.apply_actions(actions())

    user_notes = list((tmp_path / "user" / ".yucode" / "memory").glob("*.md"))
    project_notes = list((tmp_path / ".yucode" / "memory").glob("*.md"))
    indexes = manager.load_indexes()

    assert len(user_notes) == 2 and len(project_notes) == 2
    assert "scope: user" in next(path.read_text(encoding="utf-8") for path in user_notes if path.name != "index.md")
    assert "scope: project" in next(path.read_text(encoding="utf-8") for path in project_notes if path.name != "index.md")
    assert {index.scope for index in indexes} == {MemoryScope.USER, MemoryScope.PROJECT}


def test_updates_existing_note_instead_of_creating_duplicate(tmp_path: Path) -> None:
    manager = MemoryManager(FakeProvider(), tmp_path, tmp_path / "user")
    manager.apply_actions((actions()[0],))
    note_path = next(path for path in (tmp_path / "user" / ".yucode" / "memory").glob("*.md") if path.name != "index.md")
    note_id = note_path.stem

    manager.apply_actions((MemoryAction("update", MemoryScope.USER, MemoryKind.USER_PREFERENCE, "回答风格", "用户偏好简短的中文回答。", note_id),))

    assert [path.name for path in (tmp_path / "user" / ".yucode" / "memory").glob("*.md") if path.name != "index.md"] == [f"{note_id}.md"]
    assert "回答风格" in note_path.read_text(encoding="utf-8")


def test_background_update_uses_tool_free_json_request(tmp_path: Path) -> None:
    provider = FakeProvider(['{"actions":[{"operation":"create","scope":"project","kind":"reference","title":"文档","content":"README 是参考资料。"}]}'])
    manager = MemoryManager(provider, tmp_path, tmp_path / "user")

    async def check() -> None:
        manager.schedule_update((Message("user", "记住 README 是参考资料"), Message("assistant", "好的")))
        await manager.wait_for_pending_updates()

    asyncio.run(check())

    assert provider.requests[0].tools == ()
    assert (tmp_path / ".yucode" / "memory" / "index.md").is_file()


def test_bad_background_output_becomes_diagnostic_without_writing(tmp_path: Path) -> None:
    manager = MemoryManager(FakeProvider(["不是 JSON"]), tmp_path, tmp_path / "user")

    async def check() -> None:
        manager.schedule_update((Message("user", "你好"), Message("assistant", "好")))
        await manager.wait_for_pending_updates()

    asyncio.run(check())

    assert any("失败" in message for message in manager.drain_diagnostics())
    assert not (tmp_path / ".yucode" / "memory").exists()


def test_background_update_accepts_markdown_wrapped_json(tmp_path: Path) -> None:
    provider = FakeProvider(['以下是 JSON：\n```json\n{"actions":[{"operation":"create","scope":"user","kind":"user_preference","title":"回答语言","content":"用户偏好中文。"}]}\n```'])
    manager = MemoryManager(provider, tmp_path, tmp_path / "user")

    async def check() -> None:
        manager.schedule_update((Message("user", "记住我偏好中文"), Message("assistant", "好的")))
        await manager.wait_for_pending_updates()

    asyncio.run(check())

    assert not manager.drain_diagnostics()
    assert (tmp_path / "user" / ".yucode" / "memory" / "index.md").is_file()


def test_large_index_is_limited_when_compaction_is_unavailable(tmp_path: Path) -> None:
    manager = MemoryManager(FakeProvider(), tmp_path, tmp_path / "user")
    many = tuple(
        MemoryAction("create", MemoryScope.PROJECT, MemoryKind.PROJECT_KNOWLEDGE, f"标题 {index}", "内容 " * 100)
        for index in range(240)
    )
    manager.apply_actions(many)

    content = (tmp_path / ".yucode" / "memory" / "index.md").read_text(encoding="utf-8")
    assert len(content.splitlines()) <= INDEX_MAX_LINES
    assert len(content.encode("utf-8")) <= INDEX_MAX_BYTES
    assert any("缩减" in message for message in manager.drain_diagnostics())
