import json
import redis.asyncio as redis
import asyncio
from typing import Dict, Any

class QueueService:
    queue_name = "whatsapp_outbound"
    schedule_queue_name = "schedule_queue"
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, to: str, text: str, attempts: int = 0):
        """
        Stavlja poruku u Redis Listu (FIFO Queue).
        """
        payload = {
            "to": to,
            "text": text,
            "attempts": attempts 
        }
        # RPUSH dodaje na kraj liste
        await self.redis.rpush(self.queue_name, json.dumps(payload))

    async def schedule_retry(self, payload: Dict[str, Any]):
        """
        Stavlja poruku natrag u red s odgodom koristeći Redis ZSET.
        """
        attempts = payload.get('attempts', 0) + 1
        
        # Limit pokušaja (npr. 5)
        if attempts >= 5:
            return "DEAD_LETTER"

        # Eksponencijalni backoff: 2^attempts (2s, 4s, 8s...)
        delay = 2 ** attempts
        payload['attempts'] = attempts
        
        # ZADD dodaje u Sorted Set gdje je score = vrijeme izvršavanja
        await self.redis.zadd(
            self.schedule_queue_name, 
            {json.dumps(payload): asyncio.get_event_loop().time() + delay}
        )
        return "SCHEDULED"