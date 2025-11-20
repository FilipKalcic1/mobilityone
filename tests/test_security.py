import pytest
import hmac
import hashlib
from unittest.mock import AsyncMock
from fastapi import Request, HTTPException
from security import validate_infobip_signature
from config import get_settings

@pytest.mark.asyncio
async def test_security_valid_signature():
    settings = get_settings()
    body = b"test_body"
    
    signature = hmac.new(
        settings.INFOBIP_SECRET_KEY.encode(), 
        body, 
        hashlib.sha256
    ).hexdigest()
    header = f"sha256={signature}"
    
    request = Request({"type": "http", "headers": [], "query_string": ""})
    request.body = AsyncMock(return_value=body)
    
    await validate_infobip_signature(request, header)

@pytest.mark.asyncio
async def test_security_invalid_signature():
    request = Request({"type": "http", "headers": [], "query_string": ""})
    request.body = AsyncMock(return_value=b"test_body")
    
    with pytest.raises(HTTPException) as exc:
        await validate_infobip_signature(request, "sha256=krivi_hash")
    
    assert exc.value.status_code == 403