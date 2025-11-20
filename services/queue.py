import json
import redis.asyncio as redis
import asyncio
import uuid

QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_SCHEDULE = "schedule_retry"

class QueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, to: str, text: str, correlation_id: str = None, attempts: int = 0):
        """
        Stavlja poruku u red za slanje.
        Prima optional 'correlation_id' za praćenje poruke kroz sustav.
        """
        # Ako ID nije proslijeđen (npr. iz starih testova), generiraj novi
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        payload = json.dumps({
            "to": to,
            "text": text,
            "cid": correlation_id, # CID putuje s porukom
            "attempts": attempts
        })
        
        # RPUSH dodaje na kraj liste
        await self.redis.rpush(QUEUE_OUTBOUND, payload)

    async def schedule_retry(self, payload: dict):
        """
        Stavlja poruku natrag u red s odgodom koristeći Redis ZSET.
        """
        attempts = payload.get('attempts', 0) + 1
        
        # Limit pokušaja (npr. 5) - Dead Letter logika
        if attempts >= 5:
            return 

        # Eksponencijalni backoff: 2^attempts (2s, 4s, 8s...)
        delay = 2 ** attempts
        payload['attempts'] = attempts
        
        # ZADD dodaje u Sorted Set gdje je score = vrijeme izvršavanja
        # Bitno: payload sada sadrži i 'cid', pa ga ne gubimo kod retry-a
        execute_at = asyncio.get_event_loop().time() + delay
        
        await self.redis.zadd(
            QUEUE_SCHEDULE, 
            {json.dumps(payload): execute_at}
        )