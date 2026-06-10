from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    gemini_api_key: str
    storage_bucket: str = "documents"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
