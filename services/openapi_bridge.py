import httpx
import structlog
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
        self.limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        
        headers = {}
        # Ako imamo statični token, koristimo ga odmah
        if settings.MOBILITY_API_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MOBILITY_API_TOKEN}"

        self.client = httpx.AsyncClient(timeout=20.0, limits=self.limits, headers=headers)

    async def _refresh_token(self):
        """Traži novi token koristeći Client Credentials flow."""
        if not settings.MOBILITY_AUTH_URL or not settings.MOBILITY_CLIENT_ID:
            logger.error("OAuth podaci nedostaju, ne mogu osvježiti token.")
            return False

        logger.info("Osvježavam token...", client_id=settings.MOBILITY_CLIENT_ID)
        
        payload = {
            "client_id": settings.MOBILITY_CLIENT_ID,
            "client_secret": settings.MOBILITY_CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": settings.MOBILITY_SCOPE,
            "audience": "none"
        }

        try:
            # Novi klijent samo za auth poziv
            async with httpx.AsyncClient() as auth_client:
                resp = await auth_client.post(
                    settings.MOBILITY_AUTH_URL, 
                    data=payload, 
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                resp.raise_for_status()
                new_token = resp.json().get("access_token")
                
                if new_token:
                    self.client.headers["Authorization"] = f"Bearer {new_token}"
                    logger.info("Novi token uspješno postavljen.")
                    return True
        except Exception as e:
            logger.error("Greška pri osvježavanju tokena", error=str(e))
            return False

    async def execute_tool(self, tool_def: ToolDefinition, params: Dict[str, Any]) -> Dict[str, Any]:
        path = tool_def['path']
        method = tool_def['method'].upper() 
        
        req_data = params.copy()
        for key, value in params.items():
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(value))
                if key in req_data: del req_data[key]

        full_url = f"{self.base_url}{path}"
        request_kwargs = {"json": req_data} if method not in ["GET", "DELETE"] else {"params": req_data}

        # --- RETRY LOGIKA (Max 2 pokušaja) ---
        for attempt in range(2):
            try:
                log = logger.bind(method=method, url=full_url, attempt=attempt+1)
                log.info("API Request")

                response = await self.client.request(method, full_url, **request_kwargs)
                
                # Ako je token istekao (401), probaj ga osvježiti I AKO uspiješ, nastavi petlju (retry)
                if response.status_code == 401 and attempt == 0:
                    log.warning("Token istekao (401), pokušavam refresh...")
                    if await self._refresh_token():
                        continue 
                
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                return {"error": True, "status": e.response.status_code, "message": e.response.text}
            except Exception as e:
                log.error("Greška", error=str(e))
                return {"error": True, "message": "Greška sustava."}
                
    async def close(self):
        await self.client.aclose()