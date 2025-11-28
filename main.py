import time
import structlog
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi_limiter import FastAPILimiter

from config import get_settings
from logger_config import configure_logger
from services.queue import QueueService
from services.context import ContextService

# Routeri
from routers import webhook

# Inicijalizacija loggera odmah
configure_logger()
logger = structlog.get_logger("main")
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Upravljanje životnim ciklusom aplikacije (Startup/Shutdown).
    """
    redis_client = None
    
    try:
        logger.info("System startup initiated...")
        
        # 1. Redis Konekcija
        redis_client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(redis_client)
        logger.info("Redis connected & Rate Limiter initialized.")
        
        # 2. Inicijalizacija Servisa (Samo onih koji trebaju API-ju)
        # Worker ima svoje, API svoje, ali dijele Redis
        app.state.redis = redis_client
        app.state.queue = QueueService(redis_client)
        # Context i Registry ovdje ne trebaju nužno, ali mogu biti korisni za debug
        
        logger.info("API is ready to accept traffic.")
        yield 
        
    except Exception as e:
        logger.critical("Startup Failed!", error=str(e))
        raise e
        
    finally:
        logger.info("Shutting down API...")
        if redis_client:
            await redis_client.close()
        logger.info("Shutdown complete.")

app = FastAPI(
    title="MobilityOne Fleet AI API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if settings.APP_ENV == "production" else "/docs" # Sakrij docs u produkciji
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}"
    return response

# Rute
app.include_router(webhook.router)

@app.get("/health")
async def health_check():
    """Koristi Docker za provjeru je li servis živ."""
    return {"status": "ok", "env": settings.APP_ENV}