# worker.py
import asyncio
import uuid
import signal
import socket
import redis.asyncio as redis
import httpx
import structlog
import orjson
from prometheus_client import start_http_server, Counter, Histogram

from config import get_settings
from logger_config import configure_logger
from database import AsyncSessionLocal
# [POPRAVAK] Uklonjen nepostojeći QUEUE_DLQ iz importa
from services.queue import QueueService, STREAM_INBOUND, QUEUE_OUTBOUND, QUEUE_SCHEDULE
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway
from services.user_service import UserService
from services.ai import analyze_intent

settings = get_settings()
configure_logger()
logger = structlog.get_logger("worker")

# --- DEFINICIJA METRIKA (Za Grafanu) ---
MSG_PROCESSED = Counter('whatsapp_msg_total', 'Ukupan broj obrađenih poruka', ['status'])
AI_LATENCY = Histogram('ai_processing_seconds', 'Vrijeme obrade AI zahtjeva', buckets=[1, 2, 5, 10, 20])

# --- SIGURNOST LOGIRANJA ---
SENSITIVE_KEYS = {'email', 'phone', 'password', 'token', 'authorization', 'secret', 'apikey', 'to'}

def sanitize_log_data(data: dict) -> dict:
    """Maskira osjetljive podatke u logovima."""
    if not isinstance(data, dict): return data
    clean = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_KEYS:
            clean[k] = "***MASKED***"
        elif isinstance(v, dict):
            clean[k] = sanitize_log_data(v)
        else:
            clean[k] = v
    return clean

