"""
Configuration management for the Chat Agent Framework.
Supports environment variables and .env files.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenAIConfig(BaseSettings):
    """OpenAI API configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(default="sk-f4e0893a18344bcbb696fc3dbe3cda32", description="OpenAI API key")
    base_url: str = Field(
        default="http://localhost:8045/v1",
        description="OpenAI API base URL (for compatible APIs)"
    )
    model: str = Field(
        default="gemini-2.5-flash",
        description="Default model to use"
    )
    max_tokens: int = Field(default=4096, description="Maximum tokens per request")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model for semantic search"
    )
    timeout: float = Field(default=60.0, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")


class MemoryConfig(BaseSettings):
    """Memory and context management configuration."""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Token limits
    max_context_tokens: int = Field(
        default=128000,
        description="Maximum context window size in tokens"
    )
    compression_threshold: float = Field(
        default=0.92,
        description="Trigger compression when usage reaches this ratio"
    )
    target_compression_ratio: float = Field(
        default=0.3,
        description="Target ratio after compression"
    )
    
    # Storage settings
    max_messages_in_memory: int = Field(
        default=100,
        description="Maximum messages to keep in active memory"
    )
    summary_max_tokens: int = Field(
        default=500,
        description="Maximum tokens for conversation summaries"
    )
    
    # Importance scoring
    importance_decay_factor: float = Field(
        default=0.95,
        description="Decay factor for message importance over time"
    )
    min_importance_threshold: float = Field(
        default=0.1,
        description="Minimum importance score to retain message"
    )


class AgentConfig(BaseSettings):
    """Agent loop and execution configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    max_iterations: int = Field(
        default=10,
        description="Maximum agent loop iterations"
    )
    iteration_timeout: float = Field(
        default=300.0,
        description="Timeout per iteration in seconds"
    )
    enable_parallel_tools: bool = Field(
        default=True,
        description="Enable parallel tool execution"
    )
    max_parallel_tools: int = Field(
        default=5,
        description="Maximum parallel tool calls"
    )
    
    # Interruption settings
    enable_interruption: bool = Field(
        default=True,
        description="Allow interrupting agent execution"
    )
    checkpoint_interval: float = Field(
        default=5.0,
        description="Checkpoint interval for recovery in seconds"
    )


class ToolConfig(BaseSettings):
    """Tool discovery and plugin loading configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enable_builtin_discovery: bool = Field(
        default=True,
        description="Enable discovery of built-in tools from internal package"
    )
    enable_entrypoint_discovery: bool = Field(
        default=True,
        description="Enable discovery of external tools via Python entry points"
    )
    entrypoint_group: str = Field(
        default="chat_agent_framework.tools",
        description="Entry-point group name for external tool providers"
    )
    discovery_fail_fast: bool = Field(
        default=False,
        description="Fail startup if one tool provider fails to load"
    )


class SandboxConfig(BaseSettings):
    """Docker-based code execution sandbox configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(default=True, description="Enable the code execution sandbox")
    image_name: str = Field(
        default="agent-sandbox:latest",
        description="Docker image name for the sandbox",
    )
    auto_build_image: bool = Field(
        default=True,
        description="Automatically build the sandbox image on startup if missing",
    )
    execution_timeout: float = Field(
        default=30.0,
        description="Default execution timeout in seconds",
    )
    max_execution_timeout: float = Field(
        default=120.0,
        description="Maximum allowed execution timeout in seconds",
    )
    max_output_size: int = Field(
        default=65536,
        description="Maximum output size in bytes before truncation (64 KB)",
    )
    memory_limit: str = Field(
        default="256m",
        description="Container memory limit (Docker format, e.g. 256m / 1g)",
    )
    cpu_period: int = Field(default=100_000, description="CPU CFS period (microseconds)")
    cpu_quota: int = Field(
        default=50_000,
        description="CPU CFS quota (microseconds) — 50 000 / 100 000 = 50 % of one core",
    )
    pids_limit: int = Field(
        default=64,
        description="Maximum number of PIDs inside the container",
    )
    network_enabled: bool = Field(
        default=False,
        description="Allow network access from sandbox containers by default",
    )
    container_workdir: str = Field(
        default="/workspace",
        description="Working directory inside the sandbox container",
    )


class MessageQueueConfig(BaseSettings):
    """Message queue configuration (Kafka/Redis)."""

    model_config = SettingsConfigDict(
        env_prefix="QUEUE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    backend: Literal["memory", "redis", "kafka"] = Field(
        default="memory",
        description="Message queue backend"
    )
    
    # Redis settings
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL"
    )
    
    # Kafka settings
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka bootstrap servers"
    )
    kafka_topic_prefix: str = Field(
        default="agent",
        description="Kafka topic prefix"
    )
    
    # Queue settings
    max_queue_size: int = Field(
        default=10000,
        description="Maximum queue size"
    )
    message_ttl: int = Field(
        default=3600,
        description="Message TTL in seconds"
    )
    priority_levels: int = Field(
        default=5,
        description="Number of priority levels"
    )


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DATABASE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db",
        description="Database connection URL"
    )
    pool_size: int = Field(default=10, description="Connection pool size")
    max_overflow: int = Field(default=20, description="Max overflow connections")
    echo: bool = Field(default=False, description="Echo SQL statements")


class ServerConfig(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SERVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    workers: int = Field(default=1, description="Number of workers")
    debug: bool = Field(default=False, description="Debug mode")
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins"
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Chat Agent Framework"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = Field(default=True, description="Debug mode")

    # Nested configs — each reads .env independently with its own env_prefix
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    queue: MessageQueueConfig = Field(default_factory=MessageQueueConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @field_validator("debug", mode="before")
    @classmethod
    def validate_debug(cls, v: str | bool) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "on")
        return False


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
