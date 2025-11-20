from fastapi import APIRouter, Depends, Request, status
from services.queue import QueueService
from services.cache import CacheService
from services.ai import analyze_intent
from services.context import ContextService
from tools import TOOLS_MAP
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter
import structlog

router = APIRouter()
logger = structlog.get_logger("webhook")

@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        Depends(validate_infobip_signature),
        Depends(RateLimiter(times=50, minutes=1))
    ]
) 
async def whatsapp_handler(
    request: Request,
    queue: QueueService = Depends(lambda: request.app.state.queue),
    cache: CacheService = Depends(lambda: request.app.state.cache),
    context: ContextService = Depends(lambda: request.app.state.context)
):
    try:
        body = await request.json()
        if not body.get('results'):
            return {"status": "ok", "detail": "empty"}
        
        msg = body['results'][0]
        user_text = msg.get('text', '')
        sender = msg.get('from')
        
        if not user_text or not sender:
            return {"status": "ignored"}
            
    except Exception as e:
        logger.error("Payload error", error=str(e))
        return {"status": "error"}, status.HTTP_400_BAD_REQUEST

    # 1. Spremi user poruku
    await context.add_message(sender, role="user", text=user_text)

    # 2. Dohvati povijest za kontekst (sve osim zadnje koju smo upravo dodali za prompt)
    # Zapravo, možemo poslati sve, AI.py se bavi formatiranjem
    history = await context.get_history(sender)
    # history sadrži i zadnju poruku, to je OK, ali ai.py dodaje current_text posebno.
    # Da ne dupliciramo, uzmimo history[:-1] kao "prošlost"
    history_context = history[:-1] if history else []

    # 3. AI Analiza
    ai_res = await analyze_intent(history_context, user_text)
    intent = ai_res.get("tool")
    params = ai_res.get("parameters", {})

    logger.info("AI Decision", intent=intent, params=params)

    # 4. Izvrši alat
    tool_func = TOOLS_MAP.get(intent, TOOLS_MAP['fallback'])
    
    try:
        # Pazi: tools.py mora primati (params, cache)
        response_text = await tool_func(params, cache)
    except Exception as e:
        logger.error("Tool failed", error=str(e))
        response_text = "Greška u obradi zahtjeva."

    # 5. Spremi odgovor
    await context.add_message(sender, role="assistant", text=response_text)
    
    # 6. Pošalji
    await queue.enqueue(sender, response_text)

    return {"status": "queued"}