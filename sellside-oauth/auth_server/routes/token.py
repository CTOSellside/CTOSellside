"""Endpoint /token: canje de código y rotación de refresh tokens.

Dos invariantes que valen más que el resto del archivo:

1. El `aud` del access token es la URI canónica del MCP para el que se pidió.
   Un token emitido para `odoo-mcp-sellside` no sirve en `twilio-mcp`, aunque lo
   firme la misma llave.
2. Códigos y refresh tokens son de un solo uso. Reutilizar cualquiera de los dos
   revoca toda la cadena: es la señal de que alguien copió un token.
"""

from __future__ import annotations

import base64
import secrets
from urllib.parse import unquote

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import canonical_resource_uri
from ..context import AppContext, ctx
from ..errors import (
    InvalidClient,
    InvalidGrant,
    InvalidRequest,
    InvalidScope,
    InvalidTarget,
    UnsupportedGrantType,
)
from ..m2m import (
    ASSERTION_REPLAY_TTL_SECONDS,
    JWT_BEARER_GRANT,
    AssertionError_,
    assertion_replay_id,
)
from ..models import Client, RefreshToken, hash_secret, now, random_token
from ..pkce import verify_s256

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.post("/token")
async def token(request: Request) -> JSONResponse:
    context = ctx(request)
    form = dict(await request.form())
    grant_type = str(form.get("grant_type") or "")

    # RFC 7523: en el grant jwt-bearer la assertion ES la autenticación del
    # cliente (no hay client_id/secret registrado por /register).
    if grant_type == JWT_BEARER_GRANT:
        body = await _jwt_bearer_grant(context, form)
        return JSONResponse(body, headers=NO_STORE)

    client = await _authenticate_client(context, request, form)

    if grant_type == "authorization_code":
        body = await _authorization_code_grant(context, client, form)
    elif grant_type == "refresh_token":
        if "refresh_token" not in client.grant_types:
            raise UnsupportedGrantType(grant_type)
        body = await _refresh_token_grant(context, client, form)
    else:
        raise UnsupportedGrantType(grant_type or "(vacío)")

    return JSONResponse(body, headers=NO_STORE)


async def _jwt_bearer_grant(context: AppContext, form: dict) -> dict:
    """Grant M2M para agentes (contrato: Odoo Knowledge art. 122).

    Emite SOLO access token, sin refresh: un cliente M2M puede presentar una
    assertion nueva cuando quiera, y un refresh token sería un secreto de
    larga vida que este diseño existe para evitar.
    """

    assertion = str(form.get("assertion") or "")
    if not assertion:
        raise InvalidRequest("Falta assertion")

    verifier = context.m2m_verifier
    if verifier is None:
        raise UnsupportedGrantType(JWT_BEARER_GRANT)

    try:
        claims = await verifier.verify(assertion)
    except AssertionError_ as exc:
        raise InvalidGrant(str(exc)) from exc

    sa_email = str(claims["email"]).lower()
    m2m = await context.storage.get_m2m_client(sa_email)
    if m2m is None:
        raise InvalidClient(f"{sa_email} no está registrado como cliente M2M")
    if not m2m.is_active:
        raise InvalidClient(f"El cliente M2M {sa_email} está {m2m.status}")

    # Anti-replay write-once (condición CISO): una assertion, un solo canje.
    replay_id = assertion_replay_id(assertion, claims)
    fresh = await context.storage.register_assertion(
        replay_id, now() + ASSERTION_REPLAY_TTL_SECONDS
    )
    if not fresh:
        raise InvalidGrant("Assertion ya canjeada: posible replay")

    # PoLP: lo pedido debe caber en lo registrado; lo emitido, además, en lo
    # que el servidor soporta hoy. Sin `scope` explícito se emite el registro.
    registered = set(m2m.allowed_scopes)
    requested = set(str(form.get("scope") or "").split())
    if requested and not requested <= registered:
        raise InvalidScope("El cliente M2M pidió scopes fuera de su registro")
    granted = (requested or registered) & set(context.settings.scopes_supported)
    if not granted:
        raise InvalidScope("Ningún scope habilitado para este cliente M2M")
    scope = " ".join(sorted(granted))

    # Audiencia: exacta y registrada. Con una sola audiencia registrada el
    # `resource` es opcional; con varias, obligatorio.
    audiences = {canonical_resource_uri(item) for item in m2m.allowed_audiences}
    raw_resource = form.get("resource")
    if raw_resource:
        resource = _requested_resource(form, default="")
    elif len(audiences) == 1:
        resource = next(iter(audiences))
    else:
        raise InvalidTarget(
            "Falta `resource` y el cliente M2M tiene varias audiencias registradas"
        )
    if resource not in audiences:
        raise InvalidTarget("El `resource` no está entre las audiencias registradas del cliente")
    if not context.settings.is_known_resource(resource):
        raise InvalidTarget("El `resource` no está protegido por este AS")

    access_token = _encode_access_token(
        context,
        client_id=sa_email,
        subject=f"google-sa:{claims['sub']}",
        email=sa_email,
        resource=resource,
        scope=scope,
        session_id=f"m2m:{sa_email}",
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": context.settings.access_token_ttl,
        "scope": scope,
    }


