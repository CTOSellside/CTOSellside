"""Configuración del MCP en su rol de *resource server*."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def canonical_resource_uri(value: str) -> str:
    """Misma normalización que aplica el AS.

    Si los dos lados no normalizan igual, la comparación de `aud` falla o —peor—
    pasa cuando no debería. Está duplicada a propósito: esta librería se copia
    dentro de cada MCP y no debe depender del paquete del AS.
    """

    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"}:
        raise ValueError(f"resource debe ser http(s): {value!r}")
    if parts.fragment or parts.query:
        raise ValueError("resource no puede llevar query ni fragmento")
    if not parts.netloc:
        raise ValueError(f"resource sin host: {value!r}")
    path = parts.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


@dataclass(frozen=True)
class ResourceConfig:
    """Identidad del recurso y de quién acepta tokens.

    `resource_uri` es *la* URI canónica: la que el cliente manda en `resource`,
    la que el AS pone en `aud` y la que este servidor exige. Un solo valor.
    """

    resource_uri: str
    authorization_servers: tuple[str, ...]
    resource_name: str = "MCP"
    scopes_supported: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ("/mcp",)
    documentation: str | None = None

    @property
    def metadata_path(self) -> str:
        """Ruta well-known según RFC 9728 §3.

        Para un recurso con path (`https://host/mcp`) el documento vive en
        `/.well-known/oauth-protected-resource/mcp`.
        """

        path = urlsplit(self.resource_uri).path
        if not path or path == "/":
            return "/.well-known/oauth-protected-resource"
        return f"/.well-known/oauth-protected-resource{path}"

    @property
    def metadata_url(self) -> str:
        parts = urlsplit(self.resource_uri)
        return f"{parts.scheme}://{parts.netloc}{self.metadata_path}"

    def is_protected(self, path: str) -> bool:
        return any(path == p or path.startswith(f"{p}/") for p in self.protected_paths)


def load_resource_config(
    *,
    default_resource_name: str = "MCP",
    default_scopes: tuple[str, ...] = ("odoo:read", "odoo:write"),
) -> ResourceConfig:
    resource_uri = os.environ.get("RESOURCE_URI", "").strip()
    if not resource_uri:
        raise RuntimeError("Falta RESOURCE_URI: la URI canónica pública de este MCP")

    auth_servers = tuple(
        item.strip().rstrip("/")
        for item in os.environ.get("AUTH_SERVER", "").split(",")
        if item.strip()
    )
    if not auth_servers:
        raise RuntimeError("Falta AUTH_SERVER: el issuer del servidor de autorización")

    scopes = tuple(
        item.strip()
        for item in os.environ.get("SCOPES_SUPPORTED", ",".join(default_scopes)).split(",")
        if item.strip()
    )
    protected = tuple(
        item.strip()
        for item in os.environ.get("PROTECTED_PATHS", "/mcp").split(",")
        if item.strip()
    )

    return ResourceConfig(
        resource_uri=canonical_resource_uri(resource_uri),
        authorization_servers=auth_servers,
        resource_name=os.environ.get("RESOURCE_NAME", default_resource_name),
        scopes_supported=scopes,
        protected_paths=protected,
        documentation=os.environ.get("RESOURCE_DOCUMENTATION"),
    )
