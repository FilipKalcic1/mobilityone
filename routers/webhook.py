import uuid
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ConfigDict
from typing import List

# Importamo samo QueueService jer webhook samo "baca" posao u red
from services.queue import QueueService
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# --- Pydantic Modeli (Struktura podataka koju šalje Infobip) ---
class InfobipMessage(BaseModel):
    text: str
    sender: str = Field(..., alias="from")
    messageId: str
    model_config = ConfigDict(extra='ignore')

class InfobipWebhookPayload(BaseModel):
    results: List[InfobipMessage]
    model_config = ConfigDict(extra='ignore')

# --- Helper za dohvat servisa iz app state-a (Dependency Injection) ---
def get_queue(request: Request): 
    return request.app.state.queue

# 👇 --- NOVE FUNKCIJE KOJE SU NEDOSTAJALE --- 👇
def get_context(request: Request):
    return request.app.state.context

def get_registry(request: Request):
    return request.app.state.tool_registry

def get_gateway(request: Request):
    return request.app.state.api_gateway
# 👆 ------------------------------------------ 👆

# --- Glavni Endpoint ---
@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        Depends(validate_infobip_signature),
        Depends(RateLimiter(times=60, minutes=1))
    ]
)
async def whatsapp_entrypoint(
    payload: InfobipWebhookPayload, 
    queue: QueueService = Depends(get_queue)
):
    """
    Ulazna točka. Ne radi AI analizu, samo sprema poruku u Redis (Inbound Queue).
    """
    request_id = str(uuid.uuid4())
    
    if not payload.results:
        return {"status": "ignored", "reason": "empty_results"}

    message = payload.results[0]
    
    if not message.text:
        return {"status": "ignored", "reason": "no_text"}

    # Spremanje u Redis za Workera
    await queue.enqueue_inbound(
        sender=message.sender, 
        text=message.text, 
        message_id=message.messageId
    )
    
    logger.info("Message queued for processing", sender=message.sender, req_id=request_id)

    return {"status": "queued", "msg_id": message.messageId}