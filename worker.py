import asyncio
import orjson
import httpx
import signal
import uuid
import socket
import redis.asyncio as redis
import structlog
from contextlib import asynccontextmanager
from config import get_settings
from logger_config import configure_logger  

# [BITNO] Importamo STREAM_INBOUND umjesto stare liste QUEUE_INBOUND
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE, STREAM_INBOUND, QUEUE_DLQ
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway
from services.ai import analyze_intent

# Metrike za monitoring
from prometheus_client import start_http_server, Counter, Histogram

settings = get_settings()
configure_logger()
logger = structlog.get_logger("worker")

# --- METRIKE ---
MSG_PROCESSED = Counter('whatsapp_msg_total', 'Total processed messages', ['status'])
AI_LATENCY = Histogram('ai_processing_seconds', 'Time spent in AI analysis')

# --- SIGURNOST ---
SENSITIVE_LOG_KEYS = {
    'email', 'phone', 'password', 'token', 'authorization', 
    'secret', 'apikey', 'access_token', 'refresh_token', 'pin', 'cvv', 'to'
}

def sanitize_log_data(data: dict) -> dict:
    """Rekurzivno maskira osjetljive ključeve za sigurno logiranje."""
    if not isinstance(data, dict):
        return data
        
    clean_data = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_LOG_KEYS:
            clean_data[k] = "***MASKED***"
            continue
            
        if isinstance(v, dict):
            clean_data[k] = sanitize_log_data(v)
            continue
            
        if isinstance(v, list):
            clean_data[k] = [sanitize_log_data(i) if isinstance(i, dict) else i for i in v]
            continue
            
        if isinstance(v, str) and len(v) > 500:
            clean_data[k] = v[:500] + "...(truncated)"
            continue
            
        clean_data[k] = v
        
    return clean_data

