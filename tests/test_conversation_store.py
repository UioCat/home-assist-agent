from importlib import import_module
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_active_conversation_and_thread_binding_survive_restart(
    tmp_path: Path,
) -> None:
    store_module = import_module("home_assist_agent.conversations.store")
    store_class = store_module.SQLiteConversationStore
    database_path = tmp_path / "conversations.db"
    store = store_class(database_path)

    created = await store.resolve_active("home-1", "person-1")
    await store.bind_thread(created.conversation_id, "019c-thread-persisted")

    restarted = store_class(database_path)
    restored = await restarted.resolve_active("home-1", "person-1")

    assert restored.conversation_id == created.conversation_id
    assert restored.codex_thread_id == "019c-thread-persisted"
    assert restored.status == "active"
    assert database_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_explicit_new_conversation_replaces_the_active_mapping(
    tmp_path: Path,
) -> None:
    store_module = import_module("home_assist_agent.conversations.store")
    store = store_module.SQLiteConversationStore(tmp_path / "conversations.db")
    first = await store.resolve_active("home-1", "person-1")
    await store.bind_thread(first.conversation_id, "019c-thread-old")

    replacement = await store.create_new("home-1", "person-1")
    current = await store.resolve_active("home-1", "person-1")
    closed = await store.get(first.conversation_id)

    assert replacement.conversation_id != first.conversation_id
    assert replacement.codex_thread_id is None
    assert current.conversation_id == replacement.conversation_id
    assert closed is not None
    assert closed.status == "closed"


@pytest.mark.asyncio
async def test_completed_message_receipt_is_reused_without_reprocessing(
    tmp_path: Path,
) -> None:
    store_module = import_module("home_assist_agent.conversations.store")
    database_path = tmp_path / "conversations.db"
    store = store_module.SQLiteConversationStore(database_path)
    conversation = await store.resolve_active("home-1", "person-1")

    first = await store.claim_message(
        message_id="message-1",
        conversation_id=conversation.conversation_id,
        channel="console",
        command="打开客厅灯",
    )
    await store.complete_message(
        "message-1",
        {"message_id": "message-1", "message": "已打开客厅灯"},
    )
    restarted = store_module.SQLiteConversationStore(database_path)
    duplicate = await restarted.claim_message(
        message_id="message-1",
        conversation_id=conversation.conversation_id,
        channel="console",
        command="打开客厅灯",
    )

    assert first.status == "processing"
    assert first.is_new is True
    assert duplicate.status == "completed"
    assert duplicate.is_new is False
    assert duplicate.response == {
        "message_id": "message-1",
        "message": "已打开客厅灯",
    }
