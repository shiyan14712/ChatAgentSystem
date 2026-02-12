"""Search tool implementation."""

from typing import Any

from app.agent.executor import BaseTool


class SearchTool(BaseTool):
    """Example search tool."""

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Search for information on the web"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str) -> str:
        """Execute search."""
        return f"Search results for: {query}"
