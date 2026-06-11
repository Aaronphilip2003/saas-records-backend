import hashlib
import hmac as hmac_lib
import json
import logging
import time
import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.supabase import supabase
from app.services.google_sheets import create_invoice_spreadsheet, sync_invoices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _make_flow() -> Flow:
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_redirect_uri],
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )


def _make_state(user_id: str) -> str:
    payload = json.dumps({"uid": user_id, "ts": int(time.time())})
    sig = hmac_lib.new(settings.state_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def _verify_state(state: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(state + "==").decode()
        payload, sig = decoded.rsplit("|", 1)
        expected = hmac_lib.new(settings.state_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac_lib.compare_digest(sig, expected):
            raise ValueError("signature mismatch")
        data = json.loads(payload)
        if time.time() - data["ts"] > 600:
            raise ValueError("state expired")
        return data["uid"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid OAuth state: {e}")


@router.get("/google/connect")
async def google_connect(user: dict = Depends(get_current_user)):
    """Returns the Google OAuth URL. Frontend redirects the user there."""
    flow = _make_flow()
    state = _make_state(user["id"])
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return {"auth_url": auth_url}


@router.get("/google/callback")
async def google_callback(code: str, state: str):
    """Google redirects here after user grants permission."""
    user_id = _verify_state(state)

    flow = _make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    try:
        ss_id, ss_url = create_invoice_spreadsheet(creds)
    except Exception as e:
        logger.error("Sheet creation failed for user %s: %s", user_id, e)
        return RedirectResponse(f"{settings.frontend_url}/app/integrations?error=sheet_creation_failed")

    supabase.table("google_integrations").upsert({
        "user_id": user_id,
        "spreadsheet_id": ss_id,
        "spreadsheet_url": ss_url,
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "last_synced_at": None,
    }, on_conflict="user_id").execute()

    return RedirectResponse(f"{settings.frontend_url}/app/integrations?connected=true")


@router.get("/google/status")
async def google_status(user: dict = Depends(get_current_user)):
    result = (
        supabase.table("google_integrations")
        .select("spreadsheet_id, spreadsheet_url, connected_at, last_synced_at")
        .eq("user_id", user["id"])
        .execute()
    )
    if not result.data:
        return {"connected": False}
    row = result.data[0]
    return {
        "connected": True,
        "spreadsheet_id": row["spreadsheet_id"],
        "spreadsheet_url": row["spreadsheet_url"],
        "connected_at": row["connected_at"],
        "last_synced_at": row["last_synced_at"],
    }


@router.post("/google/sync")
async def google_sync(user: dict = Depends(get_current_user)):
    try:
        return sync_invoices(user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Sync failed for user %s: %s", user["id"], e)
        raise HTTPException(status_code=500, detail="Sync failed")


@router.delete("/google/disconnect")
async def google_disconnect(user: dict = Depends(get_current_user)):
    supabase.table("google_integrations").delete().eq("user_id", user["id"]).execute()
    return {"message": "Disconnected"}
