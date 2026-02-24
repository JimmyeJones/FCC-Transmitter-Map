"""Application configuration."""

import logging
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://fcc:fcc@localhost:5432/fcc"
    database_sync_url: str = "postgresql://fcc:fcc@localhost:5432/fcc"

    # FCC Data
    fcc_bulk_url: str = "https://data.fcc.gov/download/pub/uls/complete/"
    fcc_weekly_url: str = "https://data.fcc.gov/download/pub/uls/daily/"
    fcc_data_dir: str = "./data/fcc_downloads"

    # App
    app_title: str = "FCC Radio License Map"
    debug: bool = False
    map_default_lat: float = 39.8283
    map_default_lng: float = -98.5795
    map_default_zoom: int = 5
    map_max_results: int = 5000

    # Cache
    redis_url: str | None = None
    cache_ttl: int = 3600
    
    # Logging
    log_level: str = "INFO"
    
    # Security
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]

    model_config = {"env_prefix": "FCC_", "env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def setup_logging(log_level: str = "INFO"):
    """Configure logging for the application."""
    settings = get_settings()
    
    # Convert string to logging level
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Suppress verbose third-party loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
