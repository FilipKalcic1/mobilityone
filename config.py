from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Literal, Optional

class Settings(BaseSettings):
    APP_ENV: Literal["development", "production", "testing"] = "development"
    
    # --- INFRASTRUCTURE ---
    REDIS_URL: str

    DATABASE_URL: str
    
    SENTRY_DSN: Optional[str] = None

    # Database Tuning 
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 3600
    
    # --- AI CONFIGURATION ---
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-3.5-turbo"
    
    # --- 3RD PARTY ---
    INFOBIP_BASE_URL: str
    INFOBIP_API_KEY: str
    INFOBIP_SENDER_NUMBER: str
    INFOBIP_SECRET_KEY: str

    # --- MOBILITY ONE API (Service Account) ---
    MOBILITY_API_URL: str 
    # Opcionalno statični token
    MOBILITY_API_TOKEN: Optional[str] = None
    
    # Auth (OAuth2 Client Credentials)
    MOBILITY_AUTH_URL: Optional[str] = None
    MOBILITY_CLIENT_ID: Optional[str] = None
    MOBILITY_CLIENT_SECRET: Optional[str] = None
    MOBILITY_SCOPE: str = "add-case"
    
    # Tools
    SWAGGER_URL: Optional[str] = None
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings() -> Settings:
    return Settings()