"""
Agent Loop - Main execution loop with async scheduling.
"""

import asyncio
import signal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from openai import AsyncOpenAI
from structlog import get_logger

from app.config import get_settings
from app.memory.manager import MemoryManager
from app.messaging.pipeline import MessagePipeline, PipelineContext
from app.messaging.queue import PriorityMessageQueue, QueuedMessage
from app.models.schemas import (
    AgentState,
    ChatRequest,
    ConversationSession,
    Message,
    MessageRole,
    MessageStatus,
    StreamChunk,
)

logger = get_logger()
settings = get_settings()


class LoopState(str, Enum):
    """Loop execution state."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class Checkpoint:
    """Checkpoint for recovery."""
    
    id: UUID
    iteration: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    state: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)


class AgentLoop:
    """
    Main agent loop with async scheduling.
    
    Features:
    - Async core scheduler
    - Interrupt and resume support
    - Multi-layer exception handling
    - Checkpoint-based recovery
    """
    
    def __init__(
        self,
        client: AsyncOpenAI,
        memory_manager: MemoryManager,
        message_queue: PriorityMessageQueue,
        pipeline: MessagePipeline | None = None
    ):
        self.client = client
        self.memory = memory_manager
        self.queue = message_queue
        self.pipeline = pipeline or self._create_default_pipeline()
        
        # Configuration
        self.config = settings.agent
        
        # State management
        self._state = LoopState.STOPPED
        self._agent_states: dict[UUID, AgentState] = {}
        self._checkpoints: dict[UUID, list[Checkpoint]] = {}
        self._tasks: dict[UUID, asyncio.Task] = {}
        
        # Control
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._interrupt_events: dict[UUID, asyncio.Event] = {}
        
        # Callbacks
        self._on_message_callbacks: list[Callable] = []
        self._on_error_callbacks: list[Callable] = []
        self._on_complete_callbacks: list[Callable] = []
        
        logger.info("Agent loop initialized")
    
    @property
    def state(self) -> LoopState:
        """Get current loop state."""
        return self._state
    
    async def start(self) -> None:
        """Start the agent loop."""
        if self._state == LoopState.RUNNING:
            logger.warning("Agent loop already running")
            return
        
        self._state = LoopState.RUNNING
        self._stop_event.clear()
        self._pause_event.clear()
        
        logger.info("Agent loop started")
        
        # Start main processing loop
        asyncio.create_task(self._main_loop())
    
    async def stop(self, timeout: float = 10.0) -> None:
        """Stop the agent loop gracefully."""
        if self._state == LoopState.STOPPED:
            return
        
        self._state = LoopState.STOPPING
        self._stop_event.set()
        
        # Cancel all running tasks
        for session_id, task in self._tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=timeout)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        self._state = LoopState.STOPPED
        logger.info("Agent loop stopped")
    
    async def pause(self) -> None:
        """Pause the agent loop."""
        if self._state == LoopState.RUNNING:
            self._state = LoopState.PAUSED
            self._pause_event.set()
            logger.info("Agent loop paused")
    
    async def resume(self) -> None:
        """Resume the agent loop."""
        if self._state == LoopState.PAUSED:
            self._state = LoopState.RUNNING
            self._pause_event.clear()
            logger.info("Agent loop resumed")
    
    async def interrupt(self, session_id: UUID) -> bool:
        """Interrupt a specific session."""
        if session_id in self._interrupt_events:
            self._interrupt_events[session_id].set()
            
            # Update agent state
            if session_id in self._agent_states:
                self._agent_states[session_id].status = MessageStatus.INTERRUPTED
            
            logger.info("Session interrupted", session_id=str(session_id))
            return True
        return False
    
    async def submit(
        self,
        request: ChatRequest,
        stream_callback: Callable | None = None
    ) -> UUID:
        """
        Submit a new chat request.
        
        Args:
            request: Chat request
            stream_callback: Optional callback for streaming responses
            
        Returns:
            Session ID
        """
        # Get or create session
        session_id = request.session_id
        if session_id:
            session = await self.memory.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
        else:
            session = await self.memory.create_session()
            session_id = session.id
        
        # Create user message
        user_message = Message(
            role=MessageRole.USER,
            content=request.message
        )
        await self.memory.add_message(session_id, user_message)
        
        # Initialize agent state
        agent_state = AgentState(session_id=session_id)
        self._agent_states[session_id] = agent_state
        
        # Create interrupt event
        self._interrupt_events[session_id] = asyncio.Event()
        
        # Enqueue the request
        await self.queue.enqueue(
            content={
                "request": request,
                "session_id": session_id,
                "stream_callback": stream_callback
            },
            priority=5,
            session_id=session_id
        )
        
        logger.info(
            "Request submitted",
            session_id=str(session_id),
            message_preview=request.message[:50]
        )
        
        return session_id
    
    def on_message(self, callback: Callable) -> None:
        """Register message callback."""
        self._on_message_callbacks.append(callback)
    
    def on_error(self, callback: Callable) -> None:
        """Register error callback."""
        self._on_error_callbacks.append(callback)
    
    def on_complete(self, callback: Callable) -> None:
        """Register completion callback."""
        self._on_complete_callbacks.append(callback)
    
    async def get_state(self, session_id: UUID) -> AgentState | None:
        """Get agent state for a session."""
        return self._agent_states.get(session_id)
    
    async def get_checkpoint(self, session_id: UUID) -> Checkpoint | None:
        """Get latest checkpoint for a session."""
        checkpoints = self._checkpoints.get(session_id, [])
        return checkpoints[-1] if checkpoints else None
    
    async def _main_loop(self) -> None:
        """Main processing loop."""
        logger.info("Main loop started")
        
        while not self._stop_event.is_set():
            # Check for pause
            if self._pause_event.is_set():
                await asyncio.sleep(0.1)
                continue
            
            try:
                # Get next message
                message = await self.queue.dequeue(timeout=1.0)
                
                if message is None:
                    continue
                
                # Process the message
                task = asyncio.create_task(
                    self._process_message(message)
                )
                self._tasks[message.session_id] = task
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(0.1)
        
        logger.info("Main loop ended")
    
    async def _process_message(self, queued_message: QueuedMessage) -> None:
        """Process a queued message."""
        session_id = queued_message.session_id
        content = queued_message.content
        
        if not session_id:
            return
        
        agent_state = self._agent_states.get(session_id)
        if not agent_state:
            return
        
        interrupt_event = self._interrupt_events.get(session_id)
        
        try:
            # Create pipeline context
            context = self.pipeline.create_context(
                session_id=session_id,
                message_id=queued_message.id,
                data=content
            )
            
            # Set pipeline handler
            self.pipeline.set_handler(
                lambda ctx: self._execute_agent_loop(
                    ctx,
                    agent_state,
                    interrupt_event
                )
            )
            
            # Execute pipeline
            await self.pipeline.execute(context)
            
            # Notify completion
            for callback in self._on_complete_callbacks:
                try:
                    await callback(session_id, agent_state)
                except Exception as e:
                    logger.error(f"Completion callback error: {e}")
        
        except asyncio.CancelledError:
            agent_state.status = MessageStatus.INTERRUPTED
            logger.info("Processing cancelled", session_id=str(session_id))
        
        except Exception as e:
            agent_state.status = MessageStatus.FAILED
            agent_state.error = str(e)
            
            logger.error(
                "Processing failed",
                session_id=str(session_id),
                error=str(e)
            )
            
            for callback in self._on_error_callbacks:
                try:
                    await callback(session_id, e)
                except Exception as err:
                    logger.error(f"Error callback error: {err}")
        
        finally:
            # Cleanup
            if session_id in self._tasks:
                del self._tasks[session_id]
            if session_id in self._interrupt_events:
                del self._interrupt_events[session_id]
    
    async def _execute_agent_loop(
        self,
        context: PipelineContext,
        agent_state: AgentState,
        interrupt_event: asyncio.Event | None
    ) -> Any:
        """Execute the main agent loop for a session."""
        session_id = agent_state.session_id
        content = context.data
        
        request = content.get("request")
        stream_callback = content.get("stream_callback")
        
        max_iterations = self.config.max_iterations
        
        while agent_state.iteration < max_iterations:
            # Check for interruption
            if interrupt_event and interrupt_event.is_set():
                agent_state.status = MessageStatus.INTERRUPTED
                break
            
            # Check for stop
            if self._stop_event.is_set():
                break
            
            # Create checkpoint
            await self._create_checkpoint(session_id, agent_state)
            
            # Get messages for API call
            messages = await self.memory.get_openai_messages(session_id)
            
            # Call LLM
            try:
                if stream_callback:
                    response = await self._stream_llm_call(
                        session_id,
                        messages,
                        request.tools,
                        stream_callback
                    )
                else:
                    response = await self._llm_call(
                        session_id,
                        messages,
                        request.tools
                    )
                
                # Create assistant message
                assistant_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=response.get("content", ""),
                    tool_calls=response.get("tool_calls")
                )
                await self.memory.add_message(session_id, assistant_message)
                
                # Check if done
                if not response.get("tool_calls"):
                    agent_state.status = MessageStatus.COMPLETED
                    break
                
                # Execute tools if any
                if response.get("tool_calls"):
                    tool_results = await self._execute_tools(
                        response["tool_calls"]
                    )
                    
                    for result in tool_results:
                        tool_message = Message(
                            role=MessageRole.TOOL,
                            content=result.get("content", ""),
                            tool_call_id=result.get("tool_call_id")
                        )
                        await self.memory.add_message(session_id, tool_message)
                
                agent_state.iteration += 1
                
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                raise
        
        return agent_state
    
    async def _llm_call(
        self,
        session_id: UUID,
        messages: list[dict],
        tools: list | None = None
    ) -> dict[str, Any]:
        """Make a non-streaming LLM call."""
        response = await self.client.chat.completions.create(
            model=settings.openai.model,
            messages=messages,
            tools=tools,
            max_tokens=settings.openai.max_tokens,
            temperature=settings.openai.temperature,
        )
        
        choice = response.choices[0]
        
        return {
            "content": choice.message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in (choice.message.tool_calls or [])
            ] if choice.message.tool_calls else None
        }
    
    async def _stream_llm_call(
        self,
        session_id: UUID,
        messages: list[dict],
        tools: list | None,
        callback: Callable
    ) -> dict[str, Any]:
        """Make a streaming LLM call."""
        stream = await self.client.chat.completions.create(
            model=settings.openai.model,
            messages=messages,
            tools=tools,
            max_tokens=settings.openai.max_tokens,
            temperature=settings.openai.temperature,
            stream=True,
        )
        
        content = ""
        tool_calls = []
        thinking = ""
        in_thinking = False
        
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            
            if delta:
                # Handle thinking content
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    thinking += delta.reasoning_content
                    await callback(StreamChunk(
                        session_id=session_id,
                        type="thinking",
                        thinking=delta.reasoning_content
                    ))
                
                # Handle regular content
                if delta.content:
                    content += delta.content
                    await callback(StreamChunk(
                        session_id=session_id,
                        type="content",
                        delta=delta.content
                    ))
                
                # Handle tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        # Extend tool_calls list if needed
                        while len(tool_calls) <= tc.index:
                            tool_calls.append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            })
                        
                        if tc.id:
                            tool_calls[tc.index]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls[tc.index]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls[tc.index]["function"]["arguments"] += tc.function.arguments
        
        # Send completion
        await callback(StreamChunk(
            session_id=session_id,
            type="done",
            is_thinking_complete=True
        ))
        
        return {
            "content": content,
            "tool_calls": tool_calls if tool_calls else None
        }
    
    async def _execute_tools(
        self,
        tool_calls: list[dict]
    ) -> list[dict]:
        """Execute tool calls."""
        results = []
        
        for tc in tool_calls:
            # Placeholder for tool execution
            # In real implementation, this would call actual tools
            results.append({
                "tool_call_id": tc["id"],
                "content": f"Tool {tc['function']['name']} executed"
            })
        
        return results
    
    async def _create_checkpoint(
        self,
        session_id: UUID,
        agent_state: AgentState
    ) -> None:
        """Create a checkpoint for recovery."""
        session = await self.memory.get_session(session_id)
        
        if not session:
            return
        
        checkpoint = Checkpoint(
            id=agent_state.session_id,
            iteration=agent_state.iteration,
            state={
                "status": agent_state.status.value,
                "error": agent_state.error
            },
            messages=list(session.messages)
        )
        
        if session_id not in self._checkpoints:
            self._checkpoints[session_id] = []
        
        self._checkpoints[session_id].append(checkpoint)
        
        # Keep only last 5 checkpoints
        if len(self._checkpoints[session_id]) > 5:
            self._checkpoints[session_id] = self._checkpoints[session_id][-5:]
    
    def _create_default_pipeline(self) -> MessagePipeline:
        """Create default processing pipeline."""
        from app.messaging.pipeline import (
            LoggingMiddleware,
            MessagePipeline,
            RetryMiddleware,
            TimingMiddleware,
            ValidationMiddleware,
        )
        
        return (
            MessagePipeline()
            .use(LoggingMiddleware())
            .use(TimingMiddleware())
            .use(ValidationMiddleware())
            .use(RetryMiddleware(max_retries=3))
        )
