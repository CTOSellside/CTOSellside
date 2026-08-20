"""Middleware ASGI que exige Bearer y publica los metadatos del recurso.

Es ASGI puro y no `BaseHTTPMiddleware` a propósito: el transporte de MCP es
Streamable HTTP y `BaseHTTPMiddleware` bufferiza las respuestas en streaming.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable

from .config import ResourceConfig
from .verifier import AccessToken, TokenError, TokenVerifier

logger = logging.getLogger("mcp_resource_server")

Send = Callable[[dict], Awaitable[None]]


def protected_resource_metadata(config: ResourceConfig) -> dict:
    """Documento RFC 9728. Es lo que lee Claude para saber a qué AS ir."""

    document = {
        "resource": config.resource_uri,
        "authorization_servers": list(config.authorization_servers),
        "bearer_methods_supported": ["header"],
        "resource_name": config.resource_name,
    }
    if config.scopes_supported:
        document["scopes_supported"] = list(config.scopes_supported)
    if config.documentation:
        document["resource_documentation"] = config.documentation
    return document


def www_authenticate(config: ResourceConfig, error: str | None = None, description: str = "") -> str:
    parts = [f'Bearer realm="{_quote(config.resource_name)}"']
    if error:
        parts.append(f'error="{_quote(error)}"')
        if description:
            parts.append(f'error_description="{_quote(description)}"')
    # Sin este parámetro el cliente no sabe dónde autenticarse: es lo que
    # convierte un 401 en el punto de partida del flujo OAuth.
    parts.append(f'resource_metadata="{config.metadata_url}"')
    return ", ".join(parts)


def _quote(value: str) -> str:
    """Deja el valor en ASCII imprimible, apto para una cabecera HTTP.

    RFC 7230 limita los valores de cabecera a ASCII. Google Frontend no se
    limita a recortar el carácter ofensivo: descarta la cabecera **entera**, y
    con ella se va `resource_metadata` — el cliente recibe un 401 que no le dice
    dónde autenticarse y el flujo OAuth no arranca nunca. Como los mensajes de
    error de esta librería están en español, esto no es hipotético.

    Se translitera («expiró» → «expiro») en vez de descartar, para no dejar
    palabras mutiladas en el mensaje.
    """

    plano = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    limpio = plano.replace("\\", "").replace('"', "'")
    return " ".join(limpio.split())


class BearerAuthMiddleware:
    def __init__(
        self,
        app,
        config: ResourceConfig,
        verifier: TokenVerifier | None = None,
        *,
        serve_metadata: bool = True,
    ) -> None:
        self.app = app
        self.config = config
        self.verifier = verifier or TokenVerifier(config)
        self.serve_metadata = serve_metadata
        self._metadata_paths = {
            config.metadata_path,
            "/.well-known/oauth-protected-resource",
        }

    async def __call__(self, scope: dict, receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self.serve_metadata and path in self._metadata_paths:
            await _json_response(
                send,
                200,
                protected_resource_metadata(self.config),
                headers={"cache-control": "public, max-age=3600"},
            )
            return

        if not self.config.is_protected(path):
            await self.app(scope, receive, send)
            return

        raw = _bearer_token(scope)
        if raw is None:
            await self._challenge(send, 401, None, "")
            return

        try:
            token = await self.verifier.verify(raw)
        except TokenError as exc:
            logger.info("token rechazado en %s: %s", path, exc.description)
            await self._challenge(send, exc.status_code, exc.error, exc.description)
            return

        logger.info(
            "acceso concedido · sub=%s client=%s scopes=%s path=%s",
            token.subject, token.client_id, sorted(token.scopes), path,
        )
        # El token queda disponible para los handlers. El JWT crudo no se
        # propaga: no existe fuera de esta función.
        scope.setdefault("state", {})["access_token"] = token
        await self.app(scope, receive, send)

    async def _challenge(self, send: Send, status: int, error: str | None, description: str) -> None:
        body: dict[str, str] = {}
        if error:
            body = {"error": error, "error_description": description}
        else:
            body = {
                "error": "unauthorized",
                "error_description": "Se requiere un access token Bearer",
            }
        await _json_response(
            send,
            status,
            body,
            headers={
                "www-authenticate": www_authenticate(self.config, error, description),
                "cache-control": "no-store",
            },
        )


def _bearer_token(scope: dict) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.lower().startswith("bearer "):
                return decoded[7:].strip()
            return None
    return None


async def _json_response(send: Send, status: int, body: dict, headers: dict[str, str]) -> None:
    payload = json.dumps(body).encode("utf-8")
    raw_headers = [(b"content-type", b"application/json")]
    raw_headers += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    raw_headers.append((b"content-length", str(len(payload)).encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": payload})


def current_token(request) -> AccessToken:
    """Token validado de la petición en curso (Starlette/FastAPI)."""

    token = getattr(request.state, "access_token", None)
    if token is None:
        raise TokenError("La ruta no está protegida por BearerAuthMiddleware")
    return token
