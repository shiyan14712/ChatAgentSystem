"""
Message queue implementations supporting multiple backends.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

from structlog import get_logger

from app.config import get_settings
from app.models.schemas import MessagePriority

logger = get_logger()
settings = get_settings()


@dataclass
class QueuedMessage:
    """A message in the queue."""
    
    id: UUID = field(default_factory=uuid4)
    content: Any = None
    priority: int = MessagePriority.NORMAL
    session_id: UUID | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def __lt__(self, other: "QueuedMessage") -> bool:
        """Compare by priority (higher priority = processed first)."""
        return self.priority > other.priority


class MessageQueueBackend(ABC):
    """Abstract message queue backend."""
    
    @abstractmethod
    async def enqueue(self, message: QueuedMessage) -> bool:
        """Add a message to the queue."""
        pass
    
    @abstractmethod
    async def dequeue(self, timeout: float = 1.0) -> QueuedMessage | None:
        """Get a message from the queue."""
        pass
    
    @abstractmethod
    async def size(self) -> int:
        """Get queue size."""
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """Clear the queue."""
        pass


class InMemoryQueueBackend(MessageQueueBackend):
    """In-memory message queue backend."""
    
    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._queue: asyncio.PriorityQueue[QueuedMessage] = asyncio.PriorityQueue()
        self._size = 0
        self._lock = asyncio.Lock()
    
    async def enqueue(self, message: QueuedMessage) -> bool:
        """Add a message to the queue."""
        async with self._lock:
            if self._size >= self.max_size:
                logger.warning("Queue is full, message rejected")
                return False
            
            await self._queue.put(message)
            self._size += 1
            return True
    
    async def dequeue(self, timeout: float = 1.0) -> QueuedMessage | None:
        """Get a message from the queue with timeout."""
        try:
            async with asyncio.timeout(timeout):
                message = await self._queue.get()
                async with self._lock:
                    self._size -= 1
                return message
        except asyncio.TimeoutError:
            return None
    
    async def size(self) -> int:
        """Get queue size."""
        async with self._lock:
            return self._size
    
    async def clear(self) -> None:
        """Clear the queue."""
        async with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._size = 0


class RedisQueueBackend(MessageQueueBackend):
    """Redis-based message queue backend."""
    
    def __init__(self, redis_url: str, queue_name: str = "agent_queue"):
        self.redis_url = redis_url
        self.queue_name = queue_name
        self._redis = None
        self._connected = False
    
    async def _connect(self) -> None:
        """Connect to Redis."""
        if self._connected:
            return
        
        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(self.redis_url)
            self._connected = True
            logger.info("Connected to Redis", url=self.redis_url)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    async def enqueue(self, message: QueuedMessage) -> bool:
        """Add a message to the Redis queue."""
        await self._connect()
        
        import orjson
        
        data = {
            "id": str(message.id),
            "content": message.content,
            "priority": message.priority,
            "session_id": str(message.session_id) if message.session_id else None,
            "created_at": message.created_at.isoformat(),
            "metadata": message.metadata,
        }
        
        try:
            # Use sorted set for priority queue (lower score = higher priority)
            score = -message.priority  # Negate for descending order
            await self._redis.zadd(
                self.queue_name,
                {orjson.dumps(data): score}
            )
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue message: {e}")
            return False
    
    async def dequeue(self, timeout: float = 1.0) -> QueuedMessage | None:
        """Get a message from the Redis queue."""
        await self._connect()
        
        import orjson
        
        try:
            # Get highest priority message (lowest score)
            result = await self._redis.zpopmin(self.queue_name)
            if not result:
                await asyncio.sleep(0.1)
                return None
            
            data = orjson.loads(result[0][0])
            return QueuedMessage(
                id=UUID(data["id"]),
                content=data["content"],
                priority=data["priority"],
                session_id=UUID(data["session_id"]) if data["session_id"] else None,
                created_at=datetime.fromisoformat(data["created_at"]),
                metadata=data["metadata"],
            )
        except Exception as e:
            logger.error(f"Failed to dequeue message: {e}")
            return None
    
    async def size(self) -> int:
        """Get queue size."""
        await self._connect()
        return await self._redis.zcard(self.queue_name)
    
    async def clear(self) -> None:
        """Clear the queue."""
        await self._connect()
        await self._redis.delete(self.queue_name)


class KafkaQueueBackend(MessageQueueBackend):
    """Kafka-based message queue backend."""
    
    def __init__(
        self,
        bootstrap_servers: str,
        topic: str = "agent_messages",
        group_id: str = "agent_consumer"
    ):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self._producer = None
        self._consumer = None
        self._connected = False
    
    async def _connect(self) -> None:
        """Connect to Kafka."""
        if self._connected:
            return
        
        try:
            from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
            
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers
            )
            await self._producer.start()
            
            self._consumer = AIOKafkaConsumer(
                self.topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.group_id,
                auto_offset_reset="earliest"
            )
            await self._consumer.start()
            
            self._connected = True
            logger.info("Connected to Kafka", servers=self.bootstrap_servers)
        except Exception as e:
            logger.error(f"Failed to connect to Kafka: {e}")
            raise
    
    async def enqueue(self, message: QueuedMessage) -> bool:
        """Add a message to Kafka."""
        await self._connect()
        
        import orjson
        
        data = {
            "id": str(message.id),
            "content": message.content,
            "priority": message.priority,
            "session_id": str(message.session_id) if message.session_id else None,
            "created_at": message.created_at.isoformat(),
            "metadata": message.metadata,
        }
        
        try:
            # Use priority as key for partitioning
            await self._producer.send_and_wait(
                self.topic,
                orjson.dumps(data),
                key=str(message.priority).encode()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue message to Kafka: {e}")
            return False
    
    async def dequeue(self, timeout: float = 1.0) -> QueuedMessage | None:
        """Get a message from Kafka."""
        await self._connect()
        
        import orjson
        
        try:
            async with asyncio.timeout(timeout):
                async for msg in self._consumer:
                    data = orjson.loads(msg.value)
                    return QueuedMessage(
                        id=UUID(data["id"]),
                        content=data["content"],
                        priority=data["priority"],
                        session_id=UUID(data["session_id"]) if data["session_id"] else None,
                        created_at=datetime.fromisoformat(data["created_at"]),
                        metadata=data["metadata"],
                    )
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Failed to dequeue message from Kafka: {e}")
            return None
    
    async def size(self) -> int:
        """Kafka doesn't have a simple size method."""
        return -1  # Not supported
    
    async def clear(self) -> None:
        """Clear is not directly supported in Kafka."""
        pass


