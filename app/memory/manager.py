"""
Memory Manager - Central coordinator for memory and context management.
"""

import asyncio
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from openai import AsyncOpenAI
from structlog import get_logger

from app.config import get_settings
from app.memory.compressor import ContextCompressor
from app.memory.context import ContextWindow
from app.models.schemas import (
    ConversationSession,
    Message,
    MessageRole,
    MessageStatus,
)
from app.utils.token_counter import TokenCounter

logger = get_logger()
settings = get_settings()


class MemoryManager:
    """
    Central memory management coordinator.
    
    Features:
    - Session-based memory isolation
    - Automatic compression triggering
    - Token optimization
    - Importance-based retention
    """
    
    def __init__(
        self,
        openai_client: AsyncOpenAI,
        model: str = "gpt-4o"
    ):
        self.client = openai_client
        self.model = model
        self.token_counter = TokenCounter(model)
        self.compressor = ContextCompressor(openai_client, model)
        
        # Session storage
        self._sessions: dict[UUID, ConversationSession] = {}
        self._context_windows: dict[UUID, ContextWindow] = {}
        
        # Configuration
        self.config = settings.memory
        
        # Callbacks
        self._on_compression_callbacks: list[Callable] = []
        
        logger.info(
            "Memory manager initialized",
            max_context_tokens=self.config.max_context_tokens,
            compression_threshold=self.config.compression_threshold
        )
    
    async def create_session(
        self,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> ConversationSession:
        """Create a new conversation session."""
        session = ConversationSession(
            metadata=metadata or {}
        )
        
        # Add system message if provided
        if system_prompt:
            system_msg = Message(
                role=MessageRole.SYSTEM,
                content=system_prompt,
                importance_score=1.0
            )
            system_msg.token_count = self.token_counter.count_message_tokens(
                system_msg.to_openai_format()
            )
            session.messages.append(system_msg)
        
        # Create context window
        context_window = ContextWindow(
            max_tokens=self.config.max_context_tokens,
            model=self.model
        )
        
        # Add system message to context
        if system_prompt:
            context_window.add_message(system_msg, priority=10, lock=True)
        
        self._sessions[session.id] = session
        self._context_windows[session.id] = context_window
        
        logger.info(
            "Session created",
            session_id=str(session.id),
            has_system_prompt=bool(system_prompt)
        )
        
        return session
    
    async def get_session(self, session_id: UUID) -> ConversationSession | None:
        """Get an existing session."""
        return self._sessions.get(session_id)
    
    async def add_message(
        self,
        session_id: UUID,
        message: Message
    ) -> bool:
        """
        Add a message to a session.
        
        Automatically triggers compression if threshold is reached.
        """
        session = self._sessions.get(session_id)
        context = self._context_windows.get(session_id)
        
        if not session or not context:
            logger.error("Session not found", session_id=str(session_id))
            return False
        
        # Calculate tokens
        message.token_count = self.token_counter.count_message_tokens(
            message.to_openai_format()
        )
        
        # Add to session
        session.add_message(message)
        
        # Add to context window
        priority = self._calculate_priority(message)
        added = context.add_message(message, priority=priority)
        
        if not added:
            # Need to compress first
            await self._handle_overflow(session_id)
            added = context.add_message(message, priority=priority)
        
        # Check if compression needed
        if context.usage_ratio >= self.config.compression_threshold:
            await self._trigger_compression(session_id)
        
        return added
    
    async def get_messages(
        self,
        session_id: UUID,
        include_compressed: bool = False
    ) -> list[Message]:
        """Get messages for a session."""
        session = self._sessions.get(session_id)
        context = self._context_windows.get(session_id)
        
        if not session:
            return []
        
        if context and include_compressed:
            return context.get_all_messages()
        
        return session.messages
    
    async def get_openai_messages(
        self,
        session_id: UUID
    ) -> list[dict[str, Any]]:
        """Get messages in OpenAI API format."""
        session = self._sessions.get(session_id)
        
        if not session:
            return []
        
        messages = []
        
        # Add summary if exists
        if session.summary:
            messages.append({
                "role": "system",
                "content": f"[历史对话摘要]\n{session.summary}"
            })
        
        # Add active messages
        for msg in session.messages:
            messages.append(msg.to_openai_format())
        
        return messages
    
    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
        if session_id in self._context_windows:
            del self._context_windows[session_id]
        
        logger.info("Session deleted", session_id=str(session_id))
        return True
    
    async def list_sessions(
        self,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[list[ConversationSession], int]:
        """List all sessions with pagination."""
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True
        )
        
        total = len(sessions)
        start = (page - 1) * page_size
        end = start + page_size
        
        return sessions[start:end], total
    
    def on_compression(self, callback: Callable) -> None:
        """Register a compression callback."""
        self._on_compression_callbacks.append(callback)
    
    async def _trigger_compression(self, session_id: UUID) -> None:
        """Trigger context compression for a session."""
        session = self._sessions.get(session_id)
        context = self._context_windows.get(session_id)
        
        if not session or not context:
            return
        
        logger.info(
            "Triggering compression",
            session_id=str(session_id),
            usage_ratio=context.usage_ratio
        )
        
        # Get messages to compress
        messages = context.get_active_messages()
        
        # Run compression
        compressed, summary = await self.compressor.compress_context(
            messages,
            self.config.max_context_tokens
        )
        
        # Update session
        if summary:
            if session.summary:
                session.summary = f"{session.summary}\n\n{summary}"
            else:
                session.summary = summary
        
        # Update context window
        context.clear(keep_locked=True)
        for msg in compressed:
            priority = self._calculate_priority(msg)
            context.add_message(msg, priority=priority)
        
        # Update session messages
        session.messages = compressed
        
        # Notify callbacks
        for callback in self._on_compression_callbacks:
            try:
                await callback(session_id, len(messages), len(compressed))
            except Exception as e:
                logger.error(f"Compression callback error: {e}")
    
    async def _handle_overflow(self, session_id: UUID) -> None:
        """Handle context overflow by aggressive compression."""
        context = self._context_windows.get(session_id)
        
        if not context:
            return
        
        # Move oldest warm segments to cold
        while context.warm_segments:
            context.move_to_cold(0, summary="[已压缩的历史消息]")
        
        # Optimize context
        context.optimize(target_ratio=0.5)
    
    def _calculate_priority(self, message: Message) -> int:
        """Calculate message priority for context window."""
        priority = 5  # Default
        
        if message.role == MessageRole.SYSTEM:
            priority = 10
        elif message.role == MessageRole.USER:
            priority = 7
        elif message.role == MessageRole.ASSISTANT:
            priority = 5
        elif message.role == MessageRole.TOOL:
            priority = 3
        
        # Boost for tool calls
        if message.tool_calls:
            priority += 2
        
        return priority
    
    def get_stats(self, session_id: UUID | None = None) -> dict[str, Any]:
        """Get memory statistics."""
        if session_id:
            context = self._context_windows.get(session_id)
            session = self._sessions.get(session_id)
            
            if not context or not session:
                return {}
            
            return {
                "session_id": str(session_id),
                "message_count": len(session.messages),
                "context_stats": context.get_stats(),
                "has_summary": bool(session.summary),
            }
        
        # Global stats
        total_messages = sum(len(s.messages) for s in self._sessions.values())
        total_tokens = sum(
            sum(m.token_count for m in s.messages)
            for s in self._sessions.values()
        )
        
        return {
            "session_count": len(self._sessions),
            "total_messages": total_messages,
            "total_tokens": total_tokens,
        }
