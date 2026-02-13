"""
Python code execution tool — ``BaseTool`` implementation.

Allows the LLM to write and run Python code in a Docker-based sandbox.
Automatically discovered by the tool-discovery mechanism (SPI).
"""

from __future__ import annotations

from typing import Any

from app.agent.executor import BaseTool
from app.sandbox.executor import CodeExecutor
from app.sandbox.models import ExecutionStatus


class PythonExecutorTool(BaseTool):
    """Execute Python code in a secure Docker sandbox."""

    def __init__(self) -> None:
        self._executor = CodeExecutor()

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "python_executor"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in an isolated Docker sandbox and return stdout/stderr. "
            "Pre-installed packages: numpy, pandas, matplotlib, sympy, scipy, requests. "
            "Use for: mathematical computation, data analysis, chart generation, "
            "algorithm verification, or any task that benefits from running real code. "
            "The code's standard output will be captured and returned as the result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source code to execute",
                },
                "install_packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Extra pip packages to install before running (optional). "
                        "Only use this for packages not already pre-installed."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": "Execution timeout in seconds (default 30, max 120)",
                },
            },
            "required": ["code"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        code: str,
        install_packages: list[str] | None = None,
        timeout: float = 30.0,
    ) -> str:
        """Execute Python code and return a formatted result string."""
        result = await self._executor.execute(
            code=code,
            timeout=timeout,
            install_packages=install_packages,
        )

        # --- format output for the LLM --------------------------------
        if result.status == ExecutionStatus.SECURITY_BLOCKED:
            return f"⚠️ Security check failed: {result.error}"

        if result.status == ExecutionStatus.TIMEOUT:
            return (
                f"⏰ Execution timed out after {timeout:.0f}s. "
                "Consider optimising your code or increasing the timeout."
            )

        parts: list[str] = []

        if result.stdout:
            parts.append(f"STDOUT:\n{result.stdout}")

        if result.stderr:
            label = "STDERR" if result.exit_code != 0 else "WARNINGS"
            parts.append(f"{label}:\n{result.stderr}")

        if result.exit_code != 0:
            parts.append(f"Exit code: {result.exit_code}")

        if not parts:
            parts.append("(no output)")

        if result.truncated:
            parts.append("[Output was truncated due to size limit]")

        parts.append(f"Execution time: {result.execution_time:.2f}s")

        return "\n\n".join(parts)