async def _authorization_code_grant(context: AppContext, client: Client, form: dict) -> dict:
    code = str(form.get("code") or "")
    if not code:
        raise InvalidRequest("Falta code")

    stored, replayed = await context.storage.consume_authorization_code(hash_secret(code))
    if stored is None:
        raise InvalidGrant("Código desconocido o expirado")
    if replayed:
        # OAuth 2.1 §4.1.3.1: un código reutilizado invalida lo que ya salió de él.
        await context.storage.revoke_session(stored.session_id)
        raise InvalidGrant("Código ya utilizado; se revocaron los tokens asociados")
    if stored.expires_at < now():
        raise InvalidGrant("Código expirado")
    if not secrets.compare_digest(stored.client_id, client.client_id):
        raise InvalidGrant("El código fue emitido para otro cliente")

    redirect_uri = str(form.get("redirect_uri") or "")
    if not redirect_uri or not secrets.compare_digest(redirect_uri, stored.redirect_uri):
        raise InvalidGrant("redirect_uri no coincide con el de la autorización")

    verifier = str(form.get("code_verifier") or "")
    if not verify_s256(verifier, stored.code_challenge):
        raise InvalidGrant("code_verifier no valida contra el code_challenge")

    resource = _requested_resource(form, default=stored.resource)
    if resource != stored.resource:
        raise InvalidTarget("El `resource` no coincide con el de la autorización")

    return await _issue_tokens(
        context,
        client=client,
        resource=stored.resource,
        scope=stored.scope,
        subject=stored.subject,
        subject_email=stored.subject_email,
        session_id=stored.session_id,
        family_id=random_token(16),
    )


async def _refresh_token_grant(context: AppContext, client: Client, form: dict) -> dict:
    presented = str(form.get("refresh_token") or "")
    if not presented:
        raise InvalidRequest("Falta refresh_token")

    stored, replayed = await context.storage.consume_refresh_token(hash_secret(presented))
    if stored is None:
        raise InvalidGrant("Refresh token desconocido")
    if replayed:
        revoked = await context.storage.revoke_family(stored.family_id)
        raise InvalidGrant(
            f"Refresh token reutilizado; se revocó la cadena completa ({revoked} tokens)"
        )
    if stored.expires_at < now():
        raise InvalidGrant("Refresh token expirado")
    if not secrets.compare_digest(stored.client_id, client.client_id):
        raise InvalidGrant("El refresh token fue emitido para otro cliente")

    # Un scope retirado de la configuración deja de emitirse aunque el refresh
    # token lo llevara. Sin esto, reducir SCOPES_SUPPORTED no surte efecto hasta
    # que caduquen todos los refresh vivos —treinta días por defecto—, que es
    # justo lo contrario de lo que espera quien acaba de recortar un permiso.
    concedidos = set(stored.scope.split()) & set(context.settings.scopes_supported)
    if not concedidos:
        raise InvalidScope(
            "Ninguno de los scopes de este token sigue habilitado en el servidor; "
            "hay que volver a autorizar"
        )

    requested_scope = form.get("scope")
    if requested_scope:
        requested = set(str(requested_scope).split())
        if not requested <= set(stored.scope.split()):
            raise InvalidScope("Un refresh solo puede reducir el scope, nunca ampliarlo")
        concedidos &= requested
        if not concedidos:
            raise InvalidScope("Los scopes pedidos ya no están habilitados en el servidor")

    scope = " ".join(sorted(concedidos))

    resource = _requested_resource(form, default=stored.resource)
    if resource != stored.resource:
        raise InvalidTarget("El `resource` no coincide con el del token original")

    return await _issue_tokens(
        context,
        client=client,
        resource=stored.resource,
        scope=scope,
        subject=stored.subject,
        subject_email=stored.subject_email,
        session_id=stored.session_id,
        family_id=stored.family_id,
        # La cadena no se renueva indefinidamente: conserva el vencimiento original.
        refresh_expires_at=stored.expires_at,
    )


