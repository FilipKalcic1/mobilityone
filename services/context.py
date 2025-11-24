import redis.asyncio as redis
import time
import orjson  
import tiktoken 
import structlog
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from openai import AsyncOpenAI
from config import get_settings

logger = structlog.get_logger("context")
settings = get_settings()

# --- KONFIGURACIJA KONTEKSTA ---

CONTEXT_TTL = 3600  # Vrijeme trajanja sesije (1 sat)
MAX_TOKENS = 2500   # Granica nakon koje pokrećemo sažimanje
TARGET_TOKENS = 1500 # Ciljana veličina konteksta nakon sažimanja (ostavljamo prostora za nove poruke)

class Message(BaseModel):
    role: str
    content: Optional[str] = None
    timestamp: float = 0.0
    
    # Polja za Tool Use (OpenAI zahtijeva ovo za rekonstrukciju)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        # Tiktoken encoder za gpt-3.5/4
        self.encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")

        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: Optional[str], **kwargs):
        """
        Sprema novu poruku u povijest i pokreće upravljanje veličinom konteksta.
        """
        msg = Message(
            role=role, 
            content=content, 
            timestamp=time.time(),
            **kwargs
        )
        key = self._key(sender)
        
        data = orjson.dumps(msg.model_dump(exclude_none=True)).decode('utf-8')


        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, data)
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()
        

        await self._manage_context_size(key)

    async def get_history(self, sender: str) -> List[dict]:
        """Dohvaća cijelu povijest razgovora za korisnika."""
        key = self._key(sender)
        raw_data = await self.redis.lrange(key, 0, -1)
        return [orjson.loads(m) for m in raw_data]
    
    async def clear_history(self, sender: str):
        """Briše povijest (npr. na zahtjev korisnika)."""
        await self.redis.delete(self._key(sender))

    async def _manage_context_size(self, key: str):
        """
        Pametno upravljanje kontekstom:
        Ako broj tokena prelazi MAX_TOKENS, stare poruke se sažimaju u jednu sistemsku poruku.
        """
        try:
            raw_data = await self.redis.lrange(key, 0, -1)
            if not raw_data:
                return

            messages = [orjson.loads(m) for m in raw_data]
            

            total_tokens = 0
            token_counts = []
            
            for msg in messages:

                content = msg.get("content") or ""

                if msg.get("tool_calls"):
                    content += str(msg["tool_calls"])
                
                count = len(self.encoding.encode(content)) + 4 
                token_counts.append(count)
                total_tokens += count

            if total_tokens <= MAX_TOKENS:
                return

            logger.info("Context limit exceeded, summarizing...", current=total_tokens, max=MAX_TOKENS)


            kept_tokens = 0
            split_index = 0
            
            for i in range(len(messages) - 1, -1, -1):
                kept_tokens += token_counts[i]
                if kept_tokens >= TARGET_TOKENS:
                    split_index = i
                    break
            
            # Ako je split index 0 ili 1, nemamo što puno sažeti, samo brišemo najstariju
            if split_index < 2:
                await self.redis.lpop(key)
                return


            to_summarize = messages[:split_index]

            summary_text = await self._generate_summary(to_summarize)
            
            if not summary_text:

                await self.redis.ltrim(key, split_index, -1)
                return


            summary_msg = Message(
                role="system", 
                content=f"PRETHODNI KONTEKST (SAŽETAK): {summary_text}",
                timestamp=time.time()
            )
            summary_data = orjson.dumps(summary_msg.model_dump()).decode('utf-8')

            async with self.redis.pipeline() as pipe:
                await pipe.ltrim(key, split_index, -1)
                await pipe.lpush(key, summary_data)
                await pipe.execute()
            
            logger.info("Context summarized successfully", removed_count=len(to_summarize))

        except Exception as e:
            logger.error("Error managing context size", error=str(e))
            await self.redis.ltrim(key, -20, -1) # Zadrži zadnjih 20

    async def _generate_summary(self, messages: List[dict]) -> Optional[str]:
        """Poziva OpenAI za kreiranje sažetka."""
        try:
            # Pretvaramo listu poruka u tekst za prompt
            conversation_text = ""
            for m in messages:
                role = m.get("role", "unknown")
                content = m.get("content")
                if content:
                    conversation_text += f"{role}: {content}\n"
                elif m.get("tool_calls"):
                    conversation_text += f"{role}: [Poziva alat: {m['tool_calls'][0]['function']['name']}]\n"
                elif m.get("tool_call_id"): # Rezultat alata
                    conversation_text += f"tool_result: {content}\n"

            system_prompt = (
                "Ti si AI asistent. Tvoj zadatak je sažeti gornji dio razgovora između korisnika i bota. "
                "Sačuvaj sve KLJUČNE informacije: imena, brojeve tablica, ID-eve, lokacije i status zadnjeg zahtjeva. "
                "Sažetak mora biti kratak i informativan za nastavak razgovora."
            )

            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo", # Koristimo jeftiniji model za sažimanje
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": conversation_text}
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.warning("Failed to generate summary via OpenAI", error=str(e))
            return None