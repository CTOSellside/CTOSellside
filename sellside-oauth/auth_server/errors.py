"""Errores OAuth con la forma que exige RFC 6749 §5.2."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class OAuthError(Exception):
    """Error que se serializa como respuesta OAuth JSON."""

    def __init__(
        self,
        error: str,
        description: str | None = None,
        *,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status_code = status_code
        self.headers = headers or {}

    def to_dict(self) -> dict[str, str]:
        payload = {"error": self.error}
        if self.description:
            payload["error_description"] = self.description
        return payload


class InvalidRequest(OAuthError):
    def __init__(self, description: str) -> None:
        super().__init__("invalid_request", description)


class InvalidClient(OAuthError):
    def __init__(self, description: str) -> None:
        # 401 porque el cliente no se pudo autenticar.
        super().__init__(
            "invalid_client",
            description,
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="sellside-auth"'},
        )


class InvalidGrant(OAuthError):
    def __init__(self, description: str) -> None:
        super().__init__("invalid_grant", description)


class UnsupportedGrantType(OAuthError):
    def __init__(self, grant_type: str) -> None:
        super().__init__("unsupported_grant_type", f"grant_type no soportado: {grant_type}")


class InvalidScope(OAuthError):
    def __init__(self, description: str) -> None:
        super().__init__("invalid_scope", description)


class InvalidTarget(OAuthError):
    """RFC 8707: el `resource` pedido no lo sirve este AS."""

    def __init__(self, description: str) -> None:
        super().__init__("invalid_target", description)


async def oauth_error_handler(_: Request, exc: OAuthError) -> JSONResponse:
    return JSONResponse(
        exc.to_dict(),
        status_code=exc.status_code,
        headers={**NO_STORE, **exc.headers},
    )
