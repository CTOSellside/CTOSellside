"""Registro dinámico de clientes (RFC 7591).

Sin este endpoint hay que pegar client_id y client_secret a mano en los ajustes
avanzados del conector de Claude. Con él, Claude se registra solo la primera vez
que alguien añade el MCP.

El endpoint es público —así lo requiere el flujo— así que lleva dos frenos:
la lista de hosts permitidos para redirect_uris y un límite por IP.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import ctx
from ..errors import InvalidRequest, OAuthError
from ..models import Client, hash_secret, now, random_token
from ..redirects import RedirectUriError, validate_registration_redirect_uri

router = APIRouter()

SUPPORTED_AUTH_METHODS = {"none", "client_secret_basic", "client_secret_post"}
SUPPORTED_GRANT_TYPES = {"authorization_code", "refresh_token"}
MAX_REDIRECT_URIS = 10


@router.post("/register", status_code=201)
async def register(request: Request) -> JSONResponse:
    context = ctx(request)
    settings = context.settings

    client_ip = (request.client.host if request.client else "unknown")
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    if context.registration_limiter.hit(client_ip, settings.registration_rate_limit):
        raise OAuthError(
            "temporarily_unavailable",
            "Demasiados registros desde esta dirección; reintenta más tarde",
            status_code=429,
        )

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - cuerpo no-JSON
        raise InvalidRequest("El cuerpo debe ser JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidRequest("El cuerpo debe ser un objeto JSON")

    redirect_uris = payload.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise _metadata_error("redirect_uris es obligatorio y debe ser una lista no vacía")
    if len(redirect_uris) > MAX_REDIRECT_URIS:
        raise _metadata_error(f"máximo {MAX_REDIRECT_URIS} redirect_uris")
    if not all(isinstance(uri, str) for uri in redirect_uris):
        raise _metadata_error("redirect_uris debe contener solo strings")

    try:
        redirect_uris = [
            validate_registration_redirect_uri(uri, settings.allowed_redirect_hosts)
            for uri in redirect_uris
        ]
    except RedirectUriError as exc:
        raise _metadata_error(str(exc)) from exc

    grant_types = payload.get("grant_types") or ["authorization_code", "refresh_token"]
    if not set(grant_types) <= SUPPORTED_GRANT_TYPES:
        raise _metadata_error(
            f"grant_types soportados: {sorted(SUPPORTED_GRANT_TYPES)}"
        )
    if "authorization_code" not in grant_types:
        raise _metadata_error("authorization_code es obligatorio")

    response_types = payload.get("response_types") or ["code"]
    if set(response_types) != {"code"}:
        raise _metadata_error("response_types solo soporta ['code']")

    auth_method = payload.get("token_endpoint_auth_method", "none")
    if auth_method not in SUPPORTED_AUTH_METHODS:
        raise _metadata_error(
            f"token_endpoint_auth_method soportados: {sorted(SUPPORTED_AUTH_METHODS)}"
        )

    requested_scope = payload.get("scope") or " ".join(settings.scopes_supported)
    unknown = set(requested_scope.split()) - set(settings.scopes_supported)
    if unknown:
        raise _metadata_error(f"scopes desconocidos: {sorted(unknown)}")

    client_name = str(payload.get("client_name") or "")[:100]

    client_id = f"c_{random_token(18)}"
    client_secret: str | None = None
    secret_hash: str | None = None
    if auth_method != "none":
        client_secret = secrets.token_urlsafe(32)
        secret_hash = hash_secret(client_secret)

    client = Client(
        client_id=client_id,
        redirect_uris=list(redirect_uris),
        client_name=client_name,
        client_secret_hash=secret_hash,
        token_endpoint_auth_method=auth_method,
        grant_types=list(grant_types),
        response_types=["code"],
        scope=requested_scope,
        created_at=now(),
    )
    await context.storage.create_client(client)

    body = {
        "client_id": client_id,
        "client_id_issued_at": client.created_at,
        "client_name": client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
        "response_types": client.response_types,
        "token_endpoint_auth_method": auth_method,
        "scope": requested_scope,
    }
    if client_secret:
        body["client_secret"] = client_secret
        body["client_secret_expires_at"] = 0  # no expira
    return JSONResponse(body, status_code=201, headers={"Cache-Control": "no-store"})


def _metadata_error(description: str) -> OAuthError:
    # RFC 7591 §3.2.2 define su propio código para metadatos inválidos.
    return OAuthError("invalid_client_metadata", description, status_code=400)
