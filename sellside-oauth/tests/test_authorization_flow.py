"""Flujo completo: /authorize → login → consentimiento → /token → refresh."""

from __future__ import annotations

import httpx
import jwt

from .conftest import (
    OTHER_RESOURCE,
    REDIRECT_URI,
    RESOURCE,
    authorize_and_get_code,
    code_from_redirect,
    pkce_pair,
    register_client,
)


async def _token_for(client, *, scope: str = "odoo:read") -> tuple[dict, str]:
    registration = await register_client(client)
    verifier, challenge = pkce_pair()
    redirect = await authorize_and_get_code(
        client, registration["client_id"], challenge, scope=scope
    )
    code = code_from_redirect(redirect)
    response = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": registration["client_id"],
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), registration["client_id"]


async def test_flujo_completo_emite_token_con_audiencia_correcta(client, settings):
    tokens, client_id = await _token_for(client)

    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == settings.access_token_ttl
    assert tokens["refresh_token"]

    claims = jwt.decode(
        tokens["access_token"],
        jwt.PyJWK(
            (await client.get("/.well-known/jwks.json")).json()["keys"][0]
        ).key,
        algorithms=["RS256"],
        audience=RESOURCE,
        issuer=settings.issuer,
    )
    # El corazón del asunto: el token vale para este MCP y para ningún otro.
    assert claims["aud"] == RESOURCE
    assert claims["client_id"] == client_id
    assert claims["scope"] == "odoo:read"
    assert claims["email"] == "javier.lozano@sellside.cl"


async def test_state_vuelve_intacto_y_se_incluye_iss(client):
    registration = await register_client(client)
    _, challenge = pkce_pair()
    redirect = await authorize_and_get_code(client, registration["client_id"], challenge)
    location = redirect.headers["location"]
    assert "state=st-123" in location
    assert "iss=" in location  # RFC 9207


async def test_pkce_incorrecto_no_canjea(client):
    registration = await register_client(client)
    _, challenge = pkce_pair()
    redirect = await authorize_and_get_code(client, registration["client_id"], challenge)
    code = code_from_redirect(redirect)

    response = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": registration["client_id"],
            "code_verifier": "b" * 64,
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_authorize_exige_pkce(client):
    registration = await register_client(client)
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "scope": "odoo:read",
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 302
    assert "error=invalid_request" in response.headers["location"]
    assert "code_challenge" in response.headers["location"]


async def test_authorize_rechaza_plain(client):
    registration = await register_client(client)
    _, challenge = pkce_pair()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "plain",
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 302
    assert "error=invalid_request" in response.headers["location"]


async def test_redirect_uri_no_registrada_no_redirige(client):
    """Un redirect_uri desconocido se muestra en pantalla, nunca se sigue."""

    registration = await register_client(client)
    _, challenge = pkce_pair()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback/../evil",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400
    assert "location" not in response.headers


async def test_resource_desconocido_se_rechaza(client):
    registration = await register_client(client)
    _, challenge = pkce_pair()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://sendgrid-mcp.ajeno.test/mcp",
        },
    )
    assert response.status_code == 302
    assert "error=invalid_target" in response.headers["location"]


async def test_token_rechaza_resource_distinto_al_autorizado(client):
    registration = await register_client(client)
    verifier, challenge = pkce_pair()
    redirect = await authorize_and_get_code(client, registration["client_id"], challenge)
    code = code_from_redirect(redirect)

    response = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": registration["client_id"],
            "code_verifier": verifier,
            "resource": OTHER_RESOURCE,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


async def test_codigo_es_de_un_solo_uso_y_el_replay_revoca(client, storage):
    registration = await register_client(client)
    verifier, challenge = pkce_pair()
    redirect = await authorize_and_get_code(client, registration["client_id"], challenge)
    code = code_from_redirect(redirect)

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": registration["client_id"],
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    first = await client.post("/token", data=body)
    assert first.status_code == 200
    refresh_token = first.json()["refresh_token"]

    second = await client.post("/token", data=body)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"

    # El replay tira abajo lo que ya se había emitido con ese código.
    reuse = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": registration["client_id"],
        },
    )
    assert reuse.status_code == 400
    assert reuse.json()["error"] == "invalid_grant"


