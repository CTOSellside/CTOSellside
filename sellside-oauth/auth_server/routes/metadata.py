"""Metadatos del servidor de autorización (RFC 8414) y JWKS."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import ctx

router = APIRouter()

# Los metadatos cambian solo con un despliegue; una hora de caché es segura y
# evita que cada arranque de conector golpee el servicio.
_CACHE = {"Cache-Control": "public, max-age=3600"}


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> JSONResponse:
    settings = ctx(request).settings
    return JSONResponse(
        {
            "issuer": settings.issuer,
            "authorization_endpoint": settings.authorization_endpoint,
            "token_endpoint": settings.token_endpoint,
            "registration_endpoint": settings.registration_endpoint,
            "revocation_endpoint": settings.revocation_endpoint,
            "jwks_uri": settings.jwks_uri,
            "scopes_supported": list(settings.scopes_supported),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_basic",
                "client_secret_post",
            ],
            "revocation_endpoint_auth_methods_supported": [
                "none",
                "client_secret_basic",
                "client_secret_post",
            ],
            # OAuth 2.1: `plain` no existe.
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
            # RFC 9207: la respuesta de /authorize incluye `iss`.
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": "https://github.com/CTOSellside/CTOSellside/tree/main/sellside-oauth",
        },
        headers=_CACHE,
    )


@router.get("/.well-known/jwks.json")
async def jwks(request: Request) -> JSONResponse:
    return JSONResponse(ctx(request).key.jwks(), headers=_CACHE)


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    context = ctx(request)
    return JSONResponse(
        {
            "status": "ok",
            "issuer": context.settings.issuer,
            "idp": context.idp.name,
            "storage": context.settings.storage_backend,
            "resources": list(context.settings.protected_resources),
        }
    )
