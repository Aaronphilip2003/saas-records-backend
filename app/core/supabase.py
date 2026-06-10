from supabase import create_client, Client
from app.core.config import settings

# Service-role client — bypasses RLS, used only server-side
supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)
