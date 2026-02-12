"""Tool discovery for built-in and external providers (SPI style)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any

from structlog import get_logger

from app.agent.executor import BaseTool, ToolExecutor

logger = get_logger()

DEFAULT_ENTRYPOINT_GROUP = "chat_agent_framework.tools"
DEFAULT_INTERNAL_PACKAGE = "app.agent.tools.internal"


def _iter_builtin_tool_classes(package_name: str) -> Iterable[type[BaseTool]]:
    """Yield concrete BaseTool subclasses from the internal tools package."""
    package = importlib.import_module(package_name)

    for module_info in pkgutil.walk_packages(package.__path__, f"{package_name}."):
        module = importlib.import_module(module_info.name)

        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            if not issubclass(cls, BaseTool):
                continue
            if inspect.isabstract(cls):
                continue
            yield cls


def _coerce_loaded_to_tools(loaded: Any) -> list[BaseTool]:
    """Convert an entry point loaded object into tool instances."""
    if inspect.isclass(loaded) and issubclass(loaded, BaseTool):
        return [loaded()]

    if isinstance(loaded, BaseTool):
        return [loaded]

    if callable(loaded):
        return _coerce_loaded_to_tools(loaded())

    if isinstance(loaded, Iterable) and not isinstance(loaded, str):
        tools: list[BaseTool] = []
        for item in loaded:
            tools.extend(_coerce_loaded_to_tools(item))
        return tools

    raise TypeError(f"Unsupported entry point object type: {type(loaded)!r}")


def discover_tools(
    include_builtin: bool = True,
    include_entrypoints: bool = True,
    entrypoint_group: str = DEFAULT_ENTRYPOINT_GROUP,
    builtin_package: str = DEFAULT_INTERNAL_PACKAGE,
    fail_fast: bool = False,
) -> list[BaseTool]:
    """Discover tools from internal package and external entry points."""
    discovered: list[BaseTool] = []
    registered_names: set[str] = set()

    def add_tool(tool: BaseTool, source: str) -> None:
        if tool.name in registered_names:
            logger.warning(
                "Duplicate tool name ignored during discovery",
                tool_name=tool.name,
                source=source,
            )
            return

        registered_names.add(tool.name)
        discovered.append(tool)

    if include_builtin:
        try:
            for cls in _iter_builtin_tool_classes(builtin_package):
                add_tool(cls(), source=builtin_package)
        except Exception as exc:
            logger.error("Built-in tool discovery failed", error=str(exc))
            if fail_fast:
                raise

    if include_entrypoints:
        eps = entry_points(group=entrypoint_group)
        for ep in eps:
            try:
                loaded = ep.load()
                tools = _coerce_loaded_to_tools(loaded)
                for tool in tools:
                    add_tool(tool, source=f"entrypoint:{ep.name}")
            except Exception as exc:
                logger.error(
                    "External tool entry point failed",
                    entrypoint=ep.name,
                    group=entrypoint_group,
                    error=str(exc),
                )
                if fail_fast:
                    raise

    return discovered


def register_discovered_tools(
    executor: ToolExecutor,
    include_builtin: bool = True,
    include_entrypoints: bool = True,
    entrypoint_group: str = DEFAULT_ENTRYPOINT_GROUP,
    fail_fast: bool = False,
) -> ToolExecutor:
    """Discover and register tools onto an executor."""
    tools = discover_tools(
        include_builtin=include_builtin,
        include_entrypoints=include_entrypoints,
        entrypoint_group=entrypoint_group,
        fail_fast=fail_fast,
    )

    for tool in tools:
        executor.register_tool(tool)

    logger.info(
        "Tool discovery completed",
        count=len(tools),
        include_builtin=include_builtin,
        include_entrypoints=include_entrypoints,
        entrypoint_group=entrypoint_group,
    )
    return executor
