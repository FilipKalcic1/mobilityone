import hmac
import hashlib
import structlog
from fastapi import Request, HTTPException, Header
from config import get_settings

logger = structlog.get_logger("security")

async def validate_infobip_signature(request: Request, x_hub_signature: str = Header(None)):
    """
    Validira integritet poruke koristeći HMAC-SHA256.
    """
    settings = get_settings()
    
    # 1. Razvojni prečac (Dev Bypass) - samo uz upozorenje
    if settings.APP_ENV != "production" and not x_hub_signature:
        logger.warning("SECURITY WARNING: Zahtjev bez potpisa propušten (Non-Prod env).")
        return

    # 2. Provjera postojanja zaglavlja
    if not x_hub_signature:
        logger.warning("Odbijen zahtjev: Nedostaje potpis.")
        raise HTTPException(status_code=403, detail="Signature missing")

    # 3. Validacija formata
    try:
        parts = x_hub_signature.split('=')
        if len(parts) != 2 or parts[0] != 'sha256':
            raise ValueError
        received_sig = parts[1]
    except ValueError:
        logger.warning("Odbijen zahtjev: Neispravan format potpisa.")
        raise HTTPException(status_code=403, detail="Invalid signature format")

    # 4. Izračun i usporedba
    try:
        body = await request.body()
        expected_sig = hmac.new(
            settings.INFOBIP_SECRET_KEY.encode(), 
            body, 
            hashlib.sha256
        ).hexdigest()
    except Exception as e:
        logger.error("Interna greška pri validaciji potpisa", error=str(e))
        raise HTTPException(status_code=500, detail="Security check failed")

    if not hmac.compare_digest(expected_sig, received_sig):
        # POPRAVLJENO: Ne logiramo 'expected_sig' da ne curi informacija napadaču!
        # Logiramo samo da je došlo do mismatch-a.
        logger.error("Sigurnosna povreda: Potpis ne odgovara.", received_partial=received_sig[:10] + "...")
        raise HTTPException(status_code=403, detail="Invalid signature")