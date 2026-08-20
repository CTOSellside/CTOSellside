"""Fixtures: un AS completo en memoria y un MCP de prueba apuntando a él."""

from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth_server.app import create_app  # noqa: E402
from auth_server.config import Settings  # noqa: E402
from auth_server.identity import DevIdentityProvider  # noqa: E402
from auth_server.keys import generate_key_pem  # noqa: E402
from auth_server.storage.memory import MemoryStorage  # noqa: E402

ISSUER = "http://testserver"
RESOURCE = "https://odoo-mcp.test/mcp"
OTHER_RESOURCE = "https://twilio-mcp.test/mcp"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"

_KEY_PEM = generate_key_pem()


def make_settings(**overrides) -> Settings:
    resources = overrides.pop("protected_resources", (RESOURCE, OTHER_RESOURCE))
    defaults = dict(
        issuer=ISSUER,
        signing_key_pem=_KEY_PEM,
        protected_resources=resources,
        idp_mode="dev",
        storage_backend="memory",
        allowed_redirect_hosts=("claude.ai", "localhost"),
        _resource_set=frozenset(resources),
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def auth_app(settings, storage):
    return create_app(
        settings,
        storage=storage,
        identity_provider=DevIdentityProvider(),
    )


@pytest.fixture
async def client(auth_app):
    transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as http:
        yield http


def pkce_pair(verifier: str = "a" * 64) -> tuple[str, str]:
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def register_client(client: httpx.AsyncClient, **overrides) -> dict:
    payload = {"client_name": "Claude (test)", "redirect_uris": [REDIRECT_URI]}
    payload.update(overrides)
    response = await client.post("/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def authorize_and_get_code(
    client: httpx.AsyncClient,
    client_id: str,
    challenge: str,
    *,
    resource: str = RESOURCE,
    scope: str = "odoo:read",
    state: str = "st-123",
    email: str = "javier.lozano@sellside.cl",
    redirect_uri: str = REDIRECT_URI,
) -> httpx.Response:
    """Recorre /authorize → login → consentimiento y devuelve el último 302."""

    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": scope,
            "state": state,
            "resource": resource,
        },
    )
    if response.status_code != 302 or "/dev/login" not in response.headers.get("location", ""):
        return response

    login_url = response.headers["location"]
    request_id = re.search(r"state=([^&]+)", login_url).group(1)
    nonce = re.search(r"nonce=([^&]+)", login_url).group(1)

    response = await client.post(
        "/dev/login", data={"state": request_id, "nonce": nonce, "email": email}
    )
    assert response.status_code == 302, response.text

    response = await client.get(response.headers["location"])
    assert response.status_code == 200, response.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    return await client.post(
        "/consent",
        data={"request_id": request_id, "csrf_token": csrf, "decision": "allow"},
    )


def mint_token(**overrides) -> str:
    """Forja un token firmado con la llave del AS de pruebas.

    Sirve para construir los casos que el flujo normal no produce: audiencia
    ajena, token expirado, algoritmo cambiado.
    """

    import time

    import jwt

    from auth_server.keys import SigningKey

    key = SigningKey(_KEY_PEM)
    issued_at = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "dev:javier.lozano@sellside.cl",
        "aud": RESOURCE,
        "client_id": "c_test",
        "scope": "odoo:read",
        "email": "javier.lozano@sellside.cl",
        "sid": "sid-test",
        "jti": "jti-test",
        "iat": issued_at,
        "exp": issued_at + 900,
    }
    claims.update(overrides)
    return jwt.encode(claims, key.private_key, algorithm="RS256", headers={"kid": key.kid})


def code_from_redirect(response: httpx.Response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    match = re.search(r"[?&]code=([^&]+)", location)
    assert match, f"sin código en {location}"
    return match.group(1)
