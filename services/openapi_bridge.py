import httpx
import structlog
import asyncio
from typing import Dict, Any, TypedDict, Optional
from config import get_settings 

logger = structlog.get_logger("openapi_bridge")
settings = get_settings()

class ToolDefinition(TypedDict):
    path: str
    method: str
    description: str
    openai_schema: Dict[str, Any]
    operationId: Optional[str]

class OpenAPIGateway:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        
        # Connection Pooling za High Concurrency
        self.limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        
        headers = {}
        if settings.MOBILITY_API_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MOBILITY_API_TOKEN}"

        self.client = httpx.AsyncClient(timeout=15.0, limits=self.limits, headers=headers)
        
        # Safety Mechanisms
        self._circuit_open = False 
        self._failure_count = 0
        self._auth_lock = asyncio.Lock() 

    async def execute_tool(self, tool_def: ToolDefinition, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._circuit_open:
            return {"error": True, "message": "Vanjski servis je privremeno nedostupan (Circuit Open)."}

        path = tool_def['path']
        method = tool_def['method'].upper()
        
        req_data = params.copy()
        
        # 1. Path Parameters Injection
        for key, value in params.items():
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(value))
                if key in req_data: del req_data[key]

        # 2. Header Extraction (npr. x-tenant)
        headers = {}
        keys_to_remove = []
        for key, value in req_data.items():
            if key.lower().startswith('x-') or key.lower() == 'tenantid':
                headers[key] = str(value)
                keys_to_remove.append(key)
        
        for k in keys_to_remove: del req_data[k]

        full_url = f"{self.base_url}{path}"
        
        request_kwargs = {"headers": headers}
        if method in ["GET", "DELETE"]:
            request_kwargs["params"] = req_data
        else:
            request_kwargs["json"] = req_data

        # 3. Execution & Retry Policy
        for attempt in range(2):
            try:
                response = await self.client.request(method, full_url, **request_kwargs)
                
                # Token Refresh on 401
                if response.status_code == 401 and attempt == 0:
                    logger.info("Token expired (401), refreshing...")
                    if await self._secure_refresh_token():
                        continue 
                    else:
                        return {"error": True, "message": "Greška pri obnovi tokena."}
                
                # Server Errors
                if response.status_code >= 500:
                    await self._record_failure()
                    return {"error": True, "message": "Vanjski sustav ima poteškoća."}

                self._failure_count = 0 
                response.raise_for_status()
                
                if response.status_code == 204:
                    return {"status": "success", "data": None}
                    
                return response.json()

            except httpx.RequestError:
                await self._record_failure()
                return {"error": True, "message": "Greška u mrežnoj komunikaciji."}
            except Exception as e:
                logger.error("Tool exec error", error=str(e))
                return {"error": True, "message": "Interna greška sustava."}
        
        return {"error": True, "message": "Autorizacija neuspjela."}

    async def _secure_refresh_token(self) -> bool:
        """Thread-safe token refresh."""
        if not settings.MOBILITY_AUTH_URL: return False

        if self._auth_lock.locked():
            async with self._auth_lock: return True

        async with self._auth_lock:
            try:
                logger.info("Refreshing token...")
                payload = {
                    "client_id": settings.MOBILITY_CLIENT_ID,
                    "client_secret": settings.MOBILITY_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                    "scope": settings.MOBILITY_SCOPE
                }
                async with httpx.AsyncClient() as client:
                    resp = await client.post(settings.MOBILITY_AUTH_URL, data=payload)
                    resp.raise_for_status()
                    token = resp.json().get("access_token")
                    if token:
                        self.client.headers["Authorization"] = f"Bearer {token}"
                        return True
            except Exception as e:
                logger.error("Token refresh failed", error=str(e))
                return False
        return False

    async def _record_failure(self):
        self._failure_count += 1
        if self._failure_count > 5:
            self._circuit_open = True
            asyncio.create_task(self._reset_breaker())

    async def _reset_breaker(self):
        await asyncio.sleep(30)
        self._circuit_open = False
        self._failure_count = 0

    async def close(self):
        await self.client.aclose()