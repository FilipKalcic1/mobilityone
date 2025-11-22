import hmac
import hashlib
import structlog
from fastapi import Request, HTTPException, Header, status
from config import get_settings

logger = structlog.get_logger("security")

async def validate_infobip_signature(request: Request, x_hub_signature: str = Header(None)):
    settings = get_settings()
    

    if settings.APP_ENV != "production" and not x_hub_signature:
        logger.warning("Security skipped (DEV MODE)")
        return

    if not x_hub_signature:
        raise HTTPException(status_code=403, detail="Missing signature")

    try:
        algo, signature = x_hub_signature.split('=')
        if algo != 'sha256': raise ValueError
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid signature format")

    body = await request.body()
    expected = hmac.new(
        settings.INFOBIP_SECRET_KEY.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.error("Signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")