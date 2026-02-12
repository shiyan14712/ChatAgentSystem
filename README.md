# Chat Agent Framework

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)
![Vue](https://img.shields.io/badge/Vue-3.5+-brightgreen.svg)
![TypeScript](https://img.shields.io/badge/TypeScript-5.6+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

**一个生产级别的聊天对话Agent框架，支持OpenAI Compatible API**

[功能特性](#功能特性) • [快速开始](#快速开始) • [API文档](#api文档) • [配置说明](#配置说明) • [扩展开发](#扩展开发)

</div>

---

## 1 目录

- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [核心模块详解](#核心模块详解)
- [API文档](#api文档)
- [配置说明](#配置说明)
- [扩展开发](#扩展开发)
- [技术栈](#技术栈)

---

## 2 功能特性

> 🏷️ Label 说明（仅标注后端）：
> - `【未实现】`：文档中有能力描述，但当前后端代码未落地
> - `【部分实现】`：已有基础能力，但未形成完整闭环
> - `【未接入主链路】`：模块已实现，但当前 API 主调用路径未使用

### 🧠 内存与上下文管理

| 特性 | 描述 |
|------|------|
| **智能压缩算法** | 当上下文使用率达到92%阈值时自动触发压缩，保留关键信息 |
| **重要性评分** | 基于消息角色、位置、关键词等多维度评分，智能筛选保留内容 |
| **分层存储** | Hot(活跃) → Warm(近期) → Cold(归档) 三层存储机制 |
| **Token优化** | 动态上下文窗口调整，最大化利用模型上下文能力 `【部分实现】`（当前为固定上限 + 压缩触发，未实现按模型能力动态扩缩容） |
| **历史摘要** | 对压缩的历史对话生成智能摘要，保留上下文连贯性 |

### 🔄 Agent循环系统

| 特性 | 描述 |
|------|------|
| **异步核心调度器** | 基于asyncio的异步架构，支持并发处理多个会话 `【未接入主链路】`（当前 `/chat` 与 `/chat/stream` 直接走 `ChatAgent` 调用链） |
| **中断和恢复** | 支持中断正在进行的对话，可从检查点恢复执行 `【部分实现】`（已有中断标记与检查点写入，未实现检查点恢复执行） |
| **检查点机制** | 定期保存执行状态，异常时可恢复 `【部分实现】`（已保存检查点，未提供恢复 API / 自动恢复流程） |
| **多层异常处理** | 完善的错误捕获和恢复机制，保证系统稳定性 |
| **工具调用** | 支持并行工具执行，可扩展自定义工具 |

### 📨 消息处理管道

| 特性 | 描述 |
|------|------|
| **优先级队列** | 支持消息优先级调度，紧急消息优先处理 |
| **多后端支持** | Memory(内存) / Redis / Kafka 三种后端可选 |
| **中间件系统** | 内置日志、计时、重试、限流等中间件，可自定义扩展 |
| **消息TTL** | 支持消息过期时间设置 `【未实现】`（配置项已存在，消费路径未执行 TTL 过期裁剪） |

### 💻 前端特性

| 特性 | 描述 |
|------|------|
| **思考模式展示** | AI思考过程置灰显示，思考结束后可折叠展开 |
| **流式响应** | 实时显示AI回复，打字机效果 |
| **对话管理** | 独立会话ID管理，支持多会话切换 |
| **自动标题** | 对话开始时自动生成主题标题 |
| **Markdown渲染** | 支持代码高亮、表格、列表等富文本展示 |
| **响应式设计** | 适配桌面和移动端 |

---

## 3 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Vue3 Frontend                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ChatWindow│  │ChatMessage│ │ChatInput │  │ChatSidebar│       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
│       └──────────────┼──────────────┼──────────────┘            │
│                      ▼              ▼                            │
│              ┌──────────────────────────┐                       │
│              │   Pinia Store (State)    │                       │
│              └────────────┬─────────────┘                       │
└───────────────────────────┼─────────────────────────────────────┘
                            │ HTTP/SSE
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    API Layer                              │   │
│  │  /chat/  /chat/stream  /sessions/  /chat/title           │   │
│  └────────────────────────────┬─────────────────────────────┘   │
│                               ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   ChatAgent Core                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │   │
│  │  │ Agent Loop  │  │Tool Executor│  │Memory Manager│      │   │
│  │  │(Async Sched)│  │ (Parallel)  │  │(Compress 92%)│      │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘      │   │
│  │         └────────────────┼────────────────┘              │   │
│  └──────────────────────────┼───────────────────────────────┘   │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               Message Pipeline & Queue                    │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐               │   │
│  │  │Middleware│→ │Priority Q│→ │  Handler │               │   │
│  │  │(Log/Retry)│  │(Redis/Kafka)│ │          │               │   │
│  │  └──────────┘  └──────────┘  └──────────┘               │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ OpenAI API
                            ▼
                   ┌─────────────────┐
                   │   OpenAI /      │
                   │   Compatible API│
                   └─────────────────┘
```

---

## 4 Quick Start

**0. 环境要求**

- **后端**: Python 3.12+
- **前端**: Node.js 18+ / Bun
- **可选**: Redis 7+ (用于消息队列), Kafka (用于大规模部署)

**1. 后端启动**

```bash
# 进入后端目录
cd agent-backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env

# 编辑 .env 文件，填入你的配置
# OPENAI_API_KEY=your-api-key-here
# OPENAI_BASE_URL=https://api.openai.com/v1

# 启动开发服务器
uvicorn app.main:app --reload --port 8000

# 或生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**2. 前端启动**

```bash
# 进入前端目录
cd agent-frontend

# 安装依赖
npm install
# 或使用 pnpm
pnpm install
# 或使用 bun
bun install

# 启动开发服务器
npm run dev

# 构建生产版本
npm run build
```

**3. 访问应用**

- **前端界面**: http://localhost:5173
- **API文档 (Swagger)**: http://localhost:8000/docs
- **API文档 (ReDoc)**: http://localhost:8000/redoc

---

## 5 核心模块详解

### 1) Memory Management

#### 智能压缩算法

当上下文使用率达到92%阈值时自动触发压缩：

```python
# 压缩触发条件
def should_compress(self, messages: list[Message], max_tokens: int) -> bool:
    total_tokens = sum(m.token_count for m in messages)
    usage_ratio = total_tokens / max_tokens
    return usage_ratio >= 0.92  # 92% 阈值
```

#### 重要性评分

基于多维度评分决定消息保留优先级：

```python
def score(self, message: Message, position: int, total: int) -> float:
    # 位置因子 (最近的消息更重要)
    position_factor = self.decay_factor ** (total - position - 1)
    
    # 角色权重 (system > user > assistant > tool)
    role_weights = {
        MessageRole.SYSTEM: 1.0,
        MessageRole.USER: 0.8,
        MessageRole.ASSISTANT: 0.6,
        MessageRole.TOOL: 0.5,
    }
    
    # 关键词分析
    keyword_score = self._analyze_keywords(message.content)
    
    # 工具调用加成
    tool_bonus = 0.2 if message.tool_calls else 0.0
    
    return weighted_sum(...)
```

#### 分层存储

```
┌─────────────────────────────────────────────┐
│                 Hot Layer                    │
│  - 当前活跃对话                              │
│  - 完整消息内容                              │
│  - 最高优先级                                │
├─────────────────────────────────────────────┤
│                Warm Layer                    │
│  - 近期历史对话                              │
│  - 可快速恢复                                │
│  - 中等优先级                                │
├─────────────────────────────────────────────┤
│                Cold Layer                    │
│  - 归档历史                                  │
│  - 仅保留摘要                                │
│  - 低优先级                                  │
└─────────────────────────────────────────────┘
```

### 2) Agent Main Loop

#### 异步调度器

```python
class AgentLoop:
    async def _main_loop(self) -> None:
        """主处理循环"""
        while not self._stop_event.is_set():
            # 检查暂停
            if self._pause_event.is_set():
                await asyncio.sleep(0.1)
                continue
            
            # 获取下一条消息
            message = await self.queue.dequeue(timeout=1.0)
            if message:
                # 异步处理
                task = asyncio.create_task(
                    self._process_message(message)
                )
                self._tasks[message.session_id] = task
```

#### 中断和恢复

```python
# 中断会话
async def interrupt(self, session_id: UUID) -> bool:
    if session_id in self._interrupt_events:
        self._interrupt_events[session_id].set()
        self._agent_states[session_id].status = MessageStatus.INTERRUPTED
        return True
    return False

# 检查点恢复
async def _create_checkpoint(self, session_id: UUID, state: AgentState):
    checkpoint = Checkpoint(
        id=state.session_id,
        iteration=state.iteration,
        state={"status": state.status.value},
        messages=list(session.messages)
    )
    self._checkpoints[session_id].append(checkpoint)
```

> 🏷️ `【部分实现】` 当前后端已实现“中断标记 + 检查点写入”，但尚未实现“从检查点恢复继续执行”的完整流程（包括恢复入口、状态回放、重入执行）。

### 3) Message Pipeline

#### 中间件系统

```python
# 内置中间件
pipeline = (
    MessagePipeline()
    .use(LoggingMiddleware())      # 日志记录
    .use(TimingMiddleware())       # 性能计时
    .use(ValidationMiddleware())   # 数据验证
    .use(RetryMiddleware(max_retries=3))  # 重试机制
    .use(RateLimitMiddleware(rps=10.0))   # 限流
)
```

> 🏷️ `【未实现】` `QUEUE_MESSAGE_TTL` 已在配置中定义，但当前队列 `enqueue/dequeue` 主路径未依据 TTL 进行过期过滤。

### 4) Tool SPI 注册与发现（核心实现）

#### 注册调用链（后端真实执行路径）

框架启动后，工具注册链路如下：

1. `ChatAgent.__init__` 调用 `create_default_executor()`
2. `app.agent.executor.create_default_executor()` 转调 `app.agent.tools.register.create_default_executor()`
3. `register.create_default_executor()` 创建 `ToolExecutor`，并执行 `register_default_tools(executor)`
4. `register_default_tools()` 读取 `settings.tools.*`，调用 `register_discovered_tools(...)`
5. `register_discovered_tools()` 内部调用 `discover_tools(...)` 完成发现，再逐个 `executor.register_tool(tool)`

这意味着：**工具发现发生在 Agent 初始化阶段**，不是每次请求动态发现。

#### 发现策略细节

`discover_tools(...)` 同时支持两类来源：

- Built-in：遍历 `app.agent.tools.internal` 包
    - 使用 `pkgutil.walk_packages` 递归扫描模块
    - 使用 `inspect.getmembers(..., inspect.isclass)` 收集类
    - 仅保留“定义在当前模块中 + `BaseTool` 子类 + 非抽象类”
    - 通过无参构造实例化

- Entry Points（SPI）：读取 `importlib.metadata.entry_points(group=...)`
    - 对每个 entry point 调用 `ep.load()`
    - 允许以下返回形态（会被统一转换）：
        - `BaseTool` 子类
        - `BaseTool` 实例
        - 工厂函数（返回上述对象）
        - 可迭代对象（元素可递归为上述任意形态）

#### 重名冲突与异常策略

- 重名工具（同 `tool.name`）处理：**先到先得**，后续同名工具会被忽略并记录 warning 日志
- 发现异常处理：
    - `TOOLS_DISCOVERY_FAIL_FAST=false`：记录 error 日志并跳过失败提供方
    - `TOOLS_DISCOVERY_FAIL_FAST=true`：抛出异常，启动失败（适合强一致生产环境）

#### 环境变量与行为映射

```env
TOOLS_ENABLE_BUILTIN_DISCOVERY=true        # 是否扫描内置工具包
TOOLS_ENABLE_ENTRYPOINT_DISCOVERY=true     # 是否启用 entry points 发现
TOOLS_ENTRYPOINT_GROUP=chat_agent_framework.tools
TOOLS_DISCOVERY_FAIL_FAST=false            # 单个 provider 失败时是否直接失败
```

#### 第三方插件接入约束（实践建议）

- entry point 推荐指向**无状态工具类**或**轻量工厂函数**
- 工具 `name` 必须全局唯一（建议使用前缀，如 `weather.query`）
- `execute(**kwargs)` 中避免长阻塞；需要网络 I/O 时必须异步化
- 保证 `parameters` 与实际函数参数语义一致，避免模型构造非法参数

#### 运行时排障清单

当工具未生效时，优先检查：

1. 插件包是否安装到**当前运行环境**
2. `TOOLS_ENTRYPOINT_GROUP` 是否与插件声明一致
3. 工具名是否与现有工具冲突（冲突时后加载者会被忽略）
4. `TOOLS_DISCOVERY_FAIL_FAST` 是否导致启动直接失败
5. 启动日志中是否出现 `Tool discovery completed` / `External tool entry point failed`

---

## 6 APIs

### 聊天接口

#### 发送消息 (非流式)

```http
POST /api/v1/chat/
Content-Type: application/json

{
  "message": "你好，请介绍一下自己",
  "session_id": "uuid-or-null-for-new-session"
}
```

**响应:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": {
    "id": "...",
    "role": "assistant",
    "content": "你好！我是Chat Agent...",
    "created_at": "2024-01-01T00:00:00Z"
  },
  "status": "completed",
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 100,
    "total_tokens": 120
  }
}
```

#### 发送消息 (流式)

```http
POST /api/v1/chat/stream
Content-Type: application/json

{
  "message": "写一个Python函数",
  "session_id": "uuid-or-null"
}
```

**SSE 响应格式:**
```
data: {"session_id":"...","type":"session","delta":"uuid"}

data: {"session_id":"...","type":"thinking","thinking":"让我思考一下..."}

data: {"session_id":"...","type":"content","delta":"好的"}

data: {"session_id":"...","type":"content","delta":"，我来"}

data: {"session_id":"...","type":"done","is_thinking_complete":true}
```

#### 生成对话标题

```http
POST /api/v1/chat/title
Content-Type: application/json

{
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**响应:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Python函数编写讨论"
}
```

### 会话管理

#### 获取会话列表

```http
GET /api/v1/sessions/?page=1&page_size=20
```

#### 获取会话详情

```http
GET /api/v1/sessions/{session_id}
```

#### 删除会话

```http
DELETE /api/v1/sessions/{session_id}
```

---

## 7 配置说明

### 后端配置 (.env)

```env
# ============ OpenAI 配置 ============
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=4096
OPENAI_TEMPERATURE=0.7
OPENAI_TIMEOUT=60.0
OPENAI_MAX_RETRIES=3

# ============ 内存配置 ============
MEMORY_MAX_CONTEXT_TOKENS=128000
MEMORY_COMPRESSION_THRESHOLD=0.92
MEMORY_TARGET_COMPRESSION_RATIO=0.3
MEMORY_MAX_MESSAGES_IN_MEMORY=100
MEMORY_SUMMARY_MAX_TOKENS=500
MEMORY_IMPORTANCE_DECAY_FACTOR=0.95

# ============ Agent 配置 ============
AGENT_MAX_ITERATIONS=10
AGENT_ITERATION_TIMEOUT=300
AGENT_ENABLE_PARALLEL_TOOLS=true
AGENT_MAX_PARALLEL_TOOLS=5
AGENT_ENABLE_INTERRUPTION=true

# ============ 消息队列配置 ============
QUEUE_BACKEND=memory
# Redis 配置 (如果使用 Redis)
QUEUE_REDIS_URL=redis://localhost:6379/0
# Kafka 配置 (如果使用 Kafka)
QUEUE_KAFKA_BOOTSTRAP_SERVERS=localhost:9092
QUEUE_KAFKA_TOPIC_PREFIX=agent
# 消息 TTL（当前后端消费链路未启用）
QUEUE_MESSAGE_TTL=3600  # `【未实现】`

# ============ 数据库配置 ============
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agent_db

# ============ 服务器配置 ============
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_DEBUG=true
SERVER_CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]

# ============ 应用配置 ============
ENVIRONMENT=development
```

### 前端配置

创建 `.env.local`:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

---

## 8 扩展开发

### 添加自定义工具

```python
from app.agent.executor import BaseTool

class WeatherTool(BaseTool):
    """天气查询工具"""
    
    @property
    def name(self) -> str:
        return "get_weather"
    
    @property
    def description(self) -> str:
        return "获取指定城市的天气信息"
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "温度单位"
                }
            },
            "required": ["city"]
        }
    
    async def execute(self, city: str, unit: str = "celsius") -> str:
        # 实现天气查询逻辑
        weather_data = await fetch_weather(city)
        return f"{city}当前温度: {weather_data['temp']}°"

# 注册工具
from app.agent.core import ChatAgent

agent = ChatAgent()
agent.register_tool(WeatherTool())
```

### SPI 服务发现（推荐）

框架采用“服务发现”方式加载工具，完整实现细节已收敛到 **5 核心模块详解 → 4) Tool SPI 注册与发现（核心实现）**，本节仅保留接入示例。

#### 第三方包接入示例（`pyproject.toml`）

```toml
[project.entry-points."chat_agent_framework.tools"]
weather = "my_plugin.weather:WeatherTool"
batch_tools = "my_plugin.bundle:provide_tools"
```

其中 `provide_tools` 可以返回：

- 单个 `BaseTool` 子类/实例
- 或工具列表（可混合类与实例）

### 添加自定义中间件

```python
from app.messaging.pipeline import PipelineMiddleware, PipelineContext
from typing import Callable, Awaitable

class AuthMiddleware(PipelineMiddleware):
    """认证中间件"""
    
    @property
    def name(self) -> str:
        return "auth"
    
    async def process(
        self,
        context: PipelineContext,
        next_handler: Callable[[PipelineContext], Awaitable]
    ):
        # 验证 token
        token = context.metadata.get("token")
        if not self._validate_token(token):
            raise UnauthorizedError("Invalid token")
        
        # 继续处理
        return await next_handler(context)
    
    def _validate_token(self, token: str) -> bool:
        # 实现验证逻辑
        return True

# 使用中间件
pipeline.use(AuthMiddleware())
```

### 自定义压缩策略

```python
from app.memory.compressor import CompressionStrategy

class CustomCompressor(CompressionStrategy):
    """自定义压缩策略"""
    
    async def compress(
        self,
        messages: list[Message],
        target_ratio: float
    ) -> tuple[list[Message], str | None]:
        # 实现自定义压缩逻辑
        retained = []
        summary = None
        
        for msg in messages:
            if self._should_keep(msg):
                retained.append(msg)
            else:
                summary = await self._summarize(msg)
        
        return retained, summary
```

---

## 9 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 运行时 |
| FastAPI | 0.115+ | Web框架 |
| Pydantic | 2.10+ | 数据验证 |
| OpenAI SDK | 1.55+ | LLM调用 |
| Tiktoken | 0.8+ | Token计数 |
| Structlog | 24.4+ | 日志系统 |
| Redis | 5.2+ | 消息队列(可选) |
| Aiokafka | 0.12+ | Kafka支持(可选) |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue | 3.5+ | UI框架 |
| TypeScript | 5.6+ | 类型系统 |
| Pinia | 2.2+ | 状态管理 |
| Vite | 5.4+ | 构建工具 |
| Tailwind CSS | 3.4+ | 样式框架 |
| Marked | 14.0+ | Markdown解析 |
| Axios | 1.7+ | HTTP客户端 |

---

## 10 License

MIT License

---

<div align="center">

**Made with ❤️ by Chat Agent Framework Team**

</div>
