import asyncio
from database import engine, Base
from models import UserMapping 
import structlog

logger = structlog.get_logger("db_init")

async def init_models():
    logger.info("Creating database tables...")
    async with engine.begin() as conn:
        # Ovo kreira tablice ako ne postoje
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tables created successfully.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_models())