"""Grant jwt-bearer (RFC 7523) — cada test cubre una condición del dictamen
CISO del 20-08-2026 (contrato en Odoo Knowledge art. 122)."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest

from auth_server.keys import SigningKey, generate_key_pem
from auth_server.m2m import GoogleAssertionVerifier, JWT_BEARER_GRANT
from auth_server.models import M2MClient

from .conftest import ISSUER, OTHER_RESOURCE, RESOURCE

SA_EMAIL = "rosa-runtime@proyecto-test.iam.gserviceaccount.com"

# Llave "de Google" para los tests: el verifier la descubre vía el JWKS mockeado.
_GOOGLE_KEY = SigningKey(generate_key_pem())


def google_jwks_transport() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_GOOGLE_KEY.jwks())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_assertion(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": ISSUER,
        "sub": "117234567890123456789",
        "email": SA_EMAIL,
        "email_verified": True,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(
        claims,
        _GOOGLE_KEY.private_key,
        algorithm="RS256",
        headers={"kid": _GOOGLE_KEY.kid},
    )


@pytest.fixture
def m2m_app(auth_app):
    auth_app.state.ctx.m2m_verifier = GoogleAssertionVerifier(
        expected_audience=ISSUER, http_client=google_jwks_transport()
    )
    return auth_app


@pytest.fixture
async def m2m_client_http(m2m_app):
    transport = httpx.ASGITransport(app=m2m_app)
    async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as http:
        yield http


async def register_m2m(storage, **overrides) -> M2MClient:
    fields = dict(
        sa_email=SA_EMAIL,
        status="active",
        allowed_scopes=["odoo:read"],
        allowed_audiences=[RESOURCE],
    )
    fields.update(overrides)
    client = M2MClient(**fields)
    await storage.save_m2m_client(client)
    return client


async def exchange(http, assertion: str, **extra) -> httpx.Response:
    data = {"grant_type": JWT_BEARER_GRANT, "assertion": assertion}
    data.update(extra)
    return await http.post("/token", data=data)


async def test_emision_feliz_solo_access_token(m2m_client_http, m2m_app, storage):
    await register_m2m(storage)
    response = await exchange(m2m_client_http, make_assertion())
    assert response.status_code == 200
    body = response.json()

    # Sin refresh token: el cliente M2M re-presenta assertion cuando quiera.
    assert "refresh_token" not in body
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "odoo:read"

    key = m2m_app.state.ctx.key
    claims = jwt.decode(
        body["access_token"],
        jwt.PyJWK(key.public_jwk).key,
        algorithms=["RS256"],
        audience=RESOURCE,
    )
    assert claims["iss"] == ISSUER
    assert claims["client_id"] == SA_EMAIL
    assert claims["email"] == SA_EMAIL
    assert claims["sub"].startswith("google-sa:")
    assert claims["scope"] == "odoo:read"


async def test_replay_de_assertion_se_rechaza(m2m_client_http, storage):
    """Condición CISO 2: write-once — una assertion, un solo canje."""

    await register_m2m(storage)
    assertion = make_assertion()
    first = await exchange(m2m_client_http, assertion)
    assert first.status_code == 200
    second = await exchange(m2m_client_http, assertion)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


async def test_aud_distinto_se_rechaza(m2m_client_http, storage):
    """Condición CISO 1: aud exacto del AS — un ID token pedido para otro
    servicio no sirve aquí (anti token-relay)."""

    await register_m2m(storage)
    response = await exchange(
        m2m_client_http, make_assertion(aud="https://otro-servicio.example.com")
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_assertion_vieja_se_rechaza(m2m_client_http, storage):
    """Condición CISO 1: ventana iat de 300 segundos."""

    await register_m2m(storage)
    response = await exchange(
        m2m_client_http, make_assertion(iat=int(time.time()) - 400)
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_email_sin_verificar_se_rechaza(m2m_client_http, storage):
    await register_m2m(storage)
    response = await exchange(m2m_client_http, make_assertion(email_verified=False))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_sa_no_registrada_se_rechaza(m2m_client_http, storage):
    """Sin registro en m2m_clients no hay emisión, aunque la assertion sea
    válida: la allowlist es la autorización, la assertion solo autentica."""

    response = await exchange(m2m_client_http, make_assertion())
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_cliente_revocado_se_rechaza(m2m_client_http, storage):
    """Condición CISO 3: status revoked corta la emisión de inmediato."""

    await register_m2m(storage, status="revoked")
    response = await exchange(m2m_client_http, make_assertion())
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_escalada_de_scope_se_rechaza(m2m_client_http, storage):
    """Condición CISO 4 (PoLP): registrado solo odoo:read, pedir write falla."""

    await register_m2m(storage)  # allowed_scopes = [odoo:read]
    response = await exchange(
        m2m_client_http, make_assertion(), scope="odoo:read odoo:write"
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


async def test_resource_fuera_de_audiencias_se_rechaza(m2m_client_http, storage):
    await register_m2m(storage)  # allowed_audiences = [RESOURCE]
    response = await exchange(
        m2m_client_http, make_assertion(), resource=OTHER_RESOURCE
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


async def test_metadata_anuncia_el_grant(client):
    response = await client.get("/.well-known/oauth-authorization-server")
    assert JWT_BEARER_GRANT in response.json()["grant_types_supported"]
