"""
SQLAlchemy ORM models for sessions and messages.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


class SessionModel(Base):
    """Persistent conversation session."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    current_iteration: Mapped[int] = mapped_column(Integer, default=0)

    # Token tracking
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Memory management
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata (JSONB for efficient querying in PostgreSQL)
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False, server_default="{}"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    messages: Mapped[list["MessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageModel.created_at",
        lazy="selectin",
    )
    todo_list: Mapped["TodoListModel | None"] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Session {self.id} title={self.title!r}>"


class TodoListModel(Base):
    """Persistent todo list bound to a session (0 or 1 per session)."""

    __tablename__ = "session_todo_lists"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="active", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    session: Mapped["SessionModel"] = relationship(back_populates="todo_list")
    items: Mapped[list["TodoItemModel"]] = relationship(
        back_populates="todo_list",
        cascade="all, delete-orphan",
        order_by="TodoItemModel.order_index",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TodoList {self.id} session={self.session_id} rev={self.revision}>"


class TodoItemModel(Base):
    """A single item in a todo list."""

    __tablename__ = "session_todo_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    todo_list_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("session_todo_lists.id", ondelete="CASCADE"),
        index=True,
    )
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    todo_list: Mapped["TodoListModel"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return f"<TodoItem {self.id} label={self.label!r} status={self.status}>"


class MessageModel(Base):
    """Persistent chat message belonging to a session."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
    )

    # Core message fields
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When content is a list of ContentBlocks, serialized to JSONB
    content_blocks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Scoring / compression
    importance_score: Mapped[float] = mapped_column(Float, default=1.0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    is_compressed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Metadata
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False, server_default="{}"
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )

    # Relationships
    session: Mapped["SessionModel"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message {self.id} role={self.role}>"
