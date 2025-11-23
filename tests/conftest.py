import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from fastapi_limiter import FastAPILimiter
import fnmatch  # Za keys matching

from main import app
from services.queue import QueueService
from services.cache import CacheService
from services.context import ContextService

from routers.webhook import get_queue, get_context, get_registry, get_gateway

class FakeRedis:
    def __init__(self):
        self.data = {}    
        self.lists = {}    
        self.sets = {}     

    async def get(self, key): return self.data.get(key)
    async def set(self, key, value, *args, **kwargs): self.data[key] = value; return True
    async def setex(self, key, time, value): self.data[key] = value; return True
    async def delete(self, key): 
        if key in self.data: del self.data[key]
        return 1

    # [FIX] Dodana metoda keys za ToolRegistry testove
    async def keys(self, pattern="*"):
        # Jednostavna implementacija glob matchinga
        return [k for k in self.data.keys() if fnmatch.fnmatch(k, pattern)]

    async def rpush(self, key, value):
        if key not in self.lists: self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])
    
    async def llen(self, key): return len(self.lists.get(key, []))
    
    async def lrange(self, key, start, end): 
        lst = self.lists.get(key, [])
        if end == -1: return lst[start:]
        return lst[start:end+1]

    async def ltrim(self, key, start, end):
        if key not in self.lists: return True
        lst = self.lists[key]
        if end == -1:
            self.lists[key] = lst[start:]
        else:
            self.lists[key] = lst[start:end+1]
        return True

    async def blpop(self, key, timeout=0):
        if key in self.lists and self.lists[key]: return [key, self.lists[key].pop(0)]
        return None
    
    async def expire(self, key, time): return True
    async def zadd(self, key, mapping): return 1
    async def zrangebyscore(self, key, min, max, start=None, num=None): return [] 
    async def zrem(self, key, member): return 1
    
    def pipeline(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): pass
    async def execute(self): return []
    async def eval(self, *args, **kwargs): return 0
    async def evalsha(self, *args, **kwargs): return 0
    async def script_load(self, script): return "dummy_sha"
    async def close(self): pass
    async def aclose(self): pass

@pytest.fixture
def redis_client():
    return FakeRedis()

@pytest_asyncio.fixture
async def async_client(redis_client):
    queue_service = QueueService(redis_client)
    cache_service = CacheService(redis_client)
    context_service = ContextService(redis_client)
    
    mock_registry = MagicMock()
    mock_registry.find_relevant_tools = AsyncMock(return_value=[])
    mock_registry.tools_map = {
        "get_vehicle_location": {
            "path": "/vehicles/loc", "method": "GET", "description": "Test tool"
        }
    }

    mock_gateway = MagicMock()
    mock_gateway.execute_tool = AsyncMock(return_value={"status": "mocked_success"})
    
    app.dependency_overrides[get_queue] = lambda: queue_service
    app.dependency_overrides[get_context] = lambda: context_service
    app.dependency_overrides[get_registry] = lambda: mock_registry
    app.dependency_overrides[get_gateway] = lambda: mock_gateway
    
    await FastAPILimiter.init(redis_client)
    
    app.state.redis = redis_client
    app.state.queue = queue_service
    app.state.cache = cache_service
    app.state.context = context_service
    app.state.api_gateway = mock_gateway 
    app.state.tool_registry = mock_registry 

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides = {}