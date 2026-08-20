"""Backend Firestore (modo nativo) para Cloud Run.

Colecciones:
    oauth_clients            client_id -> Client
    oauth_auth_requests      request_id -> AuthorizationRequest
    oauth_codes              sha256(code) -> AuthorizationCode
    oauth_refresh_tokens     sha256(refresh) -> RefreshToken

Los documentos efímeros llevan `expire_at` (timestamp) para que la política TTL
de Firestore los borre sola. Configúrala una vez por colección:

    gcloud firestore fields ttls update expire_at \\
        --collection-group=oauth_codes --enable-ttl

El consumo de códigos y de refresh tokens va dentro de una transacción: es lo
que hace que "un solo uso" sea una garantía y no una intención.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google.cloud import firestore  # type: ignore[import-untyped]

from ..models import AuthorizationCode, AuthorizationRequest, Client, M2MClient, RefreshToken, now
from . import Storage

CLIENTS = "oauth_clients"
AUTH_REQUESTS = "oauth_auth_requests"
CODES = "oauth_codes"
REFRESH = "oauth_refresh_tokens"
# Grant jwt-bearer (RFC 7523). `m2m_clients` lo escribe SOLO infraestructura;
# `rcc_as_seen_assertions` necesita TTL sobre `expire_at`:
#     gcloud firestore fields ttls update expire_at \
#         --collection-group=rcc_as_seen_assertions --enable-ttl
M2M_CLIENTS = "m2m_clients"
SEEN_ASSERTIONS = "rcc_as_seen_assertions"


def _expire_at(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


class FirestoreStorage(Storage):
    def __init__(self, project: str | None = None, database: str = "(default)") -> None:
        self._db = firestore.AsyncClient(project=project, database=database)

    # --- clientes ---------------------------------------------------------
    async def create_client(self, client: Client) -> None:
        await self._db.collection(CLIENTS).document(client.client_id).set(client.to_dict())

    async def get_client(self, client_id: str) -> Client | None:
        snapshot = await self._db.collection(CLIENTS).document(client_id).get()
        if not snapshot.exists:
            return None
        return Client.from_dict(snapshot.to_dict())

    # --- peticiones pendientes --------------------------------------------
    async def save_auth_request(self, request: AuthorizationRequest) -> None:
        payload = request.to_dict()
        payload["expire_at"] = _expire_at(request.expires_at)
        await self._db.collection(AUTH_REQUESTS).document(request.request_id).set(payload)

    async def get_auth_request(self, request_id: str) -> AuthorizationRequest | None:
        snapshot = await self._db.collection(AUTH_REQUESTS).document(request_id).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        data.pop("expire_at", None)
        request = AuthorizationRequest.from_dict(data)
        if request.expires_at < now():
            return None
        return request

    async def delete_auth_request(self, request_id: str) -> None:
        await self._db.collection(AUTH_REQUESTS).document(request_id).delete()

    # --- códigos -----------------------------------------------------------
    async def save_authorization_code(self, code: AuthorizationCode) -> None:
        payload = code.to_dict()
        # Se conserva un rato más que su vigencia para poder detectar replays.
        payload["expire_at"] = _expire_at(code.expires_at + 3600)
        await self._db.collection(CODES).document(code.code_hash).set(payload)

    async def consume_authorization_code(self, code_hash: str) -> tuple[AuthorizationCode | None, bool]:
        ref = self._db.collection(CODES).document(code_hash)

        @firestore.async_transactional
        async def _consume(transaction):  # noqa: ANN001 - tipo interno del SDK
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None, False
            data = snapshot.to_dict()
            data.pop("expire_at", None)
            code = AuthorizationCode.from_dict(data)
            if code.consumed:
                return code, True
            transaction.update(ref, {"consumed": True})
            code.consumed = True
            return code, False

        return await _consume(self._db.transaction())

    # --- refresh tokens ----------------------------------------------------
    async def save_refresh_token(self, token: RefreshToken) -> None:
        payload = token.to_dict()
        payload["expire_at"] = _expire_at(token.expires_at + 86400)
        await self._db.collection(REFRESH).document(token.token_hash).set(payload)

    async def find_refresh_token(self, token_hash: str) -> RefreshToken | None:
        snapshot = await self._db.collection(REFRESH).document(token_hash).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        data.pop("expire_at", None)
        return RefreshToken.from_dict(data)

    async def consume_refresh_token(self, token_hash: str) -> tuple[RefreshToken | None, bool]:
        ref = self._db.collection(REFRESH).document(token_hash)

        @firestore.async_transactional
        async def _consume(transaction):  # noqa: ANN001
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None, False
            data = snapshot.to_dict()
            data.pop("expire_at", None)
            token = RefreshToken.from_dict(data)
            if token.consumed:
                return token, True
            transaction.update(ref, {"consumed": True})
            token.consumed = True
            return token, False

        return await _consume(self._db.transaction())

    async def revoke_family(self, family_id: str) -> int:
        return await self._revoke_where("family_id", family_id)

    async def revoke_session(self, session_id: str) -> int:
        return await self._revoke_where("session_id", session_id)

    async def revoke_subject(self, subject: str) -> int:
        return await self._revoke_where("subject", subject)

    # --- clientes M2M -------------------------------------------------------
    async def get_m2m_client(self, sa_email: str) -> M2MClient | None:
        snapshot = await self._db.collection(M2M_CLIENTS).document(sa_email.lower()).get()
        if not snapshot.exists:
            return None
        return M2MClient.from_dict(snapshot.to_dict())

    async def register_assertion(self, assertion_id: str, expires_at: int) -> bool:
        payload = {"expire_at": _expire_at(expires_at), "registered_at": now()}
        try:
            # `create` es atómico: falla si el documento ya existe. Es la
            # garantía write-once del anti-replay — nunca get-luego-set.
            await self._db.collection(SEEN_ASSERTIONS).document(assertion_id).create(payload)
            return True
        except Exception:  # noqa: BLE001 - AlreadyExists (u otro fallo: denegar)
            return False

    async def _revoke_where(self, field: str, value: str) -> int:
        query = (
            self._db.collection(REFRESH)
            .where(filter=firestore.FieldFilter(field, "==", value))
            .where(filter=firestore.FieldFilter("consumed", "==", False))
        )
        count = 0
        batch = self._db.batch()
        async for snapshot in query.stream():
            batch.update(snapshot.reference, {"consumed": True})
            count += 1
            if count % 400 == 0:
                await batch.commit()
                batch = self._db.batch()
        if count % 400 != 0:
            await batch.commit()
        return count
