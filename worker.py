import asyncio
import redis.asyncio as redis
import json
import httpx
from config import get_settings
from services.queue import QueueService 
import structlog
from typing import Optional

settings = get_settings()
logger = structlog.get_logger("worker")

# Globalne reference za lakše testiranje (mocking)
redis_client: Optional[redis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None

async def send_to_infobip(client: httpx.AsyncClient, payload: dict):
    url = f"https://{settings.INFOBIP_BASE_URL}/whatsapp/1/message/text"
    headers = {
        "Authorization": f"App {settings.INFOBIP_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "from": settings.INFOBIP_SENDER_NUMBER,
        "to": payload['to'],
        "content": {"text": payload['text']}
    }
    resp = await client.post(url, json=body, headers=headers, timeout=10.0)
    resp.raise_for_status()

async def run_worker():
    global redis_client, http_client
    
    # Inicijalizacija
    if redis_client is None:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if http_client is None:
        http_client = httpx.AsyncClient()
        
    q = QueueService(redis_client)
    logger.info("Worker started")

    while True:
        try:
            # 1. Glavni red
            task = await redis_client.blpop(q.queue_name, timeout=1)
            if task:
                data = json.loads(task[1])
                try:
                    await send_to_infobip(http_client, data)
                    logger.info("Sent", to=data.get('to'))
                except Exception as e:
                    logger.error("Send failed, retrying", error=str(e))
                    await q.schedule_retry(data)

            # 2. Retry red (ZSET)
            now = asyncio.get_event_loop().time()
            # Dohvati dospjele zadatke
            tasks = await redis_client.zrangebyscore(q.schedule_queue_name, 0, now, start=0, num=1)
            
            if tasks:
                if await redis_client.zrem(q.schedule_queue_name, tasks[0]):
                    payload = json.loads(tasks[0])
                    logger.info("Retrying message", to=payload.get('to'))
                    await q.enqueue(payload['to'], payload['text'], payload['attempts'])

        except Exception as e:
            logger.error("Worker loop error", error=str(e))
            await asyncio.sleep(1)

if __name__ == "__main__":
    from logger_config import configure_logger
    configure_logger()
    asyncio.run(run_worker())