class WhatsappWorker:
    def __init__(self):
        self.worker_id = str(uuid.uuid4())[:8]
        self.hostname = socket.gethostname()
        self.redis = None
        self.http = None
        self.queue = None
        self.context = None
        self.registry = None
        self.gateway = None
        self.running = True

    async def start(self):
        """Inicijalizacija resursa i glavna petlja."""
        logger.info("Inicijalizacija workera...", id=self.worker_id)
        
        # 1. Pokretanje Metrics Servera (port 8001)
        try:
            start_http_server(8001)
            logger.info("Prometheus metrics server running on port 8001")
        except Exception as e:
            logger.warning("Failed to start metrics server", error=str(e))
        
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        self.context = ContextService(self.redis)
        
        # 2. Inicijalizacija Tool Registryja s Auto-Updateom
        self.registry = ToolRegistry(self.redis)
        try:
            await self.registry.load_swagger("swagger.json")
            
            # Provjeravamo postoji li URL za auto-update u configu
            swagger_url = getattr(settings, "SWAGGER_URL", None)
            if swagger_url:
                logger.info("Pokrećem background auto-update alata", url=swagger_url)
                # Pokrećemo kao background task
                asyncio.create_task(self.registry.start_auto_update(swagger_url))
            else:
                logger.info("SWAGGER_URL nije postavljen, auto-update isključen.")

        except Exception as e:
            logger.error("Failed to load tools definition", error=str(e))

        if settings.MOBILITY_API_URL:
             self.gateway = OpenAPIGateway(base_url=settings.MOBILITY_API_URL)
        
        # 3. Inicijalizacija Redis Stream Grupe
        try:
            # mkstream=True kreira stream ako ne postoji. ID="$" znači čitaj samo nove poruke.
            await self.redis.xgroup_create(STREAM_INBOUND, "workers_group", id="$", mkstream=True)
            logger.info("Consumer group 'workers_group' ensured.")
        except redis.ResponseError as e:
            # Ignoriramo grešku ako grupa već postoji ("BUSYGROUP")
            if "BUSYGROUP" not in str(e):
                logger.error("Redis Stream Group Error", error=str(e))
        
        logger.info("Worker spreman.", host=self.hostname, id=self.worker_id)

        while self.running:
            # Heartbeat
            await self.redis.setex("worker:heartbeat", 30, "alive")
            await self.redis.setex(f"worker:heartbeat:{self.hostname}:{self.worker_id}", 30, "alive")
            
            try:
                await asyncio.gather(
                    self._process_outbound(),       # Stari queue za izlaz (odgovori)
                    self._process_retries(),        # Stari queue za retry
                    self._process_inbound_stream(), # [NOVO] Stream za ulazne poruke
                    return_exceptions=True 
                )
                await asyncio.sleep(0.05) 
            except Exception as e:
                logger.error("Kritična greška u petlji", error=str(e))
                await asyncio.sleep(1)
        
        await self.shutdown()

    @asynccontextmanager
    async def _distributed_lock(self, resource_id: str, ttl_ms: int = 10000):
        """Redis Distributed Lock za sprječavanje race-conditiona."""
        lock_key = f"lock:msg:{resource_id}"
        token = str(uuid.uuid4())
        
        if not await self.redis.set(lock_key, token, nx=True, px=ttl_ms):
            yield False
            return

        try:
            yield True
        finally:
            # Lua skripta za sigurno brisanje samo našeg tokena
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await self.redis.eval(script, 1, lock_key, token)

    async def _process_inbound_stream(self):
        """
        Čita poruke iz Redis Streama koristeći Consumer Group.
        """
        if not self.running: return

        try:
            # XREADGROUP: Čitaj kao član grupe 'workers_group'
            # ">" znači: daj mi samo nove poruke koje nitko drugi nije uzeo
            streams = await self.redis.xreadgroup(
                groupname="workers_group", 
                consumername=self.worker_id, 
                streams={STREAM_INBOUND: ">"}, 
                count=1, 
                block=1000 # Čekaj do 1s
            )

            if not streams: return

            for stream_name, messages in streams:
                for msg_id, data in messages:
                    # data je dict (jer smo tako spremili u queue.py)
                    await self._handle_stream_message(msg_id, data)

        except Exception as e:
            logger.error("Stream processing error", error=str(e))

    async def _handle_stream_message(self, msg_id: str, payload: dict):
        """
        Obrada jedne poruke iz streama uz ACK logiku.
        """
        message_id = payload.get('message_id', 'unknown')
        
        try:
            # Zaključaj obradu poruke da izbjegnemo duplikate
            async with self._distributed_lock(message_id) as locked:
                if locked:
                    with AI_LATENCY.time():
                        await self._handle_message_logic(payload)
                    
                    # [CRITICAL] Potvrdi da je poruka uspješno obrađena
                    await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
                    # Opcionalno: Brišemo iz streama radi uštede memorije
                    await self.redis.xdel(STREAM_INBOUND, msg_id)
                    
                    MSG_PROCESSED.labels(status="success").inc()
                else:
                    logger.warning("Poruka zaključana na drugom workeru (duplicate?)", msg_id=message_id)
                    # Ako je zaključana, vjerojatno je duplicate event. Sigurno je ACK-ati.
                    await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
        
        except Exception as e:
            logger.error("Greška obrade stream poruke", error=str(e), payload=sanitize_log_data(payload))
            MSG_PROCESSED.labels(status="error").inc()
            # NE radimo ACK. Poruka ostaje u 'Pending Entry List' (PEL) za kasniji retry/inspekciju.

    async def _handle_message_logic(self, payload: dict):
        """Poslovna logika: User -> AI -> [Tool -> AI] -> Response"""
        sender = payload['sender']
        user_text = payload['text']
        logger.info("Početak obrade", sender=sender)

        if not self.registry.is_ready:
            await self.queue.enqueue(sender, "Sustav se ažurira (Alati nedostupni). Pokušajte kasnije.")
            return

        await self.context.add_message(sender, "user", user_text)

        # Maksimalno 3 koraka (AI -> Tool -> AI -> Tool -> AI -> Response)
        for step in range(3):
            current_input = user_text if step == 0 else None
            should_continue = await self._execute_ai_step(sender, current_input)
            if not should_continue:
                return

        await self.queue.enqueue(sender, "Zahtjev je previše složen. Molimo pojednostavite.")

    async def _execute_ai_step(self, sender: str, user_text: str | None) -> bool:
        """Jedan korak odlučivanja AI modela."""
        history = await self.context.get_history(sender)
        
        search_query = user_text
        if not search_query:
            # Ako smo u tool loopu, tražimo zadnji user input za RAG
            for msg in reversed(history):
                if msg['role'] == 'user':
                    search_query = msg.get('content')
                    break
        
        tools = await self.registry.find_relevant_tools(search_query or "help")
        decision = await analyze_intent(history, user_text, tools=tools)

        if decision.get("tool"):
            raw_calls = decision.get("raw_tool_calls", [])
            tool_calls_dict = [t.model_dump() for t in raw_calls] if raw_calls else []

            await self.context.add_message(
                sender, 
                "assistant", 
                content=None, 
                tool_calls=tool_calls_dict
            )
            
            await self._execute_tool_call(sender, decision)
            return True # Nastavi petlju

        response = self._ensure_text_response(decision.get("response_text"))
        if response:
            await self.context.add_message(sender, "assistant", response)
            await self.queue.enqueue(sender, response)
        
        return False # Završi petlju

    async def _execute_tool_call(self, sender: str, decision: dict):
        tool_name = decision["tool"]
        params = decision["parameters"]
        call_id = decision.get("tool_call_id")
        
        logger.info("AI poziva alat", tool=tool_name, params=sanitize_log_data(params))
        
        tool_def = self.registry.tools_map.get(tool_name)
        if not tool_def or not self.gateway:
            err = orjson.dumps({"error": "Tool not found"}).decode('utf-8')
            await self.context.add_message(sender, "tool", err, tool_call_id=call_id, name=tool_name)
            return

        api_result = await self.gateway.execute_tool(tool_def, params)
        logger.info("Rezultat alata", tool=tool_name, result=sanitize_log_data(api_result))

        tool_msg = orjson.dumps(api_result).decode('utf-8')
        
        await self.context.add_message(
            sender, 
            "tool", 
            tool_msg, 
            tool_call_id=call_id, 
            name=tool_name
        )

    def _ensure_text_response(self, raw_response: str) -> str:
        """Validacija da AI nije vratio JSON umjesto teksta."""
        if not raw_response: return "Nisam razumio."
        try:
            if isinstance(orjson.loads(raw_response), dict):
                return "Akcija izvršena, ali nisam uspio generirati sažetak."
        except:
            pass
        return raw_response

    async def _process_outbound(self):
        """Obrada izlaznih poruka (List/Queue)."""
        if not self.running: return
        task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
        if not task: return
        try:
            payload = orjson.loads(task[1])
            await self._send_infobip(payload)
        except Exception as e:
            logger.warning("Outbound fail", error=str(e))
            if 'payload' in locals():
                await self.queue.schedule_retry(payload)

    async def _process_retries(self):
        """Obrada poruka koje čekaju na retry."""
        if not self.running: return
        now = asyncio.get_event_loop().time()
        tasks = await self.redis.zrangebyscore(QUEUE_SCHEDULE, 0, now, start=0, num=1)
        if tasks and await self.redis.zrem(QUEUE_SCHEDULE, tasks[0]):
            data = orjson.loads(tasks[0])
            await self.queue.enqueue(data['to'], data['text'], data.get('cid'), data['attempts'])

    async def _send_infobip(self, payload):
        url = f"https://{settings.INFOBIP_BASE_URL}/whatsapp/1/message/text"
        headers = {"Authorization": f"App {settings.INFOBIP_API_KEY}", "Content-Type": "application/json"}
        body = {"from": settings.INFOBIP_SENDER_NUMBER, "to": payload['to'], "content": {"text": payload['text']}}
        
        logger.info("Šaljem poruku", to="***MASKED***")
        resp = await self.http.post(url, json=body, headers=headers)
        resp.raise_for_status()

    async def shutdown(self):
        logger.info("Gašenje workera...")
        if self.http: await self.http.aclose()
        if self.gateway: await self.gateway.close()
        if self.redis: await self.redis.aclose()
        logger.info("Worker zaustavljen.")

async def main():
    worker = WhatsappWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'running', False))
    await worker.start()

if __name__ == "__main__":
    asyncio.run(main())