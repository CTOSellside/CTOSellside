"""Ejemplo de `odoo-mcp-sellside` convertido en resource server.

Lo que hay que copiar a los MCP reales son las tres primeras secciones: la
configuración del recurso, el middleware y la comprobación de scope por
herramienta. El resto es un MCP de juguete para que el archivo se pueda correr.

    RESOURCE_URI=https://odoo-mcp-sellside-843056793102.southamerica-west1.run.app/mcp \\
    AUTH_SERVER=https://sellside-auth-843056793102.southamerica-west1.run.app \\
    uvicorn examples.odoo_mcp_app:app
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mcp_resource_server import (
    ODOO_POLICY,
    BearerAuthMiddleware,
    OdooCredentials,
    TokenVerifier,
    ToolNotAllowed,
    current_token,
    load_resource_config,
)

logger = logging.getLogger("odoo_mcp")

# 1. Identidad del recurso: URI canónica propia y AS en el que confía.
config = load_resource_config(default_resource_name="odoo-mcp-sellside")

app = FastAPI(title="odoo-mcp-sellside", docs_url=None, redoc_url=None, openapi_url=None)

# 2. Un middleware: publica los metadatos, exige Bearer y valida `aud`.
app.add_middleware(BearerAuthMiddleware, config=config, verifier=TokenVerifier(config))


# 3. Credencial de Odoo del servidor. El token de Claude nunca llega hasta acá.
def odoo_client() -> OdooCredentials:
    return OdooCredentials.from_env()


TOOLS = [
    {"name": "odoo_search_read", "description": "Buscar y leer registros de Odoo"},
    {"name": "odoo_create", "description": "Crear un registro"},
    {"name": "odoo_write", "description": "Modificar un registro"},
    {"name": "odoo_unlink", "description": "Eliminar un registro"},
]


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    token = current_token(request)
    payload = await request.json()
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "odoo-mcp-sellside", "version": "0.1.0"},
        })

    if method == "tools/list":
        # Se anuncian solo las herramientas que el scope del token permite: así
        # el modelo no intenta llamar a algo que va a rebotar con 403.
        allowed = set(ODOO_POLICY.allowed_tools(token))
        return _result(request_id, {"tools": [t for t in TOOLS if t["name"] in allowed]})

    if method == "tools/call":
        name = payload.get("params", {}).get("name", "")
        try:
            ODOO_POLICY.check(name, token)
        except ToolNotAllowed as exc:
            logger.warning("denegado %s para sub=%s (%s)", name, token.subject, exc.description)
            return _error(request_id, -32001, exc.description, status=403)

        # Auditoría: quién, con qué cliente, qué herramienta.
        logger.info(
            "tool=%s sub=%s email=%s client=%s jti=%s",
            name, token.subject, token.email, token.client_id, token.jti,
        )
        result = _call_odoo(name, payload.get("params", {}).get("arguments", {}))
        return _result(request_id, {"content": [{"type": "text", "text": result}]})

    return _error(request_id, -32601, f"Método no soportado: {method}")


def _call_odoo(tool: str, arguments: dict) -> str:
    credentials = odoo_client()
    # Aquí iría la llamada XML-RPC/JSON-RPC real a Odoo, autenticada con
    # `credentials`. El usuario de Odoo detrás de esta credencial debe tener las
    # ACLs mínimas: es el último control antes de los datos.
    return f"[stub] {tool}({arguments}) contra {credentials.url}/{credentials.database}"


def _result(request_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status,
    )
