"""
Chat Agent - Main agent class that coordinates all components.
"""

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable
from uuid import UUID

from openai import AsyncOpenAI
from structlog import get_logger

from app.agent.executor import ToolExecutor, create_default_executor
from app.agent.loop import AgentLoop, LoopState
from app.config import get_settings
from app.database.repository import SessionRepository
from app.memory.manager import MemoryManager
from app.messaging.pipeline import MessagePipeline, create_default_pipeline
from app.messaging.queue import PriorityMessageQueue, create_message_queue
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationSession,
    Message,
    MessageRole,
    MessageStatus,
    SessionInfo,
    StreamChunk,
    TodoItem,
    TodoItemStatus,
    TodoList,
    TitleResponse,
)

if TYPE_CHECKING:
    from app.services.todo_service import TodoService

logger = get_logger()
settings = get_settings()

# System prompt fragment that instructs the LLM to use the todo tool
TODO_SYSTEM_PROMPT = """你拥有一个名为 manage_todo_list 的工具。
当用户的请求需要多步骤才能完成时（例如：多阶段分析、多文件操作、复杂计划执行等），
你**必须**在开始工作前先调用 manage_todo_list 来创建任务清单。

使用规则：
1. 在开始多步骤任务前，调用 manage_todo_list 创建完整的步骤清单（所有步骤 pending，第一步设为 running）。
2. 每完成一个步骤后，再次调用 manage_todo_list，将已完成的步骤标记为 completed，下一步标记为 running。
3. 每次调用都必须发送**完整列表**（不是增量更新）。
4. 同一时间最多只有一个步骤处于 running 状态。
5. 对于简单的单步任务（如简单问答、翻译等），**不要**调用此工具。
"""


