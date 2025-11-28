import hmac
import hashlib
import structlog
from fastapi import Request, HTTPException, Header
from config import get_settings

logger = structlog.get_logger("security")

async def validate_infobip_signature(request: Request, x_hub_signature: str = Header(None)):
    """
    Validira integritet poruke koristeći HMAC-SHA256 potpis.
    """
    settings = get_settings()
    
    # U Developmentu dopuštamo testiranje bez potpisa (ali logiramo warning)
    if settings.APP_ENV != "production":
        if not x_hub_signature:
            logger.warning("SECURITY: Missing signature allowed in NON-PROD env.")
            return
    
    if not x_hub_signature:
        logger.error("Security Block: Missing Signature")
        raise HTTPException(status_code=403, detail="Signature required")

    # Parsiranje potpisa (format: "sha256=xxxx...")
    try:
        algo, received_sig = x_hub_signature.split('=')
        if algo != 'sha256': raise ValueError
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid signature format")

    # Izračun očekivanog potpisa
    try:
        body = await request.body()
        expected_sig = hmac.new(
            settings.INFOBIP_SECRET_KEY.encode(), 
            body, 
            hashlib.sha256
        ).hexdigest()
    except Exception as e:
        logger.error("Signature calculation failed", error=str(e))
        raise HTTPException(status_code=500, detail="Internal Security Error")

    # Sigurna usporedba (Timing Attack safe)
    if not hmac.compare_digest(expected_sig, received_sig):
        logger.error("Security Block: Invalid Signature", expected="***", received="***")
        raise HTTPException(status_code=403, detail="Invalid signature")