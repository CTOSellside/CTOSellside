"""El MCP como resource server: 401, metadatos, validación de `aud` y scopes."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mcp_resource_server import (
    ODOO_POLICY,
    BearerAuthMiddleware,
    ResourceConfig,
    TokenVerifier,
    ToolNotAllowed,
    current_token,
)

from .conftest import ISSUER, OTHER_RESOURCE, RESOURCE, mint_token

RESOURCE_CONFIG = ResourceConfig(
    resource_uri=RESOURCE,
    authorization_servers=(ISSUER,),
    resource_name="odoo-mcp-sellside",
    scopes_supported=("odoo:read", "odoo:write"),
    protected_paths=("/mcp",),
)


@pytest.fixture
async def mcp_client(auth_app):
    """MCP de prueba cuyo verificador descubre el AS por HTTP (vía ASGI)."""

    as_transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=as_transport) as as_http:
        app = FastAPI()
        app.add_middleware(
            BearerAuthMiddleware,
            config=RESOURCE_CONFIG,
            verifier=TokenVerifier(RESOURCE_CONFIG, http_client=as_http),
        )

        @app.post("/mcp")
        async def mcp(request: Request) -> JSONResponse:
            token = current_token(request)
            payload = await request.json()
            tool = payload.get("params", {}).get("name", "")
            try:
                ODOO_POLICY.check(tool, token)
            except ToolNotAllowed as exc:
                return JSONResponse({"error": exc.description}, status_code=403)
            return JSONResponse({"ok": True, "sub": token.subject, "tool": tool})

        @app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse({"status": "ok"})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://odoo-mcp.test") as http:
            yield http


async def test_sin_token_devuelve_401_con_www_authenticate(mcp_client):
    response = await mcp_client.post("/mcp", json={})
    assert response.status_code == 401

    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    # Sin `resource_metadata` el cliente no sabe a qué AS ir.
    assert 'resource_metadata="https://odoo-mcp.test/.well-known/oauth-protected-resource/mcp"' in challenge


async def test_metadatos_del_recurso(mcp_client):
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        response = await mcp_client.get(path)
        assert response.status_code == 200, path
        document = response.json()
        assert document["resource"] == RESOURCE
        assert ISSUER in document["authorization_servers"]


async def test_token_valido_pasa(mcp_client):
    response = await mcp_client.post(
        "/mcp",
        json={"params": {"name": "odoo_search_read"}},
        headers={"authorization": f"Bearer {mint_token()}"},
    )
    assert response.status_code == 200
    assert response.json()["sub"] == "dev:javier.lozano@sellside.cl"


async def test_token_de_otro_recurso_se_rechaza(mcp_client):
    """El caso que justifica todo: firma válida, audiencia ajena."""

    response = await mcp_client.post(
        "/mcp",
        json={"params": {"name": "odoo_search_read"}},
        headers={"authorization": f"Bearer {mint_token(aud=OTHER_RESOURCE)}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert "otro recurso" in response.json()["error_description"]


async def test_token_expirado_se_rechaza(mcp_client):
    expired = mint_token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    response = await mcp_client.post(
        "/mcp", json={}, headers={"authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
    assert "expiró" in response.json()["error_description"]


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: mint_token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600),
        lambda: mint_token(aud=OTHER_RESOURCE),
        lambda: mint_token(iss="https://as.ajeno.test"),
        lambda: "no-es-un-jwt",
    ],
    ids=["expirado", "otra-audiencia", "otro-issuer", "basura"],
)
async def test_www_authenticate_es_ascii_puro(mcp_client, token_factory):
    """Las cabeceras son ASCII (RFC 7230).

    Los mensajes de error de esta librería están en español y terminan dentro de
    `WWW-Authenticate`. Google Frontend no recorta el carácter ofensivo: descarta
    la cabecera entera, y con ella `resource_metadata`. El cliente recibiría un
    401 sin saber dónde autenticarse y el flujo OAuth no arrancaría.
    """

    response = await mcp_client.post(
        "/mcp", json={}, headers={"authorization": f"Bearer {token_factory()}"}
    )
    challenge = response.headers["www-authenticate"]
    assert challenge.isascii(), f"cabecera con bytes no-ASCII: {challenge!r}"
    assert "resource_metadata=" in challenge
    # El cuerpo JSON sí puede llevar acentos: va en UTF-8 y nadie lo filtra.
    assert response.json()["error_description"]


async def test_challenge_sin_token_tambien_es_ascii(mcp_client):
    response = await mcp_client.post("/mcp", json={})
    assert response.headers["www-authenticate"].isascii()


async def test_alg_none_se_rechaza(mcp_client):
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "atacante", "aud": RESOURCE, "scope": "odoo:write",
         "iat": int(time.time()), "exp": int(time.time()) + 900},
        key="",
        algorithm="none",
    )
    response = await mcp_client.post(
        "/mcp", json={}, headers={"authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401
    assert "Algoritmo" in response.json()["error_description"]


async def test_hs256_con_la_clave_publica_se_rechaza(mcp_client):
    """Confusión de algoritmo: un HS256 no entra aunque el resto del token cuadre."""

    forged = jwt.encode(
        {"iss": ISSUER, "sub": "atacante", "aud": RESOURCE, "scope": "odoo:write",
         "iat": int(time.time()), "exp": int(time.time()) + 900},
        key="un-secreto-cualquiera",
        algorithm="HS256",
    )
    response = await mcp_client.post(
        "/mcp", json={}, headers={"authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


async def test_token_de_otro_issuer_se_rechaza(mcp_client):
    response = await mcp_client.post(
        "/mcp",
        json={},
        headers={"authorization": f"Bearer {mint_token(iss='https://as.ajeno.test')}"},
    )
    assert response.status_code == 401
    assert "confianza" in response.json()["error_description"]


async def test_scope_de_lectura_no_habilita_escritura(mcp_client):
    response = await mcp_client.post(
        "/mcp",
        json={"params": {"name": "odoo_write"}},
        headers={"authorization": f"Bearer {mint_token(scope='odoo:read')}"},
    )
    assert response.status_code == 403
    assert "odoo:write" in response.json()["error"]


async def test_scope_de_escritura_habilita_unlink(mcp_client):
    response = await mcp_client.post(
        "/mcp",
        json={"params": {"name": "odoo_unlink"}},
        headers={"authorization": f"Bearer {mint_token(scope='odoo:read odoo:write')}"},
    )
    assert response.status_code == 200


async def test_herramienta_no_declarada_se_deniega(mcp_client):
    response = await mcp_client.post(
        "/mcp",
        json={"params": {"name": "odoo_execute_kw"}},
        headers={"authorization": f"Bearer {mint_token(scope='odoo:read odoo:write')}"},
    )
    assert response.status_code == 403


async def test_rutas_no_protegidas_siguen_abiertas(mcp_client):
    response = await mcp_client.get("/health")
    assert response.status_code == 200


async def test_el_token_crudo_no_queda_disponible_para_reenviarlo(mcp_client):
    """Sin passthrough: el objeto validado no conserva el JWT de Claude."""

    from dataclasses import fields

    from mcp_resource_server import AccessToken

    names = {f.name for f in fields(AccessToken)}
    assert "raw" not in names and "token" not in names
    assert not any("token" in name for name in names)
