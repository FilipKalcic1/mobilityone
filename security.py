import hmac
import hashlib
import logging
from fastapi import Request, HTTPException, Header, status
from config import get_settings
import structlog

logger = structlog.get_logger("security")

async def validate_infobip_signature(request: Request, x_hub_signature: str = Header(None)):
    """
    Middleware dependency koji validira autentičnost zahtjeva.
    Računa HMAC-SHA256 tijela zahtjeva i uspoređuje ga s Infobip zaglavljem.
    """
    settings = get_settings()
    
    # U developmentu dopuštamo prolaz bez potpisa radi lakšeg testiranja
    if settings.APP_ENV != "production":
        if not x_hub_signature:
            logger.warning("DEV MODE: Skipping signature check (Header missing).")
            return

    if not x_hub_signature:
        logger.error("Odbijen zahtjev: Nedostaje X-Hub-Signature header.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing integrity signature")

    # Infobip šalje format: "sha256=vaš_hash_ovdje"
    try:
        parts = x_hub_signature.split('=')
        if len(parts) != 2 or parts[0] != 'sha256':
            raise ValueError
        signature_hash = parts[1]
    except ValueError:
        logger.error("Invalid signature format", header=x_hub_signature)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature format")

    if not settings.INFOBIP_SECRET_KEY:
        logger.critical("INFOBIP_SECRET_KEY nije postavljen u konfiguraciji!")
        raise HTTPException(status_code=500, detail="Server security misconfiguration")

    # Čitamo tijelo zahtjeva
    body = await request.body()
    
    # Izračunaj očekivani hash koristeći naš tajni ključ
    expected_hash = hmac.new(
        key=settings.INFOBIP_SECRET_KEY.encode('utf-8'),
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()

    # Sigurna usporedba (timing-attack safe)
    if not hmac.compare_digest(expected_hash, signature_hash):
        logger.error("Signature mismatch!", expected=expected_hash, received=signature_hash)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")