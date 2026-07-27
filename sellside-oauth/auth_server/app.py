"""Construcción de la aplicación FastAPI de sellside-auth."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import Settings, load_settings
from .context import AppContext
from .errors import OAuthError, oauth_error_handler
from .identity import DevIdentityProvider, GoogleIdentityProvider
from .keys import SigningKey
from .routes import authorize, metadata, register, revoke, token
from .sessions import SessionManager
from .storage import Storage, build_storage

logger = logging.getLogger("sellside_auth")


def build_identity_provider(settings: Settings):
    if settings.idp_mode == "google":
        return GoogleIdentityProvider(
            client_id=settings.google_client_id or "",
            client_secret=settings.google_client_secret or "",
            redirect_uri=settings.google_redirect_uri,
            allowed_domains=settings.allowed_email_domains,
            allowed_emails=settings.allowed_emails,
        )
    logger.warning(
        "IDP_MODE=dev: el login no verifica identidades. No usar en producción."
    )
    return DevIdentityProvider(
        allowed_domains=settings.allowed_email_domains,
        allowed_emails=settings.allowed_emails,
    )


def create_app(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    identity_provider=None,
) -> FastAPI:
    settings = settings or load_settings()
    key = SigningKey(settings.signing_key_pem)

    app = FastAPI(
        title="sellside-auth",
        description="Servidor de autorización OAuth 2.1 para los MCP de Sellside",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = AppContext(
        settings=settings,
        key=key,
        storage=storage or build_storage(settings),
        sessions=SessionManager(key, settings.issuer),
        idp=identity_provider or build_identity_provider(settings),
    )

    app.add_exception_handler(OAuthError, oauth_error_handler)
    app.include_router(metadata.router)
    app.include_router(register.router)
    app.include_router(authorize.router)
    app.include_router(token.router)
    app.include_router(revoke.router)

    logger.info(
        "sellside-auth listo · issuer=%s · idp=%s · recursos=%s",
        settings.issuer,
        app.state.ctx.idp.name,
        ", ".join(settings.protected_resources),
    )
    return app
