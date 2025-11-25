# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Literal, Optional

class Settings(BaseSettings):
    APP_ENV: Literal["development", "production", "testing"] = "development"
    REDIS_URL: str
    SWAGGER_URL: Optional[str] = None
    
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-3.5-turbo"
    AI_CONFIDENCE_THRESHOLD: float = 0.85

    INFOBIP_BASE_URL: str
    INFOBIP_API_KEY: str
    INFOBIP_SENDER_NUMBER: str
    INFOBIP_SECRET_KEY: str

    MOBILITY_API_URL: str 
    MOBILITY_API_TOKEN: Optional[str] = None

    # --- NOVO: OVO MORAŠ DODATI DA BI RADIO LOGIN ---
    MOBILITY_AUTH_URL: Optional[str] = None 
    MOBILITY_CLIENT_ID: Optional[str] = None
    MOBILITY_CLIENT_SECRET: Optional[str] = None
    MOBILITY_SCOPE: str = "add-case"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings() -> Settings:
    return Settings()