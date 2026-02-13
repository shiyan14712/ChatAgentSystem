"""Data models for the code execution sandbox."""

from dataclasses import dataclass, field
from enum import Enum


class ExecutionStatus(str, Enum):
    """Status of a code execution."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    SECURITY_BLOCKED = "security_blocked"


@dataclass
class ExecutionRequest:
    """Request to execute Python code in the sandbox."""

    code: str
    timeout: float = 30.0
    install_packages: list[str] | None = None
    enable_network: bool = False


@dataclass
class ExecutionResult:
    """Result of a sandbox code execution."""

    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    execution_time: float = 0.0
    error: str | None = None
    truncated: bool = False


@dataclass
class SecurityCheckResult:
    """Result of AST-based security pre-check."""

    passed: bool
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
