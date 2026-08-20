"""Del scope del token al permiso concreto sobre Odoo.

Aquí se decide si un token da lectura o escritura. Dos reglas:

* **Denegar por defecto.** Una herramienta sin scope declarado no se ejecuta.
  Agregar una herramienta nueva sin tocar este mapa la deja inaccesible, que es
  el fallo correcto.
* **Sin passthrough.** Las credenciales de Odoo salen de la configuración del
  servidor, nunca del token que mandó Claude. La spec de MCP lo prohíbe
  explícitamente y `OdooCredentials` no tiene forma de recibir un token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .verifier import AccessToken, InsufficientScope

SCOPE_READ = "odoo:read"
SCOPE_WRITE = "odoo:write"


class ToolNotAllowed(InsufficientScope):
    def __init__(self, tool: str, required: str) -> None:
        super().__init__(required)
        self.description = f"La herramienta '{tool}' requiere el scope {required}"
        self.tool = tool


class ScopePolicy:
    """Mapa herramienta → scope requerido."""

    def __init__(self, tool_scopes: dict[str, str]) -> None:
        self._tool_scopes = dict(tool_scopes)

    def required_scope(self, tool: str) -> str | None:
        return self._tool_scopes.get(tool)

    def check(self, tool: str, token: AccessToken) -> None:
        required = self.required_scope(tool)
        if required is None:
            raise ToolNotAllowed(tool, "(herramienta no declarada en la política)")
        if not token.has_scope(required):
            raise ToolNotAllowed(tool, required)

    def allowed_tools(self, token: AccessToken) -> list[str]:
        """Herramientas que este token puede usar; sirve para filtrar tools/list."""

        return sorted(
            tool for tool, scope in self._tool_scopes.items() if token.has_scope(scope)
        )


# Política del MCP de Odoo. `odoo_unlink` es destructivo y por eso no comparte
# scope con las escrituras normales aunque hoy ambos exijan odoo:write: cuando
# quieras separarlo, cambia el valor aquí y nada más.
ODOO_TOOL_SCOPES = {
    "odoo_search": SCOPE_READ,
    "odoo_search_read": SCOPE_READ,
    "odoo_read": SCOPE_READ,
    "odoo_fields_get": SCOPE_READ,
    "odoo_create": SCOPE_WRITE,
    "odoo_write": SCOPE_WRITE,
    "odoo_unlink": SCOPE_WRITE,
}

ODOO_POLICY = ScopePolicy(ODOO_TOOL_SCOPES)


@dataclass(frozen=True)
class OdooCredentials:
    """Credencial propia del MCP contra Odoo.

    Se construye solo desde el entorno del servidor. No hay constructor que
    acepte el token del usuario: el passthrough no es una opción que exista.
    """

    url: str
    database: str
    username: str
    api_key: str

    @classmethod
    def from_env(cls) -> "OdooCredentials":
        missing = [
            name
            for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(f"Faltan variables de Odoo: {', '.join(missing)}")
        return cls(
            url=os.environ["ODOO_URL"],
            database=os.environ["ODOO_DB"],
            username=os.environ["ODOO_USERNAME"],
            api_key=os.environ["ODOO_API_KEY"],
        )

    def __repr__(self) -> str:  # no filtrar la API key en trazas ni logs
        return f"OdooCredentials(url={self.url!r}, database={self.database!r}, username={self.username!r})"
