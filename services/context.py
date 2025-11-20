import redis.asyncio as redis
import json
from typing import List, Dict
import time

# Pamtimo kontekst 30 minuta (1800 sekundi)
CONTEXT_TTL = 1800 
MAX_HISTORY_LENGTH = 10 

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    def _get_key(self, sender: str) -> str:
        return f"context:{sender}"

    async def get_history(self, sender: str) -> List[Dict[str, str]]:
        """Dohvaća povijest kao listu rječnika za AI."""
        key = self._get_key(sender)
        try:
            # Dohvati sve (lrange 0 -1)
            history_json = await self.redis.lrange(key, 0, -1)
            # Deserijalizacija
            return [json.loads(item) for item in history_json]
        except Exception:
            # Ako dođe do greške, vrati praznu listu (bolje nego srušiti app)
            return []

    async def add_message(self, sender: str, role: str, text: str):
        """Dodaje poruku i održava limit veličine povijesti."""
        key = self._get_key(sender)
        
        # Timestamp je koristan za debugging
        msg_obj = {"role": role, "content": text, "timestamp": time.time()}
        new_entry = json.dumps(msg_obj)
        
        # Transakcija (pipeline) za atomsku operaciju
        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, new_entry)
            await pipe.ltrim(key, -MAX_HISTORY_LENGTH, -1) # Zadrži zadnjih N
            await pipe.expire(key, CONTEXT_TTL) # Resetiraj timer
            await pipe.execute()