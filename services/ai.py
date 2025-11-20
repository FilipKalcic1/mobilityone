import json
import structlog
from openai import AsyncOpenAI
from config import get_settings
from typing import List, Dict, Any

settings = get_settings()
logger = structlog.get_logger("ai")
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """
You are a Fleet Management AI Assistant.
Analyze the user request and extract intent.

TOOLS:
- 'vehicle_status': User asks about vehicle location or status. Requires 'plate'.
- 'ina_info': User asks about INA card balance/info. Requires 'card_id'.
- 'fallback': If intent is unclear or unrelated.

OUTPUT FORMAT (JSON ONLY):
{
  "tool": "vehicle_status" | "ina_info" | "fallback",
  "confidence": float (0.0-1.0),
  "parameters": {
    "plate": "string or null",
    "card_id": "string or null"
  }
}
"""

async def analyze_intent(history: List[Dict], current_text: str) -> Dict[str, Any]:
    if not current_text:
        return {"tool": "fallback", "parameters": {}}

    # Priprema poruka
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Filtriramo povijest da sadrži samo role i content
    for h in history:
        messages.append({"role": h.get("role"), "content": h.get("content")})
    messages.append({"role": "user", "content": current_text})

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"} # Forsiramo validan JSON
        )
        
        data = json.loads(response.choices[0].message.content)
        
        # Sigurnosna provjera confidence-a
        if data.get("confidence", 0) < settings.AI_CONFIDENCE_THRESHOLD:
            return {"tool": "fallback", "parameters": {}}
            
        return data

    except Exception as e:
        logger.error("AI inference failed", error=str(e))
        return {"tool": "fallback", "parameters": {}}