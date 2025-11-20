import structlog
import logging
import sys

def configure_logger():
    """Konfigurira strukturirano JSON logiranje."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    
    # Preusmjeri standardni logging na structlog
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)