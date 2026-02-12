"""
Async repository for todo-list CRUD against PostgreSQL.

Every write operation bumps ``revision`` and returns the fresh snapshot so
the caller can immediately broadcast it via SSE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from app.database.models import TodoItemModel, TodoListModel
from app.models.schemas import TodoItem, TodoItemStatus, TodoList

logger = get_logger()


class TodoRepository:
    """Async CRUD for session todo-lists with optimistic-lock revision."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_todo_list(self, session_id: UUID) -> TodoList | None:
        """Return the current todo-list for a session, or *None*."""
        async with self._sf() as db:
            stmt = select(TodoListModel).where(
                TodoListModel.session_id == session_id
            )
            result = await db.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_schema(row)

    # ------------------------------------------------------------------
    # Write — each returns the updated snapshot
    # ------------------------------------------------------------------

    async def create_or_replace(
        self,
        session_id: UUID,
        title: str,
        items: list[TodoItem],
    ) -> TodoList:
        """Create (or fully replace) the todo-list for a session."""
        async with self._sf() as db:
            # Delete existing if any
            existing_stmt = select(TodoListModel).where(
                TodoListModel.session_id == session_id
            )
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing:
                await db.delete(existing)
                await db.flush()

            tl = TodoListModel(
                id=uuid4(),
                session_id=session_id,
                title=title,
                revision=1,
                status="active",
            )
            for idx, item in enumerate(items):
                tl.items.append(
                    TodoItemModel(
                        id=UUID(item.id) if item.id else uuid4(),
                        label=item.label,
                        status=item.status.value,
                        order_index=item.order_index or idx + 1,
                    )
                )
            db.add(tl)
            await db.commit()
            await db.refresh(tl)
            logger.debug(
                "Todo list created",
                session_id=str(session_id),
                item_count=len(items),
            )
            return self._to_schema(tl)

    async def set_item_status(
        self,
        session_id: UUID,
        item_id: str,
        status: TodoItemStatus,
    ) -> TodoList:
        """Set a single item's status and bump revision."""
        async with self._sf() as db:
            tl = await self._load_or_raise(db, session_id)

            found = False
            for item in tl.items:
                if str(item.id) == item_id:
                    item.status = status.value
                    found = True
                    break
            if not found:
                raise ValueError(f"TodoItem {item_id} not found in session {session_id}")

            tl.revision += 1
            await db.commit()
            await db.refresh(tl)
            return self._to_schema(tl)

    async def advance_step(self, session_id: UUID) -> TodoList:
        """
        Mark the current *running* item as *completed* and the next
        *pending* item as *running*.  Bump revision once.
        """
        async with self._sf() as db:
            tl = await self._load_or_raise(db, session_id)

            sorted_items = sorted(tl.items, key=lambda i: i.order_index)
            next_pending = None
            for item in sorted_items:
                if item.status == "running":
                    item.status = "completed"
                elif item.status == "pending" and next_pending is None:
                    next_pending = item
            if next_pending is not None:
                next_pending.status = "running"

            tl.revision += 1
            await db.commit()
            await db.refresh(tl)
            return self._to_schema(tl)

    async def complete_all(self, session_id: UUID) -> TodoList:
        """Mark every item as completed and set list status to completed."""
        async with self._sf() as db:
            tl = await self._load_or_raise(db, session_id)
            for item in tl.items:
                item.status = "completed"
            tl.status = "completed"
            tl.revision += 1
            await db.commit()
            await db.refresh(tl)
            return self._to_schema(tl)

    async def clear(self, session_id: UUID) -> None:
        """Remove the todo-list for a session entirely."""
        async with self._sf() as db:
            await db.execute(
                delete(TodoListModel).where(
                    TodoListModel.session_id == session_id
                )
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_or_raise(
        self, db: AsyncSession, session_id: UUID
    ) -> TodoListModel:
        stmt = select(TodoListModel).where(
            TodoListModel.session_id == session_id
        )
        tl = (await db.execute(stmt)).scalar_one_or_none()
        if tl is None:
            raise ValueError(f"No todo list for session {session_id}")
        return tl

    @staticmethod
    def _to_schema(model: TodoListModel) -> TodoList:
        return TodoList(
            id=str(model.id),
            title=model.title,
            revision=model.revision,
            updated_at=(
                model.updated_at.isoformat()
                if model.updated_at
                else datetime.now(timezone.utc).isoformat()
            ),
            items=[
                TodoItem(
                    id=str(i.id),
                    label=i.label,
                    status=TodoItemStatus(i.status),
                    order_index=i.order_index,
                )
                for i in sorted(model.items, key=lambda x: x.order_index)
            ],
        )
