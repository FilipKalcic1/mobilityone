import redis.asyncio as redis
import time
import orjson  
import tiktoken 
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


CONTEXT_TTL = 3600 
MAX_TOKENS = 2000 

class Message(BaseModel):
    role: str
    content: Optional[str] = None
    timestamp: float = 0.0

    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

        self.encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: Optional[str], **kwargs):
        """
        Sprema poruku u povijest. 
        [FIX] kwargs omogućuje prosljeđivanje 'tool_calls', 'tool_call_id', 'name'.
        """
        msg = Message(
            role=role, 
            content=content, 
            timestamp=time.time(),
            **kwargs
        )
        key = self._key(sender)
        

        data = orjson.dumps(msg.model_dump(exclude_none=True)).decode('utf-8')

        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, data)
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()
        
        await self._trim_by_tokens(key)

    async def get_history(self, sender: str) -> List[dict]:
        key = self._key(sender)
        raw_data = await self.redis.lrange(key, 0, -1)

        return [orjson.loads(m) for m in raw_data]
    
    async def clear_history(self, sender: str):
        await self.redis.delete(self._key(sender))

    async def _trim_by_tokens(self, key: str):
        """
        Briše stare poruke dok suma tokena ne padne ispod MAX_TOKENS.
        Zadržana originalna logika s tiktokenom.
        """
        raw_data = await self.redis.lrange(key, 0, -1)
        messages = [orjson.loads(m) for m in raw_data]
        
        total_tokens = 0
        keep_indices = []
        
        for i, msg in enumerate(reversed(messages)):

            content = msg.get("content") or ""
            tokens = len(self.encoding.encode(str(content)))
            
            if total_tokens + tokens > MAX_TOKENS:
                break
            
            total_tokens += tokens
            keep_indices.append(len(messages) - 1 - i)
            
        if keep_indices:
            start_index = min(keep_indices)
            if start_index > 0:
                await self.redis.ltrim(key, start_index, -1)