import uuid
import structlog
from fastapi import APIRouter, Depends, Request, status
from services.queue import QueueService
from services.cache import CacheService
from services.ai import analyze_intent
from services.context import ContextService
from tools import TOOLS_MAP
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# --- DEPENDENCY INJECTION POMOĆNE FUNKCIJE ---
def get_queue(request: Request) -> QueueService:
    return request.app.state.queue

def get_cache(request: Request) -> CacheService:
    return request.app.state.cache

def get_context(request: Request) -> ContextService:
    return request.app.state.context

# --- HEALTH CHECK (NOVO) ---
# Ovo koriste Kubernetes ili Docker da vide je li servis živ
@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "whatsapp-webhook"}


@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        Depends(validate_infobip_signature),
        Depends(RateLimiter(times=60, minutes=1))
    ]
)

async def whatsapp_entrypoint(
    request: Request,
    queue: QueueService = Depends(get_queue),
    cache: CacheService = Depends(get_cache),
    context: ContextService = Depends(get_context)
):
    # 1. Generiranje Correlation ID-a (Traceability)
    request_id = str(uuid.uuid4())
    log = logger.bind(req_id=request_id)

    # 2. Parsiranje
    try:
        data = await request.json()
        if not data.get('results'):
            return {"status": "ignored", "reason": "empty_results"}
            
        message_data = data['results'][0] ### 
        user_text = message_data.get('text')
        sender = message_data.get('from')
        
        # Obogaćujemo log s pošiljateljem
        log = log.bind(sender=sender)

    except Exception as e:
        log.error("Payload processing failed", error=str(e))
        return {"status": "error", "detail": "bad_payload"}

    if not user_text or not sender:
        return {"status": "ignored"}

    # 3. Kontekst i AI
    try:
        await context.add_message(sender, "user", user_text)
        history = await context.get_history(sender)
        
        # AI analiza
        ai_decision = await analyze_intent(history[:-1], user_text)
        tool_name = ai_decision.get("tool", "fallback")
        params = ai_decision.get("parameters", {})
        
        log.info("Intent detected", intent=tool_name, confidence=ai_decision.get("confidence"))

        # 4. Izvršavanje Alata (OVDJE VRAĆAMO PROVJERU)
        handler = TOOLS_MAP.get(tool_name, TOOLS_MAP["fallback"])
        
        try:
            response_text = await handler(params, cache)
        except Exception as tool_error:
            # Ako alat (baza) pukne, logiramo ali i obavještavamo korisnika
            log.error("Tool execution failed", error=str(tool_error))
            response_text = "Došlo je do greške u sustavu. Molim pokušajte kasnije."

        # 5. Slanje odgovora u Red (s ID-om zahtjeva!)
        await context.add_message(sender, "assistant", response_text)
        await queue.enqueue(sender, response_text, correlation_id=request_id)

        return {"status": "queued", "req_id": request_id}

    except Exception as e:
        # Ovo hvata samo kritične greške (npr. pukao Redis ili AI servis)
        log.error("Critical system error", error=str(e))
        return {"status": "error"}