class WhatsappWorker:
    def __init__(self):
        self.worker_id = str(uuid.uuid4())[:8]
        self.hostname = socket.gethostname() # Identifikacija kontejnera
        self.running = True
        
        # Resursi se inicijaliziraju u start()
        self.redis = None
        self.gateway = None
        self.http = None
        self.queue = None
        self.context = None
        self.registry = None

    async def start(self):
        """Inicijalizacija infrastrukture i pokretanje glavne petlje."""
        logger.info("Worker starting", id=self.worker_id, host=self.hostname)
        
        # 1. Start Prometheus Metrics Server
        try:
            start_http_server(8001)
            logger.info("Prometheus metrics server running on port 8001")
        except Exception as e:
            logger.warning("Failed to start metrics server", error=str(e))
        
        # 2. Povezivanje na infrastrukturu
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        self.context = ContextService(self.redis)
        
        # 3. API Gateway
        if settings.MOBILITY_API_URL:
            self.gateway = OpenAPIGateway(base_url=settings.MOBILITY_API_URL)
        else:
            logger.warning("MOBILITY_API_URL not set. AI tools will fail.")

        # 4. Tool Registry (S logikom prioriteta URL vs File)
        self.registry = ToolRegistry(self.redis)
        swagger_src = settings.SWAGGER_URL or "swagger.json"
        
        try:
            logger.info(f"Loading swagger from: {swagger_src}")
            await self.registry.load_swagger(swagger_src)
            if swagger_src.startswith("http"):
                asyncio.create_task(self.registry.start_auto_update(swagger_src))
        except Exception as e:
            logger.error("Failed to load Swagger definition", error=str(e))

        # 5. Osiguraj Redis Consumer Group
        try:
            await self.redis.xgroup_create(STREAM_INBOUND, "workers_group", id="$", mkstream=True)
        except redis.ResponseError:
            pass 

        logger.info("Worker ready. Processing loop started.")
        
        # 6. Glavna Petlja
        while self.running:
            # Heartbeat za healthcheck (Globalni i per-worker)
            await self.redis.setex("worker:heartbeat", 30, "alive")
            await self.redis.setex(f"worker:heartbeat:{self.hostname}:{self.worker_id}", 30, "alive")

            try:
                # Paralelno obrađuj:
                # 1. Ulazne poruke (Stream)
                # 2. Izlazne poruke (Queue)
                # 3. Ponovne pokušaje (Retry Schedule)
                await asyncio.gather(
                    self._process_inbound_batch(),
                    self._process_outbound(),
                    self._process_retries(),
                    return_exceptions=True
                )
                await asyncio.sleep(0.01)
                
            except Exception as e:
                logger.error("Critical Main Loop Error", error=str(e))
                await asyncio.sleep(1)

        await self.shutdown()

    async def _process_inbound_batch(self):
        """Čita do 10 poruka odjednom i obrađuje ih paralelno."""
        if not self.running: return

        try:
            streams = await self.redis.xreadgroup(
                groupname="workers_group",
                consumername=self.worker_id,
                streams={STREAM_INBOUND: ">"},
                count=10,
                block=2000
            )
            
            if not streams: return

            tasks = []
            for _, messages in streams:
                for msg_id, data in messages:
                    tasks.append(self._process_single_message_transaction(msg_id, data))
            
            if tasks:
                await asyncio.gather(*tasks)

        except Exception as e:
            logger.error("Stream read error", error=str(e))

    async def _process_single_message_transaction(self, msg_id: str, payload: dict):
        """Obrađuje jednu poruku, mjeri vrijeme i šalje ACK."""
        try:
            sender = payload.get('sender')
            text = payload.get('text', '').strip()
            
            if sender and text:
                # Rate Limiting
                if await self._check_rate_limit(sender):
                    # Mjerenje vremena obrade za Grafanu
                    with AI_LATENCY.time():
                        await self._handle_business_logic(sender, text)
                    
                    MSG_PROCESSED.labels(status="success").inc()
                else:
                    logger.warning("Rate limit exceeded", sender=sender)
                    MSG_PROCESSED.labels(status="rate_limit").inc()
            
            await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
            await self.redis.xdel(STREAM_INBOUND, msg_id)

        except Exception as e:
            logger.error("Message processing failed", id=msg_id, error=str(e))
            MSG_PROCESSED.labels(status="error").inc()
            await self.queue.store_inbound_dlq(payload, str(e))
            await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
            await self.redis.xdel(STREAM_INBOUND, msg_id)

    async def _handle_business_logic(self, sender: str, text: str):
        """Glavna logika: DB Identifikacija -> Onboarding -> AI."""
        async with AsyncSessionLocal() as session:
            user_service = UserService(session, self.gateway)
            
            user = await user_service.get_active_identity(sender)
            
            if not user:
                await self._handle_onboarding(sender, text, user_service)
                return

            identity_context = (
                f"SYSTEM ENFORCEMENT: You are acting on behalf of the user '{user.display_name}'. "
                f"The Internal API User Identifier is '{user.api_identity}'. "
                f"RULE: For EVERY tool call you generate, you MUST set the parameter 'User' (or 'email') to '{user.api_identity}'."
                f"Do NOT ask the user for their username."
            )
            
            await self.context.add_message(sender, "user", text)
            await self._run_ai_loop(sender, text, identity_context)

    async def _handle_onboarding(self, sender: str, text: str, service: UserService):
        """Logika za registraciju novih korisnika."""
        key = f"onboarding:{sender}"
        state = await self.redis.get(key)
        
        if state == "WAITING_EMAIL":
            if "@" not in text or len(text) < 5:
                await self.queue.enqueue(sender, "Neispravan format e-maila. Molim pokušajte ponovo.")
                return

            name = await service.onboard_user(sender, text)
            
            if name:
                await self.redis.delete(key)
                await self.queue.enqueue(sender, f"✅ Povezano! Pozdrav {name}, vaš račun je potvrđen.")
            else:
                await self.queue.enqueue(sender, f"❌ E-mail '{text}' nije pronađen u sustavu.")
        else:
            welcome_msg = "👋 Dobrodošli u MobilityOne AI! Molim upišite vašu službenu e-mail adresu."
            await self.queue.enqueue(sender, welcome_msg)
            await self.redis.setex(key, 900, "WAITING_EMAIL")

    async def _run_ai_loop(self, sender, text, system_ctx):
        """AI Petlja: Thought -> Action -> Observation."""
        for _ in range(3): 
            history = await self.context.get_history(sender)
            
            search_query = text
            if not search_query:
                for msg in reversed(history):
                    if msg['role'] == 'user':
                        search_query = msg['content']
                        break
            
            tools = await self.registry.find_relevant_tools(search_query or "help")
            
            decision = await analyze_intent(
                history, text, tools, 
                system_instruction=system_ctx
            )
            
            if decision.get("tool"):
                await self.context.add_message(
                    sender, "assistant", None, 
                    tool_calls=decision["raw_tool_calls"]
                )
                
                tool_name = decision["tool"]
                tool_def = self.registry.tools_map.get(tool_name)
                
                if tool_def:
                    logger.info("Executing tool", tool=tool_name, params=sanitize_log_data(decision["parameters"]))
                    result = await self.gateway.execute_tool(tool_def, decision["parameters"])
                else:
                    result = {"error": "Tool not found"}
                
                await self.context.add_message(
                    sender, "tool", 
                    orjson.dumps(result).decode('utf-8'), 
                    tool_call_id=decision["tool_call_id"],
                    name=tool_name
                )
                text = None 
            else:
                resp = decision.get("response_text")
                await self.context.add_message(sender, "assistant", resp)
                await self.queue.enqueue(sender, resp)
                break 

    async def _check_rate_limit(self, sender: str) -> bool:
        key = f"rate:{sender}"
        count = await self.redis.incr(key)
        if count == 1: 
            await self.redis.expire(key, 60)
        return count <= 20

    async def _process_outbound(self):
        """Obrađuje red za slanje poruka (Queue -> WhatsApp)."""
        if not self.running: return
        
        try:
            task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
            if not task: return
            
            payload = orjson.loads(task[1])
            await self._send_infobip(payload)
            
        except Exception as e:
            logger.error("Outbound processing error", error=str(e))
            # Ako slanje ne uspije, vrati u retry queue (ako imamo payload)
            if 'payload' in locals():
                await self.queue.schedule_retry(payload)

    async def _process_retries(self):
        """
        Provjerava ima li poruka koje su spremne za ponovno slanje.
        """
        if not self.running: return
        
        try:
            now = asyncio.get_event_loop().time()
            # Dohvati 1 poruku kojoj je istekao delay
            tasks = await self.redis.zrangebyscore(QUEUE_SCHEDULE, 0, now, start=0, num=1)
            
            if tasks:
                # Atomski ukloni iz ZSET-a i prebaci u red za slanje
                if await self.redis.zrem(QUEUE_SCHEDULE, tasks[0]):
                    data = orjson.loads(tasks[0])
                    logger.info("Retrying message", cid=data.get('cid'), attempt=data.get('attempts'))
                    
                    # Vraćamo u glavni queue
                    await self.queue.enqueue(
                        to=data['to'], 
                        text=data['text'], 
                        correlation_id=data.get('cid'), 
                        attempts=data.get('attempts')
                    )
        except Exception as e:
            logger.error("Retry processing error", error=str(e))

    async def _send_infobip(self, payload):
        """Šalje HTTP zahtjev prema Infobipu."""
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
        
        try:
            # Maskiramo broj u logovima
            logger.info("Šaljem poruku", to="***MASKED***")
            resp = await self.http.post(url, json=body, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to send WhatsApp message", error=str(e))
            raise e # Dižemo grešku da bi _process_outbound znao da treba retry

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Worker shutting down...")
        self.running = False
        await asyncio.sleep(2) 
        
        if self.http: await self.http.aclose()
        if self.gateway: await self.gateway.close()
        if self.redis: await self.redis.aclose()
        logger.info("Shutdown complete.")

# --- Moderni Entry Point ---
async def main():
    worker = WhatsappWorker()
    
    # Postavljanje signala unutar main loopa
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'running', False))
    
    await worker.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass