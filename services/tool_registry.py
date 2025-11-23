import json
import httpx
import structlog
import hashlib
import os
import numpy as np
import redis.asyncio as redis
from typing import List, Dict
from config import get_settings
from openai import AsyncOpenAI

logger = structlog.get_logger("tool_registry")
settings = get_settings()

class ToolRegistry:
    def __init__(self, redis_client: redis.Redis):
        self.tools_map = {}      
        self.tools_vectors = []  
        self.tools_names = []    
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.redis = redis_client 
        self.is_ready = False # Flag: Jesu li alati spremni?

    async def load_swagger(self, source: str):
        """
        Učitava Swagger definicije i generira/dohvaća embeddinge.
        Podržava 'Graceful Degradation' - ne ruši app ako datoteka fali.
        """
        logger.info("Pokrećem učitavanje definicija alata", source=source)
        spec = None

        # --- ODSJEK ZA UDALJENI IZVOR (Remote Config) ---
        # Otkomentirati kada bude dostupna URL opcija
        # if source.startswith("http"):
        #     try:
        #         logger.info("Dohvaćam Swagger s udaljenog izvora...")
        #         async with httpx.AsyncClient() as client:
        #             resp = await client.get(source, timeout=10.0)
        #             resp.raise_for_status()
        #             spec = resp.json()
        #             
        #             # Ako smo uspješno dohvatili s neta, a imamo lokalni stari file, brišemo ga radi sigurnosti/čišćenja
        #             if os.path.exists("swagger.json"):
        #                 os.remove("swagger.json")
        #                 logger.info("Obrisana lokalna swagger.json datoteka (pregazila ju je remote verzija).")
        #     except Exception as e:
        #         logger.error("Neuspjeli dohvat s URL-a", error=str(e))
        #         # Ovdje možemo dodati 'return' ili pokušati fallback na lokalni file
        
        # --- LOKALNI IZVOR ---
        if spec is None:
            try:
                with open(source, 'r', encoding='utf-8') as f:
                    spec = json.load(f)
            except FileNotFoundError:
                logger.error(f"CRITICAL: Swagger datoteka '{source}' nije pronađena! Alati će biti nedostupni.")
                self.is_ready = False
                return
            except json.JSONDecodeError:
                logger.error(f"CRITICAL: Datoteka '{source}' nije validan JSON.")
                self.is_ready = False
                return

        try:
            await self._parse_and_embed(spec)
            self.is_ready = True
            logger.info("Učitavanje alata uspješno završeno", tools_count=len(self.tools_names))
        except Exception as e:
            logger.critical("Neočekivana greška pri parsiranju alata", error=str(e))
            self.is_ready = False

    async def find_relevant_tools(self, user_query: str, top_k: int = 3) -> List[Dict]:
        """Pronađi najbolje alate koristeći Cosine Similarity."""
        if not self.is_ready or not self.tools_vectors:
            return []

        try:
            query_vector = await self._get_embedding(user_query)
            
            # Vektorska operacija (dot product)
            scores = np.dot(self.tools_vectors, query_vector)
            top_indices = np.argsort(scores)[-top_k:][::-1]
            
            relevant_tools = []
            for idx in top_indices:
                tool_name = self.tools_names[idx]
                score = float(scores[idx])
                # Možemo dodati prag, npr. if score > 0.3:
                logger.debug("Tool match candidate", name=tool_name, score=score)
                relevant_tools.append(self.tools_map[tool_name]['openai_schema'])
                
            return relevant_tools
        except Exception as e:
            logger.error("Greška pri pretraživanju alata", error=str(e))
            return []

    async def _get_embedding(self, text: str):
        """Poziva OpenAI za embedding teksta."""
        text = text.replace("\n", " ")
        response = await self.client.embeddings.create(
            input=[text], 
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    async def _parse_and_embed(self, spec: dict):
        """Iterira kroz Swagger i priprema alate."""
        for path, methods in spec.get('paths', {}).items():
            for method, details in methods.items():
                if method.lower() not in ['get', 'post', 'put', 'delete']:
                    continue

                op_id = details.get('operationId', f"{method}_{path}")
                description = details.get('summary', '') + " " + details.get('description', '')
                
                # Generiranje hasha za cache key
                desc_hash = hashlib.md5(description.encode('utf-8')).hexdigest()
                cache_key = f"tool_embedding:{op_id}:{desc_hash}"

                vector = None
                # Pokušaj dohvata iz Redisa
                cached_data = await self.redis.get(cache_key)
                
                if cached_data:
                    vector = json.loads(cached_data)
                else:
                    logger.info("Generiram novi embedding", tool=op_id)
                    vector = await self._get_embedding(description)
                    # Spremamo u Redis (bez TTL-a ili s dugim TTL-om)
                    await self.redis.set(cache_key, json.dumps(vector))
                
                # Spremanje u memoriju
                self.tools_map[op_id] = {
                    "path": path,
                    "method": method,
                    "description": description,
                    "openai_schema": self._create_openai_schema(op_id, description, details),
                    "operationId": op_id
                }
                self.tools_vectors.append(vector)
                self.tools_names.append(op_id)

    def _create_openai_schema(self, name, description, details):
        """Konvertira Swagger parametre u OpenAI function format."""
        properties = {}
        required_fields = []

        # 1. Query/Path Parameters
        if "parameters" in details:
            for param in details["parameters"]:
                param_name = param.get("name")
                param_schema = param.get("schema", {})
                param_type = param_schema.get("type", "string")
                param_desc = param.get("description", "")

                properties[param_name] = {
                    "type": param_type,
                    "description": param_desc
                }

                if param.get("required", False):
                    required_fields.append(param_name)

        # 2. Request Body (JSON)
        if "requestBody" in details:
            content = details["requestBody"].get("content", {})
            json_schema = content.get("application/json", {}).get("schema", {})
            
            if "properties" in json_schema:
                for prop_name, prop_def in json_schema["properties"].items():
                    properties[prop_name] = {
                        "type": prop_def.get("type", "string"),
                        "description": prop_def.get("description", "")
                    }
                    
                    if prop_name in json_schema.get("required", []):
                        required_fields.append(prop_name)

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_fields
                }
            }
        }