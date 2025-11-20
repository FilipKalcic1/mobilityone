import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from fastapi_limiter import FastAPILimiter

# Import tvoje aplikacije
from main import app
from services.queue import QueueService
from services.cache import CacheService
from services.context import ContextService

# --- FAKE REDIS (Čista implementacija) ---
class FakeRedis:
    def __init__(self):
        self.data = {}     # key: value
        self.lists = {}    # key: [val1, val2]
        self.sets = {}     # key: {member: score}
        
    # --- OSNOVNE ASYNC METODE ---
    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value
        return True

    async def setex(self, key, time, value):
        self.data[key] = value
        return True
        
    async def delete(self, key):
        if key in self.data: del self.data[key]
        if key in self.lists: del self.lists[key]
        if key in self.sets: del self.sets[key]
        return 1

    # --- LISTE ---
    async def rpush(self, key, value):
        if key not in self.lists: self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])
    
    # FIX: Dodana metoda llen koju test traži
    async def llen(self, key):
        if key not in self.lists: return 0
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        if key not in self.lists: return []
        lst = self.lists[key]
        if end == -1: return lst[start:]
        return lst[start:end+1]

    async def ltrim(self, key, start, end):
        if key not in self.lists: return
        if end == -1: self.lists[key] = self.lists[key][start:]
        else: self.lists[key] = self.lists[key][start:end+1]
        return True
        
    async def blpop(self, key, timeout=0):
        if key in self.lists and self.lists[key]:
            item = self.lists[key].pop(0)
            return [key, item]
        return None

    async def expire(self, key, time):
        return True

    # --- ZSET ---
    async def zadd(self, key, mapping):
        if key not in self.sets: self.sets[key] = {}
        self.sets[key].update(mapping)
        return 1
        
    async def zrangebyscore(self, key, min, max, start=None, num=None):
        return [] 
        
    async def zrem(self, key, member):
        return 1

    # --- PIPELINE ---
    def pipeline(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def execute(self):
        return []

    # --- FASTAPI LIMITER SUPPORT ---
    async def eval(self, *args, **kwargs): return 0
    async def evalsha(self, *args, **kwargs): return 0
    
    async def script_load(self, script): 
        return "dummy_sha_123"
    
    # --- CLOSE ---
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
    
    await FastAPILimiter.init(redis_client)
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac