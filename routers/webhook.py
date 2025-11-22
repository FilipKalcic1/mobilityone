import uuid
import structlog
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, ConfigDict # Import ConfigDict
from typing import List

from services.queue import QueueService
from services.cache import CacheService
from services.ai import analyze_intent
from services.context import ContextService
from tools import TOOLS_MAP
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# --- PYDANTIC MODELI ---
class InfobipMessage(BaseModel):
    text: str
    sender: str = Field(..., alias="from")
    messageId: str
    

    model_config = ConfigDict(extra='ignore')

class InfobipWebhookPayload(BaseModel):
    results: List[InfobipMessage]
    

    model_config = ConfigDict(extra='ignore')


def get_queue(request: Request): return request.app.state.queue
def get_cache(request: Request): return request.app.state.cache
def get_context(request: Request): return request.app.state.context

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
    context: ContextService = Depends(get_context)
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
        await context.add_message(sender, "user", user_text)
        history = await context.get_history(sender)
        
        ai_decision = await analyze_intent(history[:-1], user_text)
        tool_name = ai_decision.get("tool", "fallback")
        params = ai_decision.get("parameters", {})
        
        log.info("Intent detected", intent=tool_name)

        handler = TOOLS_MAP.get(tool_name, TOOLS_MAP["fallback"])
        
        try:
            response_text = await handler(params, cache)
        except Exception as tool_error:
            log.error("Tool execution failed", error=str(tool_error))
            response_text = "Došlo je do greške u sustavu. Molim pokušajte kasnije."

        await context.add_message(sender, "assistant", response_text)
        await queue.enqueue(sender, response_text, correlation_id=request_id)

        return {"status": "queued", "req_id": request_id}

    except Exception as e:
        log.error("Internal processing error", error=str(e))
        return {"status": "error"}