import orjson  
import structlog
from typing import List, Dict, Any
from openai import AsyncOpenAI
from config import get_settings

settings = get_settings()
logger = structlog.get_logger("ai")
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


SYSTEM_PROMPT = """
Ti si odgovoran i oprezan AI asistent za upravljanje voznim parkom (MobilityOne).
Tvoj cilj je točno izvršavati zadatke koristeći dostupne alate.

### PRAVILA SIGURNOSTI (CRITICAL):
1. **SAFE AKCIJE (GET/READ):** - Ako korisnik traži informaciju (npr. "Gdje je vozilo?", "Stanje računa"), ODMAH pozovi alat. 
   - Nemoj tražiti potvrdu za čitanje podataka.

2. **DANGEROUS AKCIJE (POST/DELETE/UPDATE):** - Ako korisnik želi nešto promijeniti:
   - **NIKADA** ne pozivaj alat odmah!
   - **PRVO** objasni što ćeš učiniti i traži "DA".
   - **TEK NAKON** potvrde pozovi alat.

### UPUTE:
- Budi kratak, profesionalan i direktan.
- Ako alat vrati grešku, prenesi je korisniku.
"""

async def analyze_intent(
    history: List[Dict], 
    current_text: str, 
    tools: List[Dict] = None,
    retry_count: int = 0 
) -> Dict[str, Any]:
    """
    Šalje upit OpenAI modelu uz ispravnu rekonstrukciju povijesti.
    """
    
    if retry_count > 1:
        logger.error("Max retries reached for JSON correction")
        return {"tool": None, "response_text": "Tehnička greška u formatu podataka."}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    

    for h in history:
        msg = {
            "role": h.get("role"),
            "content": h.get("content")
        }

        if "tool_calls" in h:
            msg["tool_calls"] = h["tool_calls"]
        

        if "tool_call_id" in h:
            msg["tool_call_id"] = h["tool_call_id"]
        

        if "name" in h:
            msg["name"] = h["name"]
            
        messages.append(msg)



    if current_text:
        messages.append({"role": "user", "content": current_text})

    try:
        call_args = {
            "model": settings.OPENAI_MODEL,
            "messages": messages,
            "temperature": 0, 
        }

        if tools:
            call_args["tools"] = tools
            call_args["tool_choice"] = "auto" 

        response = await client.chat.completions.create(**call_args)
        msg = response.choices[0].message
        
        
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            function_name = tool_call.function.name
            arguments_str = tool_call.function.arguments
            
            try:
                
                parameters = orjson.loads(arguments_str)
            except orjson.JSONDecodeError:
                logger.warning("AI generated invalid JSON parameters, retrying...", raw=arguments_str, attempt=retry_count)
                return await analyze_intent(history, current_text, tools, retry_count + 1)

            logger.info("AI selected tool", tool=function_name)
            
            return {
                "tool": function_name,
                "parameters": parameters,
                
                "tool_call_id": tool_call.id,     
                "raw_tool_calls": msg.tool_calls, 
                "response_text": None
            }
            
        
        return {
            "tool": None,
            "parameters": {},
            "response_text": msg.content
        }

    except Exception as e:
        logger.error("AI inference failed", error=str(e))
        return {"tool": None, "response_text": "Isprike, sustav je trenutno nedostupan zbog tehničke greške."}