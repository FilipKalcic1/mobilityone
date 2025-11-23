import orjson 
import redis.asyncio as redis
import asyncio
import uuid
import structlog

logger = structlog.get_logger("queue")

# --- Konstante redova ---
QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_SCHEDULE = "schedule_retry"
QUEUE_DLQ = "whatsapp_dlq"

# [NOVO] Stream za ulazne poruke (zamjenjuje staru listu QUEUE_INBOUND)
# Koristimo Stream jer on pamti poruke i omogućuje "Ack" potvrdu
STREAM_INBOUND = "whatsapp_stream_inbound" 

class QueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, to: str, text: str, correlation_id: str = None, attempts: int = 0):
        """
        Šalje poruku u outbound red (za Workera da pošalje na WhatsApp).
        Zadržavamo običnu listu (RPUSH) jer tu imamo 'schedule_retry' logiku koja pokriva greške.
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
        [POBOLJŠANO] Prima webhook od Infobipa i sprema ga u Redis Stream.
        
        Zašto Stream?
        - Ako se worker sruši usred obrade, poruka ostaje u streamu kao 'pending'.
        - Worker je mora eksplicitno potvrditi (XACK) tek kad je gotov.
        - Time sprječavamo gubitak poruka ("At-least-once delivery").
        """
        payload = {
            "sender": sender,
            "text": text,
            "message_id": message_id,
            # Redis Stream zahtijeva stringove/bytes za vrijednosti, float timestamp pretvaramo u str
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        # Koristimo xadd umjesto rpush za dodavanje u stream
        # '*' znači da Redis sam generira ID unosa
        await self.redis.xadd(STREAM_INBOUND, payload)

    async def schedule_retry(self, payload: dict):
        """
        Ako slanje prema WhatsAppu ne uspije, zakazuje ponovni pokušaj s eksponencijalnim odmakom.
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
        
        # ZADD koristi sorted set za zakazivanje u budućnosti
        await self.redis.zadd(QUEUE_SCHEDULE, {data: execute_at})

    async def move_to_dlq(self, payload: dict): 
        """
        Prebacuje poruku u 'groblje poruka' (Dead Letter Queue) za kasniju ručnu analizu.
        """
        payload['failed_at'] = asyncio.get_event_loop().time()
        data = orjson.dumps(payload).decode('utf-8')
        
        await self.redis.rpush(QUEUE_DLQ, data)