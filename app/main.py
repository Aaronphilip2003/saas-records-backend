from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import documents, health, integrations
from app.core.config import settings

app = FastAPI(title="SaaS Records API", version="0.1.0")

_frontend = settings.frontend_url
_origins = ["http://localhost:3000"]
if _frontend and _frontend not in _origins:
    _origins.append(_frontend)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(documents.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
