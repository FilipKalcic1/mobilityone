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

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)