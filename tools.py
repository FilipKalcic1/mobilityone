import asyncio
from services.cache import CacheService

# --- DATA LAYER (Ovo bi u pravoj app bio repository pattern) ---
async def _fetch_vehicle_from_db(plate: str) -> str:
    # Simulacija DB poziva - ovdje ide pravi SQL
    await asyncio.sleep(0.2) 
    return f"Vozilo {plate} je locirano u Zagrebu. Status: U pokretu."

async def _fetch_card_from_api(card_id: str) -> str:
    # Simulacija vanjskog API-ja
    await asyncio.sleep(0.2)
    return f"INA Kartica {card_id}: Stanje 150.45 EUR."

# --- BUSINESS LOGIC ---
async def tool_vehicle_status(params: dict, cache: CacheService) -> str:
    plate = params.get("plate")
    if not plate:
        return "Nedostaje registracija vozila. Molim navedite registraciju."
    
    # Normalizacija ključa
    clean_plate = plate.strip().upper()
    cache_key = f"vehicle:{clean_plate}"
    
    return await cache.get_or_compute(cache_key, _fetch_vehicle_from_db, clean_plate)

async def tool_ina_info(params: dict, cache: CacheService) -> str:
    card_id = params.get("card_id")
    if not card_id:
        return "Nedostaje broj kartice."
        
    cache_key = f"ina:{card_id.strip()}"
    return await cache.get_or_compute(cache_key, _fetch_card_from_api, card_id)

async def tool_fallback(params: dict, cache: CacheService) -> str:
    return "Nisam siguran kako pomoći s tim. Mogu provjeriti status vozila ili INA kartice."

# Registry
TOOLS_MAP = {
    "vehicle_status": tool_vehicle_status,
    "ina_info": tool_ina_info,
    "fallback": tool_fallback
}