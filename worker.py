import asyncio
import json
import httpx
import signal
import redis.asyncio as redis
import structlog
from config import get_settings
from logger_config import configure_logger  
# Importamo nove servise jer ih worker sada treba
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE, QUEUE_INBOUND
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway
from services.ai import analyze_intent

settings = get_settings()
configure_logger()
logger = structlog.get_logger("worker")

class WhatsappWorker:
    def __init__(self):
        self.redis = None
        self.http = None
        self.queue = None
        self.context = None
        self.registry = None
        self.gateway = None
        self.running = True

    async def start(self):
        """Pokreće worker proces."""
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        self.context = ContextService(self.redis)
        
        # Učitavanje alata
        self.registry = ToolRegistry()
        try:
            await self.registry.load_swagger("swagger.json")
        except Exception as e:
            logger.error("Failed to load swagger in worker", error=str(e))

        # Inicijalizacija Gatewaya
        if settings.MOBILITY_API_URL:
             self.gateway = OpenAPIGateway(base_url=settings.MOBILITY_API_URL)
        
        logger.info("Worker started", env=settings.APP_ENV)

        while self.running:
            try:
                # Heartbeat za Docker healthcheck
                await self.redis.setex("worker:heartbeat", 30, "alive")

                await asyncio.gather(
                    self._process_outbound(),
                    self._process_retries(),
                    self._process_inbound(), # <--- NOVO
                    return_exceptions=True 
                )
                
                await asyncio.sleep(0.1) 
                
            except Exception as e:
                logger.error("Critical loop error", error=str(e))
                await asyncio.sleep(1)

        await self.shutdown()

    async def _process_inbound(self):
        """Obrađuje dolazne poruke (AI Logika)"""
        if not self.running: return

        task = await self.redis.blpop(QUEUE_INBOUND, timeout=1)
        if not task: return

        try:
            payload = json.loads(task[1])
            sender = payload['sender']
            user_text = payload['text']
            
            log = logger.bind(sender=sender)
            log.info("Processing inbound message")

            # 1. Spremi poruku korisnika
            await self.context.add_message(sender, "user", user_text)
            
            # 2. Pronađi alate
            relevant_tools = await self.registry.find_relevant_tools(user_text, top_k=5)

            # 3. AI Analiza
            history = await self.context.get_history(sender)
            # Šaljemo history bez zadnje poruke jer analyze_intent dodaje current_text
            ai_decision = await analyze_intent(history[:-1], user_text, tools=relevant_tools)
            
            final_response_text = ""

            # 4. Izvršavanje alata
            if ai_decision.get("tool"):
                tool_name = ai_decision["tool"]
                params = ai_decision["parameters"]
                
                log.info("Executing tool", tool=tool_name)
                
                tool_def = self.registry.tools_map.get(tool_name)
                if tool_def and self.gateway:
                    api_result = await self.gateway.execute_tool(tool_def, params)
                    final_response_text = f"✅ *Status:* Uspjeh\n📦 *Podaci:* {json.dumps(api_result, ensure_ascii=False)}"
                else:
                    final_response_text = "⚠️ Greška: Alat nedostupan."
            else:
                final_response_text = ai_decision.get("response_text", "Nisam razumio upit.")

            # 5. Slanje odgovora
            await self.context.add_message(sender, "assistant", final_response_text)
            await self.queue.enqueue(sender, final_response_text)

        except Exception as e:
            logger.error("Error processing inbound", error=str(e))

    async def _process_outbound(self):
        if not self.running: return

        task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
        if not task: return

        try:
            payload = json.loads(task[1])
            # ... (ostatak koda isti kao prije)
            cid = payload.get("cid", "unknown")
            try:
                await self._send_infobip(payload)
            except Exception as e:
                logger.warn("Send failed", error=str(e))
                await self.queue.schedule_retry(payload)
                
        except json.JSONDecodeError:
            pass

    async def _process_retries(self):
        if not self.running: return
        now = asyncio.get_event_loop().time()
        tasks = await self.redis.zrangebyscore(QUEUE_SCHEDULE, 0, now, start=0, num=1)
        if tasks:
            task_str = tasks[0]
            if await self.redis.zrem(QUEUE_SCHEDULE, task_str):
                data = json.loads(task_str)
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
        if self.gateway: await self.gateway.close() # Zatvaramo gateway
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