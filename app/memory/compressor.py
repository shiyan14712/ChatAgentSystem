"""
Context compression algorithms for memory optimization.
Implements intelligent compression with importance scoring.
"""

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from openai import AsyncOpenAI
from structlog import get_logger

from app.config import get_settings
from app.models.schemas import Message, MessageRole
from app.utils.token_counter import TokenCounter

logger = get_logger()
settings = get_settings()


class CompressionStrategy(ABC):
    """Abstract base class for compression strategies."""
    
    @abstractmethod
    async def compress(
        self,
        messages: list[Message],
        target_ratio: float
    ) -> tuple[list[Message], str | None]:
        """Compress messages and return (retained_messages, summary)."""
        pass


class ImportanceScorer:
    """Scores message importance for retention decisions."""
    
    def __init__(self, decay_factor: float = 0.95):
        self.decay_factor = decay_factor
        self._keyword_weights = {
            "error": 0.3,
            "important": 0.2,
            "critical": 0.3,
            "remember": 0.2,
            "key": 0.15,
            "decision": 0.25,
            "conclusion": 0.2,
            "result": 0.15,
        }
    
    def score(self, message: Message, position: int, total: int) -> float:
        """
        Calculate importance score for a message.
        
        Factors:
        - Position (recent messages more important)
        - Role (system > user > assistant)
        - Content keywords
        - Existing importance score
        - Tool calls (higher importance)
        """
        base_score = message.importance_score
        
        # Position decay
        position_factor = self.decay_factor ** (total - position - 1)
        
        # Role weights
        role_weights = {
            MessageRole.SYSTEM: 1.0,
            MessageRole.USER: 0.8,
            MessageRole.ASSISTANT: 0.6,
            MessageRole.TOOL: 0.5,
        }
        role_factor = role_weights.get(message.role, 0.5)
        
        # Keyword analysis
        content = self._extract_text(message)
        keyword_score = 0.0
        content_lower = content.lower()
        for keyword, weight in self._keyword_weights.items():
            if keyword in content_lower:
                keyword_score += weight
        
        # Tool call bonus
        tool_bonus = 0.2 if message.tool_calls else 0.0
        
        # Combine scores
        final_score = (
            base_score * 0.3 +
            position_factor * 0.3 +
            role_factor * 0.2 +
            min(keyword_score, 0.3) * 0.15 +
            tool_bonus
        )
        
        return min(1.0, final_score)
    
    def _extract_text(self, message: Message) -> str:
        """Extract text content from a message."""
        if isinstance(message.content, str):
            return message.content
        elif isinstance(message.content, list):
            texts = []
            for block in message.content:
                if block.text:
                    texts.append(block.text)
            return " ".join(texts)
        return ""


class SummaryCompressor(CompressionStrategy):
    """Compresses messages by generating summaries."""
    
    def __init__(
        self,
        client: AsyncOpenAI,
        token_counter: TokenCounter,
        model: str = "gpt-4o-mini"
    ):
        self.client = client
        self.token_counter = token_counter
        self.model = model
        self.scorer = ImportanceScorer()
    
    async def compress(
        self,
        messages: list[Message],
        target_ratio: float
    ) -> tuple[list[Message], str | None]:
        """Compress messages using LLM summarization."""
        if len(messages) < 3:
            return messages, None
        
        # Calculate target token count
        total_tokens = sum(m.token_count for m in messages)
        target_tokens = int(total_tokens * target_ratio)
        
        # Score and sort messages by importance
        scored_messages = []
        for i, msg in enumerate(messages):
            score = self.scorer.score(msg, i, len(messages))
            scored_messages.append((score, i, msg))
        
        # Always keep system messages and recent messages
        keep_indices = set()
        messages_to_summarize = []
        
        for score, i, msg in scored_messages:
            if msg.role == MessageRole.SYSTEM:
                keep_indices.add(i)
            elif i >= len(messages) - 3:  # Keep last 3 messages
                keep_indices.add(i)
            elif score >= 0.7:  # High importance
                keep_indices.add(i)
            else:
                messages_to_summarize.append(msg)
        
        # Generate summary for messages to be compressed
        summary = None
        if messages_to_summarize:
            summary = await self._generate_summary(messages_to_summarize)
        
        # Build retained messages list
        retained = [messages[i] for i in sorted(keep_indices)]
        
        return retained, summary
    
    async def _generate_summary(self, messages: list[Message]) -> str:
        """Generate a summary of messages using LLM."""
        # Build conversation text
        conversation_parts = []
        for msg in messages:
            role = msg.role.value
            content = self._extract_text(msg)
            conversation_parts.append(f"[{role}]: {content}")
        
        conversation_text = "\n".join(conversation_parts)
        
        # Truncate if too long
        max_input_tokens = 3000
        if self.token_counter.count_tokens(conversation_text) > max_input_tokens:
            conversation_text = self.token_counter.truncate_text(
                conversation_text, max_input_tokens
            )
        
        prompt = f"""请总结以下对话内容，保留关键信息、决策和结论。使用简洁的中文：

{conversation_text}

总结："""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.memory.summary_max_tokens,
                temperature=0.3,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return self._extractive_summary(messages)
    
    def _extractive_summary(self, messages: list[Message]) -> str:
        """Fallback extractive summary."""
        key_points = []
        for msg in messages:
            text = self._extract_text(msg)
            # Extract sentences with key indicators
            sentences = re.split(r'[。！？.!?]', text)
            for sentence in sentences:
                if any(kw in sentence for kw in ["重要", "关键", "决定", "结论", "结果"]):
                    key_points.append(sentence.strip())
        
        if key_points:
            return " | ".join(key_points[:5])
        
        # Fallback: first 200 chars of each message
        return " | ".join(
            self._extract_text(m)[:200] for m in messages[:3]
        )
    
    def _extract_text(self, message: Message) -> str:
        """Extract text content from a message."""
        if isinstance(message.content, str):
            return message.content
        elif isinstance(message.content, list):
            texts = []
            for block in message.content:
                if block.text:
                    texts.append(block.text)
            return " ".join(texts)
        return ""


class SlidingWindowCompressor(CompressionStrategy):
    """Simple sliding window compression strategy."""
    
    def __init__(self, token_counter: TokenCounter, max_tokens: int):
        self.token_counter = token_counter
        self.max_tokens = max_tokens
        self.scorer = ImportanceScorer()
    
    async def compress(
        self,
        messages: list[Message],
        target_ratio: float
    ) -> tuple[list[Message], str | None]:
        """Compress using sliding window with importance scoring."""
        target_tokens = int(self.max_tokens * target_ratio)
        
        # Always keep system messages
        system_messages = [m for m in messages if m.role == MessageRole.SYSTEM]
        other_messages = [m for m in messages if m.role != MessageRole.SYSTEM]
        
        # Calculate current tokens
        system_tokens = sum(m.token_count for m in system_messages)
        remaining_budget = target_tokens - system_tokens
        
        if remaining_budget <= 0:
            return system_messages, None
        
        # Score and select messages
        scored = []
        for i, msg in enumerate(other_messages):
            score = self.scorer.score(msg, i, len(other_messages))
            scored.append((score, i, msg))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # Select messages until budget exhausted
        selected = []
        current_tokens = 0
        for score, original_idx, msg in scored:
            if current_tokens + msg.token_count <= remaining_budget:
                selected.append((original_idx, msg))
                current_tokens += msg.token_count
        
        # Sort back to original order
        selected.sort(key=lambda x: x[0])
        retained = [msg for _, msg in selected]
        
        # Generate brief summary of removed content
        removed = [msg for msg in other_messages if msg not in retained]
        summary = None
        if removed:
            summary = f"[已压缩 {len(removed)} 条历史消息]"
        
        return system_messages + retained, summary


class ContextCompressor:
    """
    Main context compression controller.
    Coordinates different compression strategies.
    """
    
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "gpt-4o-mini"
    ):
        self.client = client
        self.token_counter = TokenCounter(model)
        self.summary_compressor = SummaryCompressor(
            client, self.token_counter, model
        )
        self.settings = settings.memory
        
        logger.info(
            "Context compressor initialized",
            compression_threshold=self.settings.compression_threshold,
            target_ratio=self.settings.target_compression_ratio
        )
    
    def should_compress(self, messages: list[Message], max_tokens: int) -> bool:
        """Check if compression should be triggered."""
        total_tokens = sum(m.token_count for m in messages)
        usage_ratio = total_tokens / max_tokens
        return usage_ratio >= self.settings.compression_threshold
    
    async def compress_context(
        self,
        messages: list[Message],
        max_tokens: int
    ) -> tuple[list[Message], str | None]:
        """
        Compress conversation context.
        
        Returns:
            Tuple of (compressed_messages, summary)
        """
        if not self.should_compress(messages, max_tokens):
            return messages, None
        
        logger.info(
            "Starting context compression",
            message_count=len(messages),
            total_tokens=sum(m.token_count for m in messages)
        )
        
        # Use summary compression for better quality
        compressed, summary = await self.summary_compressor.compress(
            messages,
            self.settings.target_compression_ratio
        )
        
        # Verify compression result
        new_tokens = sum(m.token_count for m in compressed)
        logger.info(
            "Context compression completed",
            original_count=len(messages),
            compressed_count=len(compressed),
            original_tokens=sum(m.token_count for m in messages),
            new_tokens=new_tokens,
            compression_ratio=new_tokens / sum(m.token_count for m in messages)
        )
        
        return compressed, summary
    
    def calculate_importance_scores(
        self,
        messages: list[Message]
    ) -> list[tuple[Message, float]]:
        """Calculate importance scores for all messages."""
        scorer = ImportanceScorer(self.settings.importance_decay_factor)
        results = []
        
        for i, msg in enumerate(messages):
            score = scorer.score(msg, i, len(messages))
            results.append((msg, score))
        
        return results
