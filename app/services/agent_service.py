"""
Agent service for dependency injection and lifecycle management.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from structlog import get_logger

from app.agent.core import ChatAgent
from app.config import get_settings
from app.database import SessionRepository, init_db, close_db, get_session_factory
from app.database.todo_repository import TodoRepository
from app.services.todo_service import TodoService

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
    
    # Initialize database
    try:
        await init_db()
        sf = get_session_factory()
        repository = SessionRepository(sf)
        todo_repo = TodoRepository(sf)
        todo_service = TodoService(todo_repo)
        logger.info("Database persistence enabled")
    except Exception as e:
        logger.warning(
            "Database initialization failed — running in memory-only mode",
            error=str(e),
        )
        repository = None
        todo_service = None
    
    # Create and start agent
    _agent = ChatAgent(repository=repository, todo_service=todo_service)
    await _agent.start()
    
    logger.info("Agent started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down agent...")
    await _agent.stop()
    _agent = None
    
    # Close database
    await close_db()
    
    logger.info("Agent stopped")
