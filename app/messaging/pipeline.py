"""
Message processing pipeline with middleware support.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, TypeVar
from uuid import UUID

from structlog import get_logger

from app.models.schemas import MessageStatus

logger = get_logger()

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class PipelineContext:
    """Context passed through the pipeline."""
    
    session_id: UUID
    message_id: UUID
    data: Any = None
    status: MessageStatus = MessageStatus.PENDING
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    
    # Processing history
    history: list[dict[str, Any]] = field(default_factory=list)
    
    def add_history(self, stage: str, result: Any = None, error: str | None = None) -> None:
        """Add a processing history entry."""
        self.history.append({
            "stage": stage,
            "timestamp": datetime.utcnow().isoformat(),
            "result": result,
            "error": error,
        })


class PipelineMiddleware(ABC, Generic[T, R]):
    """Abstract middleware for the pipeline."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Middleware name."""
        pass
    
    @abstractmethod
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Process the context and call next handler."""
        pass


class LoggingMiddleware(PipelineMiddleware):
    """Middleware for logging message processing."""
    
    @property
    def name(self) -> str:
        return "logging"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Log processing start and end."""
        logger.info(
            "Pipeline processing started",
            session_id=str(context.session_id),
            message_id=str(context.message_id)
        )
        
        start_time = time.time()
        
        try:
            result = await next_handler(context)
            
            duration = time.time() - start_time
            logger.info(
                "Pipeline processing completed",
                session_id=str(context.session_id),
                message_id=str(context.message_id),
                duration_ms=round(duration * 1000, 2)
            )
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                "Pipeline processing failed",
                session_id=str(context.session_id),
                message_id=str(context.message_id),
                error=str(e),
                duration_ms=round(duration * 1000, 2)
            )
            raise


class TimingMiddleware(PipelineMiddleware):
    """Middleware for timing processing stages."""
    
    @property
    def name(self) -> str:
        return "timing"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Track timing for each stage."""
        context.metadata["timing"] = {}
        
        result = await next_handler(context)
        
        context.completed_at = datetime.utcnow()
        total_time = (context.completed_at - context.started_at).total_seconds()
        context.metadata["timing"]["total_seconds"] = total_time
        
        return result


class RetryMiddleware(PipelineMiddleware):
    """Middleware for retrying failed operations."""
    
    def __init__(self, max_retries: int = 3, delay: float = 1.0):
        self.max_retries = max_retries
        self.delay = delay
    
    @property
    def name(self) -> str:
        return "retry"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Retry on failure."""
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                return await next_handler(context)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"Retry attempt {attempt + 1}/{self.max_retries}",
                        error=str(e)
                    )
                    await asyncio.sleep(self.delay * (attempt + 1))
        
        raise last_error


class ValidationMiddleware(PipelineMiddleware):
    """Middleware for validating messages."""
    
    @property
    def name(self) -> str:
        return "validation"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Validate message data."""
        if context.data is None:
            context.status = MessageStatus.FAILED
            context.error = "Message data is None"
            raise ValueError("Message data cannot be None")
        
        # Add validation result to context
        context.metadata["validated"] = True
        
        return await next_handler(context)


class RateLimitMiddleware(PipelineMiddleware):
    """Middleware for rate limiting."""
    
    def __init__(self, requests_per_second: float = 10.0):
        self.rate = requests_per_second
        self._semaphore = asyncio.Semaphore(int(requests_per_second * 2))
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()
    
    @property
    def name(self) -> str:
        return "rate_limit"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], asyncio.Future[R]]
    ) -> R:
        """Apply rate limiting."""
        async with self._semaphore:
            async with self._lock:
                now = time.time()
                min_interval = 1.0 / self.rate
                elapsed = now - self._last_request_time
                
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                
                self._last_request_time = time.time()
            
            return await next_handler(context)


class MessagePipeline:
    """
    Message processing pipeline with middleware support.
    
    Features:
    - Configurable middleware chain
    - Async processing
    - Error handling
    - Context propagation
    """
    
    def __init__(self):
        self._middlewares: list[PipelineMiddleware] = []
        self._handler: Callable | None = None
    
    def add_middleware(self, middleware: PipelineMiddleware) -> "MessagePipeline":
        """Add a middleware to the pipeline."""
        self._middlewares.append(middleware)
        return self
    
    def use(self, middleware: PipelineMiddleware) -> "MessagePipeline":
        """Alias for add_middleware."""
        return self.add_middleware(middleware)
    
    def set_handler(self, handler: Callable) -> "MessagePipeline":
        """Set the final handler."""
        self._handler = handler
        return self
    
    async def execute(self, context: PipelineContext) -> Any:
        """Execute the pipeline."""
        if not self._handler:
            raise RuntimeError("No handler set for pipeline")
        
        # Build middleware chain
        async def final_handler(ctx: PipelineContext) -> Any:
            ctx.status = MessageStatus.PROCESSING
            try:
                result = await self._handler(ctx)
                ctx.status = MessageStatus.COMPLETED
                return result
            except Exception as e:
                ctx.status = MessageStatus.FAILED
                ctx.error = str(e)
                raise
        
        # Chain middlewares
        handler = final_handler
        for middleware in reversed(self._middlewares):
            current_handler = handler
            
            async def wrapped_handler(
                ctx: PipelineContext,
                mw: PipelineMiddleware = middleware,
                next_h: Callable = current_handler
            ) -> Any:
                return await mw.process(ctx, next_h)
            
            handler = wrapped_handler
        
        return await handler(context)
    
    def create_context(
        self,
        session_id: UUID,
        message_id: UUID,
        data: Any = None,
        metadata: dict[str, Any] | None = None
    ) -> PipelineContext:
        """Create a new pipeline context."""
        return PipelineContext(
            session_id=session_id,
            message_id=message_id,
            data=data,
            metadata=metadata or {}
        )


def create_default_pipeline() -> MessagePipeline:
    """Create a pipeline with default middlewares."""
    return (
        MessagePipeline()
        .use(LoggingMiddleware())
        .use(TimingMiddleware())
        .use(ValidationMiddleware())
        .use(RetryMiddleware(max_retries=3))
        .use(RateLimitMiddleware(requests_per_second=10.0))
    )
