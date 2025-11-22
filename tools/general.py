from services.cache import CacheService

async def tool_fallback(params: dict, cache: CacheService) -> str:
    """
    Zadana funkcija kada AI nije siguran ili nema alata.
    """
    return (
        "Nisam siguran kako pomoći s tim upitom. "
        "Mogu provjeriti status vozila (npr. 'Gdje je ZG-123') "
        "ili stanje INA kartice (npr. 'Stanje kartice 12345')."
    )