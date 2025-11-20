import asyncio
import structlog
from services.cache import CacheService

logger = structlog.get_logger()

# Simulacija baze
async def _db_fetch_vehicle(plate: str):
    await asyncio.sleep(0.5) # Simulacija latencije
    return f"Vozilo {plate} je u Zagrebu. Status: OK."

async def _db_fetch_ina(card_id: str):
    await asyncio.sleep(0.5)
    return f"Kartica {card_id}: Stanje 200 EUR."

# Javne funkcije koje koriste cache
async def tool_vehicle_status(query: str, cache: CacheService) -> str:
    # Kreiramo ključ na temelju upita
    cache_key = f"vehicle:{query.strip().replace(' ', '_')}"
    
    # CacheService će provjeriti Redis prije nego pozove _db_fetch_vehicle
    result = await cache.get_or_compute(cache_key, _db_fetch_vehicle, query)
    return result

async def tool_ina_info(query: str, cache: CacheService) -> str:
    cache_key = f"ina:{query.strip()}"
    result = await cache.get_or_compute(cache_key, _db_fetch_ina, query)
    return result

async def tool_fallback(query: str, cache: CacheService) -> str:
    return "Mogu provjeriti status vozila ili INA kartice."

TOOLS_MAP = {
    "vehicle_status": tool_vehicle_status,
    "ina_info": tool_ina_info,
    "fallback": tool_fallback
}