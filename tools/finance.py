from services.cache import CacheService

MOCK_CARDS_DB = {
    "12345": {"balance": 150.50, "limit": 500.00, "currency": "EUR"},
    "98765": {"balance": 20.00, "limit": 200.00, "currency": "EUR"}
}

async def _fetch_card_data(card_id: str) -> dict:
    """Simulacija dohvata financijskih podataka."""
    return MOCK_CARDS_DB.get(card_id)

async def tool_ina_info(params: dict, cache: CacheService) -> str:
    """
    Alat za provjeru stanja INA kartice.
    """
    card_id = params.get("card_id")
    if not card_id:
        return "Nedostaje broj kartice za provjeru stanja."

    clean_id = card_id.strip()
    cache_key = f"ina:{clean_id}"

    async def fetch_logic():
        data = await _fetch_card_data(clean_id)
        if not data:
            return None
        return (f"INA Kartica {clean_id}: "
                f"Trenutno stanje {data['balance']} {data['currency']} "
                f"(Limit: {data['limit']} {data['currency']}).")

    result = await cache.get_or_compute(cache_key, fetch_logic)

    if not result:
        return f"Kartica s brojem {clean_id} nije pronađena ili je neaktivna."

    return result