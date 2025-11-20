import redis.asyncio as redis
import time
import json
from typing import List
from pydantic import BaseModel

CONTEXT_TTL = 1800
MAX_HISTORY_LENGTH = 10

class Message(BaseModel):
    role: str
    content: str
    timestamp: float = 0.0

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: str):
        msg = Message(role=role, content=content, timestamp=time.time())
        key = self._key(sender)
        
        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, msg.model_dump_json())
            await pipe.ltrim(key, -MAX_HISTORY_LENGTH, -1)
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()

    async def get_history(self, sender: str) -> List[dict]:
        key = self._key(sender)
        raw_data = await self.redis.lrange(key, 0, -1)
        # Vraćamo format spreman za OpenAI (samo role i content)
        return [json.loads(m) for m in raw_data]