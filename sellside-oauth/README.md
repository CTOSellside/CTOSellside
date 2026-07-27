# sellside-oauth

OAuth 2.1 para los MCP de Sellside en GCP. Dos piezas:

| Pieza | Rol | Qué es |
|---|---|---|
| `auth_server/` | **Authorization server** | `sellside-auth`: emite los tokens. Un servicio de Cloud Run. |
| `mcp_resource_server/` | **Resource server** | Librería que cada MCP monta para validar tokens. No es un servicio. |

Esto implementa la sección 8 del runbook —«lo que no se resuelve por consola»—.
Los scripts de `deploy/` cubren las fases 0 a 4.

## Por qué existe un servicio nuevo

El servidor de autorización de Google emite tokens para APIs de Google. No
acepta registro dinámico de clientes (RFC 7591) ni emite tokens cuyo `audience`
apunte a un servicio de Cloud Run propio. Identity-Aware Proxy exige que quien
llama presente un ID token de Google, y Claude no puede hacerlo.

El cliente OAuth de Google que ya existe en el proyecto **sí** se usa aquí,
pero en su rol legítimo: autenticar personas en la pantalla de consentimiento.
Google dice *quién eres*; `sellside-auth` decide *qué puedes hacer* en los MCP.

## Arquitectura

Un AS compartido, cinco resource servers (opción B del runbook). El código de
criptografía vive en un solo lugar; los MCP solo validan firma y `aud`.

```
Claude  ──401 + WWW-Authenticate──>  odoo-mcp-sellside   (resource server)
   │                                        │
   │        PRM: authorization_servers = [sellside-auth]
   │                                        ▼
   └──OAuth 2.1 (PKCE S256, RFC 8707)──>  sellside-auth  (authorization server)
                                            │
                                       Firestore: clientes, códigos, refresh tokens
                                       Secret Manager: llave de firma RSA
                                       Google Sign-In: identidad del usuario
```

Añadir `sendgrid-mcp`, `twilio-mcp` o `github-mcp` es agregar su URI a
`PROTECTED_RESOURCES` y montar el middleware en ese servicio. No hay más
servidores de autorización que mantener.

## Lo que implementa

**En `sellside-auth`:**

- `GET /.well-known/oauth-authorization-server` — RFC 8414
- `GET /.well-known/jwks.json` — llave pública, `kid` derivado del thumbprint (RFC 7638)
- `POST /register` — registro dinámico, RFC 7591, con hosts permitidos y límite por IP
- `GET /authorize` — PKCE **S256 obligatorio**, `resource` (RFC 8707), redirect URIs por
  coincidencia exacta, `iss` en la respuesta (RFC 9207)
- `POST /token` — `aud` = URI canónica del MCP, refresh rotativo con detección de reuso
- `POST /revoke` — RFC 7009
- Login vía Google Sign-In + pantalla de consentimiento

**En `mcp_resource_server`:**

- `GET /.well-known/oauth-protected-resource[/path]` — RFC 9728
- Middleware ASGI: valida firma RS256, expiración y **`aud` exacto**; 401 con
  `WWW-Authenticate: Bearer … resource_metadata="…"`
- `ScopePolicy`: mapeo herramienta → scope, denegar por defecto
- `OdooCredentials`: credencial propia del servidor, sin forma de recibir el token
  del usuario (prohibición de passthrough)

## Decisiones que conviene conocer

**El `issuer` es `https://sellside-auth-<PROJECT_NUMBER>.southamerica-west1.run.app`
y no se mueve.** Decidido: la URL `*.run.app` es estable, no depende de que la
región soporte mapeo de dominios de Cloud Run y no obliga a montar un
balanceador global. `deploy/env.sh` la fija.

Queda grabada en los metadatos y en el claim `iss` de cada token emitido, así
que cambiarla rompe todas las conexiones existentes. Migrar a `auth.sellside.cl`
más adelante no sería editar una variable: sería levantar el dominio, mantener
ambos issuers durante la transición y reautorizar cada conector.

**Un `resource` por token.** El endpoint rechaza múltiples valores de `resource`
en `/authorize`: un token con dos audiencias es un token que sirve en dos
sitios, y eso es exactamente lo que la validación de `aud` intenta evitar.

**Reusar un código o un refresh token revoca la cadena.** No es paranoia: es la
única señal disponible de que alguien copió un token. Ver
`test_codigo_es_de_un_solo_uso_y_el_replay_revoca` y `test_refresh_rota_y_detecta_reuso`.

**El token de Claude no viaja a Odoo.** `AccessToken` ni siquiera conserva el JWT
original —hay un test que lo comprueba por reflexión—, así que no hay forma
accidental de reenviarlo. Odoo se llama con la credencial del servidor.

**Los access tokens duran 15 minutos.** Como son JWT, nadie los consulta contra
el AS; revocar corta la renovación, no el token vivo. Si necesitas un corte
instantáneo: `deploy/rollback-acceso.sh` o rotar la llave de firma.

## Desplegar

Paso a paso para Cloud Shell, con los bloques listos para pegar:
[`deploy/CLOUD-SHELL.md`](deploy/CLOUD-SHELL.md). Resumen:

```bash
export PROJECT=odoo-serverless-ss-001
export REGION=southamerica-west1

deploy/00-inventario.sh          # qué existe hoy (no modifica nada)
deploy/01-infraestructura.sh     # APIs, Firestore, service account, llave RSA

export GOOGLE_CLIENT_ID=...      # el cliente OAuth que ya existe en el proyecto
export GOOGLE_CLIENT_SECRET=...
deploy/02-desplegar-as.sh        # despliega sellside-auth

deploy/03-configurar-mcp.sh      # apunta el MCP a su AS (requiere el código ya integrado)

USE_IAM_TOKEN=1 deploy/04-verificar.sh   # las cinco comprobaciones, con el MCP aún cerrado
deploy/05-abrir-acceso.sh                # el paso irreversible; verifica antes y pide confirmación
```

En el cliente OAuth de Google hay que autorizar una redirect URI:
`${ISSUER}/callback/google`.

Después: **Claude → Ajustes → Conectores → Añadir conector personalizado**, con
la URL del MCP.

### Verificar antes de abrir

Mientras el MCP siga cerrado por IAM, una petición sin credenciales la corta
Cloud Run con 403 y nunca llega a tu código. Por eso `USE_IAM_TOKEN=1`: manda un
ID token de Google, que pasa el filtro de IAM y llega al middleware, y el
middleware debe rechazarlo igual —no es un access token para este recurso—.
Ese 401 lo produce tu código, que es justo lo que hay que comprobar antes de
poner `allUsers`.

## Integrar un MCP

```python
from mcp_resource_server import (
    BearerAuthMiddleware, TokenVerifier, current_token, load_resource_config,
    ODOO_POLICY, OdooCredentials,
)

config = load_resource_config()   # lee RESOURCE_URI y AUTH_SERVER del entorno
app.add_middleware(BearerAuthMiddleware, config=config, verifier=TokenVerifier(config))

@app.post("/mcp")
async def mcp(request: Request):
    token = current_token(request)
    ODOO_POLICY.check(nombre_de_la_herramienta, token)   # lanza 403 si falta el scope
    ...
```

Ejemplo completo y ejecutable en `examples/odoo_mcp_app.py`.

Variables de entorno del MCP:

| Variable | Ejemplo |
|---|---|
| `RESOURCE_URI` | `https://odoo-mcp-sellside-….run.app/mcp` |
| `AUTH_SERVER` | `https://sellside-auth-….run.app` |
| `SCOPES_SUPPORTED` | `odoo:read,odoo:write` |
| `PROTECTED_PATHS` | `/mcp` |

## Configuración del AS

| Variable | Por defecto | Nota |
|---|---|---|
| `ISSUER` | — | Obligatoria. No cambiarla después del primer token. |
| `JWT_SIGNING_KEY` | — | PEM RSA. En Cloud Run llega desde Secret Manager. |
| `PROTECTED_RESOURCES` | — | URIs canónicas de los MCP, separadas por coma. |
| `STORAGE_BACKEND` | `memory` | En Cloud Run: `firestore`. |
| `IDP_MODE` | `google` | `dev` solo en local; se niega a arrancar con issuer público. |
| `ALLOWED_EMAIL_DOMAINS` | — | Ej. `sellside.cl`. Vacío = cualquier cuenta de Google. |
| `ALLOWED_REDIRECT_HOSTS` | `claude.ai,claude.com,localhost,127.0.0.1` | Filtro del registro dinámico. |
| `ACCESS_TOKEN_TTL` | `900` | Segundos. |
| `REFRESH_TOKEN_TTL` | `2592000` | 30 días. |
| `REQUIRE_RESOURCE_PARAM` | `true` | Exigir `resource` en `/authorize`. |
| `REGISTRATION_RATE_LIMIT` | `20` | Registros por hora y por IP, por instancia. |

## Desarrollo local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/dev-key.pem
export JWT_SIGNING_KEY_FILE=/tmp/dev-key.pem
export ISSUER=http://localhost:8080
export PROTECTED_RESOURCES=http://localhost:9090/mcp
export IDP_MODE=dev          # login de mentira, solo para localhost

uvicorn main:app --port 8080 --reload
```

```bash
pytest -q     # 41 pruebas: flujo completo, PKCE, rotación, aud, scopes
```

Las pruebas levantan el AS y un MCP de juguete en memoria y recorren el flujo
entero, incluidos los casos que importan: token de otro recurso, `alg=none`,
código reutilizado, refresh reutilizado, escritura con scope de lectura.

## Revocación

```bash
python tools/revocar.py listar --sujeto google:1234567890
python tools/revocar.py sujeto google:1234567890    # corta a una persona
python tools/revocar.py cliente c_AbCdEf            # corta a un conector
deploy/rollback-acceso.sh                           # corta a todo el mundo, ya
```

## Rotar la llave de firma

Invalida **todos** los access tokens en circulación de inmediato. Los refresh
tokens sobreviven —viven en Firestore, no en el JWT—, así que los clientes se
recuperan solos en el siguiente refresh.

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/nueva.pem
gcloud secrets versions add oauth-jwt-signing-key --data-file=/tmp/nueva.pem
shred -u /tmp/nueva.pem
gcloud run services update sellside-auth --region "$REGION" \
  --set-secrets=JWT_SIGNING_KEY=oauth-jwt-signing-key:latest
```

El `kid` se deriva de la llave, así que el JWKS publica el nuevo sin coordinar
identificadores. Los resource servers refrescan al ver un `kid` desconocido.

## Pendiente antes de producción

- [x] Issuer definitivo: la URL `*.run.app` (ver «Decisiones que conviene conocer»)
- [ ] Autorizar `${ISSUER}/callback/google` en el cliente OAuth de Google
- [ ] Integrar el middleware en el código real de `odoo-mcp-sellside`
- [ ] ACLs de solo lectura (o escritura acotada) en el usuario de Odoo del MCP
- [ ] Revisar `SEGURIDAD.md` completo antes de correr `05-abrir-acceso.sh`
