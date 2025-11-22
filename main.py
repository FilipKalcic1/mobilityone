from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis
import time
import structlog
from fastapi_limiter import FastAPILimiter

from routers import webhook
from config import get_settings
from logger_config import configure_logger

from services.queue import QueueService
from services.cache import CacheService
from services.context import ContextService
from services.tool_registry import ToolRegistry
from services.openapi_bridge import OpenAPIGateway

configure_logger()
logger = structlog.get_logger("main")
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup & Shutdown logika.
    """
    redis_client = None
    api_gateway = None
    
    try:
        logger.info("Connecting to Redis...")
        redis_client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(redis_client)
        logger.info("Redis connected successfully")
        
        app.state.redis = redis_client
        app.state.queue = QueueService(redis_client)
        app.state.cache = CacheService(redis_client)
        app.state.context = ContextService(redis_client)
        
        logger.info("Loading Tool Registry...")
        # --- BITNO: Prosljeđujemo redis_client ---
        registry = ToolRegistry(redis_client)
        
        try:
            await registry.load_swagger("swagger.json")
        except FileNotFoundError:
            logger.critical("Missing 'swagger.json' file! Cannot start without API definitions.")
            raise

        app.state.tool_registry = registry
        
        if not settings.MOBILITY_API_URL:
             error_msg = "CRITICAL: MOBILITY_API_URL is missing in configuration (.env)!"
             logger.critical(error_msg)
             raise ValueError(error_msg)

        gateway_url = settings.MOBILITY_API_URL
        logger.info("Initializing API Gateway", target_url=gateway_url)

        api_gateway = OpenAPIGateway(base_url=gateway_url)
        app.state.api_gateway = api_gateway
        
        logger.info("System fully operational", loaded_tools=len(registry.tools_names))
        
    except Exception as e:
        logger.critical("Startup failed", error=str(e))
        raise e
    
    yield 
    
    logger.info("Shutting down services...")
    if redis_client:
        await redis_client.close()
    if api_gateway:
        await api_gateway.close()
    logger.info("Shutdown complete.")

app = FastAPI(
    title="MobilityOne Fleet AI",
    description="Enterprise WhatsApp AI Microservice",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

app.include_router(webhook.router)

@app.get("/health")
async def health_check():
    """K8s / Docker healthcheck endpoint"""
    return {
        "status": "ok", 
        "service": "fleet-ai-worker",
        "version": "2.0.0"
    }