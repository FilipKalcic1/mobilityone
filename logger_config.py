import structlog
import logging
import sys
from config import get_settings

def configure_logger():
    settings = get_settings()
    
    processors = [
        structlog.contextvars.merge_contextvars, # Podrška za async context
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # ovdje se logs prilagođavaju ovisno o tome da li testiramo ili smo u produkciji 


    if settings.APP_ENV == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Preusmjeri standardni Python logging na structlog
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)