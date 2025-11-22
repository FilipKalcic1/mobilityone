import httpx
import structlog
from typing import Dict, Any

logger = structlog.get_logger("openapi_bridge")

class OpenAPIGateway:
    def __init__(self, base_url: str):

        self.base_url = base_url.rstrip('/')
        

        self.limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        self.client = httpx.AsyncClient(timeout=20.0, limits=self.limits)

    async def execute_tool(self, tool_def: Dict, params: Dict) -> Any:
        """
        Dinamički izvršava HTTP zahtjev na temelju definicije alata.
        Ne koristi hardkodirane if-else blokove za metode.
        """
        path = tool_def['path']
        method = tool_def['method'].upper() # httpx voli UPPERCASE (GET, POST...)
        

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
                "message": "Nisam uspio kontaktirati sustav (Network Error). Molim pokušajte kasnije."
            }
            
        except Exception as e:

            log.error("Unexpected error in bridge", error=str(e))
            return {"error": True, "message": "Došlo je do interne greške prilikom obrade zahtjeva."}

    async def close(self):
        """Zatvara konekcije pri gašenju aplikacije."""
        await self.client.aclose()