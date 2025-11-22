from .vehicles import tool_vehicle_status
from .finance import tool_ina_info
from .general import tool_fallback


TOOLS_MAP = {
    "vehicle_status": tool_vehicle_status,
    "ina_info": tool_ina_info,
    "fallback": tool_fallback
}


__all__ = ["TOOLS_MAP"]