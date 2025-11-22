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

# Konfiguracija logiranja i učitavanje postavki
configure_logger()
logger = structlog.get_logger("main")
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Upravlja životnim ciklusom aplikacije (Startup & Shutdown).
    """
    # 1. Startup: Spajanje na Redis
    try:
        redis_client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        
        # Inicijalizacija Rate Limitera
        await FastAPILimiter.init(redis_client)
        logger.info("Redis connected successfully")
        
        # 2. Dependency Injection: Servisi dostupni u cijeloj aplikaciji
        app.state.redis = redis_client
        app.state.queue = QueueService(redis_client)
        app.state.cache = CacheService(redis_client)
        app.state.context = ContextService(redis_client)
        
    except Exception as e:
        logger.critical("Failed to initialize services", error=str(e))
        raise e
    
    yield # Aplikacija radi ovdje
    
    # 3. Shutdown: Čišćenje resursa
    # Provjeravamo postoji li varijabla prije zatvaranja
    if 'redis_client' in locals():
        await redis_client.close()
        logger.info("Redis connection closed")

app = FastAPI(
    title="MobilityOne Fleet AI",
    description="Enterprise WhatsApp AI Microservice",
    version="1.0.0",
    lifespan=lifespan
)

# --- MIDDLEWARE (Lanac obrade) ---

# 1. CORS (Dopušta pristup iz preglednika)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # U produkciji zamijeniti s npr. ["https://dashboard.mobilityone.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. CUSTOM Middleware: Performance Monitoring
# Mjeri koliko vremena serveru treba da obradi svaki zahtjev
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    
    # Prosljeđuje zahtjev dalje (prema routerima)
    response = await call_next(request)
    
    # Računa trajanje
    process_time = time.time() - start_time
    
    # Dodaje informaciju u zaglavlje odgovora (X-Process-Time)
    response.headers["X-Process-Time"] = str(process_time)
    
    return response

# --- RUTE (Poslovna logika) ---
app.include_router(webhook.router)

# --- SYSTEM ENDPOINTS (Infrastruktura) ---

@app.get("/")
async def root():
    """Landing page za ljude (da ne vide 404 grešku)."""
    return {
        "status": "online",
        "service": "MobilityOne AI",
        "docs_url": "/docs",
        "health_url": "/health"
    }

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "whatsapp-webhook"}
