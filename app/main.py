"""
Chat Agent Framework - Main Application Entry Point.

A production-ready chat agent framework with OpenAI Compatible API support.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html

from app.api import chat_router, session_router
from app.config import get_settings
from app.services import agent_lifespan

settings = get_settings()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        0 if settings.debug else 20
    )
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    async with agent_lifespan(app):
        yield


# Create FastAPI application
app = FastAPI(
    title="Chat Agent Framework",
    description="""
A production-ready chat agent framework with OpenAI Compatible API support.

## Features

- **Intelligent Memory Management**: Automatic context compression at 92% threshold
- **Agent Loop System**: Async scheduling with interrupt/resume support
- **Message Pipeline**: Priority-based message queuing with middleware support
- **Tool Execution**: Parallel tool execution with timeout handling

## Usage

1. Create a session or use existing one
2. Send chat messages via `/chat` or `/chat/stream`
3. Generate conversation titles with `/chat/title`
4. Manage sessions via `/sessions` endpoints
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler."""
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
            "details": str(exc) if settings.debug else None
        }
    )


# Include routers
app.include_router(chat_router, prefix="/api/v1")
app.include_router(session_router, prefix="/api/v1")


# Health check endpoint
@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": settings.environment
    }


# Root endpoint
@app.get("/", tags=["root"])
async def root() -> dict:
    """Root endpoint with API information."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
        reload=settings.debug
    )
