import json
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
1. **SAFE AKCIJE (GET/READ):** - Ako korisnik traži informaciju (npr. "Gdje je vozilo?", "Stanje računa", "Daj mi izvještaj"), ODMAH pozovi odgovarajući alat. 
   - Nemoj tražiti potvrdu za čitanje podataka.

2. **DANGEROUS AKCIJE (POST/DELETE/UPDATE):** - Ako korisnik želi nešto promijeniti, obrisati ili poslati (npr. "Obriši vozilo", "Ažuriraj status", "Prijavi servis", "Blokiraj karticu"):
   - **NIKADA** ne pozivaj alat odmah u prvom koraku!
   - **PRVO** objasni korisniku što ćeš učiniti i traži jasnu potvrdu (npr. "Jeste li sigurni da želite prijaviti servis za ZG-123? Odgovorite s DA.").
   - **TEK NAKON** što korisnik napiše "DA", "Potvrđujem" ili slično u idućoj poruci (provjeri povijest razgovora), pozovi alat.

### UPUTE ZA RAZGOVOR:
- Budi kratak, profesionalan i direktan.
- Ako alat vrati grešku (npr. "Vozilo nije nađeno"), točno to prenesi korisniku. Ne izmišljaj uspjeh.
- Nikad ne izmišljaj ID-eve, registracije ili podatke koji nisu eksplicitno navedeni u razgovoru ili dohvaćeni alatom.
"""

async def analyze_intent(
    history: List[Dict], 
    current_text: str, 
    tools: List[Dict] = None,
    retry_count: int = 0 
) -> Dict[str, Any]:
    """
    Šalje upit OpenAI modelu. 
    Sadrži logiku za automatski retry u slučaju neispravnog JSON formata.
    """
    

    if retry_count > 1:
        logger.error("Max retries reached for JSON correction")
        return {"tool": None, "response_text": "Došlo je do tehničke greške u obradi podataka."}

    if not current_text:
        return {"tool": None, "response_text": ""}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for h in history:
        role = "assistant" if h.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": h.get("content")})
    
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
                parameters = json.loads(arguments_str)
            except json.JSONDecodeError:
                
                logger.warning("AI generated invalid JSON parameters, retrying...", raw=arguments_str, attempt=retry_count)
                
                return await analyze_intent(history, current_text, tools, retry_count + 1)

            logger.info("AI selected tool", tool=function_name)
            return {
                "tool": function_name,
                "parameters": parameters,
                "response_text": None
            }
            
        
        return {
            "tool": None,
            "parameters": {},
            "response_text": msg.content
        }

    except Exception as e:
        logger.error("AI inference failed", error=str(e))
        return {"tool": None, "response_text": "Isprike, trenutno imam poteškoća s obradom vašeg zahtjeva."}