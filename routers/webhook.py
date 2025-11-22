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

# --- Helper za dohvat servisa iz app state-a ---
def get_queue(request: Request): 
    return request.app.state.queue

# --- Glavni Endpoint ---
@router.post(
    "/webhook/whatsapp", 
    dependencies=[
        # 1. Sigurnost: Provjeri je li ovo stvarno Infobip
        Depends(validate_infobip_signature),
        # 2. Zaštita: Ne dopusti više od 60 zahtjeva u minuti po IP-u
        Depends(RateLimiter(times=60, minutes=1))
    ]
)
async def whatsapp_entrypoint(
    payload: InfobipWebhookPayload, 
    queue: QueueService = Depends(get_queue)
):
    """
    Ovo je ulazna točka. Mora biti ultra-brza (< 200ms).
    Ne radi nikakvu AI analizu, samo sprema poruku u red za čekanje.
    """
    
    # Generiramo ID zahtjeva samo za naše logove (traceability)
    request_id = str(uuid.uuid4())
    
    # Validacija: Je li payload prazan?
    if not payload.results:
        return {"status": "ignored", "reason": "empty_results"}

    message = payload.results[0]
    
    # Validacija: Ima li poruka tekst? (Možda je slika ili lokacija, što zasad ignoriramo)
    if not message.text:
        return {"status": "ignored", "reason": "no_text"}

    # --- KLJUČNI TRENUTAK ---
    # Ovdje ne zovemo AI. Samo kažemo Redisu: "Evo nova poruka, neka Worker to riješi kad stigne."
    # Ovo traje 2 milisekunde.
    await queue.enqueue_inbound(
        sender=message.sender, 
        text=message.text, 
        message_id=message.messageId
    )
    
    logger.info("Message queued for processing", sender=message.sender, req_id=request_id)

    # Odmah vraćamo 200 OK Infobipu da ne misli da smo timeoutali.
    return {"status": "queued", "msg_id": message.messageId}