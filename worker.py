import asyncio
import json
import httpx
import signal
import redis.asyncio as redis
import structlog
from config import get_settings
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE

settings = get_settings()

# Konfiguracija logera za JSON output (lakše za Kibanu/Datadog)
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger("worker")

class WhatsappWorker:
    def __init__(self):
        self.redis = None
        self.http = None
        self.queue = None
        self.running = True # Zastavica za kontrolu petlje

    async def start(self):
        # Inicijalizacija resursa
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        
        logger.info("Worker started", env=settings.APP_ENV)

        # Glavna petlja
        while self.running:
            try:
                # Paralelno provjeravamo oba reda (izlazni i retry)
                await self._process_outbound()
                await self._process_retries()
            except Exception as e:
                # Hvata neočekivane greške da se worker ne sruši
                logger.error("Critical loop error", error=str(e))
                await asyncio.sleep(1)
        
        # Kad running postane False, čistimo resurse
        await self.shutdown()

    async def _process_outbound(self):
        if not self.running: return
        
        # BLPOP čeka 1 sekundu. To omogućuje da svake sekunde provjerimo self.running
        task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
        if not task: return

        payload = json.loads(task[1])
        cid = payload.get("cid", "unknown")
        to_number = payload.get("to")
        
        # Vežemo logger za ovaj konkretan zadatak
        log = logger.bind(cid=cid, to=to_number)
        
        try:
            await self._send_infobip(payload)
            log.info("Message sent successfully")
        except Exception as e:
            log.warn("Send failed, scheduling retry", error=str(e))
            # Ako slanje ne uspije, vrati u retry red
            await self.queue.schedule_retry(payload)

    async def _process_retries(self):
        if not self.running: return
        
        now = asyncio.get_event_loop().time()
        # Dohvati zadatke kojima je prošlo vrijeme čekanja
        tasks = await self.redis.zrangebyscore(QUEUE_SCHEDULE, 0, now, start=0, num=1)
        
        if tasks:
            # Atomski ukloni iz ZSET-a da ga drugi workeri ne uzmu
            if await self.redis.zrem(QUEUE_SCHEDULE, tasks[0]):
                data = json.loads(tasks[0])
                # Vrati u glavni red za slanje
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
        resp.raise_for_status() # Baca grešku ako nije 2xx

    async def shutdown(self):
        logger.info("Shutting down worker...")
        if self.http: await self.http.aclose()
        if self.redis: await self.redis.aclose()
        logger.info("Worker stopped gracefully.")

# Entry point
async def main():
    worker = WhatsappWorker()
    
    # Postavljamo hvatanje signala (Ctrl+C ili Docker Stop)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'running', False))
    
    await worker.start()

if __name__ == "__main__":
    asyncio.run(main())