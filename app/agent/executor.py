"""
Tool Executor - Handles tool/function execution for the agent.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from structlog import get_logger

from app.config import get_settings
from app.models.schemas import ToolDefinition

logger = get_logger()
settings = get_settings()


@dataclass
class ToolResult:
    """Result of a tool execution."""
    
    tool_call_id: str
    name: str
    success: bool = True
    content: str = ""
    error: str | None = None
    execution_time: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Abstract base class for tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass
    
    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for parameters."""
        return {"type": "object", "properties": {}}
    
    @property
    def required(self) -> list[str]:
        """Required parameters."""
        return []
    
    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool."""
        pass
    
    def to_definition(self) -> ToolDefinition:
        """Convert to ToolDefinition."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            required=self.required
        )
    
    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI function format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


class ToolRegistry:
    """Registry for available tools."""
    
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.info(f"Tool registered: {tool.name}")
    
    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        if name in self._tools:
            del self._tools[name]
    
    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def list_tools(self) -> list[BaseTool]:
        """List all registered tools."""
        return list(self._tools.values())
    
    def get_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions."""
        return [tool.to_definition() for tool in self._tools.values()]
    
    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Get tools in OpenAI format."""
        return [tool.to_openai_format() for tool in self._tools.values()]


class ToolExecutor:
    """
    Executes tool calls with parallel execution support.
    
    Features:
    - Parallel tool execution
    - Timeout handling
    - Error recovery
    - Execution logging
    """
    
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        max_parallel: int = 5,
        default_timeout: float = 30.0
    ):
        self.registry = registry or ToolRegistry()
        self.max_parallel = max_parallel
        self.default_timeout = default_timeout
        
        # Execution history
        self._history: dict[UUID, list[ToolResult]] = {}
    
    def register_tool(self, tool: BaseTool) -> None:
        """Register a tool."""
        self.registry.register(tool)
    
    async def execute(
        self,
        tool_calls: list[dict[str, Any]],
        session_id: UUID | None = None
    ) -> list[ToolResult]:
        """
        Execute multiple tool calls.
        
        Args:
            tool_calls: List of tool call objects from LLM
            session_id: Optional session ID for tracking
            
        Returns:
            List of tool results
        """
        if not tool_calls:
            return []
        
        # Create semaphore for parallel execution
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        async def execute_with_semaphore(tc: dict) -> ToolResult:
            async with semaphore:
                return await self._execute_single(tc)
        
        # Execute in parallel
        tasks = [execute_with_semaphore(tc) for tc in tool_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to error results
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(ToolResult(
                    tool_call_id=tool_calls[i].get("id", ""),
                    name=tool_calls[i].get("function", {}).get("name", "unknown"),
                    success=False,
                    error=str(result)
                ))
            else:
                final_results.append(result)
        
        # Store in history
        if session_id:
            if session_id not in self._history:
                self._history[session_id] = []
            self._history[session_id].extend(final_results)
        
        return final_results
    
    async def _execute_single(self, tool_call: dict) -> ToolResult:
        """Execute a single tool call."""
        tool_id = tool_call.get("id", "")
        function = tool_call.get("function", {})
        name = function.get("name", "")
        
        # Parse arguments
        try:
            arguments = json.loads(function.get("arguments", "{}"))
        except json.JSONDecodeError:
            return ToolResult(
                tool_call_id=tool_id,
                name=name,
                success=False,
                error="Invalid JSON arguments"
            )
        
        # Get tool
        tool = self.registry.get(name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_id,
                name=name,
                success=False,
                error=f"Tool '{name}' not found"
            )
        
        # Execute with timeout
        start_time = datetime.utcnow()
        
        try:
            async with asyncio.timeout(self.default_timeout):
                result = await tool.execute(**arguments)
            
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            
            return ToolResult(
                tool_call_id=tool_id,
                name=name,
                success=True,
                content=result,
                execution_time=execution_time
            )
            
        except asyncio.TimeoutError:
            return ToolResult(
                tool_call_id=tool_id,
                name=name,
                success=False,
                error=f"Tool execution timed out after {self.default_timeout}s"
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_id,
                name=name,
                success=False,
                error=str(e)
            )
    
    def get_history(self, session_id: UUID) -> list[ToolResult]:
        """Get execution history for a session."""
        return self._history.get(session_id, [])
    
    def clear_history(self, session_id: UUID | None = None) -> None:
        """Clear execution history."""
        if session_id:
            self._history.pop(session_id, None)
        else:
            self._history.clear()


def create_default_executor() -> ToolExecutor:
    """Create an executor with default tools."""
    from app.agent.tools.register import create_default_executor as create_executor

    return create_executor()
