import structlog
import logging
import sys

def configure_logger():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    # Preusmjeravamo uvicorn/fastapi logove na naš format
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)