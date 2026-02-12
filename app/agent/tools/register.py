"""Default tool registration orchestration."""

from app.config import get_settings
from app.agent.executor import ToolExecutor
from app.agent.tools.discovery import register_discovered_tools


def register_default_tools(executor: ToolExecutor) -> ToolExecutor:
    """Register tools using discovery configuration."""
    settings = get_settings()
    return register_discovered_tools(
        executor,
        include_builtin=settings.tools.enable_builtin_discovery,
        include_entrypoints=settings.tools.enable_entrypoint_discovery,
        entrypoint_group=settings.tools.entrypoint_group,
        fail_fast=settings.tools.discovery_fail_fast,
    )


def create_default_executor() -> ToolExecutor:
    """Create an executor preloaded with built-in internal tools."""
    return register_default_tools(ToolExecutor())
