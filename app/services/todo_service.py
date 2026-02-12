"""
TodoService — business façade that wraps TodoRepository writes
and broadcasts todo-list snapshots over SSE.

Usage (inside ChatAgent / agent loop):

    todo_svc = TodoService(todo_repo)
    snapshot = await todo_svc.create_todo_list(
        session_id, "数据分析", ["收集数据源", "数据清洗", "建模评估"]
    )
    # The first item is automatically set to *running*.
    # Each call returns the latest TodoList snapshot that
    # should be pushed to the SSE stream.
"""

from __future__ import annotations

from typing import Callable, Awaitable
from uuid import UUID

from structlog import get_logger

from app.database.todo_repository import TodoRepository
from app.models.schemas import (
    StreamChunk,
    TodoItem,
    TodoItemStatus,
    TodoList,
)

logger = get_logger()

# Type alias: async callback that receives a StreamChunk
SSEBroadcastFn = Callable[[StreamChunk], Awaitable[None]]


class TodoService:
    """
    Stateless service layer for todo-lists.

    Every mutating method:
      1. writes to the DB (via ``TodoRepository``)
      2. builds a ``StreamChunk(type="todo_list")``
      3. invokes the *broadcast* callback so the SSE generator
         can yield the chunk to the client.
    """

    def __init__(self, repo: TodoRepository):
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API (called from agent loop)
    # ------------------------------------------------------------------

    async def create_todo_list(
        self,
        session_id: UUID,
        title: str,
        labels: list[str],
        *,
        broadcast: SSEBroadcastFn | None = None,
    ) -> TodoList:
        """
        Create (or replace) a todo-list for *session_id*.

        All items start as ``pending``; the first item is set to
        ``running`` automatically.
        """
        items = [
            TodoItem(
                label=label,
                status=TodoItemStatus.RUNNING if idx == 0 else TodoItemStatus.PENDING,
                order_index=idx + 1,
            )
            for idx, label in enumerate(labels)
        ]
        snapshot = await self._repo.create_or_replace(session_id, title, items)
        await self._broadcast(session_id, snapshot, broadcast)
        return snapshot

    async def create_or_replace_with_items(
        self,
        session_id: UUID,
        title: str,
        items: list[TodoItem],
        *,
        broadcast: SSEBroadcastFn | None = None,
    ) -> TodoList:
        """
        Create (or replace) a todo-list using pre-built ``TodoItem`` objects.

        This allows the caller to specify exact statuses per item
        (e.g. when the LLM supplies status in the tool call).
        """
        snapshot = await self._repo.create_or_replace(session_id, title, items)
        await self._broadcast(session_id, snapshot, broadcast)
        return snapshot

    async def advance_step(
        self,
        session_id: UUID,
        *,
        broadcast: SSEBroadcastFn | None = None,
    ) -> TodoList:
        """
        Mark the current running item as completed, and promote
        the next pending item to running.
        """
        snapshot = await self._repo.advance_step(session_id)
        await self._broadcast(session_id, snapshot, broadcast)
        return snapshot

    async def set_item_status(
        self,
        session_id: UUID,
        item_id: str,
        status: TodoItemStatus,
        *,
        broadcast: SSEBroadcastFn | None = None,
    ) -> TodoList:
        """Set a specific item status."""
        snapshot = await self._repo.set_item_status(session_id, item_id, status)
        await self._broadcast(session_id, snapshot, broadcast)
        return snapshot

    async def complete_all(
        self,
        session_id: UUID,
        *,
        broadcast: SSEBroadcastFn | None = None,
    ) -> TodoList:
        """Mark every item as completed."""
        snapshot = await self._repo.complete_all(session_id)
        await self._broadcast(session_id, snapshot, broadcast)
        return snapshot

    async def clear(self, session_id: UUID) -> None:
        """Remove the todo list entirely."""
        await self._repo.clear(session_id)

    async def get_todo_list(self, session_id: UUID) -> TodoList | None:
        """Read-only fetch (no broadcast)."""
        return await self._repo.get_todo_list(session_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _broadcast(
        session_id: UUID,
        snapshot: TodoList,
        broadcast: SSEBroadcastFn | None,
    ) -> None:
        if broadcast is None:
            return
        chunk = StreamChunk(
            session_id=session_id,
            type="todo_list",
            delta="",
            todo_list=snapshot,
        )
        try:
            await broadcast(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to broadcast todo snapshot",
                session_id=str(session_id),
                error=str(exc),
            )
