import uuid
import json
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ConfigDict
from typing import List

from services.queue import QueueService
from services.cache import CacheService
from services.ai import analyze_intent
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# Pydantic modeli
class InfobipMessage(BaseModel):
    text: str
    sender: str = Field(..., alias="from")
    messageId: str
    model_config = ConfigDict(extra='ignore')

class InfobipWebhookPayload(BaseModel):
    results: List[InfobipMessage]
    model_config = ConfigDict(extra='ignore')

# Dependency Helperi
def get_queue(request: Request): return request.app.state.queue
def get_cache(request: Request): return request.app.state.cache
def get_context(request: Request): return request.app.state.context
def get_registry(request: Request): return request.app.state.tool_registry
def get_gateway(request: Request): return request.app.state.api_gateway

@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        Depends(validate_infobip_signature),
        Depends(RateLimiter(times=60, minutes=1))
    ]
)
async def whatsapp_entrypoint(
    payload: InfobipWebhookPayload, 
    queue: QueueService = Depends(get_queue),
    cache: CacheService = Depends(get_cache),
    context: ContextService = Depends(get_context),
    registry: ToolRegistry = Depends(get_registry),
    gateway: OpenAPIGateway = Depends(get_gateway)
):
    request_id = str(uuid.uuid4())
    log = logger.bind(req_id=request_id)

    if not payload.results:
        return {"status": "ignored", "reason": "empty_results"}

    message = payload.results[0]
    user_text = message.text
    sender = message.sender
    log = log.bind(sender=sender)

    if not user_text:
        return {"status": "ignored", "reason": "no_text"}

    try:
        # 1. Spremi poruku korisnika u povijest
        await context.add_message(sender, "user", user_text)
        
        # 2. ŠEF SALE: Pronađi top 5 relevantnih alata (Semantic Search)
        relevant_tools = await registry.find_relevant_tools(user_text, top_k=5)
        log.info("Relevant tools found", count=len(relevant_tools))

        # 3. KONOBAR (AI): Donesi odluku s filtriranim alatima
        history = await context.get_history(sender)
        # Šaljemo historiju bez zadnje poruke jer smo je upravo dodali u context, 
        # ali 'analyze_intent' je dodaje opet. (Mala optimizacija: context servis bi mogao
        # samo vraćati listu, a ne spremati odmah, ali ovako je sigurnije za persistenciju).
        # Koristimo history[:-1] da ne duplamo zadnju poruku ako 'analyze_intent' to radi.
        # Zapravo, tvoj 'analyze_intent' ručno dodaje 'current_text', pa mu dajemo history BEZ zadnje poruke.
        ai_decision = await analyze_intent(history[:-1], user_text, tools=relevant_tools)
        
        final_response_text = ""

        # 4. IZVRŠITELJ (Bridge): Ako je AI odabrao alat
        if ai_decision.get("tool"):
            tool_name = ai_decision["tool"]
            params = ai_decision["parameters"]
            
            log.info("Executing tool", tool=tool_name)
            
            # Dohvati definiciju alata iz registra
            tool_def_full = registry.tools_map.get(tool_name)
            
            if tool_def_full:
                # Zovi pravi API
                api_result = await gateway.execute_tool(tool_def_full, params)
                
                # Formatiraj odgovor (Ovdje bi idealno išao još jedan AI pass, 
                # ali za sada vraćamo JSON string da uštedimo tokene i latenciju)
                final_response_text = f"✅ *Status:* Uspjeh\n📦 *Podaci:* {json.dumps(api_result, ensure_ascii=False)}"
            else:
                final_response_text = "⚠️ Greška: AI je pokušao koristiti nepostojeći alat."
        else:
            # Ako nije alat, samo vrati tekst koji je AI generirao
            final_response_text = ai_decision.get("response_text", "Nisam razumio upit.")

        # 5. Pošalji odgovor korisniku
        await context.add_message(sender, "assistant", final_response_text)
        await queue.enqueue(sender, final_response_text, correlation_id=request_id)

        return {"status": "queued", "req_id": request_id}

    except Exception as e:
        log.error("Webhook processing failed", error=str(e))
        return {"status": "error"}