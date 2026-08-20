"""Interfaz de persistencia del AS.

Dos implementaciones: memoria (tests y desarrollo) y Firestore (Cloud Run).
El contrato importante es que `consume_authorization_code` y
`consume_refresh_token` sean atómicos: de ahí depende que un código no se pueda
canjear dos veces y que la detección de reuso de refresh tokens sirva de algo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AuthorizationCode, AuthorizationRequest, Client, M2MClient, RefreshToken


class Storage(ABC):
    # --- clientes ---------------------------------------------------------
    @abstractmethod
    async def create_client(self, client: Client) -> None: ...

    @abstractmethod
    async def get_client(self, client_id: str) -> Client | None: ...

    # --- peticiones /authorize pendientes ---------------------------------
    @abstractmethod
    async def save_auth_request(self, request: AuthorizationRequest) -> None: ...

    @abstractmethod
    async def get_auth_request(self, request_id: str) -> AuthorizationRequest | None: ...

    @abstractmethod
    async def delete_auth_request(self, request_id: str) -> None: ...

    # --- códigos de autorización ------------------------------------------
    @abstractmethod
    async def save_authorization_code(self, code: AuthorizationCode) -> None: ...

    @abstractmethod
    async def consume_authorization_code(self, code_hash: str) -> tuple[AuthorizationCode | None, bool]:
        """Marca el código como usado.

        Devuelve `(código, era_replay)`. Si `era_replay` es True el código ya
        se había canjeado: quien llama debe revocar todo lo emitido a partir de
        él (OAuth 2.1 §4.1.3.1).
        """

    # --- refresh tokens ----------------------------------------------------
    @abstractmethod
    async def save_refresh_token(self, token: RefreshToken) -> None: ...

    @abstractmethod
    async def consume_refresh_token(self, token_hash: str) -> tuple[RefreshToken | None, bool]:
        """Igual que los códigos: devuelve `(token, era_replay)`."""

    @abstractmethod
    async def revoke_family(self, family_id: str) -> int:
        """Revoca toda la cadena de rotación. Devuelve cuántos se revocaron."""

    @abstractmethod
    async def revoke_session(self, session_id: str) -> int:
        """Revoca lo emitido en una sesión de autorización concreta."""

    @abstractmethod
    async def revoke_subject(self, subject: str) -> int:
        """Revoca todos los refresh tokens de un usuario."""

    @abstractmethod
    async def find_refresh_token(self, token_hash: str) -> RefreshToken | None: ...

    # --- clientes M2M (grant jwt-bearer, RFC 7523) --------------------------
    # Este servicio SOLO LEE `m2m_clients`; las altas/bajas son de Terraform.
    @abstractmethod
    async def get_m2m_client(self, sa_email: str) -> M2MClient | None: ...

    @abstractmethod
    async def register_assertion(self, assertion_id: str, expires_at: int) -> bool:
        """Marca write-once de anti-replay (condición CISO).

        Devuelve True si la assertion se registró por primera vez; False si ya
        existía (replay: quien llama DEBE rechazar el canje). La implementación
        tiene que ser un create atómico — nunca un get-luego-set.
        """


def build_storage(settings) -> Storage:  # noqa: ANN001 - evita import circular
    if settings.storage_backend == "firestore":
        from .firestore import FirestoreStorage

        return FirestoreStorage(
            project=settings.firestore_project,
            database=settings.firestore_database,
        )
    from .memory import MemoryStorage

    return MemoryStorage()


__all__ = ["Storage", "build_storage"]
