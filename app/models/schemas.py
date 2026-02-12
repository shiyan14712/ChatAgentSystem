"""
Data models and schemas for the Chat Agent Framework.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class MessageRole(str, Enum):
    """Message role enumeration."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessagePriority(int, Enum):
    """Message priority levels."""
    LOW = 1
    NORMAL = 3
    HIGH = 5
    URGENT = 7
    CRITICAL = 9


class MessageStatus(str, Enum):
    """Message processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ContentType(str, Enum):
    """Content type enumeration."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"


class ContentBlock(BaseModel):
    """A content block within a message."""
    
    type: ContentType = Field(description="Content type")
    text: str | None = Field(default=None, description="Text content")
    data: dict[str, Any] | None = Field(default=None, description="Additional data")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")


class Message(BaseModel):
    """A chat message."""
    
    id: UUID = Field(default_factory=uuid4, description="Message ID")
    role: MessageRole = Field(description="Message role")
    content: str | list[ContentBlock] = Field(description="Message content")
    name: str | None = Field(default=None, description="Sender name (for tool messages)")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls")
    tool_call_id: str | None = Field(default=None, description="Tool call ID")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    importance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    token_count: int = Field(default=0)
    is_compressed: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI API format."""
        message: dict[str, Any] = {"role": self.role.value}
        
        if isinstance(self.content, str):
            message["content"] = self.content
        else:
            # Convert content blocks to OpenAI format
            content_parts = []
            for block in self.content:
                if block.type == ContentType.TEXT:
                    content_parts.append({"type": "text", "text": block.text or ""})
                elif block.type == ContentType.IMAGE:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": block.data.get("url", "")}
                    })
            message["content"] = content_parts
        
        if self.name:
            message["name"] = self.name
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        
        return message


class ConversationSession(BaseModel):
    """A conversation session."""
    
    id: UUID = Field(default_factory=uuid4, description="Session ID")
    title: str | None = Field(default=None, description="Conversation title")
    messages: list[Message] = Field(default_factory=list, description="Messages")
    
    # State
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    current_iteration: int = Field(default=0)
    
    # Token tracking
    total_tokens: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Memory management
    compressed_messages: list[Message] = Field(
        default_factory=list,
        description="Compressed/historical messages"
    )
    summary: str | None = Field(default=None, description="Conversation summary")
    
    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        """Add a message to the conversation."""
        self.messages.append(message)
        self.updated_at = datetime.utcnow()

    def get_openai_messages(self) -> list[dict[str, Any]]:
        """Get all messages in OpenAI format."""
        result = []
        
        # Add compressed messages summary if exists
        if self.summary:
            result.append({
                "role": "system",
                "content": f"[Previous conversation summary]\n{self.summary}"
            })
        
        # Add active messages
        for msg in self.messages:
            result.append(msg.to_openai_format())
        
        return result


class ToolDefinition(BaseModel):
    """Tool/function definition."""
    
    name: str = Field(description="Tool name")
    description: str = Field(description="Tool description")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema for parameters"
    )
    required: list[str] = Field(default_factory=list, description="Required parameters")


class AgentState(BaseModel):
    """Agent execution state."""
    
    session_id: UUID = Field(description="Session ID")
    iteration: int = Field(default=0)
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    current_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = Field(default=None)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# API Request/Response Models

class ChatRequest(BaseModel):
    """Chat API request."""
    
    message: str = Field(description="User message")
    session_id: UUID | None = Field(default=None, description="Session ID (for continuing)")
    stream: bool = Field(default=True, description="Enable streaming response")
    tools: list[ToolDefinition] | None = Field(default=None, description="Available tools")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()


class ChatResponse(BaseModel):
    """Chat API response."""
    
    session_id: UUID = Field(description="Session ID")
    message: Message = Field(description="Assistant message")
    status: MessageStatus = Field(description="Response status")
    usage: dict[str, int] = Field(default_factory=dict, description="Token usage")
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamChunk(BaseModel):
    """Streaming response chunk."""
    
    session_id: UUID = Field(description="Session ID")
    delta: str = Field(default="", description="Content delta")
    type: str = Field(description="Chunk type: content, thinking, tool_call, todo_list, done, error")
    tool_calls: list[dict[str, Any]] | None = Field(default=None)
    thinking: str | None = Field(default=None, description="Thinking content")
    is_thinking_complete: bool = Field(default=False)
    todo_list: "TodoList | None" = Field(default=None, description="Full todo list snapshot")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    """Session information."""
    
    id: UUID = Field(description="Session ID")
    title: str | None = Field(description="Conversation title")
    message_count: int = Field(description="Number of messages")
    created_at: datetime = Field(description="Creation time")
    updated_at: datetime = Field(description="Last update time")
    status: MessageStatus = Field(description="Session status")
    preview: str | None = Field(default=None, description="Last message preview")


class SessionListResponse(BaseModel):
    """Session list response."""
    
    sessions: list[SessionInfo] = Field(description="Session list")
    total: int = Field(description="Total count")
    page: int = Field(default=1)
    page_size: int = Field(default=20)


class TitleRequest(BaseModel):
    """Title generation request."""
    
    session_id: UUID = Field(description="Session ID")


class TitleResponse(BaseModel):
    """Title generation response."""
    
    session_id: UUID = Field(description="Session ID")
    title: str = Field(description="Generated title")


class ErrorResponse(BaseModel):
    """Error response."""
    
    error: str = Field(description="Error message")
    code: str = Field(description="Error code")
    details: dict[str, Any] | None = Field(default=None)


# ---------------------------------------------------------------
# Todo-list schemas
# ---------------------------------------------------------------

class TodoItemStatus(str, Enum):
    """Three-state status expected by the frontend."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


class TodoItem(BaseModel):
    """A single todo item."""
    id: str = Field(default_factory=lambda: str(uuid4()), description="Item UUID")
    label: str = Field(description="Short description")
    status: TodoItemStatus = Field(default=TodoItemStatus.PENDING)
    order_index: int = Field(default=0, description="Display order")


class TodoList(BaseModel):
    """Full todo list snapshot for a session."""
    id: str = Field(default_factory=lambda: str(uuid4()), description="TodoList UUID")
    title: str = Field(default="", description="Overall task title")
    items: list[TodoItem] = Field(default_factory=list, description="Ordered items")
    revision: int = Field(default=1, description="Monotonically increasing version")
    updated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z",
        description="ISO8601 timestamp",
    )
