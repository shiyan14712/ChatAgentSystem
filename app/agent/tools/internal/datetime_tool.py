"""DateTime tool implementation."""

from datetime import datetime
from typing import Any

import pytz

from app.agent.executor import BaseTool


class DateTimeTool(BaseTool):
    """Tool to get current date and time."""

    @property
    def name(self) -> str:
        return "get_datetime"

    @property
    def description(self) -> str:
        return "Get the current date and time"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Timezone (e.g., 'UTC', 'Asia/Shanghai')"
                }
            }
        }

    async def execute(self, timezone: str = "UTC") -> str:
        """Get current datetime."""
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            return now.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception as exc:
            return f"Error: {str(exc)}"
