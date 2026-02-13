"""
Docker-based sandbox manager for Python code execution.

Lifecycle per execution:
  1. Create an ephemeral container from the pre-built sandbox image
  2. Copy user code into the container via tar archive (no volume mounts)
  3. Start the container and wait for exit (with timeout)
  4. Capture stdout / stderr
  5. Force-remove the container

All Docker SDK calls are synchronous and wrapped with ``asyncio.to_thread``
to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import DockerException, ImageNotFound
from structlog import get_logger

from app.sandbox.models import ExecutionRequest, ExecutionResult, ExecutionStatus

if TYPE_CHECKING:
    from app.config import SandboxConfig

logger = get_logger()

# Root of the project (for locating sandbox/Dockerfile)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class DockerSandboxManager:
    """
    Manages Docker containers for secure Python code execution.

    The manager is designed to be long-lived (created once at application
    startup).  Each ``execute()`` call creates a short-lived throw-away
    container.
    """

    def __init__(self, config: "SandboxConfig") -> None:
        self._config = config
        self._client: docker.DockerClient | None = None
        self._image_ready = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Connect to Docker daemon and prepare the sandbox image."""
        try:
            self._client = await asyncio.to_thread(docker.from_env)
            await asyncio.to_thread(self._client.ping)
            logger.info("Docker daemon connected")
        except DockerException as exc:
            logger.error("Cannot connect to Docker", error=str(exc))
            raise RuntimeError(
                "Docker is not available. Install and start Docker to enable code execution."
            ) from exc

        if self._config.auto_build_image:
            await self._ensure_image()

    async def shutdown(self) -> None:
        """Release Docker client resources."""
        if self._client:
            await asyncio.to_thread(self._client.close)
            self._client = None
            logger.info("Docker sandbox manager shut down")

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    async def _ensure_image(self) -> None:
        """Build or verify the sandbox Docker image."""
        assert self._client is not None
        try:
            await asyncio.to_thread(self._client.images.get, self._config.image_name)
            self._image_ready = True
            logger.info("Sandbox image found", image=self._config.image_name)
        except ImageNotFound:
            logger.info("Sandbox image not found — building …", image=self._config.image_name)
            await self._build_image()

    async def _build_image(self) -> None:
        """Build the sandbox image from ``sandbox/Dockerfile``."""
        assert self._client is not None
        dockerfile_dir = _PROJECT_ROOT / "sandbox"
        if not (dockerfile_dir / "Dockerfile").exists():
            raise FileNotFoundError(
                f"Sandbox Dockerfile not found at {dockerfile_dir / 'Dockerfile'}"
            )

        def _build() -> None:
            self._client.images.build(  # type: ignore[union-attr]
                path=str(dockerfile_dir),
                tag=self._config.image_name,
                rm=True,
            )

        await asyncio.to_thread(_build)
        self._image_ready = True
        logger.info("Sandbox image built", image=self._config.image_name)

    # ------------------------------------------------------------------
    # Code execution
    # ------------------------------------------------------------------

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Run Python code inside an ephemeral Docker container."""
        if not self._client or not self._image_ready:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                error="Sandbox not initialized. Call initialize() first.",
            )

        container = None
        start_time = time.monotonic()

        try:
            # --- prepare script -------------------------------------------
            script = self._build_script(request)

            # --- create container -----------------------------------------
            container = await asyncio.to_thread(self._create_container, request)

            # --- inject code via tar archive ------------------------------
            await asyncio.to_thread(self._copy_code_to_container, container, script)

            # --- start & wait --------------------------------------------
            await asyncio.to_thread(container.start)

            try:
                exit_info = await asyncio.wait_for(
                    asyncio.to_thread(container.wait),
                    timeout=request.timeout,
                )
                exit_code: int = exit_info.get("StatusCode", -1)
            except asyncio.TimeoutError:
                await self._safe_kill(container)
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    error=f"Execution timed out after {request.timeout}s",
                    execution_time=time.monotonic() - start_time,
                )

            # --- capture output -------------------------------------------
            stdout_raw: bytes = await asyncio.to_thread(
                container.logs, stdout=True, stderr=False
            )
            stderr_raw: bytes = await asyncio.to_thread(
                container.logs, stdout=False, stderr=True
            )

            stdout_str, stderr_str, truncated = self._process_output(
                stdout_raw, stderr_raw
            )

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.ERROR,
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=exit_code,
                execution_time=time.monotonic() - start_time,
                error=stderr_str if exit_code != 0 else None,
                truncated=truncated,
            )

        except Exception as exc:
            logger.error("Sandbox execution error", error=str(exc))
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                error=f"Sandbox error: {exc}",
                execution_time=time.monotonic() - start_time,
            )
        finally:
            if container:
                await self._safe_remove(container)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_script(self, request: ExecutionRequest) -> str:
        """Build the full Python script to execute inside the container."""
        parts: list[str] = []

        # Optional: pip install extra packages
        if request.install_packages:
            pkg_list = ", ".join(repr(p) for p in request.install_packages)
            parts.append(
                "import subprocess as _sp, sys as _sys\n"
                f"_sp.check_call([_sys.executable, '-m', 'pip', 'install', '-q', {pkg_list}])\n"
                "del _sp, _sys\n"
            )

        parts.append(request.code)
        return "\n".join(parts)

    def _create_container(self, request: ExecutionRequest):  # noqa: ANN201
        """Create (but don't start) the sandbox container."""
        assert self._client is not None
        return self._client.containers.create(
            image=self._config.image_name,
            command=["python", "-u", "/workspace/main.py"],
            working_dir=self._config.container_workdir,
            detach=True,
            # Resource limits
            mem_limit=self._config.memory_limit,
            cpu_period=self._config.cpu_period,
            cpu_quota=self._config.cpu_quota,
            pids_limit=self._config.pids_limit,
            # Network isolation
            network_disabled=not (request.enable_network or self._config.network_enabled),
            # Security hardening
            security_opt=["no-new-privileges"],
        )

    @staticmethod
    def _copy_code_to_container(container, code: str) -> None:  # noqa: ANN001
        """Inject code into the container as ``/workspace/main.py`` via tar."""
        buf = io.BytesIO()
        code_bytes = code.encode("utf-8")
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="main.py")
            info.size = len(code_bytes)
            tar.addfile(info, io.BytesIO(code_bytes))
        buf.seek(0)
        container.put_archive("/workspace", buf)

    def _process_output(
        self, stdout_raw: bytes, stderr_raw: bytes
    ) -> tuple[str, str, bool]:
        """Decode and optionally truncate captured output."""
        max_size = self._config.max_output_size
        truncated = False

        stdout_str = stdout_raw.decode("utf-8", errors="replace")
        stderr_str = stderr_raw.decode("utf-8", errors="replace")

        if len(stdout_str) > max_size:
            stdout_str = stdout_str[:max_size] + "\n… [output truncated]"
            truncated = True
        if len(stderr_str) > max_size:
            stderr_str = stderr_str[:max_size] + "\n… [output truncated]"
            truncated = True

        return stdout_str, stderr_str, truncated

    @staticmethod
    async def _safe_kill(container) -> None:  # noqa: ANN001
        try:
            await asyncio.to_thread(container.kill)
        except Exception:
            pass

    @staticmethod
    async def _safe_remove(container) -> None:  # noqa: ANN001
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:
            pass
