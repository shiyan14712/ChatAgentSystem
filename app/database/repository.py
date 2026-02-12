"""
Async repository for session / message CRUD against PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from app.database.models import MessageModel, SessionModel
from app.models.schemas import (
    ContentBlock,
    ConversationSession,
    Message,
    MessageRole,
    MessageStatus,
)

logger = get_logger()


class SessionRepository:
    """
    Async CRUD repository for conversation sessions and messages.

    Converts between Pydantic domain models and SQLAlchemy ORM models.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_db_session(session: ConversationSession) -> SessionModel:
        return SessionModel(
            id=session.id,
            title=session.title,
            status=session.status.value,
            current_iteration=session.current_iteration,
            total_tokens=session.total_tokens,
            prompt_tokens=session.prompt_tokens,
            completion_tokens=session.completion_tokens,
            summary=session.summary,
            extra_metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _to_db_message(session_id: UUID, message: Message) -> MessageModel:
        # Handle content: str vs list[ContentBlock]
        content_text: str | None = None
        content_blocks: list | None = None

        if isinstance(message.content, str):
            content_text = message.content
        elif isinstance(message.content, list):
            content_blocks = [block.model_dump() for block in message.content]

        return MessageModel(
            id=message.id,
            session_id=session_id,
            role=message.role.value,
            content=content_text,
            content_blocks=content_blocks,
            name=message.name,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            importance_score=message.importance_score,
            token_count=message.token_count,
            is_compressed=message.is_compressed,
            extra_metadata=message.metadata,
            created_at=message.created_at,
        )

    @staticmethod
    def _from_db_message(db_msg: MessageModel) -> Message:
        # Reconstruct content
        content: str | list[ContentBlock]
        if db_msg.content_blocks is not None:
            content = [ContentBlock(**b) for b in db_msg.content_blocks]
        else:
            content = db_msg.content or ""

        return Message(
            id=db_msg.id,
            role=MessageRole(db_msg.role),
            content=content,
            name=db_msg.name,
            tool_calls=db_msg.tool_calls,
            tool_call_id=db_msg.tool_call_id,
            importance_score=db_msg.importance_score,
            token_count=db_msg.token_count,
            is_compressed=db_msg.is_compressed,
            metadata=db_msg.extra_metadata or {},
            created_at=db_msg.created_at.replace(tzinfo=None) if db_msg.created_at else datetime.utcnow(),
        )

    @staticmethod
    def _from_db_session(db_session: SessionModel) -> ConversationSession:
        messages = [
            SessionRepository._from_db_message(m) for m in db_session.messages
        ]
        return ConversationSession(
            id=db_session.id,
            title=db_session.title,
            messages=messages,
            status=MessageStatus(db_session.status),
            current_iteration=db_session.current_iteration,
            total_tokens=db_session.total_tokens,
            prompt_tokens=db_session.prompt_tokens,
            completion_tokens=db_session.completion_tokens,
            summary=db_session.summary,
            metadata=db_session.extra_metadata or {},
            created_at=db_session.created_at.replace(tzinfo=None) if db_session.created_at else datetime.utcnow(),
            updated_at=db_session.updated_at.replace(tzinfo=None) if db_session.updated_at else datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(self, session: ConversationSession) -> None:
        """Persist a newly created session (with any initial messages)."""
        async with self._session_factory() as db:
            db_session = self._to_db_session(session)
            # Also persist initial messages (e.g. system prompt)
            for msg in session.messages:
                db_session.messages.append(self._to_db_message(session.id, msg))
            db.add(db_session)
            await db.commit()
            logger.debug("Session persisted", session_id=str(session.id))

    async def get_session(self, session_id: UUID) -> ConversationSession | None:
        """Load a session with all its messages from the database."""
        async with self._session_factory() as db:
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            result = await db.execute(stmt)
            db_session = result.scalar_one_or_none()
            if db_session is None:
                return None
            return self._from_db_session(db_session)

    async def save_message(self, session_id: UUID, message: Message) -> None:
        """Append a single message to an existing session."""
        async with self._session_factory() as db:
            db_msg = self._to_db_message(session_id, message)
            db.add(db_msg)
            # Bump session updated_at
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == session_id)
                .values(updated_at=func.now())
            )
            await db.commit()

    async def update_session(self, session: ConversationSession) -> None:
        """Update session-level scalar fields (title, summary, tokens, …)."""
        async with self._session_factory() as db:
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == session.id)
                .values(
                    title=session.title,
                    status=session.status.value,
                    current_iteration=session.current_iteration,
                    total_tokens=session.total_tokens,
                    prompt_tokens=session.prompt_tokens,
                    completion_tokens=session.completion_tokens,
                    summary=session.summary,
                    extra_metadata=session.metadata,
                    updated_at=func.now(),
                )
            )
            await db.commit()

    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a session and its messages (cascade)."""
        async with self._session_factory() as db:
            result = await db.execute(
                delete(SessionModel).where(SessionModel.id == session_id)
            )
            await db.commit()
            deleted = result.rowcount > 0  # type: ignore[union-attr]
            if deleted:
                logger.debug("Session deleted from DB", session_id=str(session_id))
            return deleted

    async def list_sessions(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ConversationSession], int]:
        """Return a paginated list of sessions (most recently updated first)."""
        async with self._session_factory() as db:
            # Total count
            count_stmt = select(func.count()).select_from(SessionModel)
            total = (await db.execute(count_stmt)).scalar() or 0

            # Paginated query
            offset = (page - 1) * page_size
            stmt = (
                select(SessionModel)
                .order_by(SessionModel.updated_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            db_sessions = result.scalars().all()

            sessions = [self._from_db_session(s) for s in db_sessions]
            return sessions, total

    async def replace_session_messages(
        self,
        session_id: UUID,
        messages: list[Message],
        summary: str | None = None,
    ) -> None:
        """
        Replace all messages of a session (used after compression).

        Deletes existing messages and inserts the new compressed set.
        """
        async with self._session_factory() as db:
            # Delete old messages
            await db.execute(
                delete(MessageModel).where(MessageModel.session_id == session_id)
            )
            # Insert new messages
            for msg in messages:
                db.add(self._to_db_message(session_id, msg))
            # Update summary
            if summary is not None:
                await db.execute(
                    update(SessionModel)
                    .where(SessionModel.id == session_id)
                    .values(summary=summary, updated_at=func.now())
                )
            await db.commit()
            logger.debug(
                "Session messages replaced after compression",
                session_id=str(session_id),
                new_count=len(messages),
            )
