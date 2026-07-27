"""Fase 4 del runbook, pasos 3 y 4: metadatos del AS y registro dinámico."""

from __future__ import annotations

import pytest

from .conftest import ISSUER, REDIRECT_URI, register_client


async def test_authorization_server_metadata(client):
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    document = response.json()

    assert document["issuer"] == ISSUER
    assert document["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert document["token_endpoint"] == f"{ISSUER}/token"
    assert document["registration_endpoint"] == f"{ISSUER}/register"
    # OAuth 2.1: PKCE S256 y nada más.
    assert document["code_challenge_methods_supported"] == ["S256"]
    assert "plain" not in document["code_challenge_methods_supported"]


async def test_jwks_expone_solo_la_parte_publica(client):
    document = (await client.get("/.well-known/jwks.json")).json()
    key = document["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert set(key) == {"kty", "n", "e", "alg", "use", "kid"}
    assert "d" not in key and "p" not in key


async def test_registro_dinamico_devuelve_client_id(client):
    registration = await register_client(client)
    assert registration["client_id"].startswith("c_")
    assert registration["redirect_uris"] == [REDIRECT_URI]
    # Cliente público: sin secreto que se pueda filtrar desde Claude.
    assert "client_secret" not in registration
    assert registration["token_endpoint_auth_method"] == "none"


async def test_registro_confidencial_entrega_secreto_una_vez(client):
    registration = await register_client(
        client, token_endpoint_auth_method="client_secret_basic"
    )
    assert registration["client_secret"]
    assert registration["client_secret_expires_at"] == 0


@pytest.mark.parametrize(
    "redirect_uris",
    [
        ["http://evil.example/callback"],                 # http fuera de loopback
        ["https://evil.example/callback"],                # host no permitido
        ["https://claude.ai/callback#frag"],              # con fragmento
        [],                                               # vacío
        ["no-es-una-uri"],                                # sin host
    ],
)
async def test_registro_rechaza_redirect_uris_invalidas(client, redirect_uris):
    response = await client.post(
        "/register", json={"client_name": "malo", "redirect_uris": redirect_uris}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


async def test_registro_rechaza_scopes_desconocidos(client):
    response = await client.post(
        "/register",
        json={"redirect_uris": [REDIRECT_URI], "scope": "odoo:read admin:todo"},
    )
    assert response.status_code == 400
    assert "admin:todo" in response.json()["error_description"]


async def test_localhost_se_acepta_para_desarrollo(client):
    registration = await register_client(
        client, redirect_uris=["http://localhost:8765/callback"]
    )
    assert registration["redirect_uris"] == ["http://localhost:8765/callback"]