class ChatAgent:
    """
    Main Chat Agent class.
    
    This is the primary interface for interacting with the agent framework.
    It coordinates:
    - Memory management
    - Agent loop execution
    - Tool execution
    - Message queuing
    
    Usage:
        agent = ChatAgent()
        await agent.start()
        
        # Non-streaming
        response = await agent.chat("Hello!")
        
        # Streaming
        async for chunk in agent.chat_stream("Hello!"):
            print(chunk.delta)
        
        await agent.stop()
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        repository: SessionRepository | None = None,
        todo_service: "TodoService | None" = None,
    ):
        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            api_key=api_key or settings.openai.api_key,
            base_url=base_url or settings.openai.base_url,
            timeout=settings.openai.timeout,
            max_retries=settings.openai.max_retries
        )
        
        self.model = model or settings.openai.model
        
        # Initialize components
        self.memory = MemoryManager(self.client, self.model, repository=repository)
        self.queue = create_message_queue(settings.queue.backend)
        self.pipeline = create_default_pipeline()
        self.tool_executor = create_default_executor()
        self.todo_service = todo_service
        
        # Agent loop
        self._loop: AgentLoop | None = None
        self._running = False
        
        # Interrupt events for active sessions (keyed by session_id)
        self._interrupt_events: dict[UUID, asyncio.Event] = {}
        
        # Stream callbacks
        self._stream_callbacks: dict[UUID, asyncio.Queue] = {}
        
        logger.info(
            "ChatAgent initialized",
            model=self.model,
            base_url=settings.openai.base_url
        )
    
    async def start(self) -> None:
        """Start the agent."""
        if self._running:
            return
        
        # Create and start agent loop
        self._loop = AgentLoop(
            client=self.client,
            memory_manager=self.memory,
            message_queue=self.queue,
            pipeline=self.pipeline,
            todo_service=self.todo_service,
        )
        
        # Register callbacks
        self._loop.on_message(self._handle_message)
        self._loop.on_error(self._handle_error)
        self._loop.on_complete(self._handle_complete)
        
        await self._loop.start()
        self._running = True
        
        logger.info("ChatAgent started")
    
    async def stop(self) -> None:
        """Stop the agent."""
        if not self._running:
            return
        
        if self._loop:
            await self._loop.stop()
        
        self._running = False
        logger.info("ChatAgent stopped")
    
    async def chat(
        self,
        message: str,
        session_id: UUID | None = None,
        tools: list | None = None
    ) -> ChatResponse:
        """
        Send a message and get a response (non-streaming).
        
        Args:
            message: User message
            session_id: Optional session ID for continuing conversation
            tools: Optional list of tools available for this request
            
        Returns:
            ChatResponse with the assistant's reply
        """
        if not self._running:
            await self.start()
        
        # Create request
        request = ChatRequest(
            message=message,
            session_id=session_id,
            stream=False,
            tools=tools or self.tool_executor.registry.get_openai_tools()
        )
        max_iterations = settings.agent.max_iterations
        
        # Get or create session
        if session_id:
            session = await self.memory.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
        else:
            session = await self.memory.create_session(
                system_prompt=TODO_SYSTEM_PROMPT,
            )

        # Inject todo system prompt for existing sessions that don't have it
        if session_id and not any(
            m.role == MessageRole.SYSTEM
            and "manage_todo_list" in (m.content if isinstance(m.content, str) else "")
            for m in session.messages
        ):
            sys_msg = Message(
                role=MessageRole.SYSTEM,
                content=TODO_SYSTEM_PROMPT,
                importance_score=1.0,
            )
            await self.memory.add_message(session.id, sys_msg)
        
        # Add user message
        user_msg = Message(role=MessageRole.USER, content=message)
        await self.memory.add_message(session.id, user_msg)
        
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        assistant_msg: Message | None = None
        iterations = 0
        
        # Register interrupt event
        interrupt_event = asyncio.Event()
        self._interrupt_events[session.id] = interrupt_event
        
        try:
          while iterations < max_iterations:
            if interrupt_event.is_set():
                break
            messages = await self.memory.get_openai_messages(session.id)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=request.tools,
                max_tokens=settings.openai.max_tokens,
                temperature=settings.openai.temperature,
            )

            if response.usage:
                prompt_tokens += response.usage.prompt_tokens
                completion_tokens += response.usage.completion_tokens
                total_tokens += response.usage.total_tokens

            choice = response.choices[0]
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=choice.message.content or "",
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (choice.message.tool_calls or [])
                ]
                if choice.message.tool_calls
                else None,
            )
            await self.memory.add_message(session.id, assistant_msg)

            if not assistant_msg.tool_calls:
                break

            # Separate todo tool calls from regular ones
            todo_tcs = [
                tc for tc in assistant_msg.tool_calls
                if tc.get("function", {}).get("name") == "manage_todo_list"
            ]
            regular_tcs = [
                tc for tc in assistant_msg.tool_calls
                if tc.get("function", {}).get("name") != "manage_todo_list"
            ]

            # Handle todo tool calls
            for tc in todo_tcs:
                result_content = await self._handle_todo_tool_call(
                    session.id, tc, broadcast=None,
                )
                tool_msg = Message(
                    role=MessageRole.TOOL,
                    content=result_content,
                    tool_call_id=tc.get("id", ""),
                )
                await self.memory.add_message(session.id, tool_msg)

            # Handle regular tool calls
            if regular_tcs:
                tool_results = await self.tool_executor.execute(
                    regular_tcs,
                    session.id,
                )
                for result in tool_results:
                    tool_msg = Message(
                        role=MessageRole.TOOL,
                        content=result.content,
                        tool_call_id=result.tool_call_id,
                    )
                    await self.memory.add_message(session.id, tool_msg)

            iterations += 1

          if interrupt_event.is_set():
            # Return what we have so far as an interrupted response
            if assistant_msg is None:
                assistant_msg = Message(role=MessageRole.ASSISTANT, content="[已中断]")
                await self.memory.add_message(session.id, assistant_msg)
            return ChatResponse(
                session_id=session.id,
                message=assistant_msg,
                status=MessageStatus.INTERRUPTED,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            )

          if assistant_msg is None:
            raise RuntimeError("LLM did not return any response")

          if assistant_msg.tool_calls:
            raise RuntimeError(
                f"Reached max tool iterations ({max_iterations}) without completion"
            )
        
          return ChatResponse(
            session_id=session.id,
            message=assistant_msg,
            status=MessageStatus.COMPLETED,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
          )
        finally:
            self._interrupt_events.pop(session.id, None)
    
    async def chat_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        tools: list | None = None
    ) -> AsyncIterator[StreamChunk]:
        """
        Send a message and stream the response.
        
        Args:
            message: User message
            session_id: Optional session ID for continuing conversation
            tools: Optional list of tools available
            
        Yields:
            StreamChunk objects with content deltas
        """
        if not self._running:
            await self.start()
        
        # Get or create session
        if session_id:
            session = await self.memory.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
        else:
            session = await self.memory.create_session(
                system_prompt=TODO_SYSTEM_PROMPT,
            )
        
        # Yield session ID first
        yield StreamChunk(
            session_id=session.id,
            type="session",
            delta=str(session.id)
        )

        # If this is a brand-new session that didn't have a system prompt
        # yet, inject the todo system prompt so the LLM knows about the
        # tool for subsequent rounds.
        if session_id and not any(
            m.role == MessageRole.SYSTEM
            and "manage_todo_list" in (m.content if isinstance(m.content, str) else "")
            for m in session.messages
        ):
            sys_msg = Message(
                role=MessageRole.SYSTEM,
                content=TODO_SYSTEM_PROMPT,
                importance_score=1.0,
            )
            await self.memory.add_message(session.id, sys_msg)
        
        # Add user message
        user_msg = Message(role=MessageRole.USER, content=message)
        await self.memory.add_message(session.id, user_msg)
        max_iterations = settings.agent.max_iterations
        available_tools = tools or self.tool_executor.registry.get_openai_tools()
        iterations = 0
        pending_tool_calls = False
        
        # Create stream queue
        stream_queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
        self._stream_callbacks[session.id] = stream_queue

        # Register interrupt event
        interrupt_event = asyncio.Event()
        self._interrupt_events[session.id] = interrupt_event

        # SSE broadcast helper — yields todo_list chunks inline
        todo_chunks: list[StreamChunk] = []

        async def _todo_broadcast(chunk: StreamChunk) -> None:
            """Buffer a todo-list SSE chunk so the outer generator can yield it."""
            todo_chunks.append(chunk)
        
        try:
            while iterations < max_iterations:
                # Check for interruption before each iteration
                if interrupt_event.is_set():
                    break
                messages = await self.memory.get_openai_messages(session.id)
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=available_tools,
                    max_tokens=settings.openai.max_tokens,
                    temperature=settings.openai.temperature,
                    stream=True,
                )

                content = ""
                thinking = ""
                tool_calls = []

                async for chunk in stream:
                    # Check for interruption during streaming
                    if interrupt_event.is_set():
                        await stream.close()
                        break

                    delta = chunk.choices[0].delta if chunk.choices else None

                    if delta:
                        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                            thinking += delta.reasoning_content
                            stream_chunk = StreamChunk(
                                session_id=session.id,
                                type="thinking",
                                thinking=delta.reasoning_content,
                            )
                            yield stream_chunk

                        if delta.content:
                            content += delta.content
                            stream_chunk = StreamChunk(
                                session_id=session.id,
                                type="content",
                                delta=delta.content,
                            )
                            yield stream_chunk

                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                while len(tool_calls) <= tc.index:
                                    tool_calls.append(
                                        {
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    )

                                if tc.id:
                                    tool_calls[tc.index]["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        tool_calls[tc.index]["function"]["name"] = tc.function.name
                                    if tc.function.arguments:
                                        tool_calls[tc.index]["function"]["arguments"] += tc.function.arguments

                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls if tool_calls else None,
                    metadata={"thinking": thinking} if thinking else {},
                )
                await self.memory.add_message(session.id, assistant_msg)

                # If interrupted during streaming, break the outer loop
                if interrupt_event.is_set():
                    break

                if not tool_calls:
                    pending_tool_calls = False
                    break

                pending_tool_calls = True

                # --- Handle tool calls (with special todo treatment) ---
                todo_tool_calls = [
                    tc for tc in tool_calls
                    if tc.get("function", {}).get("name") == "manage_todo_list"
                ]
                regular_tool_calls = [
                    tc for tc in tool_calls
                    if tc.get("function", {}).get("name") != "manage_todo_list"
                ]

                # Process todo tool calls via TodoService
                for tc in todo_tool_calls:
                    result_content = await self._handle_todo_tool_call(
                        session.id, tc, _todo_broadcast,
                    )
                    tool_msg = Message(
                        role=MessageRole.TOOL,
                        content=result_content,
                        tool_call_id=tc.get("id", ""),
                    )
                    await self.memory.add_message(session.id, tool_msg)

                # Flush any buffered todo SSE chunks
                for todo_chunk in todo_chunks:
                    yield todo_chunk
                todo_chunks.clear()

                # Process regular tool calls via ToolExecutor
                if regular_tool_calls:
                    tool_results = await self.tool_executor.execute(
                        regular_tool_calls,
                        session.id,
                    )
                    for result in tool_results:
                        tool_msg = Message(
                            role=MessageRole.TOOL,
                            content=result.content,
                            tool_call_id=result.tool_call_id,
                        )
                        await self.memory.add_message(session.id, tool_msg)

                iterations += 1

            if interrupt_event.is_set():
                # Send interrupted signal
                yield StreamChunk(
                    session_id=session.id,
                    type="done",
                    delta="[已中断]",
                    is_thinking_complete=True,
                )
            elif pending_tool_calls:
                raise RuntimeError(
                    f"Reached max tool iterations ({max_iterations}) without completion"
                )
            else:
                # Send completion
                yield StreamChunk(
                    session_id=session.id,
                    type="done",
                    is_thinking_complete=True,
                )
            
        finally:
            # Cleanup
            self._interrupt_events.pop(session.id, None)
            if session.id in self._stream_callbacks:
                del self._stream_callbacks[session.id]

    # ---- helpers for robust LLM argument parsing ----

    @staticmethod
    def _extract_label(item: Any) -> str:
        """Extract the label from a todo item dict sent by the LLM.

        Different models may use ``label``, ``title``, ``name``, ``text``,
        ``description``, or ``content`` as the key.  Fall back to ``str(item)``
        when the item is a plain string.
        """
        if isinstance(item, str):
            return item
        for key in ("label", "title", "name", "text", "description", "content"):
            val = item.get(key)
            if val:
                return str(val)
        # Last resort: stringify the whole thing
        return str(item)

    @staticmethod
    def _normalise_status(raw: str) -> TodoItemStatus:
        """Map various LLM status strings to ``TodoItemStatus``."""
        mapping: dict[str, TodoItemStatus] = {
            "pending": TodoItemStatus.PENDING,
            "not-started": TodoItemStatus.PENDING,
            "not_started": TodoItemStatus.PENDING,
            "todo": TodoItemStatus.PENDING,
            "running": TodoItemStatus.RUNNING,
            "in-progress": TodoItemStatus.RUNNING,
            "in_progress": TodoItemStatus.RUNNING,
            "active": TodoItemStatus.RUNNING,
            "current": TodoItemStatus.RUNNING,
            "completed": TodoItemStatus.COMPLETED,
            "done": TodoItemStatus.COMPLETED,
            "finished": TodoItemStatus.COMPLETED,
            "complete": TodoItemStatus.COMPLETED,
        }
        return mapping.get(raw.lower().strip(), TodoItemStatus.PENDING)

    async def _handle_todo_tool_call(
        self,
        session_id: UUID,
        tool_call: dict,
        broadcast: Callable | None = None,
    ) -> str:
        """
        Process a ``manage_todo_list`` tool call from the LLM.

        Parses the arguments, delegates to ``TodoService``, and returns
        a confirmation string that gets fed back to the model as the
        tool result.
        """
        import json as _json

        raw_args = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args = _json.loads(raw_args)
        except _json.JSONDecodeError:
            return '{"ok":false,"error":"Invalid JSON arguments"}'

        logger.debug("manage_todo_list raw args", raw_args=raw_args)

        title = args.get("title", "")
        # Accept multiple possible keys for the item list
        items_raw: list = (
            args.get("items")
            or args.get("todoList")
            or args.get("todo_list")
            or args.get("steps")
            or []
        )

        if not self.todo_service:
            return _json.dumps({"ok": True, "message": "Todo list noted (no persistence)."})

        # Build TodoItem list with robust field-name extraction
        todo_items: list[TodoItem] = []
        for idx, it in enumerate(items_raw):
            label = self._extract_label(it)
            raw_status = it.get("status") or it.get("state") or "pending" if isinstance(it, dict) else "pending"
            status = self._normalise_status(str(raw_status))
            todo_items.append(
                TodoItem(label=label, status=status, order_index=idx + 1)
            )

        # Enforce: at least one item must be "running" (pick the first
        # pending one if the LLM forgot).
        if todo_items and not any(i.status == TodoItemStatus.RUNNING for i in todo_items):
            for i in todo_items:
                if i.status == TodoItemStatus.PENDING:
                    i.status = TodoItemStatus.RUNNING
                    break

        snapshot = await self.todo_service.create_or_replace_with_items(
            session_id, title, todo_items, broadcast=broadcast,
        )

        return _json.dumps({
            "ok": True,
            "message": f"Todo list '{title}' saved with {len(todo_items)} items (revision {snapshot.revision}).",
        })
    
    async def generate_title(self, session_id: UUID) -> str:
        """
        Generate a title for the conversation.
        
        Args:
            session_id: Session ID
            
        Returns:
            Generated title
        """
        session = await self.memory.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        # Get first few messages for context
        messages = session.messages[:4]
        if not messages:
            return "新对话"
        
        # Build context
        context = "\n".join([
            f"{msg.role.value}: {msg.content if isinstance(msg.content, str) else str(msg.content)[:100]}"
            for msg in messages
            if isinstance(msg.content, str)
        ])
        
        # Generate title using system + user message pattern for better
        # compatibility with various LLM providers (e.g. Gemini)
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个标题生成助手。根据用户提供的对话内容，生成一个简短的标题（不超过20个字）。直接输出标题文本，不要加引号、前缀或任何其他内容。"
                },
                {
                    "role": "user",
                    "content": f"请为以下对话生成标题：\n\n{context}"
                }
            ],
            max_tokens=50,
            temperature=0.3,
        )
        
        title = response.choices[0].message.content or "新对话"
        title = title.strip().strip('"').strip('"').strip()
        
        # Update session
        session.title = title
        
        # Persist title update to database
        if self.memory.repository:
            try:
                await self.memory.repository.update_session(session)
            except Exception as e:
                logger.error("Failed to persist title", error=str(e))
        
        return title
    
    async def get_session(self, session_id: UUID) -> ConversationSession | None:
        """Get a session by ID."""
        return await self.memory.get_session(session_id)
    
    async def list_sessions(
        self,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[list[SessionInfo], int]:
        """List all sessions."""
        sessions, total = await self.memory.list_sessions(page, page_size)
        
        session_infos = [
            SessionInfo(
                id=s.id,
                title=s.title,
                message_count=len(s.messages),
                created_at=s.created_at,
                updated_at=s.updated_at,
                status=s.status,
                preview=s.messages[-1].content[:100] if s.messages and isinstance(s.messages[-1].content, str) else None
            )
            for s in sessions
        ]
        
        return session_infos, total
    
    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a session."""
        return await self.memory.delete_session(session_id)
    
    async def interrupt(self, session_id: UUID) -> bool:
        """Interrupt a running session."""
        if session_id in self._interrupt_events:
            self._interrupt_events[session_id].set()
            logger.info("Session interrupted", session_id=str(session_id))
            return True
        # Fallback to loop-level interrupt
        if self._loop:
            return await self._loop.interrupt(session_id)
        return False
    
    def register_tool(self, tool: Any) -> None:
        """Register a custom tool."""
        from app.agent.executor import BaseTool
        if isinstance(tool, BaseTool):
            self.tool_executor.register_tool(tool)
        else:
            raise TypeError("Tool must be an instance of BaseTool")
    
    def get_stats(self) -> dict[str, Any]:
        """Get agent statistics."""
        return {
            "running": self._running,
            "model": self.model,
            "memory_stats": self.memory.get_stats(),
            "loop_state": self._loop.state.value if self._loop else "not_initialized",
        }
    
    async def _handle_message(self, session_id: UUID, message: Any) -> None:
        """Handle message callback from agent loop."""
        if session_id in self._stream_callbacks:
            await self._stream_callbacks[session_id].put(message)
    
    async def _handle_error(self, session_id: UUID, error: Exception) -> None:
        """Handle error callback from agent loop."""
        if session_id in self._stream_callbacks:
            await self._stream_callbacks[session_id].put(
                StreamChunk(
                    session_id=session_id,
                    type="error",
                    delta=str(error)
                )
            )
    
    async def _handle_complete(self, session_id: UUID, state: Any) -> None:
        """Handle completion callback from agent loop."""
        if session_id in self._stream_callbacks:
            await self._stream_callbacks[session_id].put(None)  # Signal end
