from fastapi import FastAPI
from contextlib import asynccontextmanager
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter
from routers import webhook
from config import get_settings
from logger_config import configure_logger
from services.queue import QueueService
from services.cache import CacheService
from services.context import ContextService


configure_logger()
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    redis_client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    await FastAPILimiter.init(redis_client)
    
    # Inject services into app state
    app.state.redis = redis_client
    app.state.queue = QueueService(redis_client)
    app.state.cache = CacheService(redis_client)
    app.state.context = ContextService(redis_client)
    
    yield
    
    # Shutdown
    await redis_client.close()

app = FastAPI(lifespan=lifespan)
app.include_router(webhook.router)