async def test_refresh_rota_y_detecta_reuso(client):
    tokens, client_id = await _token_for(client)
    first_refresh = tokens["refresh_token"]

    rotated = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first_refresh,
            "client_id": client_id,
        },
    )
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh

    # Reusar el viejo mata la cadena completa, incluido el nuevo.
    replay = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first_refresh,
            "client_id": client_id,
        },
    )
    assert replay.status_code == 400
    assert "revocó la cadena" in replay.json()["error_description"]

    after = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": second_refresh,
            "client_id": client_id,
        },
    )
    assert after.status_code == 400


async def test_refresh_no_puede_ampliar_scope(client):
    tokens, client_id = await _token_for(client, scope="odoo:read")
    response = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "scope": "odoo:read odoo:write",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


async def test_codigo_no_se_canjea_con_otro_cliente(client):
    victim = await register_client(client)
    attacker = await register_client(client, client_name="atacante")
    verifier, challenge = pkce_pair()
    redirect = await authorize_and_get_code(client, victim["client_id"], challenge)
    code = code_from_redirect(redirect)

    response = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": attacker["client_id"],
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_rechazar_consentimiento_devuelve_access_denied(client):
    import re

    registration = await register_client(client)
    _, challenge = pkce_pair()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "odoo:read",
            "state": "st-deny",
            "resource": RESOURCE,
        },
    )
    request_id = re.search(r"state=([^&]+)", response.headers["location"]).group(1)
    await client.post("/dev/login", data={"state": request_id, "email": "a@sellside.cl"})
    page = await client.get(f"/authorize/continue?request_id={request_id}")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    denied = await client.post(
        "/consent",
        data={"request_id": request_id, "csrf_token": csrf, "decision": "deny"},
    )
    assert denied.status_code == 302
    assert "error=access_denied" in denied.headers["location"]
    assert "state=st-deny" in denied.headers["location"]


async def test_consent_exige_csrf(client):
    import re

    registration = await register_client(client)
    _, challenge = pkce_pair()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
    )
    request_id = re.search(r"state=([^&]+)", response.headers["location"]).group(1)
    await client.post("/dev/login", data={"state": request_id, "email": "a@sellside.cl"})

    forged = await client.post(
        "/consent",
        data={"request_id": request_id, "csrf_token": "0" * 64, "decision": "allow"},
    )
    assert forged.status_code == 403


async def test_revocacion_de_refresh_token(client):
    tokens, client_id = await _token_for(client)
    revoked = await client.post(
        "/revoke", data={"token": tokens["refresh_token"], "client_id": client_id}
    )
    assert revoked.status_code == 200

    response = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_revocar_con_access_token_corta_la_sesion(client):
    tokens, client_id = await _token_for(client)
    revoked = await client.post(
        "/revoke", data={"token": tokens["access_token"], "client_id": client_id}
    )
    assert revoked.status_code == 200

    response = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        },
    )
    assert response.status_code == 400


async def test_cliente_publico_no_debe_mandar_secreto(client):
    tokens, client_id = await _token_for(client)
    response = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "client_secret": "inventado",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_recortar_scopes_supported_afecta_a_los_refresh_vivos(client, storage):
    """Quitar un scope del AS debe cortar la renovación, no esperar 30 días.

    Es el caso operativo real: alguien concedió `odoo:write`, luego se decide que
    el conector solo lea, y el refresh token vivo no puede seguir emitiendo
    tokens de escritura.
    """

    from auth_server.app import create_app
    from auth_server.identity import DevIdentityProvider

    from .conftest import ISSUER, make_settings

    tokens, client_id = await _token_for(client, scope="odoo:read odoo:write")

    restringido = create_app(
        make_settings(scopes_supported=("odoo:read", "offline_access")),
        storage=storage,                      # mismo estado, otra configuración
        identity_provider=DevIdentityProvider(),
    )
    transport = httpx.ASGITransport(app=restringido)
    async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as http:
        response = await http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["scope"] == "odoo:read"   # `odoo:write` ya no sale
