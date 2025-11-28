import time
import orjson
import tiktoken
import structlog
from typing import List, Optional, Dict, Any
import redis.asyncio as redis
from openai import AsyncOpenAI
from config import get_settings

logger = structlog.get_logger("context")
settings = get_settings()

# --- KONFIGURACIJA MEMORIJE ---
CONTEXT_TTL = 3600 * 4  # Pamtimo kontekst 4 sata (dovoljno za smjenu)
MAX_TOKENS = 2500       # Okidač za sažimanje
TARGET_TOKENS = 1500    # Cilj nakon sažimanja

class ContextService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        # Encoder za gpt-3.5/4 (cl100k_base)
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    def _key(self, sender: str) -> str:
        return f"ctx:{sender}"

    async def add_message(self, sender: str, role: str, content: Optional[str], **kwargs):
        """
        Dodaje poruku u povijest i atomski upravlja veličinom konteksta.
        """
        msg = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
            **kwargs # Ovdje ulaze tool_calls, tool_call_id, name
        }
        
        key = self._key(sender)
        data = orjson.dumps(msg).decode('utf-8')

        # Pipeline za atomsku operaciju (Dodaj + Produži TTL)
        async with self.redis.pipeline() as pipe:
            await pipe.rpush(key, data)
            await pipe.expire(key, CONTEXT_TTL)
            await pipe.execute()
        
        # Pozadinska provjera limita (Fire & Forget bi bilo idealno, ali await je sigurniji)
        await self._enforce_token_limit(key)

    async def get_history(self, sender: str) -> List[dict]:
        """Dohvaća cijelu povijest razgovora."""
        key = self._key(sender)
        try:
            raw_data = await self.redis.lrange(key, 0, -1)
            return [orjson.loads(m) for m in raw_data]
        except Exception as e:
            logger.error("Redis read error", error=str(e))
            return []

    async def clear_history(self, sender: str):
        """Briše kontekst (npr. na kraju dana ili na zahtjev)."""
        await self.redis.delete(self._key(sender))

    async def _enforce_token_limit(self, key: str):
        """
        Pametno upravljanje kontekstom:
        Ako je povijest preduga, sažmi starije poruke pomoću AI-a.
        """
        try:
            raw_data = await self.redis.lrange(key, 0, -1)
            if not raw_data: return

            messages = [orjson.loads(m) for m in raw_data]
            total_tokens = self._count_tokens(messages)

            if total_tokens <= MAX_TOKENS:
                return

            logger.info("Context limit exceeded, summarizing...", current=total_tokens)

            # Strategija: Zadrži zadnjih N poruka koje stanu u TARGET_TOKENS
            kept_tokens = 0
            split_index = 0
            
            # Idemo unazad od najnovije poruke
            for i in range(len(messages) - 1, -1, -1):
                msg_tokens = self._count_tokens([messages[i]])
                if kept_tokens + msg_tokens > TARGET_TOKENS:
                    split_index = i + 1 # Sve prije ovoga ide u sažetak
                    break
                kept_tokens += msg_tokens

            # Ako nemamo što sažeti (samo par ogromnih poruka), briši najstariju
            if split_index < 2:
                await self.redis.lpop(key)
                return

            # Izdvoji poruke za sažimanje
            to_summarize = messages[:split_index]
            summary_text = await self._generate_summary(to_summarize)
            
            if not summary_text:
                # Fallback: Samo odreži višak
                await self.redis.ltrim(key, split_index, -1)
                return

            # Zamijeni stare poruke sažetkom
            summary_msg = {
                "role": "system",
                "content": f"SAŽETAK PRETHODNOG RAZGOVORA: {summary_text}",
                "timestamp": time.time()
            }
            summary_data = orjson.dumps(summary_msg).decode('utf-8')

            async with self.redis.pipeline() as pipe:
                await pipe.ltrim(key, split_index, -1) # Ostavi nove
                await pipe.lpush(key, summary_data)    # Ubaci sažetak na vrh
                await pipe.execute()
            
            logger.info("Context compacted", removed_msgs=split_index)

        except Exception as e:
            logger.error("Context management failed", error=str(e))
            # Emergency cleanup: Zadrži samo zadnjih 10 poruka
            await self.redis.ltrim(key, -10, -1)

    def _count_tokens(self, messages: List[dict]) -> int:
        """Procjenjuje broj tokena u porukama."""
        count = 0
        for msg in messages:
            content = msg.get("content") or ""
            # Brojimo i tool callove jer troše kontekst
            if msg.get("tool_calls"):
                content += str(msg["tool_calls"])
            count += len(self.encoding.encode(content)) + 4
        return count

    async def _generate_summary(self, messages: List[dict]) -> Optional[str]:
        """Koristi GPT-3.5 za kreiranje sažetka."""
        try:
            text_block = ""
            for m in messages:
                role = m.get("role", "unknown")
                content = m.get("content") or (str(m.get("tool_calls")) if m.get("tool_calls") else "")
                text_block += f"{role}: {content}\n"

            sys_prompt = (
                "Sažmi ovaj razgovor. Zadrži ključne informacije: imena, ID-eve, "
                "brojeve tablica, lokacije i status zadnjeg zahtjeva."
            )

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