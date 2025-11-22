import httpx
import structlog
from typing import Dict, Any, TypedDict, Optional
from config import get_settings # [NOVO] Import konfiguracije

logger = structlog.get_logger("openapi_bridge")
settings = get_settings() # [NOVO] Dohvaćanje keširanih postavki


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
        if settings.MOBILITY_API_TOKEN:

            headers["Authorization"] = f"Bearer {settings.MOBILITY_API_TOKEN}"
            logger.info("API Gateway initialized with Authorization header")


        self.client = httpx.AsyncClient(timeout=20.0, limits=self.limits, headers=headers)

    async def execute_tool(self, tool_def: ToolDefinition, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dinamički izvršava HTTP zahtjev na temelju definicije alata.
        """
        path = tool_def['path']
        method = tool_def['method'].upper() 
        
        req_data = params.copy()
        
        for key, value in params.items():
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(value))
                if key in req_data:
                    del req_data[key]

        full_url = f"{self.base_url}{path}"
        
        request_kwargs = {}
        if method in ["GET", "DELETE"]:
            request_kwargs["params"] = req_data
        else:
            request_kwargs["json"] = req_data

        log = logger.bind(method=method, url=full_url)
        log.info("API Request started")

        try:

            response = await self.client.request(method, full_url, **request_kwargs)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            log.warning("API returned error", status=e.response.status_code)
            return {
                "error": True,
                "status": e.response.status_code,
                "message": e.response.text or "Greška na udaljenom serveru"
            }
            
        except httpx.RequestError as e:
            log.error("Network connection failed", error=str(e))
            return {
                "error": True, 
                "message": "Nisam uspio kontaktirati sustav (Network Error)."
            }
            
        except Exception as e:
            log.error("Unexpected error in bridge", error=str(e))
            return {"error": True, "message": "Interna greška sustava."}

    async def close(self):
        await self.client.aclose()