"""Registros persistidos por el AS.

Nada que sea secreto se guarda en claro: los `client_secret` y los refresh
tokens viven en la base como SHA-256 del valor emitido.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any


def now() -> int:
    return int(time.time())


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class Client:
    client_id: str
    redirect_uris: list[str]
    client_name: str = ""
    client_secret_hash: str | None = None
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = field(default_factory=lambda: ["code"])
    scope: str = ""
    created_at: int = field(default_factory=now)

    @property
    def is_public(self) -> bool:
        return self.token_endpoint_auth_method == "none"

    def allows_redirect_uri(self, redirect_uri: str) -> bool:
        """Coincidencia exacta de strings. Sin prefijos, sin comodines."""

        return any(secrets.compare_digest(redirect_uri, uri) for uri in self.redirect_uris)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Client":
        return cls(**data)


@dataclass
class M2MClient:
    """Cliente máquina-a-máquina del grant jwt-bearer (RFC 7523).

    El documento vive en la colección `m2m_clients` y SOLO lo escribe
    infraestructura (Terraform/operador con IAM de admin) — condición del
    dictamen CISO: la SA de runtime del AS es read-only sobre esta colección
    y cada alta/baja se audita fuera de este servicio.
    """

    sa_email: str
    status: str = "active"  # active | suspended | revoked
    allowed_scopes: list[str] = field(default_factory=list)
    allowed_audiences: list[str] = field(default_factory=list)
    description: str = ""
    created_at: int = field(default_factory=now)
    updated_at: int = field(default_factory=now)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "M2MClient":
        known = {f for f in cls.__dataclass_fields__}  # tolera campos extra del doc
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AuthorizationRequest:
    """Petición /authorize a la espera de login y consentimiento."""

    request_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    scope: str
    state: str | None = None
    expires_at: int = 0
    login_nonce: str | None = None   # nonce del id_token del IdP upstream
    subject: str | None = None       # se completa tras el login
    subject_email: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthorizationRequest":
        return cls(**data)


@dataclass
class AuthorizationCode:
    code_hash: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    scope: str
    subject: str
    subject_email: str
    expires_at: int
    consumed: bool = False
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthorizationCode":
        return cls(**data)


@dataclass
class RefreshToken:
    token_hash: str
    family_id: str
    client_id: str
    resource: str
    scope: str
    subject: str
    subject_email: str
    expires_at: int
    session_id: str = ""
    consumed: bool = False
    created_at: int = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RefreshToken":
        return cls(**data)
