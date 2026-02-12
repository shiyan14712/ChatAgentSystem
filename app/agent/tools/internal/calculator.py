"""Calculator tool implementation."""

from typing import Any

from app.agent.executor import BaseTool


class CalculatorTool(BaseTool):
    """Example calculator tool."""

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "Perform mathematical calculations"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate"
                }
            },
            "required": ["expression"]
        }

    async def execute(self, expression: str) -> str:
        """Execute calculation."""
        try:
            allowed_chars = set("0123456789+-*/(). ")
            if not all(char in allowed_chars for char in expression):
                return "Error: Invalid characters in expression"

            result = eval(expression)
            return str(result)
        except Exception as exc:
            return f"Error: {str(exc)}"
