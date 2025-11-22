import redis.asyncio as redis
import time
import json
import tiktoken 
from typing import List
from pydantic import BaseModel

CONTEXT_TTL = 3600 
MAX_TOKENS = 2000 

class Message(BaseModel):
    role: str
    content: str
    timestamp: float = 0.0

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

        self.encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: str):
        msg = Message(role=role, content=content, timestamp=time.time())
        key = self._key(sender)
        
        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, msg.model_dump_json())
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()
        
        await self._trim_by_tokens(key)

    async def get_history(self, sender: str) -> List[dict]:
        key = self._key(sender)
        raw_data = await self.redis.lrange(key, 0, -1)
        return [json.loads(m) for m in raw_data]
    
    async def clear_history(self, sender: str):
        await self.redis.delete(self._key(sender))


    async def _trim_by_tokens(self, key: str):
        """Briše stare poruke dok suma tokena ne padne ispod MAX_TOKENS"""
        raw_data = await self.redis.lrange(key, 0, -1)
        messages = [json.loads(m) for m in raw_data]
        
        total_tokens = 0
        keep_indices = []
        

        for i, msg in enumerate(reversed(messages)):
            content = msg.get("content", "")

            tokens = len(self.encoding.encode(content))
            
            if total_tokens + tokens > MAX_TOKENS:
                break
            
            total_tokens += tokens
            keep_indices.append(len(messages) - 1 - i)
            
        if keep_indices:

            start_index = min(keep_indices)
            if start_index > 0:
                await self.redis.ltrim(key, start_index, -1)