"""
Configuration settings for the MCP Media Server.
"""
import os
import secrets
from pathlib import Path
from typing import List, Optional
from functools import lru_cache
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

class Settings(BaseSettings):
    """Settings for the MCP Media Server."""
    
    # Server Configuration
    MCP_SERVER_HOST: str = Field("127.0.0.1", description="Host address for the server")
    MCP_SERVER_PORT: int = Field(9000, description="Port for the server")
    DEBUG: bool = Field(False, description="Debug mode")
    
    # API Keys
    SUPABASE_URL: str = Field(..., description="Supabase URL")
    SUPABASE_KEY: str = Field(..., description="Supabase key")
    PINECONE_API_KEY: str = Field(..., description="Pinecone API key")
    OPENAI_API_KEY: str = Field(..., description="OpenAI API key")
    
    # FFmpeg Configuration
    FFMPEG_THREADS: int = Field(4, description="Number of threads for FFmpeg")
    FFMPEG_PRESET: str = Field("medium", description="FFmpeg preset")
    
    # Storage Configuration
    DOWNLOAD_DIR: str = Field("downloads", description="Download directory")
    PROCESSED_DIR: str = Field("processed", description="Processed directory")
    THUMBNAILS_DIR: str = Field("thumbnails", description="Thumbnails directory")
    CACHE_DIR: str = Field("cache", description="Cache directory")
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = Field(True, description="Enable rate limiting")
    RATE_LIMIT_REQUESTS: int = Field(60, description="Number of requests allowed in the period")
    RATE_LIMIT_PERIOD: int = Field(60, description="Rate limit period in seconds")
    
    # Webhook Configuration
    WEBHOOK_ENABLED: bool = Field(True, description="Enable webhooks")
    WEBHOOK_ENDPOINTS: str = Field(
        "http://localhost:8000/webhook/complete", 
        description="Comma-separated list of webhook endpoints"
    )
    
    # Database Configuration
    DB_CONNECTION_POOL: int = Field(5, description="Database connection pool size")
    DB_MAX_OVERFLOW: int = Field(10, description="Database connection overflow")
    
    # Scheduled Tasks
    SCHEDULED_TASKS_ENABLED: bool = Field(True, description="Enable scheduled tasks")
    TASK_CLEANUP_INTERVAL: int = Field(86400, description="Task cleanup interval in seconds")
    
    # Security
    JWT_SECRET: str = Field(..., description="JWT secret key")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, description="Access token expiration time in minutes")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    @validator("WEBHOOK_ENDPOINTS")
    def parse_webhook_endpoints(cls, v: str) -> List[str]:
        """Parse comma-separated webhook endpoints into a list."""
        return [endpoint.strip() for endpoint in v.split(",") if endpoint.strip()]
    
    @validator("JWT_SECRET")
    def validate_jwt_secret(cls, v: str) -> str:
        """Validate JWT secret and generate one if it's the default."""
        if v == "generate_a_secure_random_key_and_replace_this":
            return secrets.token_hex(32)
        return v
    
    def get_absolute_path(self, directory: str) -> Path:
        """Get absolute path for a directory."""
        base_path = Path(__file__).resolve().parent.parent.parent
        return base_path / directory


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    try:
        return Settings()
    except Exception as e:
        # If settings can't be loaded, provide a minimal default configuration
        print(f"Error loading settings: {e}")
        print("Using minimal default configuration")
        return Settings(
            SUPABASE_URL="not_configured",
            SUPABASE_KEY="not_configured",
            PINECONE_API_KEY="not_configured",
            OPENAI_API_KEY="not_configured",
            JWT_SECRET=secrets.token_hex(32)
        )
