"""
High-level code execution interface.

Orchestrates: security pre-check → Docker sandbox → result formatting.
This is the single entry point consumed by ``PythonExecutorTool``.
"""

from __future__ import annotations

from structlog import get_logger

from app.config import get_settings
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from app.sandbox.security import SecurityChecker

logger = get_logger()


class CodeExecutor:
    """
    Facade that combines security validation and Docker execution.

    Usage::

        executor = CodeExecutor()
        await executor.initialize()
        result = await executor.execute("print(1 + 1)")
        await executor.shutdown()
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._config = settings.sandbox
        self._manager = DockerSandboxManager(config=self._config)
        self._security = SecurityChecker()
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize Docker manager (connect + build image)."""
        if self._initialized:
            return
        await self._manager.initialize()
        self._initialized = True
        logger.info("CodeExecutor initialized")

    async def shutdown(self) -> None:
        """Release resources."""
        await self._manager.shutdown()
        self._initialized = False
        logger.info("CodeExecutor shut down")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        code: str,
        timeout: float | None = None,
        install_packages: list[str] | None = None,
        enable_network: bool = False,
    ) -> ExecutionResult:
        """
        Validate and execute Python code in the Docker sandbox.

        Args:
            code: Python source code to run.
            timeout: Max execution seconds (clamped to config limits).
            install_packages: Extra pip packages to install before running.
            enable_network: Whether to allow network access for this execution.

        Returns:
            ``ExecutionResult`` with stdout, stderr, exit code, timing, etc.
        """
        if not self._initialized:
            await self.initialize()

        # --- security pre-check ----------------------------------------
        check = self._security.validate(code)
        if not check.passed:
            logger.warning("Code blocked by security checker", reason=check.blocked_reason)
            return ExecutionResult(
                status=ExecutionStatus.SECURITY_BLOCKED,
                error=check.blocked_reason,
            )

        if check.warnings:
            logger.info("Security warnings (informational)", warnings=check.warnings)

        # --- resolve timeout -------------------------------------------
        effective_timeout = min(
            timeout or self._config.execution_timeout,
            self._config.max_execution_timeout,
        )
        effective_timeout = max(effective_timeout, 1.0)

        # --- execute in Docker -----------------------------------------
        request = ExecutionRequest(
            code=code,
            timeout=effective_timeout,
            install_packages=install_packages,
            enable_network=enable_network,
        )

        result = await self._manager.execute(request)

        logger.info(
            "Code execution finished",
            status=result.status.value,
            exit_code=result.exit_code,
            duration=f"{result.execution_time:.2f}s",
        )

        return result
