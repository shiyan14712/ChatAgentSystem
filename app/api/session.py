"""
Session management API routes.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from structlog import get_logger

from app.agent.core import ChatAgent
from app.models.schemas import (
    ConversationSession,
    ErrorResponse,
    SessionInfo,
    SessionListResponse,
)
from app.services.agent_service import get_agent

logger = get_logger()
router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get(
    "/",
    response_model=SessionListResponse,
    summary="List all sessions",
    description="Get a paginated list of all conversation sessions"
)
async def list_sessions(
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    agent: ChatAgent = Depends(get_agent)
) -> SessionListResponse:
    """
    List all conversation sessions.
    
    Sessions are sorted by last update time (most recent first).
    """
    sessions, total = await agent.list_sessions(page, page_size)
    
    return SessionListResponse(
        sessions=sessions,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get(
    "/{session_id}",
    response_model=ConversationSession,
    responses={
        404: {"model": ErrorResponse}
    },
    summary="Get a session",
    description="Get details of a specific session"
)
async def get_session(
    session_id: UUID,
    agent: ChatAgent = Depends(get_agent)
) -> ConversationSession:
    """Get a specific session by ID."""
    session = await agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete(
    "/{session_id}",
    summary="Delete a session",
    description="Delete a conversation session"
)
async def delete_session(
    session_id: UUID,
    agent: ChatAgent = Depends(get_agent)
) -> dict:
    """Delete a session."""
    success = await agent.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": str(session_id)}


@router.post(
    "/",
    response_model=ConversationSession,
    summary="Create a new session",
    description="Create a new conversation session"
)
async def create_session(
    agent: ChatAgent = Depends(get_agent)
) -> ConversationSession:
    """Create a new session."""
    session = await agent.memory.create_session()
    return session
