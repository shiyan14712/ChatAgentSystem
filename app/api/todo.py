"""
Todo-list REST API routes.

Provides a single read endpoint for the frontend to restore
the latest todo snapshot on page refresh or session switch.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from structlog import get_logger

from app.agent.core import ChatAgent
from app.models.schemas import ErrorResponse, TodoList
from app.services.agent_service import get_agent

logger = get_logger()
router = APIRouter(prefix="/sessions", tags=["todo"])


@router.get(
    "/{session_id}/todo-list",
    response_model=TodoList,
    responses={
        204: {"description": "No todo list for this session"},
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Get session todo list",
    description="Retrieve the current todo-list snapshot for a session. "
    "Returns 204 if the session has no active todo list.",
)
async def get_todo_list(
    session_id: UUID,
    agent: ChatAgent = Depends(get_agent),
) -> TodoList | Response:
    """
    Frontend calls this on page refresh / session switch to
    restore the latest todo-list state.
    """
    # Verify session exists
    session = await agent.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if agent.todo_service is None:
        return Response(status_code=204)

    todo = await agent.todo_service.get_todo_list(session_id)
    if todo is None:
        return Response(status_code=204)

    return todo
