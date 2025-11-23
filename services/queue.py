import orjson 
import redis.asyncio as redis
import asyncio
import uuid
import structlog

logger = structlog.get_logger("queue")


QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_SCHEDULE = "schedule_retry"
QUEUE_DLQ = "whatsapp_dlq"
QUEUE_INBOUND = "whatsapp_inbound" 

class QueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, to: str, text: str, correlation_id: str = None, attempts: int = 0):
        """
        Šalje poruku u outbound red (za Workera da pošalje na WhatsApp).
        """
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        payload = {
            "to": to,
            "text": text,
            "cid": correlation_id, 
            "attempts": attempts
        }
        

        data = orjson.dumps(payload).decode('utf-8')
        
        await self.redis.rpush(QUEUE_OUTBOUND, data)

    async def enqueue_inbound(self, sender: str, text: str, message_id: str):
        """
        Prima webhook od Infobipa i sprema ga za obradu.
        """
        payload = {
            "sender": sender,
            "text": text,
            "message_id": message_id,
            "timestamp": asyncio.get_event_loop().time()
        }
        

        data = orjson.dumps(payload).decode('utf-8')
        
        await self.redis.rpush(QUEUE_INBOUND, data)

    async def schedule_retry(self, payload: dict):
        """
        Ako slanje ne uspije, zakazuje ponovni pokušaj s eksponencijalnim odmakom.
        """
        attempts = payload.get('attempts', 0) + 1
        

        if attempts >= 5:
            logger.error("Max retries reached. Moving to DLQ.", cid=payload.get("cid"))
            await self.move_to_dlq(payload)
            return 

        delay = 2 ** attempts 
        payload['attempts'] = attempts
        
        execute_at = asyncio.get_event_loop().time() + delay
        
        data = orjson.dumps(payload).decode('utf-8')
        
        await self.redis.zadd(QUEUE_SCHEDULE, {data: execute_at})

    async def move_to_dlq(self, payload: dict): 
        """
        Prebacuje poruku u 'groblje poruka' (DLQ) za kasniju analizu.
        """
        payload['failed_at'] = asyncio.get_event_loop().time()
        data = orjson.dumps(payload).decode('utf-8')
        
        await self.redis.rpush(QUEUE_DLQ, data)