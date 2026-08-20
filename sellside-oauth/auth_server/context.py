"""Estado compartido del proceso: settings, llave, storage y proveedor de identidad."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request

from .config import Settings
from .identity import DevIdentityProvider, GoogleIdentityProvider
from .keys import SigningKey
from .m2m import GoogleAssertionVerifier
from .ratelimit import RegistrationRateLimiter
from .sessions import SessionManager
from .storage import Storage

SCOPE_DESCRIPTIONS = {
    "odoo:read": "leer registros de Odoo (búsquedas y lecturas)",
    "odoo:write": "crear y modificar registros de Odoo",
    "offline_access": "mantener la sesión abierta sin volver a pedir permiso",
}


@dataclass
class AppContext:
    settings: Settings
    key: SigningKey
    storage: Storage
    sessions: SessionManager
    idp: GoogleIdentityProvider | DevIdentityProvider
    # Grant jwt-bearer (M2M). None = grant deshabilitado (UnsupportedGrantType).
    m2m_verifier: GoogleAssertionVerifier | None = None
    registration_limiter: RegistrationRateLimiter = field(
        default_factory=RegistrationRateLimiter
    )

    @property
    def dev_login_enabled(self) -> bool:
        return isinstance(self.idp, DevIdentityProvider)


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx
