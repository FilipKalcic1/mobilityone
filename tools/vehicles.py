from services.cache import CacheService


MOCK_VEHICLES_DB = {
    "ZG-1234-AB": {"loc": "Zagreb, Ilica 212", "status": "Parkiran", "driver": "Ivan Horvat"},
    "RI-999-CD": {"loc": "Rijeka, Riva 1", "status": "U vožnji", "driver": "Marko Marić"},
    "ST-555-EF": {"loc": "Split, Vukovarska 5", "status": "Servis", "driver": "Ana Anić"}
}

async def _fetch_vehicle_data(plate: str) -> dict:
    """
    Interna funkcija koja simulira dohvat s MobilityOne API-ja.
    U budućnosti ovdje ide: await client.get(f'/vehicles/{plate}')
    """
    return MOCK_VEHICLES_DB.get(plate)

async def tool_vehicle_status(params: dict, cache: CacheService) -> str:
    """
    Alat za provjeru statusa vozila.
    """
    plate = params.get("plate")
    if not plate:
        return "Nedostaje registracija vozila (npr. ZG-1234-AB)."


    clean_plate = plate.strip().upper()
    cache_key = f"vehicle:{clean_plate}"

    async def fetch_logic():
        data = await _fetch_vehicle_data(clean_plate)
        if not data:
            return None
        return f"Vozilo {clean_plate} | Lokacija: {data['loc']} | Status: {data['status']} | Vozač: {data['driver']}"


    result = await cache.get_or_compute(cache_key, fetch_logic)
    
    if not result:
        return f"Vozilo s registracijom {clean_plate} nije pronađeno u sustavu."
        
    return result