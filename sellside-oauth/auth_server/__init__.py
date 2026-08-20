"""sellside-auth: servidor de autorización OAuth 2.1 para los MCP de Sellside."""

from .app import create_app

__all__ = ["create_app"]
