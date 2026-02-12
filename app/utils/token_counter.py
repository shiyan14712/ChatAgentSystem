"""
Token counting utilities using tiktoken.
"""

import functools
from typing import Any

import tiktoken
from structlog import get_logger

logger = get_logger()


def _default_model() -> str:
    """Resolve the default model name from application settings."""
    from app.config import get_settings
    return get_settings().openai.model


class TokenCounter:
    """Token counting utility using tiktoken."""
    
    def __init__(self, model: str | None = None):
        self.model = model or _default_model()
        self._encoding = self._get_encoding(self.model)
    
    @functools.lru_cache(maxsize=10)
    def _get_encoding(self, model: str) -> tiktoken.Encoding:
        """Get encoding for a model with caching."""
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback to cl100k_base for unknown models
            logger.warning(f"Unknown model {model}, using cl100k_base encoding")
            return tiktoken.get_encoding("cl100k_base")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in a text string."""
        if not text:
            return 0
        return len(self._encoding.encode(text))
    
    def count_message_tokens(self, message: dict[str, Any]) -> int:
        """
        Count tokens in a message (OpenAI format).
        
        This follows the OpenAI token counting formula:
        - Every message follows <im_start>{role/name}\n{content}<im_end>\n
        - Plus a priming message for the assistant
        """
        num_tokens = 0
        
        # Message overhead
        num_tokens += 4  # <im_start>, role, \n, <im_end>
        
        for key, value in message.items():
            if value is None:
                continue
            
            if isinstance(value, str):
                num_tokens += self.count_tokens(value)
            elif isinstance(value, list):
                # Handle content blocks
                for item in value:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            num_tokens += self.count_tokens(item.get("text", ""))
                        elif item.get("type") == "image_url":
                            # Image tokens depend on detail level
                            detail = "auto"
                            if "image_url" in item:
                                detail = item["image_url"].get("detail", "auto")
                            if detail == "low":
                                num_tokens += 85
                            else:
                                # High detail: 170 tiles + 85 base
                                num_tokens += 1105  # Approximate
            elif isinstance(value, dict):
                num_tokens += self.count_tokens(str(value))
            
            if key == "name":
                num_tokens -= 1  # Name is combined with role
        
        num_tokens += 2  # Assistant priming
        
        return num_tokens
    
    def count_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Count total tokens in a message list."""
        total = 3  # Conversation priming
        for message in messages:
            total += self.count_message_tokens(message)
        return total
    
    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token limit."""
        tokens = self._encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        
        truncated_tokens = tokens[:max_tokens]
        return self._encoding.decode(truncated_tokens)
    
    def split_by_tokens(self, text: str, chunk_size: int, overlap: int = 0) -> list[str]:
        """Split text into chunks by token count."""
        tokens = self._encoding.encode(text)
        chunks = []
        
        start = 0
        while start < len(tokens):
            end = min(start + chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunks.append(self._encoding.decode(chunk_tokens))
            
            if end >= len(tokens):
                break
            
            start = end - overlap if overlap > 0 else end
        
        return chunks


# Global instance
_token_counter: TokenCounter | None = None


def get_token_counter(model: str | None = None) -> TokenCounter:
    """Get or create a token counter instance."""
    global _token_counter
    if _token_counter is None or _token_counter.model != model:
        _token_counter = TokenCounter(model)
    return _token_counter