class PriorityMessageQueue:
    """
    High-level priority message queue.
    
    Features:
    - Multiple backend support (memory, Redis, Kafka)
    - Priority-based message ordering
    - Session-based message routing
    - TTL support
    """
    
    def __init__(
        self,
        backend: MessageQueueBackend | None = None,
        priority_levels: int = 5
    ):
        self.backend = backend or InMemoryQueueBackend()
        self.priority_levels = priority_levels
        
        # Session-specific queues
        self._session_queues: dict[UUID, asyncio.Queue[QueuedMessage]] = defaultdict(
            asyncio.Queue
        )
        
        # Statistics
        self._stats = {
            "enqueued": 0,
            "dequeued": 0,
            "errors": 0,
        }
    
    async def enqueue(
        self,
        content: Any,
        priority: int = MessagePriority.NORMAL,
        session_id: UUID | None = None,
        metadata: dict[str, Any] | None = None
    ) -> UUID:
        """
        Enqueue a message.
        
        Args:
            content: Message content
            priority: Priority level (1-9, higher = more urgent)
            session_id: Optional session ID for routing
            metadata: Optional metadata
            
        Returns:
            Message ID
        """
        message = QueuedMessage(
            content=content,
            priority=min(priority, 9),  # Cap at 9
            session_id=session_id,
            metadata=metadata or {}
        )
        
        success = await self.backend.enqueue(message)
        
        if success:
            self._stats["enqueued"] += 1
            
            # Also add to session queue if specified
            if session_id:
                await self._session_queues[session_id].put(message)
            
            logger.debug(
                "Message enqueued",
                message_id=str(message.id),
                priority=priority,
                session_id=str(session_id) if session_id else None
            )
        else:
            self._stats["errors"] += 1
        
        return message.id
    
    async def dequeue(
        self,
        timeout: float = 1.0,
        session_id: UUID | None = None
    ) -> QueuedMessage | None:
        """
        Dequeue a message.
        
        Args:
            timeout: Timeout in seconds
            session_id: Optional session ID to get session-specific message
            
        Returns:
            QueuedMessage or None if timeout
        """
        if session_id:
            # Get from session queue
            try:
                async with asyncio.timeout(timeout):
                    message = await self._session_queues[session_id].get()
                    self._stats["dequeued"] += 1
                    return message
            except asyncio.TimeoutError:
                return None
        
        # Get from main queue
        message = await self.backend.dequeue(timeout)
        if message:
            self._stats["dequeued"] += 1
        
        return message
    
    async def iter_messages(
        self,
        session_id: UUID | None = None,
        timeout: float = 1.0
    ) -> AsyncIterator[QueuedMessage]:
        """Iterate over messages."""
        while True:
            message = await self.dequeue(timeout, session_id)
            if message is None:
                break
            yield message
    
    async def size(self, session_id: UUID | None = None) -> int:
        """Get queue size."""
        if session_id:
            return self._session_queues[session_id].qsize()
        return await self.backend.size()
    
    async def clear(self, session_id: UUID | None = None) -> None:
        """Clear the queue."""
        if session_id:
            # Clear session queue
            while not self._session_queues[session_id].empty():
                try:
                    self._session_queues[session_id].get_nowait()
                except asyncio.QueueEmpty:
                    break
        else:
            await self.backend.clear()
    
    def get_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        return {
            **self._stats,
            "current_size": asyncio.get_event_loop().run_until_complete(self.size()),
        }


# Factory function
def create_message_queue(
    backend_type: str = "memory",
    **kwargs
) -> PriorityMessageQueue:
    """Create a message queue with specified backend."""
    config = settings.queue
    
    if backend_type == "memory":
        backend = InMemoryQueueBackend(
            max_size=kwargs.get("max_size", config.max_queue_size)
        )
    elif backend_type == "redis":
        backend = RedisQueueBackend(
            redis_url=kwargs.get("redis_url", config.redis_url),
            queue_name=kwargs.get("queue_name", "agent_queue")
        )
    elif backend_type == "kafka":
        backend = KafkaQueueBackend(
            bootstrap_servers=kwargs.get(
                "bootstrap_servers",
                config.kafka_bootstrap_servers
            ),
            topic=kwargs.get("topic", f"{config.kafka_topic_prefix}_messages")
        )
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")
    
    return PriorityMessageQueue(
        backend=backend,
        priority_levels=config.priority_levels
    )


# Alias for backward compatibility
MessageQueue = PriorityMessageQueue
