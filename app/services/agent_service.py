"""
Agent service for dependency injection and lifecycle management.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from structlog import get_logger

from app.agent.core import ChatAgent
from app.config import get_settings

logger = get_logger()
settings = get_settings()

# Global agent instance
_agent: ChatAgent | None = None


async def get_agent() -> ChatAgent:
    """Get the agent instance for dependency injection."""
    global _agent
    if _agent is None:
        raise RuntimeError("Agent not initialized. Use agent_lifespan.")
    return _agent


@asynccontextmanager
async def agent_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage agent lifecycle."""
    global _agent
    
    logger.info("Initializing agent...")
    
    # Create and start agent
    _agent = ChatAgent()
    await _agent.start()
    
    logger.info("Agent started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down agent...")
    await _agent.stop()
    _agent = None
    
    logger.info("Agent stopped")
