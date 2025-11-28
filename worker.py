# worker.py
import asyncio
import uuid
import signal
import redis.asyncio as redis
import httpx
import structlog
import orjson

from config import get_settings
from logger_config import configure_logger
from database import AsyncSessionLocal
from services.queue import QueueService, STREAM_INBOUND, QUEUE_OUTBOUND
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway
from services.user_service import UserService
from services.ai import analyze_intent

settings = get_settings()
configure_logger()
logger = structlog.get_logger("worker")

class WhatsappWorker:
    def __init__(self):
        self.worker_id = str(uuid.uuid4())[:8]
        self.running = True
        
        # Resursi se inicijaliziraju u start() metodi
        self.redis = None
        self.gateway = None
        self.http = None
        self.queue = None
        self.context = None
        self.registry = None

    async def start(self):
        """Inicijalizacija infrastrukture i pokretanje glavne petlje."""
        logger.info("Worker starting", id=self.worker_id)
        
        # 1. Povezivanje na infrastrukturu
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=15.0)
        self.queue = QueueService(self.redis)
        self.context = ContextService(self.redis)
        
        # 2. API Gateway (Globalni Service Account)
        if settings.MOBILITY_API_URL:
            self.gateway = OpenAPIGateway(base_url=settings.MOBILITY_API_URL)
        else:
            logger.warning("MOBILITY_API_URL not set. AI tools will fail.")

        # 3. Tool Registry (Učitavanje Swaggera)
        self.registry = ToolRegistry(self.redis)
        swagger_src = settings.SWAGGER_URL or "swagger.json"
        await self.registry.load_swagger(swagger_src)
        
        # Pokreni auto-update ako je URL (Hot-Reload alata)
        if swagger_src.startswith("http"):
            asyncio.create_task(self.registry.start_auto_update(swagger_src))

        # 4. Osiguraj Redis Consumer Group
        try:
            await self.redis.xgroup_create(STREAM_INBOUND, "workers_group", id="$", mkstream=True)
        except redis.ResponseError:
            pass # Grupa već postoji, ignoriraj

        logger.info("Worker ready. Processing loop started.")
        
        # 5. Glavna Petlja
        while self.running:
            try:
                # Paralelno obrađuj ulazne (Stream) i izlazne (Queue) poruke
                await asyncio.gather(
                    self._process_inbound_batch(),
                    self._process_outbound(),
                    return_exceptions=True
                )
                # Kratka pauza da ne vrtimo CPU na 100% ako je prazno
                await asyncio.sleep(0.01)
                
            except Exception as e:
                logger.error("Critical Main Loop Error", error=str(e))
                await asyncio.sleep(1)

        await self.shutdown()

    async def _process_inbound_batch(self):
        """
        High-Performance: Čita do 10 poruka odjednom i obrađuje ih paralelno.
        """
        if not self.running: return

        try:
            # XREADGROUP: Čitaj kao član grupe
            streams = await self.redis.xreadgroup(
                groupname="workers_group",
                consumername=self.worker_id,
                streams={STREAM_INBOUND: ">"}, # ">" = samo nove poruke
                count=10, # BATCH SIZE: 10 poruka odjednom
                block=2000 # Čekaj do 2s
            )
            
            if not streams: return

            # Priprema taskova za paralelno izvršavanje
            tasks = []
            for _, messages in streams:
                for msg_id, data in messages:
                    tasks.append(self._process_single_message_transaction(msg_id, data))
            
            # Scatter-Gather: Izvrši sve taskove istovremeno
            if tasks:
                await asyncio.gather(*tasks)

        except Exception as e:
            logger.error("Stream read error", error=str(e))

    async def _process_single_message_transaction(self, msg_id: str, payload: dict):
        """
        Obrađuje jednu poruku unutar try-except bloka i šalje ACK Redisu.
        Uključuje DLQ logiku za otrovne poruke.
        """
        try:
            sender = payload.get('sender')
            text = payload.get('text', '').strip()
            
            if sender and text:
                # Rate Limiting (Zaštita od spama - 20 msg/min)
                if await self._check_rate_limit(sender):
                    await self._handle_business_logic(sender, text)
                else:
                    logger.warning("Rate limit exceeded", sender=sender)
            
            # ACK: Potvrđujemo da je poruka obrađena
            await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
            
            # Housekeeping: Brišemo iz streama radi uštede memorije
            await self.redis.xdel(STREAM_INBOUND, msg_id)

        except Exception as e:
            logger.error("Message processing failed", id=msg_id, error=str(e))
            
            # [ROBUSTNOST] Spremi otrovnu poruku u DLQ za kasniju analizu
            await self.queue.store_inbound_dlq(payload, str(e))
            
            # Šaljemo ACK da ne zapnemo u beskonačnoj petlji s istom porukom
            await self.redis.xack(STREAM_INBOUND, "workers_group", msg_id)
            await self.redis.xdel(STREAM_INBOUND, msg_id)

    async def _handle_business_logic(self, sender: str, text: str):
        """
        Glavna logika: DB Identifikacija -> Onboarding -> AI s Kontekstom.
        """
        
        # Svaka poruka dobiva svoju DB sesiju (Connection Pooling)
        async with AsyncSessionLocal() as session:
            user_service = UserService(session, self.gateway)
            
            # 1. Identifikacija (Tko je ovo?)
            user = await user_service.get_active_identity(sender)
            
            # 2. Nepoznat korisnik -> Onboarding Flow
            if not user:
                await self._handle_onboarding(sender, text, user_service)
                return

            # 3. Poznat korisnik -> Authenticated AI Flow
            # [CRITICAL] Kreiramo sistemsku instrukciju koja "tjera" AI da koristi pravi User ID
            identity_context = (
                f"SYSTEM ENFORCEMENT: You are acting on behalf of the user '{user.display_name}'. "
                f"The Internal API User Identifier is '{user.api_identity}'. "
                f"RULE: For EVERY tool call you generate, you MUST set the parameter 'User' (or 'email') to '{user.api_identity}'."
                f"Do NOT ask the user for their username."
            )
            
            # Dodajemo userov input u povijest
            await self.context.add_message(sender, "user", text)
            
            # Pokrećemo AI petlju s injektiranim identitetom
            await self._run_ai_loop(sender, text, identity_context)

    async def _handle_onboarding(self, sender: str, text: str, service: UserService):
        """Logika za registraciju novih korisnika (State Machine)."""
        key = f"onboarding:{sender}"
        state = await self.redis.get(key)
        
        if state == "WAITING_EMAIL":
            # Brza provjera formata
            if "@" not in text or len(text) < 5:
                await self.queue.enqueue(sender, "Neispravan format e-maila. Molim pokušajte ponovo.")
                return

            # Validacija na API-ju + Spremanje u bazu
            name = await service.onboard_user(sender, text)
            
            if name:
                # Uspjeh! Brišemo stanje i pozdravljamo
                await self.redis.delete(key)
                await self.queue.enqueue(sender, f"✅ Povezano! Pozdrav {name}, vaš račun je potvrđen. Kako mogu pomoći?")
            else:
                # Neuspjeh (ne postoji na API-ju)
                await self.queue.enqueue(sender, f"❌ E-mail '{text}' nije pronađen u sustavu. Molim provjerite točnost.")
        else:
            # Prvi kontakt ikad
            welcome_msg = (
                "👋 Dobrodošli u MobilityOne AI!\n\n"
                "Nisam prepoznao vaš broj. Za početak, molim upišite vašu službenu **e-mail adresu** radi identifikacije."
            )
            await self.queue.enqueue(sender, welcome_msg)
            await self.redis.setex(key, 900, "WAITING_EMAIL") # 15 min timeout

    async def _run_ai_loop(self, sender, text, system_ctx):
        """AI Petlja: Thought -> Action -> Observation."""
        
        for _ in range(3): # Max 3 koraka
            history = await self.context.get_history(sender)
            
            # RAG Logic: Ako je tekst None (korak 2+), uzmi zadnji user input
            search_query = text
            if not search_query:
                for msg in reversed(history):
                    if msg['role'] == 'user':
                        search_query = msg['content']
                        break
            
            tools = await self.registry.find_relevant_tools(search_query or "help")
            
            # [10/10] Poziv AI-u s injektiranim identitetom
            decision = await analyze_intent(
                history, text, tools, 
                system_instruction=system_ctx
            )
            
            # Slučaj A: AI želi zvati alat
            if decision.get("tool"):
                # 1. Spremi "Thought" (AI želi zvati alat)
                await self.context.add_message(
                    sender, "assistant", None, 
                    tool_calls=decision["raw_tool_calls"]
                )
                
                # 2. Izvrši "Action" (Gateway poziv)
                tool_name = decision["tool"]
                tool_def = self.registry.tools_map.get(tool_name)
                
                if tool_def:
                    result = await self.gateway.execute_tool(tool_def, decision["parameters"])
                else:
                    result = {"error": "Tool not found in registry"}
                
                # 3. Spremi "Observation" (Rezultat alata)
                await self.context.add_message(
                    sender, "tool", 
                    orjson.dumps(result).decode('utf-8'), 
                    tool_call_id=decision["tool_call_id"],
                    name=tool_name
                )
                
                text = None # Brišemo input za iduću iteraciju
                # Petlja se nastavlja -> AI analizira rezultat

            # Slučaj B: AI ima finalni odgovor
            else:
                resp = decision.get("response_text")
                await self.context.add_message(sender, "assistant", resp)
                await self.queue.enqueue(sender, resp)
                break 

    async def _check_rate_limit(self, sender: str) -> bool:
        """Vraća False ako je korisnik prešao limit (20 msg/min)."""
        key = f"rate:{sender}"
        count = await self.redis.incr(key)
        if count == 1: 
            await self.redis.expire(key, 60)
        return count <= 20

    async def _process_outbound(self):
        """Obrađuje red za slanje poruka (Queue -> WhatsApp)."""
        if not self.running: return
        
        try:
            # Koristimo blpop s timeoutom da ne blokiramo petlju vječno
            task = await self.redis.blpop(QUEUE_OUTBOUND, timeout=1)
            if not task: return
            
            payload = orjson.loads(task[1])
            await self._send_infobip(payload)
            
        except Exception as e:
            logger.error("Outbound processing error", error=str(e))
            # U produkciji: schedule_retry(payload)

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
            resp = await self.http.post(url, json=body, headers=headers)
            resp.raise_for_status()
            logger.info("Message sent", to="***MASKED***")
        except Exception as e:
            logger.error("Failed to send WhatsApp message", error=str(e))

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Worker shutting down...")
        self.running = False
        
        # Dajemo vremena da se aktivni taskovi završe
        await asyncio.sleep(2) 
        
        if self.http: await self.http.aclose()
        if self.gateway: await self.gateway.close()
        if self.redis: await self.redis.aclose()
        logger.info("Shutdown complete.")

# Entry point
if __name__ == "__main__":
    worker = WhatsappWorker()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'running', False))
    loop.run_until_complete(worker.start())