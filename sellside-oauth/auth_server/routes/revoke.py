"""Revocación (RFC 7009).

Los access tokens son JWT: nadie los consulta contra el AS, así que revocarlos
no los apaga al instante. Lo que se revoca aquí es la capacidad de seguir
obteniendo tokens nuevos; el que ya está emitido muere solo en
`ACCESS_TOKEN_TTL` segundos (15 minutos por defecto). Para un corte inmediato
hay que quitar el binding de `allUsers` en el MCP o rotar la llave de firma —
ambos están documentados en el README.

RFC 7009 §2.2: la respuesta es 200 tanto si el token existía como si no. Decir
"ese token no existe" es filtrar información a quien está probando tokens.
"""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..context import ctx
from ..models import hash_secret
from .token import _authenticate_client  # noqa: PLC2701 - misma capa

router = APIRouter()


@router.post("/revoke")
async def revoke(request: Request) -> Response:
    context = ctx(request)
    form = dict(await request.form())
    client = await _authenticate_client(context, request, form)

    presented = str(form.get("token") or "")
    if not presented:
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    stored = await context.storage.find_refresh_token(hash_secret(presented))
    if stored is not None:
        if stored.client_id == client.client_id:
            await context.storage.revoke_family(stored.family_id)
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    # ¿Es un access token nuestro? Si lo es, cortamos la sesión que lo originó.
    try:
        claims = jwt.decode(
            presented,
            context.key.private_key.public_key(),
            algorithms=["RS256"],
            issuer=context.settings.issuer,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    if claims.get("client_id") == client.client_id and claims.get("sid"):
        await context.storage.revoke_session(claims["sid"])
    return Response(status_code=200, headers={"Cache-Control": "no-store"})
