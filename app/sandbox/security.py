"""
AST-based security validation for sandbox code execution.

Provides a lightweight first layer of defense before Docker isolation.
Docker provides the real OS-level security boundary; this module is for:
  1. Early syntax validation (fast-fail before container creation)
  2. Blocking known resource-exhaustion patterns (fork bombs, etc.)
  3. Warning about potentially dangerous operations (logged, not blocked)
"""

from __future__ import annotations

import ast

from structlog import get_logger

from app.sandbox.models import SecurityCheckResult

logger = get_logger()

# Modules that can cause resource exhaustion or escape even inside Docker
BLOCKED_MODULES: set[str] = {
    "ctypes",
    "multiprocessing",
    "signal",
    "_thread",
}

# Patterns to warn about (logged but not blocked — Docker handles isolation)
WARN_CALL_PATTERNS: set[str] = {
    "os.system",
    "os.popen",
    "os.exec",
    "os.execv",
    "os.execve",
    "os.fork",
    "subprocess.call",
    "subprocess.run",
    "subprocess.Popen",
    "shutil.rmtree",
    "__import__",
    "eval",
    "exec",
    "compile",
}


class SecurityChecker:
    """
    AST-based code security pre-check.

    With Docker isolation as the primary security boundary,
    this checker focuses on:
      - Syntax validation (avoid wasting container resources)
      - Blocking modules that enable resource exhaustion / container escape
      - Warning about suspicious patterns (informational only)
    """

    def __init__(self, blocked_modules: set[str] | None = None) -> None:
        self.blocked_modules = blocked_modules or BLOCKED_MODULES

    def validate(self, code: str) -> SecurityCheckResult:
        """Validate Python code before execution."""
        # 1. Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return SecurityCheckResult(
                passed=False,
                blocked_reason=f"Syntax error at line {exc.lineno}: {exc.msg}",
            )

        warnings: list[str] = []

        for node in ast.walk(tree):
            # 2. Block dangerous imports
            result = self._check_imports(node)
            if result is not None:
                return result

            # 3. Collect warnings for suspicious calls
            warning = self._check_calls(node)
            if warning:
                warnings.append(warning)

        if warnings:
            logger.info("Security warnings for submitted code", warnings=warnings)

        return SecurityCheckResult(passed=True, warnings=warnings)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_imports(self, node: ast.AST) -> SecurityCheckResult | None:
        """Return a failure result if the node imports a blocked module."""
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in self.blocked_modules:
                    return SecurityCheckResult(
                        passed=False,
                        blocked_reason=f"Blocked module: {alias.name}",
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in self.blocked_modules:
                    return SecurityCheckResult(
                        passed=False,
                        blocked_reason=f"Blocked module: {node.module}",
                    )
        return None

    def _check_calls(self, node: ast.AST) -> str | None:
        """Return a warning string if the call matches a known pattern."""
        if not isinstance(node, ast.Call):
            return None

        name = self._resolve_call_name(node)
        if name in WARN_CALL_PATTERNS:
            return f"Potentially dangerous call: {name}"
        return None

    @staticmethod
    def _resolve_call_name(node: ast.Call) -> str:
        """Best-effort extraction of a dotted call name from a Call AST node."""
        if isinstance(node.func, ast.Name):
            return node.func.id

        parts: list[str] = []
        current: ast.expr = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)

        return ".".join(reversed(parts))
