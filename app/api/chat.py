"""
Chat API routes.
"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from structlog import get_logger

from app.agent.core import ChatAgent
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    StreamChunk,
    TitleRequest,
    TitleResponse,
)
from app.services.agent_service import get_agent

logger = get_logger()
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    },
    summary="Send a chat message",
    description="Send a message to the agent and get a response"
)
async def chat(
    request: ChatRequest,
    agent: ChatAgent = Depends(get_agent)
) -> ChatResponse:
    """
    Send a chat message and receive a response.
    
    - **message**: The user's message
    - **session_id**: Optional session ID to continue a conversation
    - **stream**: Whether to stream the response (ignored for this endpoint)
    - **tools**: Optional list of tools available for this request
    """
    try:
        response = await agent.chat(
            message=request.message,
            session_id=request.session_id,
            tools=request.tools
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/stream",
    summary="Stream a chat response",
    description="Send a message and stream the response"
)
async def chat_stream(
    request: ChatRequest,
    agent: ChatAgent = Depends(get_agent)
) -> StreamingResponse:
    """
    Stream a chat response.
    
    Returns Server-Sent Events (SSE) with the following event types:
    - **session**: Contains the session ID
    - **thinking**: Agent's thinking process (if available)
    - **content**: Content chunks
    - **done**: Stream completed
    - **error**: Error occurred
    """
    async def event_generator():
        try:
            async for chunk in agent.chat_stream(
                message=request.message,
                session_id=request.session_id,
                tools=request.tools
            ):
                # Format as SSE
                data = chunk.model_dump_json()
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}")
            error_chunk = StreamChunk(
                session_id=request.session_id or UUID(int=0),
                type="error",
                delta=str(e)
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post(
    "/title",
    response_model=TitleResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    summary="Generate conversation title",
    description="Generate a title for the conversation based on its content"
)
async def generate_title(
    request: TitleRequest,
    agent: ChatAgent = Depends(get_agent)
) -> TitleResponse:
    """
    Generate a title for a conversation.
    
    This should be called after the first exchange to set the conversation title.
    """
    try:
        title = await agent.generate_title(request.session_id)
        return TitleResponse(
            session_id=request.session_id,
            title=title
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Title generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/interrupt/{session_id}",
    summary="Interrupt a session",
    description="Interrupt an ongoing chat session"
)
async def interrupt_session(
    session_id: UUID,
    agent: ChatAgent = Depends(get_agent)
) -> dict:
    """Interrupt an ongoing session."""
    success = await agent.interrupt(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or not running")
    return {"status": "interrupted", "session_id": str(session_id)}


@router.get(
    "/stats",
    summary="Get agent statistics",
    description="Get statistics about the agent's state"
)
async def get_stats(
    agent: ChatAgent = Depends(get_agent)
) -> dict:
    """Get agent statistics."""
    return agent.get_stats()
