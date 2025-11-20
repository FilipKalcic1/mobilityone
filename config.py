from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    # Promijenili smo ENV u APP_ENV jer docker šalje 'app_env'
    APP_ENV: str = "production"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-3.5-turbo"
    AI_CONFIDENCE_THRESHOLD: float = 0.85

    # Infobip
    INFOBIP_BASE_URL: str
    INFOBIP_API_KEY: str
    INFOBIP_SENDER_NUMBER: str
    INFOBIP_SECRET_KEY: str

    # --- KONFIGURACIJA ---
    # 'extra="ignore"' je ključan dio koji sprječava rušenje
    # ako u .env imate stare varijable (sms_sender_id, itd.)
    model_config = SettingsConfigDict(
        env_file=".env", 
        extra="ignore" 
    )

@lru_cache()
def get_settings() -> Settings:
    return Settings()