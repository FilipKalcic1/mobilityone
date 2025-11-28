import uuid
import structlog
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import List

from services.queue import QueueService
from security import validate_infobip_signature
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# --- Pydantic Modeli za Infobip ---
class InfobipMessage(BaseModel):
    text: str
    sender: str = Field(..., alias="from") # Mapira "from" -> "sender"
    messageId: str
    model_config = ConfigDict(extra='ignore')

class InfobipPayload(BaseModel):
    results: List[InfobipMessage]
    model_config = ConfigDict(extra='ignore')

# Dependency Injection
def get_queue(request: Request) -> QueueService: 
    return request.app.state.queue

@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        Depends(validate_infobip_signature), # 1. Sigurnost
        Depends(RateLimiter(times=100, minutes=1)) # 2. Anti-DDoS
    ]
)
async def whatsapp_webhook(
    payload: InfobipPayload, 
    queue: QueueService = Depends(get_queue)
):
    """
    Prihvaća poruke od Infobipa i šalje ih u Redis Stream za obradu.
    """
    request_id = str(uuid.uuid4())
    
    if not payload.results:
        return {"status": "ignored", "reason": "empty"}

    msg = payload.results[0]
    
    if not msg.text:
        logger.info("Non-text message ignored", id=msg.messageId)
        return {"status": "ignored", "reason": "no_text"}

    # 3. Brzo spremanje u Stream (Async)
    stream_id = await queue.enqueue_inbound(
        sender=msg.sender, 
        text=msg.text, 
        message_id=msg.messageId
    )
    
    logger.info("Message queued", req_id=request_id, stream_id=stream_id)
    return {"status": "queued", "id": msg.messageId}