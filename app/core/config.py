from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    groq_api_key: str
    mistral_api_key: str
    storage_bucket: str = "documents"
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/integrations/google/callback"
    frontend_url: str = "http://localhost:3000"
    state_secret: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
