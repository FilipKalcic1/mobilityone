import json
import httpx
import structlog
import hashlib
import os
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
        self.tools_map = {}      
        self.tools_vectors = []  
        self.tools_names = []    
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.redis = redis_client 
        self.is_ready = False 
        self.current_hash = None # Za praćenje promjena (ETag/Hash)

    async def start_auto_update(self, source_url: str, interval: int = 300):
        """
        [NOVO] Pozadinski task koji periodički provjerava ima li novih alata.
        Omogućuje 'Hot-Reload' bez restartanja containera.
        """
        if not source_url or not source_url.startswith("http"):
            logger.info("Auto-update skipped (lokalna datoteka).")
            return

        logger.info("Starting auto-update watchdog", source=source_url, interval=interval)
        
        while True:
            await asyncio.sleep(interval)
            try:
                async with httpx.AsyncClient() as client:
                    # Šaljemo HEAD zahtjev da provjerimo je li se file promijenio
                    resp = await client.head(source_url, timeout=5.0)
                    remote_etag = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
                    
                    # Ako server ne podržava ETag, moramo skinuti cijeli file i hashirati ga
                    if not remote_etag:
                        resp = await client.get(source_url)
                        content = resp.content
                        remote_etag = hashlib.md5(content).hexdigest()
                    
                    if remote_etag != self.current_hash:
                        logger.info("Detektirana nova verzija alata, osvježavam...", new_hash=remote_etag)
                        await self.load_swagger(source_url)
                        self.current_hash = remote_etag
                        
            except Exception as e:
                logger.warning("Failed to check for tool updates", error=str(e))

    async def load_swagger(self, source: str):
        """
        Učitava Swagger definicije (Lokalno ili URL) i generira embeddinge.
        """
        logger.info("Pokrećem učitavanje definicija alata", source=source)
        spec = None

        # --- 1. REMOTE IZVOR (URL) ---
        if source.startswith("http"):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(source, timeout=10.0)
                    resp.raise_for_status()
                    spec = resp.json()
                    
                    # Ažuriramo hash za auto-update logiku
                    self.current_hash = resp.headers.get("ETag") or hashlib.md5(resp.content).hexdigest()
                    logger.info("Uspješno dohvaćen Swagger s URL-a")
            except Exception as e:
                logger.error("Neuspjeli dohvat s URL-a", error=str(e))
                # Ako remote ne radi, pokušaj fallback na lokalni cache ako postoji
                if not self.is_ready:
                    return 

        # --- 2. LOKALNI IZVOR (File) ---
        else:
            try:
                with open(source, 'r', encoding='utf-8') as f:
                    spec = json.load(f)
            except FileNotFoundError:
                logger.error(f"CRITICAL: Swagger datoteka '{source}' nije pronađena!")
                return
            except json.JSONDecodeError:
                logger.error(f"CRITICAL: Datoteka '{source}' nije validan JSON.")
                return

        if spec:
            try:
                # Parsiranje briše stare alate i stavlja nove (Thread-safe u Pythonu zbog GIL-a za dict assign)
                await self._parse_and_embed(spec)
                self.is_ready = True
                logger.info("Učitavanje alata uspješno završeno", tools_count=len(self.tools_names))
            except Exception as e:
                logger.critical("Neočekivana greška pri parsiranju alata", error=str(e))
                # Ako je update neuspješan, zadrži staru verziju (self.is_ready ostaje True ako je bio True)

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
                
                # Prag relevantnosti (da ne šaljemo smeće modelu)
                if score > 0.25: 
                    logger.debug("Tool match candidate", name=tool_name, score=score)
                    relevant_tools.append(self.tools_map[tool_name]['openai_schema'])
                
            return relevant_tools
        except Exception as e:
            logger.error("Greška pri pretraživanju alata", error=str(e))
            return []

    async def _get_embedding(self, text: str):
        """Poziva OpenAI za embedding teksta (s Redis cacheom za upite)."""
        # Cacheiranje samih query embeddinga može dodatno ubrzati stvar
        cache_key = f"query_embed:{hashlib.md5(text.encode()).hexdigest()}"
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        text = text.replace("\n", " ")
        response = await self.client.embeddings.create(
            input=[text], 
            model="text-embedding-3-small"
        )
        vector = response.data[0].embedding
        
        # Spremi u cache na kratko vrijeme (npr. 1h)
        await self.redis.setex(cache_key, 3600, json.dumps(vector))
        return vector

    async def _parse_and_embed(self, spec: dict):
        """Iterira kroz Swagger i priprema alate."""
        # Privremene strukture da ne razbijemo live promet dok se učitava
        new_tools_map = {}
        new_tools_vectors = []
        new_tools_names = []

        for path, methods in spec.get('paths', {}).items():
            for method, details in methods.items():
                if method.lower() not in ['get', 'post', 'put', 'delete']:
                    continue

                op_id = details.get('operationId', f"{method}_{path}")
                description = details.get('summary', '') + " " + details.get('description', '')
                
                # Generiranje hasha za cache key embeddinga alata
                desc_hash = hashlib.md5(description.encode('utf-8')).hexdigest()
                cache_key = f"tool_embedding:{op_id}:{desc_hash}"

                vector = None
                cached_data = await self.redis.get(cache_key)
                
                if cached_data:
                    vector = json.loads(cached_data)
                else:
                    # Samo ako nemamo u cacheu, zovemo OpenAI
                    # Ovo štedi novac/vrijeme pri restartu
                    logger.info("Generiram novi embedding za alat", tool=op_id)
                    vector = await self._get_embedding(description)
                    await self.redis.set(cache_key, json.dumps(vector))
                
                new_tools_map[op_id] = {
                    "path": path,
                    "method": method,
                    "description": description,
                    "openai_schema": self._create_openai_schema(op_id, description, details),
                    "operationId": op_id
                }
                new_tools_vectors.append(vector)
                new_tools_names.append(op_id)

        # Atomic switch
        self.tools_map = new_tools_map
        self.tools_vectors = new_tools_vectors
        self.tools_names = new_tools_names

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