"""Sesión del navegador durante el flujo de autorización.

Es una cookie con un JWT firmado por la misma llave del AS. No lleva nada más
que el sujeto autenticado y un identificador de sesión que se usa para derivar
el token CSRF del formulario de consentimiento.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt

from .identity import Identity
from .keys import SigningKey

COOKIE_NAME = "sellside_auth_session"
SESSION_TTL = 3600
SESSION_AUDIENCE = "urn:sellside-auth:session"


class SessionManager:
    def __init__(self, key: SigningKey, issuer: str) -> None:
        self._key = key
        self._issuer = issuer
        # Secreto CSRF derivado del material de firma: estable entre instancias
        # de Cloud Run sin necesidad de otro secreto que administrar.
        self._csrf_secret = hashlib.sha256(
            self._key.public_jwk["n"].encode("ascii") + b"csrf"
        ).digest()

    def issue(self, identity: Identity) -> str:
        issued_at = int(time.time())
        payload = {
            "iss": self._issuer,
            "aud": SESSION_AUDIENCE,
            "sub": identity.subject,
            "email": identity.email,
            "sid": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": issued_at + SESSION_TTL,
        }
        return jwt.encode(payload, self._key.private_key, algorithm="RS256",
                          headers={"kid": self._key.kid})

    def read(self, cookie: str | None) -> dict | None:
        if not cookie:
            return None
        try:
            return jwt.decode(
                cookie,
                self._key.private_key.public_key(),
                algorithms=["RS256"],
                audience=SESSION_AUDIENCE,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "sid"]},
            )
        except jwt.PyJWTError:
            return None

    def csrf_token(self, sid: str, request_id: str) -> str:
        return hmac.new(
            self._csrf_secret, f"{sid}:{request_id}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def check_csrf(self, sid: str, request_id: str, presented: str) -> bool:
        return hmac.compare_digest(self.csrf_token(sid, request_id), presented or "")
