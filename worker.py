import asyncio
import json
import httpx
import signal
import redis.asyncio as redis
import structlog
from config import get_settings
from logger_config import configure_logger  
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE


settings = get_settings()
configure_logger()
logger = structlog.get_logger("worker")

class WhatsappWorker:
    def __init__(self):
        self.redis = None
        self.http = None
        self.queue = None
        self.running = True

    async def start(self):
        """Pokreće worker proces."""
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        
        logger.info("Worker started", env=settings.APP_ENV)

        while self.running:
            try:
                
                await asyncio.gather(
                    self._process_outbound(),
                    self._process_retries(),
                    return_exceptions=True 
                )
                
                await asyncio.sleep(0.1) 
                
            except Exception as e:
                logger.error("Critical loop error", error=str(e))
                await asyncio.sleep(1)

        await self.shutdown()

    async def _process_outbound(self):
        if not self.running: return


        task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
        if not task: return

        try:
            payload = json.loads(task[1])

            if not isinstance(payload, dict):
                logger.warning("Invalid payload format", data=task[1])
                return

            cid = payload.get("cid", "unknown")
            to_number = payload.get("to")
            
            log = logger.bind(cid=cid, to=to_number)
            
            try:
                await self._send_infobip(payload)
                log.info("Message sent to Infobip")
            except Exception as e:
                log.warn("Send failed, scheduling retry", error=str(e))
                await self.queue.schedule_retry(payload)
                
        except json.JSONDecodeError:
            logger.error("Corrupted JSON in queue", data=task[1])

    async def _process_retries(self):
        if not self.running: return
        

        now = asyncio.get_event_loop().time()
        tasks = await self.redis.zrangebyscore(QUEUE_SCHEDULE, 0, now, start=0, num=1)
        
        if tasks:
            task_str = tasks[0]
            if await self.redis.zrem(QUEUE_SCHEDULE, task_str):
                data = json.loads(task_str)
                logger.info("Retrying message", to=data['to'], attempt=data['attempts'])
                await self.queue.enqueue(data['to'], data['text'], data.get('cid'), data['attempts'])

    async def _send_infobip(self, payload):
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
        resp = await self.http.post(url, json=body, headers=headers)
        resp.raise_for_status()

    async def shutdown(self):
        logger.info("Shutting down worker...")
        if self.http: await self.http.aclose()
        if self.redis: await self.redis.aclose()
        logger.info("Worker stopped.")

async def main():
    worker = WhatsappWorker()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'running', False))
    
    await worker.start()

if __name__ == "__main__":
    asyncio.run(main())