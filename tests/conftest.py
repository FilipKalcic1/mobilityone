import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from fastapi_limiter import FastAPILimiter

from main import app
from services.queue import QueueService
from services.cache import CacheService
from services.context import ContextService

class FakeRedis:
    def __init__(self):
        self.data = {}    
        self.lists = {}    
        self.sets = {}     

    async def get(self, key): return self.data.get(key)
    async def set(self, key, value): self.data[key] = value; return True
    async def setex(self, key, time, value): self.data[key] = value; return True
    async def delete(self, key): 
        if key in self.data: del self.data[key]
        return 1

    async def rpush(self, key, value):
        if key not in self.lists: self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])
    
    async def llen(self, key): return len(self.lists.get(key, []))
    
    async def lrange(self, key, start, end): 
        lst = self.lists.get(key, [])
        # Redis lrange is inclusive, python slice is exclusive
        if end == -1: return lst[start:]
        return lst[start:end+1]

    async def ltrim(self, key, start, end):
        """
        Ovo je popravljena logika koja zapravo reže listu.
        Redis LTRIM zadržava elemente unutar raspona.
        """
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

    app.state.redis = redis_client
    app.state.queue = QueueService(redis_client)
    app.state.cache = CacheService(redis_client)
    app.state.context = ContextService(redis_client)
    

    mock_registry = MagicMock()
    mock_registry.find_relevant_tools = AsyncMock(return_value=[])
    mock_registry.tools_map = {
        "get_vehicle_location": {
            "path": "/vehicles/loc", "method": "GET", "description": "Test tool"
        }
    }
    app.state.tool_registry = mock_registry

    mock_gateway = MagicMock()
    mock_gateway.execute_tool = AsyncMock(return_value={"status": "mocked_success"})
    app.state.api_gateway = mock_gateway
    

    await FastAPILimiter.init(redis_client)
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac