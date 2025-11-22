import json
import httpx
import structlog
import numpy as np
import hashlib
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

    async def load_swagger(self, url_or_path: str):
        """
        Učitava Swagger i kešira embeddinge u Redisu.
        """
        logger.info("Loading Swagger definitions...", source=url_or_path)
        
        try:
            if url_or_path.startswith("http"):
                async with httpx.AsyncClient() as client:
                    spec = (await client.get(url_or_path)).json()
            else:
                with open(url_or_path, 'r', encoding='utf-8') as f:
                    spec = json.load(f)

            for path, methods in spec.get('paths', {}).items():
                for method, details in methods.items():
                    if method not in ['get', 'post', 'put', 'delete']:
                        continue

                    op_id = details.get('operationId', f"{method}_{path}")
                    description = details.get('summary', '') + " " + details.get('description', '')
                    

                    desc_hash = hashlib.md5(description.encode('utf-8')).hexdigest()
                    cache_key = f"tool_embedding:{op_id}:{desc_hash}"

                    vector = None
                    

                    cached_data = await self.redis.get(cache_key)
                    
                    if cached_data:
                        vector = json.loads(cached_data)
                    else:
                        logger.info("Computing new embedding", tool=op_id)
                        vector = await self._get_embedding(description)

                        await self.redis.set(cache_key, json.dumps(vector))
                    
                    self.tools_map[op_id] = {
                        "path": path,
                        "method": method,
                        "description": description,
                        "openai_schema": self._create_openai_schema(op_id, description, details),
                        "operationId": op_id
                    }
                    self.tools_vectors.append(vector)
                    self.tools_names.append(op_id)
            
            logger.info("Swagger loaded successfully", tools_count=len(self.tools_names))

        except Exception as e:
            logger.critical("Failed to load Swagger", error=str(e))
            raise e

    async def find_relevant_tools(self, user_query: str, top_k: int = 3) -> List[Dict]:
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
        text = text.replace("\n", " ")
        response = await self.client.embeddings.create(
            input=[text], 
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    def _create_openai_schema(self, name, description, details):
        """
        Parsira Swagger parametre i requestBody u OpenAI function schema format.
        """
        properties = {}
        required_fields = []

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