"""
manage_todo_list — internal tool that the LLM calls to create / update
the session todo list.

The tool is intentionally idempotent: each invocation receives the
*complete* todo list (title + all items with statuses).  The service
layer replaces the previous list, bumps the revision, and pushes the
snapshot to the SSE stream.
"""

from __future__ import annotations

import json
from typing import Any

from structlog import get_logger

from app.agent.executor import BaseTool

logger = get_logger()


class ManageTodoListTool(BaseTool):
    """
    LLM-facing tool for creating / updating a session todo list.

    The LLM is instructed (via system prompt) to call this tool whenever
    a multi-step task is detected.  The tool receives the full desired
    state of the todo list and delegates to ``TodoService``.
    """

    @property
    def name(self) -> str:
        return "manage_todo_list"

    @property
    def description(self) -> str:
        return (
            "Create or update a task progress list. Call this whenever you start "
            "a multi-step task. Send the COMPLETE list every time (not a diff). "
            "Statuses: pending (not started), running (currently executing), "
            "completed (finished). Only ONE item should be 'running' at a time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title describing the overall task",
                },
                "items": {
                    "type": "array",
                    "description": "Complete ordered list of todo items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "Short description of this step",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "running", "completed"],
                                "description": "Current status",
                            },
                        },
                        "required": ["label", "status"],
                    },
                },
            },
            "required": ["title", "items"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """
        Execution is handled specially by the agent loop — the tool
        executor recognises ``manage_todo_list`` and delegates to
        ``TodoService`` instead of calling this method directly.

        If this method *is* called (e.g. in tests), return a
        confirmation message.
        """
        title = kwargs.get("title", "")
        items = kwargs.get("items", [])
        return json.dumps(
            {
                "ok": True,
                "message": f"Todo list '{title}' accepted with {len(items)} items.",
            }
        )
