"""Validación de access tokens.

Lo que hace que este archivo importe: la comprobación de `aud`. Sin ella basta
un token válido emitido para *cualquier* otro servicio del mismo AS —o de otro
AS que el atacante controle y consiga que se descubra— para entrar aquí.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt

from .config import ResourceConfig

ALLOWED_ALGORITHMS = ("RS256",)
JWKS_TTL = 3600
JWKS_MIN_REFRESH_INTERVAL = 300  # evita que un `kid` basura provoque un bucle de fetch


class TokenError(Exception):
    """Token ausente, mal formado, expirado o dirigido a otro recurso."""

    def __init__(self, description: str, *, error: str = "invalid_token", status_code: int = 401):
        super().__init__(description)
        self.description = description
        self.error = error
        self.status_code = status_code


class InsufficientScope(TokenError):
    def __init__(self, required: str) -> None:
        super().__init__(
            f"El token no incluye el scope requerido: {required}",
            error="insufficient_scope",
            status_code=403,
        )
        self.required = required


@dataclass(frozen=True)
class AccessToken:
    """Resultado de validar un token.

    Deliberadamente **no** guarda el JWT original. Lo que no se conserva no se
    puede reenviar a Odoo por descuido (prohibición de passthrough de la spec).
    """

    subject: str
    email: str
    client_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    issuer: str = ""
    audience: str = ""
    jti: str = ""
    expires_at: int = 0

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise InsufficientScope(scope)


class _JwksCache:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http
        self._keys: dict[str, jwt.PyJWK] = {}
        self._jwks_uri: str | None = None
        self._issuer: str | None = None
        self._fetched_at = 0.0
        self._last_refresh_attempt = 0.0

    async def key_for(self, kid: str | None, issuer_base: str) -> jwt.PyJWK:
        fresh = time.time() - self._fetched_at < JWKS_TTL
        if kid and fresh and kid in self._keys:
            return self._keys[kid]
        if not fresh or (kid and kid not in self._keys):
            await self._refresh(issuer_base)
        if kid and kid in self._keys:
            return self._keys[kid]
        if not kid and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise TokenError("El token está firmado con una llave desconocida")

    async def _refresh(self, issuer_base: str) -> None:
        if time.time() - self._last_refresh_attempt < JWKS_MIN_REFRESH_INTERVAL and self._keys:
            return
        self._last_refresh_attempt = time.time()
        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            if self._jwks_uri is None or time.time() - self._fetched_at > JWKS_TTL:
                metadata = await client.get(
                    f"{issuer_base}/.well-known/oauth-authorization-server"
                )
                metadata.raise_for_status()
                document = metadata.json()
                if document.get("issuer", "").rstrip("/") != issuer_base.rstrip("/"):
                    raise TokenError(
                        "El issuer publicado por el AS no coincide con el configurado"
                    )
                self._issuer = document["issuer"].rstrip("/")
                self._jwks_uri = document["jwks_uri"]
            response = await client.get(self._jwks_uri)  # type: ignore[arg-type]
            response.raise_for_status()
            self._keys = {
                item["kid"]: jwt.PyJWK(item)
                for item in response.json().get("keys", [])
                if item.get("kid")
            }
            self._fetched_at = time.time()
        finally:
            if self._http is None:
                await client.aclose()


class TokenVerifier:
    def __init__(
        self,
        config: ResourceConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        leeway: int = 30,
    ) -> None:
        self._config = config
        self._leeway = leeway
        self._caches = {
            issuer: _JwksCache(http_client) for issuer in config.authorization_servers
        }

    async def verify(self, raw_token: str) -> AccessToken:
        if not raw_token or raw_token.count(".") != 2:
            raise TokenError("El token no es un JWT")

        try:
            header = jwt.get_unverified_header(raw_token)
            unverified = jwt.decode(raw_token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise TokenError(f"Token mal formado: {exc}") from exc

        if header.get("alg") not in ALLOWED_ALGORITHMS:
            # Cierra `alg: none` y la confusión RS256/HS256 de un tirón.
            raise TokenError(f"Algoritmo de firma no aceptado: {header.get('alg')}")

        issuer = str(unverified.get("iss", "")).rstrip("/")
        if issuer not in self._caches:
            raise TokenError("El token no viene de un servidor de autorización de confianza")

        key = await self._caches[issuer].key_for(header.get("kid"), issuer)
        try:
            claims = jwt.decode(
                raw_token,
                key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=issuer,
                # Comparación exacta contra la URI canónica de este servidor.
                audience=self._config.resource_uri,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("El token expiró") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenError(
                f"El token fue emitido para otro recurso, no para {self._config.resource_uri}"
            ) from exc
        except jwt.PyJWTError as exc:
            raise TokenError(f"Token inválido: {exc}") from exc

        return AccessToken(
            subject=claims["sub"],
            email=claims.get("email", ""),
            client_id=claims.get("client_id", ""),
            scopes=frozenset(str(claims.get("scope", "")).split()),
            issuer=issuer,
            audience=self._config.resource_uri,
            jti=claims.get("jti", ""),
            expires_at=int(claims["exp"]),
        )
