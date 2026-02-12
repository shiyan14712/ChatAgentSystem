"""
Context window management for dynamic token allocation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from structlog import get_logger

from app.models.schemas import Message, MessageRole
from app.utils.token_counter import TokenCounter

logger = get_logger()


def _default_model() -> str:
    """Resolve the default model name from application settings."""
    from app.config import get_settings
    return get_settings().openai.model


@dataclass
class ContextSegment:
    """A segment of context with metadata."""
    
    id: UUID = field(default_factory=uuid4)
    messages: list[Message] = field(default_factory=list)
    token_count: int = 0
    priority: int = 0  # Higher = more important
    is_locked: bool = False  # Locked segments won't be compressed
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextWindow:
    """
    Dynamic context window manager.
    
    Features:
    - Dynamic token allocation
    - Priority-based segment management
    - Automatic overflow handling
    - Layered storage (hot/warm/cold)
    """
    
    def __init__(
        self,
        max_tokens: int = 128000,
        reserved_tokens: int = 4096,  # Reserved for response
        model: str | None = None
    ):
        self.max_tokens = max_tokens
        self.reserved_tokens = reserved_tokens
        self.available_tokens = max_tokens - reserved_tokens
        self.token_counter = TokenCounter(model or _default_model())
        
        # Layered storage
        self.hot_segments: list[ContextSegment] = []  # Active context
        self.warm_segments: list[ContextSegment] = []  # Recent history
        self.cold_segments: list[ContextSegment] = []  # Archived
        
        # Current state
        self.current_tokens = 0
        self._message_index: dict[UUID, tuple[str, int, int]] = {}  # msg_id -> (layer, seg_idx, msg_idx)
    
    @property
    def usage_ratio(self) -> float:
        """Get current context usage ratio."""
        return self.current_tokens / self.available_tokens
    
    @property
    def remaining_tokens(self) -> int:
        """Get remaining token budget."""
        return self.available_tokens - self.current_tokens
    
    def add_message(
        self,
        message: Message,
        priority: int = 0,
        lock: bool = False
    ) -> bool:
        """
        Add a message to the context window.
        
        Args:
            message: Message to add
            priority: Priority level (higher = more important)
            lock: Whether to lock the message from compression
            
        Returns:
            True if added successfully, False if would overflow
        """
        # Calculate tokens
        tokens = self.token_counter.count_message_tokens(message.to_openai_format())
        message.token_count = tokens
        
        # Check if would overflow
        if tokens > self.remaining_tokens:
            logger.warning(
                "Message would overflow context window",
                message_tokens=tokens,
                remaining=self.remaining_tokens
            )
            return False
        
        # Create or get segment
        if not self.hot_segments or self._should_create_new_segment():
            segment = ContextSegment(
                messages=[message],
                token_count=tokens,
                priority=priority,
                is_locked=lock
            )
            self.hot_segments.append(segment)
            seg_idx = len(self.hot_segments) - 1
        else:
            segment = self.hot_segments[-1]
            segment.messages.append(message)
            segment.token_count += tokens
            seg_idx = len(self.hot_segments) - 1
        
        # Update index
        self._message_index[message.id] = ("hot", seg_idx, len(segment.messages) - 1)
        self.current_tokens += tokens
        
        logger.debug(
            "Message added to context",
            message_id=str(message.id),
            tokens=tokens,
            total_tokens=self.current_tokens,
            usage_ratio=self.usage_ratio
        )
        
        return True
    
    def add_messages(
        self,
        messages: list[Message],
        priority: int = 0,
        lock: bool = False
    ) -> int:
        """Add multiple messages. Returns count of successfully added messages."""
        added = 0
        for msg in messages:
            if self.add_message(msg, priority, lock):
                added += 1
            else:
                break
        return added
    
    def get_all_messages(self) -> list[Message]:
        """Get all messages in order (hot + warm + cold summaries)."""
        messages = []
        
        # Add cold summaries first
        for segment in self.cold_segments:
            if segment.metadata.get("summary"):
                summary_msg = Message(
                    role=MessageRole.SYSTEM,
                    content=f"[历史对话摘要]\n{segment.metadata['summary']}",
                    token_count=segment.token_count
                )
                messages.append(summary_msg)
        
        # Add warm messages
        for segment in self.warm_segments:
            messages.extend(segment.messages)
        
        # Add hot messages
        for segment in self.hot_segments:
            messages.extend(segment.messages)
        
        return messages
    
    def get_active_messages(self) -> list[Message]:
        """Get only active (hot) messages."""
        messages = []
        for segment in self.hot_segments:
            messages.extend(segment.messages)
        return messages
    
    def move_to_warm(self, segment_index: int) -> bool:
        """Move a hot segment to warm storage."""
        if segment_index >= len(self.hot_segments):
            return False
        
        segment = self.hot_segments.pop(segment_index)
        self.warm_segments.append(segment)
        
        # Update index
        for i, msg in enumerate(segment.messages):
            self._message_index[msg.id] = ("warm", len(self.warm_segments) - 1, i)
        
        logger.debug(
            "Segment moved to warm storage",
            segment_id=str(segment.id),
            warm_count=len(self.warm_segments)
        )
        
        return True
    
    def move_to_cold(
        self,
        segment_index: int,
        summary: str | None = None
    ) -> bool:
        """Move a warm segment to cold storage with optional summary."""
        if segment_index >= len(self.warm_segments):
            return False
        
        segment = self.warm_segments.pop(segment_index)
        
        if summary:
            segment.metadata["summary"] = summary
            # Reduce token count for summary
            original_tokens = segment.token_count
            segment.token_count = self.token_counter.count_tokens(summary) + 20
            self.current_tokens -= (original_tokens - segment.token_count)
        
        self.cold_segments.append(segment)
        
        # Update index
        for i, msg in enumerate(segment.messages):
            self._message_index[msg.id] = ("cold", len(self.cold_segments) - 1, i)
        
        logger.debug(
            "Segment moved to cold storage",
            segment_id=str(segment.id),
            cold_count=len(self.cold_segments)
        )
        
        return True
    
    def remove_message(self, message_id: UUID) -> bool:
        """Remove a specific message by ID."""
        if message_id not in self._message_index:
            return False
        
        layer, seg_idx, msg_idx = self._message_index[message_id]
        
        if layer == "hot":
            segments = self.hot_segments
        elif layer == "warm":
            segments = self.warm_segments
        else:
            return False  # Can't remove from cold
        
        if seg_idx >= len(segments):
            return False
        
        segment = segments[seg_idx]
        if msg_idx >= len(segment.messages):
            return False
        
        message = segment.messages.pop(msg_idx)
        segment.token_count -= message.token_count
        self.current_tokens -= message.token_count
        
        del self._message_index[message_id]
        
        # Reindex remaining messages in segment
        for i, msg in enumerate(segment.messages[msg_idx:], start=msg_idx):
            self._message_index[msg.id] = (layer, seg_idx, i)
        
        return True
    
    def clear(self, keep_locked: bool = True) -> None:
        """Clear context, optionally keeping locked segments."""
        if keep_locked:
            # Keep locked segments
            self.hot_segments = [s for s in self.hot_segments if s.is_locked]
            self.warm_segments = []
            self.cold_segments = []
        else:
            self.hot_segments = []
            self.warm_segments = []
            self.cold_segments = []
        
        # Recalculate tokens
        self.current_tokens = sum(s.token_count for s in self.hot_segments)
        
        # Rebuild index
        self._message_index = {}
        for seg_idx, segment in enumerate(self.hot_segments):
            for msg_idx, msg in enumerate(segment.messages):
                self._message_index[msg.id] = ("hot", seg_idx, msg_idx)
    
    def get_stats(self) -> dict[str, Any]:
        """Get context window statistics."""
        return {
            "max_tokens": self.max_tokens,
            "available_tokens": self.available_tokens,
            "current_tokens": self.current_tokens,
            "remaining_tokens": self.remaining_tokens,
            "usage_ratio": self.usage_ratio,
            "hot_segments": len(self.hot_segments),
            "warm_segments": len(self.warm_segments),
            "cold_segments": len(self.cold_segments),
            "total_messages": len(self._message_index),
        }
    
    def _should_create_new_segment(self) -> bool:
        """Determine if a new segment should be created."""
        if not self.hot_segments:
            return True
        
        last_segment = self.hot_segments[-1]
        
        # Create new segment if:
        # 1. Last segment has > 10 messages
        # 2. Last segment is locked
        # 3. Last segment has different priority
        return (
            len(last_segment.messages) >= 10 or
            last_segment.is_locked
        )
    
    def optimize(self, target_ratio: float = 0.7) -> int:
        """
        Optimize context by moving segments to lower tiers.
        
        Args:
            target_ratio: Target usage ratio after optimization
            
        Returns:
            Number of tokens freed
        """
        if self.usage_ratio <= target_ratio:
            return 0
        
        target_tokens = int(self.available_tokens * target_ratio)
        tokens_to_free = self.current_tokens - target_tokens
        freed = 0
        
        # Move warm segments to cold first
        while self.warm_segments and freed < tokens_to_free:
            segment = self.warm_segments[0]
            self.move_to_cold(0, summary="[已压缩的历史消息]")
            freed += segment.token_count
        
        # Move hot segments to warm if needed
        while self.hot_segments and freed < tokens_to_free:
            # Skip locked segments
            non_locked = [i for i, s in enumerate(self.hot_segments) if not s.is_locked]
            if not non_locked:
                break
            
            segment = self.hot_segments[non_locked[0]]
            self.move_to_warm(non_locked[0])
            # Moving to warm doesn't free tokens, just prepares for cold
        
        logger.info(
            "Context optimization completed",
            tokens_freed=freed,
            new_usage_ratio=self.usage_ratio
        )
        
        return freed
