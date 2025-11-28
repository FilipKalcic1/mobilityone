import json
import httpx
import structlog
import hashlib
import asyncio
import numpy as np
import redis.asyncio as redis
from typing import List, Dict, Optional
from config import get_settings
from openai import AsyncOpenAI

logger = structlog.get_logger("tool_registry")
settings = get_settings()

class ToolRegistry:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # In-Memory Cache (Fast Access)
        self.tools_map = {}      
        self.tools_vectors = []  
        self.tools_names = []    
        
        self.is_ready = False 
        self.current_hash = None 

    async def start_auto_update(self, source_url: str, interval: int = 300):
        """Background task za Hot-Reload alata s URL-a."""
        if not source_url.startswith("http"): return

        logger.info("Auto-update watchdog started", source=source_url)
        while True:
            await asyncio.sleep(interval)
            try:
                async with httpx.AsyncClient() as client:
                    # [POPRAVAK] Fallback logika: Ako HEAD ne radi (404/405), probaj GET
                    try:
                        response = await client.head(source_url)
                        if response.status_code >= 400:
                            raise httpx.RequestError("HEAD not supported")
                    except httpx.RequestError:
                        # Ako server ne da HEAD, koristimo GET (malo sporije ali radi)
                        response = await client.get(source_url)
                    
                    remote_hash = response.headers.get("ETag") or response.headers.get("Last-Modified")
                    
                    # Ako nemamo headere za provjeru, pretpostavi da se promijenilo ako je prošlo puno vremena
                    # ili jednostavno ignoriraj ako je response prazan.
                    
                    if remote_hash and remote_hash != self.current_hash:
                        logger.info("Detected new Swagger version, reloading...")
                        await self.load_swagger(source_url)
                        self.current_hash = remote_hash
            except Exception as e:
                logger.warning("Auto-update check failed", error=str(e))

    async def load_swagger(self, source: str):
        """Učitava Swagger (File ili URL) i generira embeddinge."""
        logger.info("Loading tools definition...", source=source)
        spec = None

        try:
            if source.startswith("http"):
                async with httpx.AsyncClient() as client:
                    resp = await client.get(source, timeout=10.0)
                    resp.raise_for_status()
                    spec = resp.json()
                    self.current_hash = resp.headers.get("ETag")
            else:
                with open(source, 'r', encoding='utf-8') as f:
                    spec = json.load(f)
        except Exception as e:
            logger.error("Failed to load Swagger", source=source, error=str(e))
            if not self.is_ready: 
                # Ako je prvi start, a Swagger fali, ne ruši sve, ali logiraj kritično
                logger.critical("Starting without tools due to swagger error!")
            return

        if spec:
            await self._process_spec(spec)
            self.is_ready = True
            logger.info("Tools loaded successfully", count=len(self.tools_names))

    async def find_relevant_tools(self, query: str, top_k: int = 3) -> List[Dict]:
        """Semantic Search alata koristeći Cosine Similarity."""
        if not self.is_ready or not self.tools_vectors: return []

        try:
            query_vec = await self._get_embedding(query)
            if not query_vec: return []

            scores = np.dot(self.tools_vectors, query_vec)
            
            # Dohvati top K indeksa
            top_indices = np.argsort(scores)[-top_k:][::-1]
            
            results = []
            for idx in top_indices:
                if scores[idx] > 0.25: # Threshold relevantnosti
                    tool_name = self.tools_names[idx]
                    results.append(self.tools_map[tool_name]['openai_schema'])
            return results
            
        except Exception as e:
            logger.error("Tool search error", error=str(e))
            return []

    async def _process_spec(self, spec: dict):
        """Parsira Swagger i priprema interne strukture."""
        new_map, new_vecs, new_names = {}, [], []

        for path, methods in spec.get('paths', {}).items():
            for method, details in methods.items():
                if method.lower() not in ['get', 'post', 'put', 'delete']: continue
                
                op_id = details.get('operationId')
                
                # [POPRAVAK] Ako nema operationId, generiraj ga iz putanje!
                if not op_id:
                    # Npr. /api/vehicle/{id} -> get_api_vehicle_id
                    clean_path = path.replace("{", "").replace("}", "").replace("/", "_").strip("_")
                    op_id = f"{method.lower()}_{clean_path}"
                    # Spremi generirani ID natrag u details da ga ostale funkcije vide
                    details['operationId'] = op_id

                desc = f"{details.get('summary', '')} {details.get('description', '')}"
                if not desc.strip():
                    desc = f"{method.upper()} {path}" # Fallback opis
                
                # Cacheiranje embeddinga u Redisu
                cache_key = f"tool_embed:{op_id}:{hashlib.md5(desc.encode()).hexdigest()}"
                
                vector = await self._get_cached_embedding(cache_key, desc)
                if vector:
                    new_map[op_id] = {
                        "path": path,
                        "method": method,
                        "openai_schema": self._to_openai_schema(op_id, desc, details)
                    }
                    new_vecs.append(vector)
                    new_names.append(op_id)

        # Atomic switch
        self.tools_map, self.tools_vectors, self.tools_names = new_map, new_vecs, new_names

    async def _get_cached_embedding(self, key: str, text: str):
        cached = await self.redis.get(key)
        if cached: return json.loads(cached)
        
        vector = await self._get_embedding(text)
        if vector:
            await self.redis.set(key, json.dumps(vector))
        return vector

    async def _get_embedding(self, text: str):
        try:
            text = text.replace("\n", " ")
            resp = await self.client.embeddings.create(
                input=[text], model="text-embedding-3-small"
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.error("Embedding generation failed", error=str(e))
            return None

    def _to_openai_schema(self, name, desc, details):
        """Konvertira Swagger operaciju u OpenAI Function format."""
        params = {"type": "object", "properties": {}, "required": []}
        
        # Path/Query parametri
        for p in details.get("parameters", []):
            p_name = p.get("name")
            p_type = p.get("schema", {}).get("type", "string")
            params["properties"][p_name] = {"type": p_type, "description": p.get("description", "")}
            if p.get("required"): params["required"].append(p_name)

        # Body parametri
        if "requestBody" in details:
            content = details["requestBody"].get("content", {})
            # Podrška za json i form-data
            schema = content.get("application/json", {}).get("schema", {}) or \
                     content.get("application/x-www-form-urlencoded", {}).get("schema", {})
            
            for p_name, p_schema in schema.get("properties", {}).items():
                params["properties"][p_name] = {
                    "type": p_schema.get("type", "string"),
                    "description": p_schema.get("description", "")
                }
                if p_name in schema.get("required", []):
                    params["required"].append(p_name)

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc[:1000], # Limit opisa
                "parameters": params
            }
        }