# services/ai.py
import orjson
import structlog
from typing import List, Dict, Any
from openai import AsyncOpenAI
from config import get_settings

settings = get_settings()
logger = structlog.get_logger("ai")
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# [CRITICAL] Originalni Prompt s pravilima sigurnosti
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
    retry_count: int = 0,
    system_instruction: str = None # [NOVO] Za injekciju identiteta (User Email/ID)
) -> Dict[str, Any]:
    """
    Glavna logika odlučivanja. Pretvara razgovor u akciju.
    Sadrži logiku za rekonstrukciju povijesti i automatski retry u slučaju neispravnog JSON-a.
    """
    
    # 1. Sigurnosni osigurač (Circuit Breaker) za beskonačne petlje
    if retry_count > 1:
        logger.error("Max retries reached for JSON correction")
        return _text_response("Tehnička greška u formatu podataka.")

    # 2. Izgradnja poruka za LLM (uključujući identitet korisnika)
    messages = _construct_messages(history, current_text, system_instruction)

    try:
        # 3. Priprema argumenta za poziv
        call_args = {
            "model": settings.OPENAI_MODEL,
            "messages": messages,
            "temperature": 0, 
        }

        if tools:
            call_args["tools"] = tools
            call_args["tool_choice"] = "auto" 

        # 4. Poziv OpenAI modelu
        response = await client.chat.completions.create(**call_args)
        msg = response.choices[0].message
        
        # 5. Obrada odluke (Alat ili Tekst)
        if msg.tool_calls:
            return await _handle_tool_decision(
                msg.tool_calls[0], 
                msg.tool_calls, 
                history, 
                current_text, 
                tools, 
                retry_count, 
                system_instruction
            )

        return _text_response(msg.content)

    except Exception as e:
        logger.error("AI inference failed", error=str(e))
        return _text_response("Isprike, sustav je trenutno nedostupan zbog tehničke greške.")


# --- Helper Methods (Clean Code & Readability) ---

def _construct_messages(history: list, text: str, instruction: str | None) -> list:
    """
    Rekonstruira povijest razgovora i dodaje ključne sistemske instrukcije.
    """
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # [CRITICAL] Ovdje ubacujemo instrukciju o User Identitetu.
    # Ovo osigurava da AI zna tko je korisnik i popunjava "User" polje u JSON-u.
    if instruction:
        msgs.append({"role": "system", "content": instruction})
    
    for h in history:
        # Sanitizacija povijesti: Uklanjamo None vrijednosti da ne srušimo OpenAI lib
        valid_msg = {
            "role": h.get("role"),
            "content": h.get("content")
        }
        
        if "tool_calls" in h:
            valid_msg["tool_calls"] = h["tool_calls"]
        
        if "tool_call_id" in h:
            valid_msg["tool_call_id"] = h["tool_call_id"]
            
        if "name" in h:
            valid_msg["name"] = h["name"]
            
        msgs.append(valid_msg)

    if text:
        msgs.append({"role": "user", "content": text})
        
    return msgs

async def _handle_tool_decision(primary_tool, all_tools, history, text, tools, retry, sys_instr) -> dict:
    """
    Parsira argumente alata i radi rekurzivni retry ako je JSON neispravan.
    """
    function_name = primary_tool.function.name
    arguments_str = primary_tool.function.arguments
    
    try:
        # Koristimo orjson za brže parsiranje
        parameters = orjson.loads(arguments_str)
        logger.info("AI selected tool", tool=function_name)
        
        return {
            "tool": function_name,
            "parameters": parameters,
            "tool_call_id": primary_tool.id,
            "raw_tool_calls": all_tools, 
            "response_text": None
        }
    except orjson.JSONDecodeError:
        logger.warning("AI generated invalid JSON parameters, retrying...", raw=arguments_str, attempt=retry)
        
        # Rekurzivni poziv analyze_intent s povećanim brojačem retryja
        return await analyze_intent(history, current_text=text, tools=tools, retry_count=retry + 1, system_instruction=sys_instr)

def _text_response(text: str) -> dict:
    """Vraća standardizirani tekstualni odgovor."""
    return {
        "tool": None,
        "parameters": {},
        "response_text": text or "Nisam razumio."
    }