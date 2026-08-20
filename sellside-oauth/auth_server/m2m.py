"""Clientes máquina-a-máquina: grant `jwt-bearer` (RFC 7523) para agentes.

El caso de uso es Rosa: su service account de Google presenta su propio ID
token OIDC como assertion y este AS emite un access token con los scopes
registrados para esa SA. No hay secreto de cliente: la credencial es la
identidad de plataforma, cuyas llaves rota Google.

Cada validación de este módulo es una condición del dictamen del CISO
(contrato en Odoo Knowledge art. 122, 20-ago-2026):

    1. `aud` de la assertion EXACTAMENTE igual al issuer de este AS.
    2. `iss` estrictamente de Google; firma contra el JWKS público de Google.
    3. Ventana de frescura: `iat` no anterior a 300 segundos.
    4. `email_verified` obligatorio.
    5. Anti-replay write-once: una assertion se canjea UNA vez
       (storage.register_assertion, colección con TTL de 10 minutos).
    6. La SA debe existir en el registro `m2m_clients` con status `active`;
       ese registro solo lo escribe infraestructura (Terraform), nunca este
       servicio.
"""

from __future__ import annotations

import hashlib
import time

import httpx
import jwt

from .identity import GOOGLE_ISSUERS, GOOGLE_JWKS_URI

# Condición CISO: la assertion no puede haber sido emitida hace más de 5 min.
MAX_ASSERTION_AGE_SECONDS = 300
# La marca anti-replay vive el doble de la ventana de frescura, con margen.
ASSERTION_REPLAY_TTL_SECONDS = 600

JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class AssertionError_(RuntimeError):
    """La assertion no es válida. El texto es apto para el error OAuth."""


class GoogleAssertionVerifier:
    """Valida ID tokens OIDC de service accounts de Google como assertions.

    Mismo patrón de JWKS-con-caché que `GoogleIdentityProvider`; separado
    porque aquí el `aud` esperado es el issuer de ESTE AS (no un client_id de
    Google) y las reglas de frescura son las del contrato M2M.
    """

    def __init__(self, expected_audience: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._expected_audience = expected_audience
        self._http = http_client
        self._jwks: dict[str, jwt.PyJWK] = {}
        self._jwks_fetched_at = 0.0

    async def verify(self, assertion: str) -> dict:
        try:
            header = jwt.get_unverified_header(assertion)
        except jwt.PyJWTError as exc:
            raise AssertionError_(f"assertion ilegible: {exc}") from exc
        if header.get("alg") != "RS256":
            raise AssertionError_("la assertion debe venir firmada RS256 por Google")

        key = await self._signing_key(header.get("kid"))
        try:
            claims = jwt.decode(
                assertion,
                key.key,
                algorithms=["RS256"],
                # Condición 1: aud exacto = issuer de este AS. PyJWT compara
                # contra el valor completo; cualquier otra audiencia se rechaza.
                audience=self._expected_audience,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AssertionError_(f"assertion inválida: {exc}") from exc

        # Condición 2: emisor estrictamente Google.
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise AssertionError_("issuer de la assertion no es Google")

        # Condición 3: frescura — iat dentro de la ventana de 300 s.
        issued_at = int(claims.get("iat") or 0)
        if issued_at < int(time.time()) - MAX_ASSERTION_AGE_SECONDS:
            raise AssertionError_(
                f"assertion demasiado antigua (iat fuera de la ventana de {MAX_ASSERTION_AGE_SECONDS}s)"
            )

        # Condición 4: identidad con email verificado.
        email = (claims.get("email") or "").lower()
        if not email or not claims.get("email_verified"):
            raise AssertionError_("la assertion no trae email verificado")

        return claims

    async def _signing_key(self, kid: str | None) -> jwt.PyJWK:
        if kid and kid in self._jwks and time.time() - self._jwks_fetched_at < 3600:
            return self._jwks[kid]
        if self._http is not None:
            response = await self._http.get(GOOGLE_JWKS_URI)
        else:
            async with httpx.AsyncClient(timeout=10.0) as http:
                response = await http.get(GOOGLE_JWKS_URI)
        response.raise_for_status()
        self._jwks = {
            item["kid"]: jwt.PyJWK(item)
            for item in response.json().get("keys", [])
            if item.get("kid")
        }
        self._jwks_fetched_at = time.time()
        if not kid or kid not in self._jwks:
            raise AssertionError_("Google firmó la assertion con una llave desconocida")
        return self._jwks[kid]


def assertion_replay_id(assertion: str, claims: dict) -> str:
    """Identificador write-once de la assertion: `jti` si viene, si no su hash.

    Los ID tokens de SA de Google no siempre traen `jti`; el hash de la
    assertion completa identifica igual de bien un canje repetido.
    """

    jti = claims.get("jti")
    if jti:
        return f"jti:{jti}"
    return "sha256:" + hashlib.sha256(assertion.encode("utf-8")).hexdigest()
