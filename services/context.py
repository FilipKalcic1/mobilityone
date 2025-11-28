import time
import orjson
import tiktoken
import structlog
import uuid
from typing import List, Optional, Union
import redis.asyncio as redis
from openai import AsyncOpenAI
from config import get_settings

logger = structlog.get_logger("context")
settings = get_settings()

# --- KONFIGURACIJA (Optimizirana za produkciju) ---
CONTEXT_TTL = 3600 * 4   # Pamtimo kontekst 4 sata
MAX_TOKENS = 2500        # Limit za cijelu povijest razgovora
TARGET_TOKENS = 1500     # Cilj nakon sažimanja
MAX_CONTENT_SIZE = 15000 # Limit za jednu poruku (15KB) - štiti od rušenja

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: Union[str, dict, list, None], **kwargs):
        """
        Glavna funkcija za spremanje.
        Automatski serijalizira objekte i reže preveliki sadržaj radi uštede.
        """
        
        # 1. BRZA SERIJALIZACIJA (Pretvaranje u tekst)
        content_str = ""
        if content is not None:
            if isinstance(content, str):
                content_str = content
            else:
                try:
                    # orjson je najbrži
                    content_str = orjson.dumps(content).decode('utf-8')
                except Exception as e:
                    logger.warning("Serialization failed, using str fallback", error=str(e))
                    content_str = str(content)
        
        # 2. ZAŠTITA OD PREVELIKIH PODATAKA (Gatekeeper)
        # Ako je poruka prevelika, reže se da ne troši tokene i ne ruši Redis.
        if len(content_str) > MAX_CONTENT_SIZE:
            # U produkciji ne spremamo original (štedimo RAM), osim ako smo u dev modu
            if settings.APP_ENV == "development":
                debug_id = str(uuid.uuid4())
                try:
                    await self.redis.setex(f"debug:overflow:{debug_id}", 3600, content_str)
                except Exception:
                    pass
            else:
                debug_id = "Production-Log"

            logger.warning("Input truncated", size=len(content_str), limit=MAX_CONTENT_SIZE)

            # AI dobiva samo sažetak
            content_str = orjson.dumps({
                "system_note": "Data truncated for performance/safety.",
                "preview": content_str[:1000] + "... (truncated)"
            }).decode('utf-8')

        # 3. SPREMANJE U REDIS (Pipeline za brzinu)
        msg = {
            "role": role,
            "content": content_str,
            "timestamp": time.time(),
            **kwargs 
        }
        
        key = self._key(sender)
        data = orjson.dumps(msg).decode('utf-8')

        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, data)
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()
            
        # Pozadinska provjera ukupnog limita tokena
        await self._enforce_token_limit(key)

    async def get_history(self, sender: str) -> List[dict]:
        """Brzo dohvaćanje povijesti."""
        key = self._key(sender)
        try:
            raw_data = await self.redis.lrange(key, 0, -1)
            return [orjson.loads(m) for m in raw_data]
        except Exception as e:
            logger.error("Redis read error", error=str(e))
            return []

    async def clear_history(self, sender: str):
        await self.redis.delete(self._key(sender))

    async def _enforce_token_limit(self, key: str):
        """Pametno sažimanje povijesti ako postane preduga."""
        try:
            # Brza provjera duljine liste (štedi CPU)
            list_len = await self.redis.llen(key)
            if list_len < 5: return 

            raw_data = await self.redis.lrange(key, 0, -1)
            messages = [orjson.loads(m) for m in raw_data]
            
            if self._count_tokens(messages) <= MAX_TOKENS:
                return

            logger.info("Context limit exceeded, summarizing...")

            kept_tokens = 0
            split_index = 0
            
            # Računamo od kraja prema početku
            for i in range(len(messages) - 1, -1, -1):
                msg_tokens = self._count_tokens([messages[i]])
                if kept_tokens + msg_tokens > TARGET_TOKENS:
                    split_index = i + 1 
                    break
                kept_tokens += msg_tokens

            if split_index < 2:
                await self.redis.lpop(key)
                return

            # Sažimanje starog dijela pomoću AI
            to_summarize = messages[:split_index]
            summary_text = await self._generate_summary(to_summarize)
            
            if not summary_text:
                await self.redis.ltrim(key, split_index, -1)
                return

            summary_msg = {
                "role": "system",
                "content": f"SAŽETAK RANIJEG RAZGOVORA: {summary_text}",
                "timestamp": time.time()
            }
            summary_data = orjson.dumps(summary_msg).decode('utf-8')

            async with self.redis.pipeline() as pipe:
                await pipe.ltrim(key, split_index, -1) 
                await pipe.lpush(key, summary_data)    
                await pipe.execute()

        except Exception as e:
            logger.error("Context cleanup failed", error=str(e))
            # Emergency cleanup
            await self.redis.ltrim(key, -10, -1)

    def _count_tokens(self, messages: List[dict]) -> int:
        count = 0
        for msg in messages:
            content = msg.get("content") or ""
            if msg.get("tool_calls"):
                content += str(msg["tool_calls"])
            count += len(self.encoding.encode(content)) + 4
        return count

    async def _generate_summary(self, messages: List[dict]) -> Optional[str]:
        try:
            text_block = ""
            for m in messages:
                role = m.get("role", "unknown")
                content = m.get("content") or str(m.get("tool_calls") or "")
                text_block += f"{role}: {content}\n"

            # Kratak i jasan prompt za sažimanje
            sys_prompt = "Sažmi ključne informacije: imena, ID-eve, brojeve, status zadnjeg zahtjeva."
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": text_block}
                ],
                max_tokens=200,
                temperature=0.3
            )
            return response.choices[0].message.content
        except Exception:
            return None