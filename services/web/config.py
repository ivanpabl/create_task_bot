from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    postgres_dsn: str = ''
    discord_client_id: str = ''
    discord_client_secret: str = ''
    discord_redirect_uri: str = 'http://localhost:8000/auth/callback'
    backend_url: str = 'http://localhost:8000'
    jwt_secret: str = 'change-me-in-production'
    backend_api_key: str = 'change-me-api-key'
    frontend_url: str = 'http://localhost:5173'
    ollama_url: str = 'http://localhost:11434'
    ollama_model: str = 'llama3'
    alert_webhook_url: str = ''
    alert_level: str = 'error'
    clickhouse_url: str = ''
    clickhouse_user: str = 'default'
    clickhouse_password: str = ''
    clickhouse_database: str = 'default'

    model_config = {'env_file': str(Path(__file__).resolve().parents[2] / '.env'), 'extra': 'ignore'}


settings = Settings()
