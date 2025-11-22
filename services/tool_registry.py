import json
import httpx
import structlog
import numpy as np
from typing import List, Dict
from config import get_settings
from openai import AsyncOpenAI

logger = structlog.get_logger("tool_registry")
settings = get_settings()

class ToolRegistry:
    def __init__(self):
        self.tools_map = {}      # Tu čuvamo detalje alata (kako se zove, koji URL)
        self.tools_vectors = []  # Tu čuvamo "brojeve" (vektore) za pretragu
        self.tools_names = []    # Popis imena da znamo koji vektor pripada kome
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def load_swagger(self, url_or_path: str):
        """
        Učitava Swagger (JSON) i uči alate.
        Može učitati s URL-a ili iz lokalnog filea.
        """
        logger.info("Loading Swagger definitions...", source=url_or_path)
        
        try:
            # Ako počinje s http, skini s neta. Inače čitaj lokalni file.
            if url_or_path.startswith("http"):
                async with httpx.AsyncClient() as client:
                    spec = (await client.get(url_or_path)).json()
            else:
                with open(url_or_path, 'r', encoding='utf-8') as f:
                    spec = json.load(f)

            # Iteriraj kroz sve funkcije u Swaggeru
            for path, methods in spec.get('paths', {}).items():
                for method, details in methods.items():
                    # Preskoči ako nije prava metoda
                    if method not in ['get', 'post', 'put', 'delete']:
                        continue

                    op_id = details.get('operationId', f"{method}_{path}")
                    description = details.get('summary', '') + " " + details.get('description', '')
                    
                    # --- OVDJE SE DOGAĐA MAGIJA ---
                    # Pretvaramo opis funkcije u brojeve (vektor)
                    vector = await self._get_embedding(description)
                    
                    # Spremamo u memoriju
                    self.tools_map[op_id] = {
                        "path": path,
                        "method": method,
                        "description": description,
                        # Ovdje ćemo kasnije dodati parsiranje parametara
                        "openai_schema": self._create_openai_schema(op_id, description, details)
                    }
                    self.tools_vectors.append(vector)
                    self.tools_names.append(op_id)
            
            logger.info("Swagger loaded successfully", tools_count=len(self.tools_names))

        except Exception as e:
            logger.critical("Failed to load Swagger", error=str(e))
            raise e

    async def find_relevant_tools(self, user_query: str, top_k: int = 3) -> List[Dict]:
        """
        Prima pitanje korisnika (npr. 'Gdje je vozilo?') 
        i vraća 3 najsličnija alata iz Swaggera.
        """
        if not self.tools_vectors:
            return []

        query_vector = await self._get_embedding(user_query)


        scores = np.dot(self.tools_vectors, query_vector)
        

        top_indices = np.argsort(scores)[-top_k:][::-1]
        
        relevant_tools = []
        for idx in top_indices:
            tool_name = self.tools_names[idx]
            logger.info("Tool found", name=tool_name, score=float(scores[idx]))
            relevant_tools.append(self.tools_map[tool_name]['openai_schema'])
            
        return relevant_tools

    async def _get_embedding(self, text: str):
        """Zove OpenAI da pretvori tekst u niz brojeva."""
        text = text.replace("\n", " ")
        response = await self.client.embeddings.create(
            input=[text], 
            model="text-embedding-3-small" # Najjeftiniji i brz model
        )
        return response.data[0].embedding

    def _create_openai_schema(self, name, description, details):
        """Pomoćna funkcija koja samo formatira JSON za OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object", 
                    "properties": {}, # Ovdje ćemo kasnije dodati logiku za parametre
                    "required": []
                }
            }
        }