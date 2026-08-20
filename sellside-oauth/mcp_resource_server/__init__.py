"""Librería de *resource server* para los MCP de Sellside.

Uso mínimo dentro de un MCP en FastAPI/Starlette:

    from mcp_resource_server import (
        BearerAuthMiddleware, TokenVerifier, current_token, load_resource_config,
    )

    config = load_resource_config()          # RESOURCE_URI + AUTH_SERVER
    app.add_middleware(BearerAuthMiddleware, config=config,
                       verifier=TokenVerifier(config))

Con eso el servicio ya publica `/.well-known/oauth-protected-resource`,
responde 401 con `WWW-Authenticate` cuando falta el token y rechaza los tokens
cuyo `aud` no sea este servidor.
"""

from .config import ResourceConfig, canonical_resource_uri, load_resource_config
from .middleware import (
    BearerAuthMiddleware,
    current_token,
    protected_resource_metadata,
    www_authenticate,
)
from .scopes import (
    ODOO_POLICY,
    ODOO_TOOL_SCOPES,
    SCOPE_READ,
    SCOPE_WRITE,
    OdooCredentials,
    ScopePolicy,
    ToolNotAllowed,
)
from .verifier import AccessToken, InsufficientScope, TokenError, TokenVerifier

__all__ = [
    "AccessToken",
    "BearerAuthMiddleware",
    "InsufficientScope",
    "ODOO_POLICY",
    "ODOO_TOOL_SCOPES",
    "OdooCredentials",
    "ResourceConfig",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "ScopePolicy",
    "TokenError",
    "TokenVerifier",
    "ToolNotAllowed",
    "canonical_resource_uri",
    "current_token",
    "load_resource_config",
    "protected_resource_metadata",
    "www_authenticate",
]
