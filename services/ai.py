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
    Šalje upit OpenAI modelu. 
    Sadrži logiku za rekonstrukciju povijesti i automatski retry u slučaju neispravnog JSON-a.
    """
    
    # Limit rekurzije za popravak JSON-a (sprječava beskonačne petlje)
    if retry_count > 1:
        logger.error("Max retries reached for JSON correction")
        return {"tool": None, "response_text": "Tehnička greška u formatu podataka."}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # --- [CRITICAL] Rekonstrukcija povijesti za OpenAI Tool Use ---
    # Ovo je dio koji smo dodali da bi worker ispravno radio.
    # OpenAI zahtijeva točan format: User -> Assistant(tool_calls) -> Tool(result)
    for h in history:
        msg = {
            "role": h.get("role"),
            "content": h.get("content")
        }
        
        # Ako je ovo poruka asistenta koja je zvala alat, moramo uključiti 'tool_calls'
        if "tool_calls" in h:
            msg["tool_calls"] = h["tool_calls"]
        
        # Ako je ovo rezultat alata (role: tool), moramo uključiti 'tool_call_id'
        if "tool_call_id" in h:
            msg["tool_call_id"] = h["tool_call_id"]
            
        # Ime alata (opcionalno, ali korisno za debug)
        if "name" in h:
            msg["name"] = h["name"]
            
        messages.append(msg)
    # --------------------------------------------------------------

    # Dodajemo trenutni user prompt samo ako postoji (u tool loopu može biti None, što je ok)
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
        
        # Slučaj A: AI želi pozvati alat
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            function_name = tool_call.function.name
            arguments_str = tool_call.function.arguments
            
            try:
                # [FIX] Koristimo orjson za brži parsing, kako smo dogovorili
                parameters = orjson.loads(arguments_str)
            except orjson.JSONDecodeError:
                logger.warning("AI generated invalid JSON parameters, retrying...", raw=arguments_str, attempt=retry_count)
                # Rekurzivni poziv s povećanim brojačem
                return await analyze_intent(history, current_text, tools, retry_count + 1)

            logger.info("AI selected tool", tool=function_name)
            
            return {
                "tool": function_name,
                "parameters": parameters,
                # [CRITICAL] Ovi podaci su nužni workeru za spremanje povijesti (popravak koji smo napravili)
                "tool_call_id": tool_call.id,
                "raw_tool_calls": msg.tool_calls, 
                "response_text": None
            }
            
        # Slučaj B: AI vraća tekstualni odgovor
        return {
            "tool": None,
            "parameters": {},
            "response_text": msg.content
        }

    except Exception as e:
        logger.error("AI inference failed", error=str(e))
        return {"tool": None, "response_text": "Isprike, sustav je trenutno nedostupan zbog tehničke greške."}