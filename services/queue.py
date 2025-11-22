import json
import redis.asyncio as redis
import asyncio
import uuid
import structlog

logger = structlog.get_logger("queue")

QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_SCHEDULE = "schedule_retry"
QUEUE_DLQ = "whatsapp_dlq"

class QueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, to: str, text: str, correlation_id: str = None, attempts: int = 0):
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        payload = json.dumps({
            "to": to,
            "text": text,
            "cid": correlation_id, 
            "attempts": attempts
        })
        await self.redis.rpush(QUEUE_OUTBOUND, payload)

    async def schedule_retry(self, payload: dict):
        """
        Pokušava ponovno poslati poruku s eksponencijalnim odmakom.
        Ako ne uspije 5 puta, šalje u DLQ.
        """
        attempts = payload.get('attempts', 0) + 1
        
        if attempts >= 5:
            logger.error("Max retries reached. Moving to DLQ.", cid=payload.get("cid"))
            await self.move_to_dlq(payload)
            return 

        # Exponential Backoff (2s, 4s, 8s...)
        delay = 2 ** attempts
        payload['attempts'] = attempts
        
        execute_at = asyncio.get_event_loop().time() + delay
        
        await self.redis.zadd(
            QUEUE_SCHEDULE, 
            {json.dumps(payload): execute_at}
        )

    async def move_to_dlq(self, payload: dict):
        """Sprema propalu poruku u DLQ za ručnu analizu."""
        payload['failed_at'] = asyncio.get_event_loop().time()
        await self.redis.rpush(QUEUE_DLQ, json.dumps(payload))