async def _issue_tokens(
    context: AppContext,
    *,
    client: Client,
    resource: str,
    scope: str,
    subject: str,
    subject_email: str,
    session_id: str,
    family_id: str,
    refresh_expires_at: int | None = None,
) -> dict:
    settings = context.settings
    issued_at = now()
    access_token = _encode_access_token(
        context,
        client_id=client.client_id,
        subject=subject,
        email=subject_email,
        resource=resource,
        scope=scope,
        session_id=session_id,
    )

    refresh_token = random_token(32)
    await context.storage.save_refresh_token(
        RefreshToken(
            token_hash=hash_secret(refresh_token),
            family_id=family_id,
            client_id=client.client_id,
            resource=resource,
            scope=scope,
            subject=subject,
            subject_email=subject_email,
            expires_at=refresh_expires_at or (issued_at + settings.refresh_token_ttl),
            session_id=session_id,
        )
    )

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl,
        "refresh_token": refresh_token,
        "scope": scope,
    }


def _encode_access_token(
    context: AppContext,
    *,
    client_id: str,
    subject: str,
    email: str,
    resource: str,
    scope: str,
    session_id: str,
) -> str:
    settings = context.settings
    issued_at = now()
    return jwt.encode(
        {
            "iss": settings.issuer,
            "sub": subject,
            "aud": resource,
            "client_id": client_id,
            "scope": scope,
            "email": email,
            # `sid` permite revocar por sesión cuando lo único que hay a mano es
            # un access token (ver /revoke).
            "sid": session_id,
            "jti": random_token(16),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + settings.access_token_ttl,
        },
        context.key.private_key,
        algorithm="RS256",
        headers={"kid": context.key.kid, "typ": "at+jwt"},
    )


def _requested_resource(form: dict, *, default: str) -> str:
    raw = form.get("resource")
    if not raw:
        return default
    try:
        return canonical_resource_uri(str(raw))
    except ValueError as exc:
        raise InvalidTarget(str(exc)) from exc


async def _authenticate_client(context: AppContext, request: Request, form: dict) -> Client:
    """Autentica al cliente: Basic, secret en el cuerpo, o público con PKCE."""

    basic_id, basic_secret = _parse_basic_auth(request.headers.get("authorization"))
    client_id = basic_id or str(form.get("client_id") or "")
    if not client_id:
        raise InvalidClient("Falta client_id")

    client = await context.storage.get_client(client_id)
    if client is None:
        raise InvalidClient("client_id desconocido")

    if client.is_public:
        # Cliente público: no hay secreto que verificar. Lo que ata el token a
        # quien inició el flujo es PKCE, que se verifica más abajo.
        if basic_secret or form.get("client_secret"):
            raise InvalidClient("Este cliente se registró como público: no debe enviar secreto")
        return client

    presented = basic_secret or str(form.get("client_secret") or "")
    if not presented or not client.client_secret_hash:
        raise InvalidClient("Falta client_secret")
    if not secrets.compare_digest(hash_secret(presented), client.client_secret_hash):
        raise InvalidClient("client_secret inválido")
    return client


def _parse_basic_auth(header: str | None) -> tuple[str | None, str | None]:
    if not header or not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception:  # noqa: BLE001 - header mal formado
        raise InvalidClient("Cabecera Basic mal formada") from None
    if ":" not in decoded:
        raise InvalidClient("Cabecera Basic mal formada")
    client_id, secret = decoded.split(":", 1)
    # RFC 6749 §2.3.1: los valores van percent-encoded dentro del Basic.
    return unquote(client_id), unquote(secret)
