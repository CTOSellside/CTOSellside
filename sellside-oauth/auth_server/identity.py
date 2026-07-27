"""Identidad del usuario final.

El AS no guarda contraseñas: delega la autenticación en Google Sign-In. Ese es
el uso legítimo del cliente OAuth de Google que ya existe en el proyecto —
Google autentica personas, sellside-auth emite los tokens para los MCP.

`DevIdentityProvider` existe para poder correr el flujo completo en local y en
los tests. `config.load_settings()` se niega a activarlo detrás de un issuer
público sin un opt-in explícito.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str


class IdentityError(RuntimeError):
    """El login no se pudo completar o la identidad no está autorizada."""


class GoogleIdentityProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        allowed_domains: tuple[str, ...] = (),
        allowed_emails: tuple[str, ...] = (),
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._allowed_domains = tuple(d.lower() for d in allowed_domains)
        self._allowed_emails = tuple(e.lower() for e in allowed_emails)
        self._http = http_client
        self._jwks: dict[str, jwt.PyJWK] = {}
        self._jwks_fetched_at = 0.0

    @property
    def name(self) -> str:
        return "google"

    def authorization_url(self, state: str, nonce: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    async def complete(self, code: str, nonce: str) -> Identity:
        async with self._client() as http:
            response = await http.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            raise IdentityError(f"Google rechazó el canje del código ({response.status_code})")
        id_token = response.json().get("id_token")
        if not id_token:
            raise IdentityError("Google no devolvió id_token")

        claims = await self._verify_id_token(id_token, nonce)
        email = (claims.get("email") or "").lower()
        if not email or not claims.get("email_verified"):
            raise IdentityError("La cuenta de Google no tiene email verificado")
        self._authorize_email(email)
        return Identity(subject=f"google:{claims['sub']}", email=email)

    def _authorize_email(self, email: str) -> None:
        if not self._allowed_domains and not self._allowed_emails:
            return
        domain = email.rsplit("@", 1)[-1]
        if email in self._allowed_emails or domain in self._allowed_domains:
            return
        raise IdentityError(f"{email} no está autorizado para usar este servidor")

    async def _verify_id_token(self, id_token: str, nonce: str) -> dict:
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        key = await self._signing_key(kid)
        try:
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(f"id_token de Google inválido: {exc}") from exc
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise IdentityError("issuer inesperado en el id_token de Google")
        if nonce and claims.get("nonce") != nonce:
            raise IdentityError("nonce no coincide: posible replay del login")
        return claims

    async def _signing_key(self, kid: str | None) -> jwt.PyJWK:
        if kid and kid in self._jwks and time.time() - self._jwks_fetched_at < 3600:
            return self._jwks[kid]
        async with self._client() as http:
            response = await http.get(GOOGLE_JWKS_URI)
        response.raise_for_status()
        self._jwks = {
            item["kid"]: jwt.PyJWK(item)
            for item in response.json().get("keys", [])
            if item.get("kid")
        }
        self._jwks_fetched_at = time.time()
        if not kid or kid not in self._jwks:
            raise IdentityError("Google firmó con una llave desconocida")
        return self._jwks[kid]

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            # Cliente inyectado (tests): no se cierra al salir del contexto.
            return _NonClosing(self._http)
        return httpx.AsyncClient(timeout=10.0)


class _NonClosing:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_exc) -> bool:
        return False


class DevIdentityProvider:
    """Login de desarrollo: pide un email y lo cree. Nunca en producción."""

    def __init__(self, allowed_domains: tuple[str, ...] = (), allowed_emails: tuple[str, ...] = ()) -> None:
        self._allowed_domains = tuple(d.lower() for d in allowed_domains)
        self._allowed_emails = tuple(e.lower() for e in allowed_emails)

    @property
    def name(self) -> str:
        return "dev"

    def authorization_url(self, state: str, nonce: str) -> str:
        return f"/dev/login?{urlencode({'state': state, 'nonce': nonce})}"

    def identity_for(self, email: str) -> Identity:
        email = (email or "").strip().lower()
        if "@" not in email:
            raise IdentityError("Email inválido")
        if self._allowed_domains or self._allowed_emails:
            domain = email.rsplit("@", 1)[-1]
            if email not in self._allowed_emails and domain not in self._allowed_domains:
                raise IdentityError(f"{email} no está autorizado")
        return Identity(subject=f"dev:{email}", email=email)


def new_nonce() -> str:
    return secrets.token_urlsafe(16)
