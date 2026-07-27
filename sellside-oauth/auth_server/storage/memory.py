"""Backend en memoria. Sirve para tests y para correr el AS en local.

No usarlo en Cloud Run: con más de una instancia cada réplica tendría su propia
copia y los códigos emitidos por una no se podrían canjear en otra.
"""

from __future__ import annotations

import asyncio

from ..models import AuthorizationCode, AuthorizationRequest, Client, RefreshToken, now
from . import Storage


class MemoryStorage(Storage):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._clients: dict[str, Client] = {}
        self._auth_requests: dict[str, AuthorizationRequest] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self._revoked_families: set[str] = set()

    async def create_client(self, client: Client) -> None:
        async with self._lock:
            self._clients[client.client_id] = client

    async def get_client(self, client_id: str) -> Client | None:
        return self._clients.get(client_id)

    async def save_auth_request(self, request: AuthorizationRequest) -> None:
        async with self._lock:
            self._auth_requests[request.request_id] = request

    async def get_auth_request(self, request_id: str) -> AuthorizationRequest | None:
        request = self._auth_requests.get(request_id)
        if request and request.expires_at < now():
            self._auth_requests.pop(request_id, None)
            return None
        return request

    async def delete_auth_request(self, request_id: str) -> None:
        async with self._lock:
            self._auth_requests.pop(request_id, None)

    async def save_authorization_code(self, code: AuthorizationCode) -> None:
        async with self._lock:
            self._codes[code.code_hash] = code

    async def consume_authorization_code(self, code_hash: str) -> tuple[AuthorizationCode | None, bool]:
        async with self._lock:
            code = self._codes.get(code_hash)
            if code is None:
                return None, False
            if code.consumed:
                return code, True
            code.consumed = True
            return code, False

    async def save_refresh_token(self, token: RefreshToken) -> None:
        async with self._lock:
            self._refresh[token.token_hash] = token

    async def find_refresh_token(self, token_hash: str) -> RefreshToken | None:
        return self._refresh.get(token_hash)

    async def consume_refresh_token(self, token_hash: str) -> tuple[RefreshToken | None, bool]:
        async with self._lock:
            token = self._refresh.get(token_hash)
            if token is None:
                return None, False
            if token.consumed or token.family_id in self._revoked_families:
                return token, True
            token.consumed = True
            return token, False

    async def revoke_family(self, family_id: str) -> int:
        async with self._lock:
            self._revoked_families.add(family_id)
            count = 0
            for token in self._refresh.values():
                if token.family_id == family_id and not token.consumed:
                    token.consumed = True
                    count += 1
            return count

    async def revoke_session(self, session_id: str) -> int:
        families = {
            token.family_id for token in self._refresh.values() if token.session_id == session_id
        }
        return sum([await self.revoke_family(family_id) for family_id in families])

    async def revoke_subject(self, subject: str) -> int:
        async with self._lock:
            count = 0
            for token in self._refresh.values():
                if token.subject == subject and not token.consumed:
                    token.consumed = True
                    self._revoked_families.add(token.family_id)
                    count += 1
            return count
