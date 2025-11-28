import asyncio
import uuid
import structlog
import orjson
import redis.asyncio as redis
from typing import Optional, Dict, Any

logger = structlog.get_logger("queue")

# --- KONSTANTE ---
STREAM_INBOUND = "whatsapp_stream_inbound"
QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_SCHEDULE = "schedule_retry"

# Odvojeni DLQ redovi za lakšu analizu grešaka
QUEUE_DLQ_OUTBOUND = "dlq:outbound"  # Neuspjela slanja prema WhatsAppu
QUEUE_DLQ_INBOUND = "dlq:inbound"    # [NOVO] Neuspjele obrade webhooka (otrovne poruke)

class QueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue_inbound(self, sender: str, text: str, message_id: str) -> str:
        """
        Sprema ulaznu poruku u Redis Stream.
        Vraća ID poruke u streamu.
        """
        payload = {
            "sender": sender,
            "text": text,
            "message_id": message_id,
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        # XADD vraća ID nove poruke (timestamp-sequence)
        stream_id = await self.redis.xadd(STREAM_INBOUND, payload)
        logger.debug("Inbound message queued", stream_id=stream_id, sender=sender)
        return stream_id

    async def store_inbound_dlq(self, payload: dict, error: str):
        """
        [ROBUSTNOST] Sprema 'otrovnu' poruku u Dead Letter Queue.
        Koristi se kada worker ne može obraditi poruku (npr. bug u kodu, invalidan format).
        Ovo sprječava gubitak podataka i omogućuje kasniju analizu.
        """
        dlq_entry = {
            "original_payload": payload,
            "error": str(error),
            "failed_at": str(asyncio.get_event_loop().time())
        }
        
        data = orjson.dumps(dlq_entry).decode('utf-8')
        await self.redis.rpush(QUEUE_DLQ_INBOUND, data)
        
        logger.critical(
            "Message moved to Inbound DLQ", 
            msg_id=payload.get('message_id'), 
            reason=error
        )

    async def enqueue(self, to: str, text: str, correlation_id: str = None, attempts: int = 0):
        """
        Šalje poruku u Outbound red (za slanje na WhatsApp).
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
        logger.debug("Outbound message enqueued", to=to, cid=correlation_id)

    async def schedule_retry(self, payload: Dict[str, Any]):
        """
        Exponential Backoff: Ako slanje ne uspije, zakazuje ponovni pokušaj.
        """
        attempts = payload.get('attempts', 0) + 1
        cid = payload.get("cid")

        # Maksimalno 5 pokušaja prije odustajanja
        if attempts >= 5:
            logger.error("Max outbound retries reached. Moving to DLQ.", cid=cid)
            
            # Dodajemo informaciju o grešci u payload
            payload['error'] = "Max retries exceeded"
            payload['failed_at'] = str(asyncio.get_event_loop().time())
            
            await self.redis.rpush(QUEUE_DLQ_OUTBOUND, orjson.dumps(payload).decode('utf-8'))
            return 

        # Eksponencijalno čekanje: 2s, 4s, 8s, 16s, 32s
        delay = 2 ** attempts 
        payload['attempts'] = attempts
        
        execute_at = asyncio.get_event_loop().time() + delay
        data = orjson.dumps(payload).decode('utf-8')
        
        # ZADD koristi sorted set za zakazivanje u budućnosti
        await self.redis.zadd(QUEUE_SCHEDULE, {data: execute_at})
        logger.info("Message rescheduled", cid=cid, attempt=attempts, delay=delay)