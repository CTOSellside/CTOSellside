"""Configuración de sellside-auth.

Todo se lee de variables de entorno porque el servicio corre en Cloud Run y el
secreto de firma se inyecta con `--set-secrets`.

El `issuer` es la decisión más cara de revertir: queda grabado en los metadatos
publicados y en el claim `iss` de cada token ya emitido. Cambiarlo invalida
todas las conexiones existentes de Claude.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_SCOPES = ("odoo:read", "odoo:write", "offline_access")

# Hosts a los que se permite registrar redirect URIs. El registro dinámico es
# abierto por diseño (RFC 7591 sin autenticación inicial); esta lista evita que
# el AS se convierta en un redirector para terceros cualesquiera.
DEFAULT_ALLOWED_REDIRECT_HOSTS = (
    "claude.ai",
    "claude.com",
    "localhost",
    "127.0.0.1",
)


class ConfigError(RuntimeError):
    """Configuración inválida: el proceso no debe arrancar."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - error de operación
        raise ConfigError(f"{name} debe ser un entero, se recibió {raw!r}") from exc


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = _env(name)
    if raw is None:
        return tuple(default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def canonical_resource_uri(value: str) -> str:
    """Normaliza una URI de recurso según RFC 8707 / spec MCP.

    Minúsculas en esquema y host, sin fragmento, sin query, y sin barra final
    salvo que el path sea la raíz. La comparación de `aud` es exacta, así que
    ambos lados —AS y resource server— tienen que normalizar igual.
    """

    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"}:
        raise ValueError(f"resource debe ser http(s), se recibió {value!r}")
    if parts.fragment:
        raise ValueError("resource no puede llevar fragmento")
    if parts.query:
        raise ValueError("resource no puede llevar query string")
    if not parts.netloc:
        raise ValueError(f"resource sin host: {value!r}")

    path = parts.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


@dataclass(frozen=True)
class Settings:
    issuer: str
    signing_key_pem: str
    protected_resources: tuple[str, ...]
    scopes_supported: tuple[str, ...] = DEFAULT_SCOPES
    storage_backend: str = "memory"
    firestore_project: str | None = None
    firestore_database: str = "(default)"

    access_token_ttl: int = 900          # 15 min: tokens de vida corta
    refresh_token_ttl: int = 30 * 24 * 3600
    authorization_code_ttl: int = 60
    auth_request_ttl: int = 600

    idp_mode: str = "google"             # google | dev
    google_client_id: str | None = None
    google_client_secret: str | None = None
    allowed_email_domains: tuple[str, ...] = ()
    allowed_emails: tuple[str, ...] = ()

    allowed_redirect_hosts: tuple[str, ...] = DEFAULT_ALLOWED_REDIRECT_HOSTS
    registration_rate_limit: int = 20    # registros por hora y por IP
    require_resource_param: bool = True

    _resource_set: frozenset[str] = field(default=frozenset(), repr=False)

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/token"

    @property
    def registration_endpoint(self) -> str:
        return f"{self.issuer}/register"

    @property
    def revocation_endpoint(self) -> str:
        return f"{self.issuer}/revoke"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.issuer}/callback/google"

    def is_known_resource(self, resource: str) -> bool:
        return resource in self._resource_set


def _read_signing_key() -> str:
    inline = _env("JWT_SIGNING_KEY")
    if inline:
        # Cloud Run inyecta el secreto como valor de env var. Si alguien lo pega
        # con \n escapados, lo normalizamos para no fallar de forma opaca.
        return inline.replace("\\n", "\n")
    path = _env("JWT_SIGNING_KEY_FILE")
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    raise ConfigError("Falta JWT_SIGNING_KEY (o JWT_SIGNING_KEY_FILE)")


def load_settings() -> Settings:
    issuer_raw = _env("ISSUER")
    if not issuer_raw:
        raise ConfigError("Falta ISSUER: es la URL pública del AS y no debe cambiar")
    issuer = issuer_raw.rstrip("/")
    issuer_parts = urlsplit(issuer)
    issuer_is_local = issuer_parts.hostname in {"localhost", "127.0.0.1", "testserver"}
    if issuer_parts.scheme != "https" and not issuer_is_local:
        raise ConfigError("ISSUER debe ser https salvo en localhost")

    resources_raw = _env_list("PROTECTED_RESOURCES")
    if not resources_raw:
        raise ConfigError(
            "Falta PROTECTED_RESOURCES: lista de URIs canónicas de los MCP que este AS protege"
        )
    try:
        resources = tuple(canonical_resource_uri(item) for item in resources_raw)
    except ValueError as exc:
        raise ConfigError(f"PROTECTED_RESOURCES inválido: {exc}") from exc

    idp_mode = (_env("IDP_MODE", "google") or "google").lower()
    if idp_mode not in {"google", "dev"}:
        raise ConfigError("IDP_MODE debe ser 'google' o 'dev'")

    google_client_id = _env("GOOGLE_CLIENT_ID")
    google_client_secret = _env("GOOGLE_CLIENT_SECRET")
    if idp_mode == "google" and not (google_client_id and google_client_secret):
        raise ConfigError(
            "IDP_MODE=google requiere GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET "
            "(este sí es el uso legítimo del cliente OAuth de Google que ya existe)"
        )
    if idp_mode == "dev" and not issuer_is_local and not _env_bool("DEV_LOGIN_ENABLED"):
        # El login de desarrollo acepta cualquier identidad. Nunca debe quedar
        # activo detrás de una URL pública por accidente.
        raise ConfigError(
            "IDP_MODE=dev con un ISSUER público requiere DEV_LOGIN_ENABLED=true explícito"
        )

    storage_backend = (_env("STORAGE_BACKEND", "memory") or "memory").lower()
    if storage_backend not in {"memory", "firestore"}:
        raise ConfigError("STORAGE_BACKEND debe ser 'memory' o 'firestore'")

    return Settings(
        issuer=issuer,
        signing_key_pem=_read_signing_key(),
        protected_resources=resources,
        scopes_supported=_env_list("SCOPES_SUPPORTED", DEFAULT_SCOPES),
        storage_backend=storage_backend,
        firestore_project=_env("GOOGLE_CLOUD_PROJECT") or _env("PROJECT"),
        firestore_database=_env("FIRESTORE_DATABASE", "(default)") or "(default)",
        access_token_ttl=_env_int("ACCESS_TOKEN_TTL", 900),
        refresh_token_ttl=_env_int("REFRESH_TOKEN_TTL", 30 * 24 * 3600),
        authorization_code_ttl=_env_int("AUTHORIZATION_CODE_TTL", 60),
        auth_request_ttl=_env_int("AUTH_REQUEST_TTL", 600),
        idp_mode=idp_mode,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        allowed_email_domains=_env_list("ALLOWED_EMAIL_DOMAINS"),
        allowed_emails=_env_list("ALLOWED_EMAILS"),
        allowed_redirect_hosts=_env_list("ALLOWED_REDIRECT_HOSTS", DEFAULT_ALLOWED_REDIRECT_HOSTS),
        registration_rate_limit=_env_int("REGISTRATION_RATE_LIMIT", 20),
        require_resource_param=_env_bool("REQUIRE_RESOURCE_PARAM", True),
        _resource_set=frozenset(resources),
    )
