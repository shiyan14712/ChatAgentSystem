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

    def __repr__(self) -> str:
        return f"<Session {self.id} title={self.title!r}>"


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
