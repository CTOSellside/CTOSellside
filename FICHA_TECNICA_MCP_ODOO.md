# MCP de Odoo — Descripción técnica y guía de réplica

**Autor:** CTO Sellside · **Fecha:** 28 de julio de 2026 · **Estado:** vigente
**Repositorio oficial:** `Sellside-SpA/rosa-control-center` (privado)

> ⚠️ **Advertencia sobre la pieza B.** El análisis de Rosa Control Center (§4)
> se hizo leyendo **`CTOSellside/rosa-control-center` en el commit `9e54001`**,
> que **no es el repositorio oficial**. El oficial es
> `Sellside-SpA/rosa-control-center`. Hasta contrastar ambos, todo lo que este
> documento afirma sobre Rosa —incluido que `/mcp/sse` está sin autenticación—
> debe tratarse como referido a un espejo posiblemente desactualizado. Ver
> [Anexo A](#anexo-a--qué-verifiqué-y-qué-no), punto 5.

Documento de arquitectura del conector MCP de Odoo tal como está construido hoy,
escrito con el objetivo explícito de **replicarlo para otra empresa en otra cuenta
de GCP**. Todo lo que sigue sale de leer el código, no de la memoria de nadie: cada
afirmación no obvia lleva su archivo y línea.

Al final, en el [Anexo A](#anexo-a--qué-verifiqué-y-qué-no), está la lista de lo
que **no** pude verificar. Léela antes de tomar decisiones de despliegue.

---

## 1. Resumen ejecutivo

Lo que llamamos «el MCP de Odoo» son en realidad **tres piezas distintas**, en
**dos proyectos de GCP**, con tres posturas de seguridad muy diferentes:

| # | Pieza | Qué es | Dónde vive | Auth |
|---|---|---|---|---|
| **A** | `@netlinksinc/odoo-mcp` v0.2.2 | El conector real, en producción | Cloud Run `odoo-mcp-sellside` | OAuth 2.1 + PKCE, embebido |
| **B** | MCP de Rosa Control Center | 5 herramientas sobre SSE dentro del BFF | Cloud Run `rosa-control-center` | **Ninguna** |
| **C** | `sellside-oauth` | Servidor de autorización externo (sin desplegar) | PR #1, sin mergear | OAuth 2.1, AS compartido |

**La pieza A es la que hay que replicar.** Es la única con calidad de producto:
10 herramientas genéricas, 7 recursos de capacidad, OAuth 2.1 completo,
credenciales cifradas en reposo y multi-usuario. Las piezas B y C son contexto
necesario —B porque es un riesgo activo, C porque explica una decisión de
arquitectura que está a medio tomar.

**El hallazgo más importante de este documento** está en la
[sección 6.3](#63-el-user-store-en-cloud-run--defecto-crítico): el almacén de
usuarios y tokens de la pieza A es **un archivo JSON en disco local**. En Cloud
Run eso es un sistema de archivos efímero en memoria. Cada arranque en frío borra
todos los tokens emitidos, y cualquier valor de `max-instances` mayor que 1 parte
el conjunto de usuarios entre instancias. **Esto condiciona toda la réplica** y
hay que resolverlo antes de vender el modelo a un segundo cliente.

---

## 2. Mapa del patrimonio

### 2.1 Proyectos de GCP

| Proyecto | Número | Región | Servicios |
|---|---|---|---|
| `odoo-serverless-ss-001` | `843056793102` | `southamerica-west1` | `odoo-mcp-sellside`, `sellside-auth` (planificado) |
| *(sin nombrar en el código)* | `30942737227` | `us-central1` | `rosa-control-center`, `sellside-workspace-search` |

El número de proyecto aparece en las URLs de Cloud Run, que es como quedaron
registrados. Fuentes:
`sellside-oauth/deploy/env.sh:12-31` y `examples/odoo_mcp_app.py:7-8` para el
primero; `rosa-control-center/backend/test-remote.js:3` y
`backend/services/workspace.service.js:6` para el segundo.

> **Para la réplica:** que el patrimonio esté partido en dos proyectos y dos
> regiones no es un diseño, es sedimento histórico. En una cuenta nueva, un solo
> proyecto y una sola región.

### 2.2 Repositorios

| Repo | Rol | Estado |
|---|---|---|
| **`Sellside-SpA/rosa-control-center`** | **Repositorio oficial.** BFF + SPA, expone la pieza B, y hogar de este documento | **Canónico — no verificado en este análisis** |
| `CTOSellside/odoo-mcp` | Fork de `NETLINKSAF/odoo-mcp` — el código de la pieza A | Sin modificaciones locales |
| `CTOSellside/odoo-mcp-sellside` | Fuente rescatado del build en producción (§3.10a) | Rama `rescate/oauth21`, por consolidar |
| `CTOSellside/rosa-control-center` | **Espejo o fork** del oficial — es lo que se leyó para §4 | Posiblemente desactualizado |
| `CTOSellside/CTOSellside` → `sellside-oauth/` | Pieza C, en PR #1 | Sin mergear |
| `CTOSellside/odoo-mcp-integration` | — | **Vacío** (solo un README con el título) |
| `CTOSellside/rosalia-odoo` | SPA React, cliente de Odoo | No participa del MCP |

El fork `odoo-mcp` está **idéntico al upstream**: un solo commit, `63ae499`,
`chore(release): v0.2.2 — OAuth hotfix bundle`, autoría de NETLINKS. No hay
commits de Sellside encima, y **no contiene ninguna configuración de despliegue
para GCP** — solo `Dockerfile` y `fly.toml`. Ver
[Anexo A](#anexo-a--qué-verifiqué-y-qué-no).

---

## 3. Pieza A — `@netlinksinc/odoo-mcp` v0.2.2

### 3.1 Identificación y confirmación

Publicado en npm como `@netlinksinc/odoo-mcp`, licencia MIT, mantenido por
NETLINKS Inc. Upstream: `github.com/NETLINKSAF/odoo-mcp`.

**Confirmación de que este es el código en producción.** El conector vivo
`Rosa_Odoo_MCP_GCP` expone esquemas que coinciden con el fuente del fork de forma
inequívoca:

| Evidencia | Conector vivo | `packages/odoo-mcp/src/tools/schemas.ts` |
|---|---|---|
| Regex de modelo | `^[a-z][a-z0-9_.]*$` | línea 8 |
| Regex de método | `^[a-z_][a-z0-9_]*$` | línea 14 |
| Límite por defecto | `80` | línea 25 |
| Contexto multi-compañía | `allowed_company_ids`, `active_company_id` | líneas 17-18 |
| Descripción de `odoo_execute` | *"Call any model method (execute_kw)… regex-validated"* | `tools/execute.ts:36` |

Coincidencia exacta, incluidas las cadenas de descripción. No es una
reimplementación parecida: es este código.

### 3.2 Estructura

Monorepo pnpm, Node ≥ 22, TypeScript, dos paquetes:

```
packages/
├── odoo-client/     # Cliente JSON-RPC de Odoo, sin dependencias
│   ├── rpc.ts       # Transporte, timeout 30 s, mapeo de errores
│   ├── auth.ts      # ApiKeyAuthStrategy (+ fallback de cookie, sin usar)
│   ├── client.ts    # Métodos ORM sobre execute_kw
│   └── sanitize.ts  # Redacción de PII antes de loguear
└── odoo-mcp/        # El servidor MCP
    ├── bin.ts              # Entrypoint, bifurca stdio/http, subcomandos CLI
    ├── config.ts           # Validación de entorno con zod, falla rápido
    ├── server.ts           # Ensambla el McpServer (fábrica por sesión)
    ├── http-transport.ts   # Servidor HTTP, ruteo, auth, rate limiting  (883 líneas)
    ├── oauth.ts            # Servidor de autorización OAuth 2.1         (734 líneas)
    ├── user-store.ts       # Allowlist + tokens + credenciales cifradas (349 líneas)
    ├── encryption.ts       # AES-256-GCM
    ├── admin.ts            # API de administración
    ├── client-cache.ts     # Caché LRU de clientes Odoo por usuario
    ├── probe.ts            # Sondeo de capacidades al arrancar
    └── tools/              # Las 10 herramientas
```

**~5.800 líneas de TypeScript** de fuente, sin contar tests. Dependencias de
producción: `@modelcontextprotocol/sdk ^1` y `zod ^3`. Nada más. `odoo-client` no
tiene dependencias en absoluto — el transporte es `fetch` nativo.

Es una superficie de dependencias notablemente pequeña para lo que hace, y eso
es una ventaja real para replicarlo: menos que auditar en cada cliente nuevo.

### 3.3 Los dos modos de operación

Un solo binario, dos modos, seleccionados por `MODE` (`config.ts:28`):

| | `MODE=stdio` (por defecto) | `MODE=http` |
|---|---|---|
| Cliente | Claude Code (subproceso local) | Claude Desktop, Cowork (conector remoto) |
| Transporte | stdio | Streamable HTTP |
| Identidad | **Una sola** — la de `ODOO_USERNAME` | **Por usuario**, cada uno con su API key |
| Auth | Ninguna (local al proceso) | OAuth 2.1 + PKCE |
| Credenciales en reposo | Ninguna | AES-256-GCM en el user store |
| Variables extra requeridas | — | `MCP_ENCRYPTION_KEY`, `MCP_ADMIN_PASSWORD` |

La validación está condicionada al modo (`config.ts:94-115`): en `stdio` las
variables de OAuth no se exigen. Esto importa para la réplica porque **el modo
stdio es la vía de menor riesgo para un piloto**: sin superficie HTTP, sin
tokens que gestionar, sin el problema del user store.

### 3.4 Variables de entorno

**Siempre requeridas** (`config.ts:19-39`):

| Variable | Validación | Nota |
|---|---|---|
| `ODOO_URL` | URL válida, se le quita la barra final | `http://` emite advertencia de texto plano (`auth.ts:236-243`) |
| `ODOO_DB` | no vacía | En Odoo.sh: `<workspace>-<branch>-<id>` |
| `ODOO_USERNAME` | no vacía | Login del usuario de servicio |
| `ODOO_API_KEY` | no vacía | Preferencias → Seguridad de la cuenta → Nueva clave API |

**Solo en `MODE=http`:**

| Variable | Validación | Consecuencia si falta |
|---|---|---|
| `MCP_ENCRYPTION_KEY` | base64 que decodifique a **exactamente 32 bytes** | `process.exit(1)` (`config.ts:94-104`) |
| `MCP_ADMIN_PASSWORD` | no vacía | `process.exit(1)` (`config.ts:106-115`) |
| `MCP_USER_STORE_PATH` | opcional | Por defecto `/var/lib/odoo-mcp/users.json` (`config.ts:137`) |
| `MCP_PUBLIC_URL` | URL válida, opcional | Sin ella, el issuer se deriva del header `Host` (`oauth.ts:199-209`) |
| `MCP_PORT` | 1-65535 | Por defecto 3000 |
| `MCP_TRUST_PROXY` | `true`/`false`/`1`/`0` | Por defecto `false` |

**Opcionales:** `ODOO_MCP_LOG_FILE` (se crea con permisos `0600`),
`ODOO_MCP_DEBUG=1` (incluye trazas de Python de Odoo en las respuestas de error
— **no usar en producción**, filtra internos del modelo).

**Retirada:** `MCP_BEARER_TOKEN` se ignora desde v0.2.1; si está presente el
servidor emite un `deprecation_warning` y sigue (`config.ts:82-91`).

Ante entrada inválida, `loadConfig` escribe `{"event":"config_error","missing":
[…],"invalid":[…]}` y sale con código 1. **Solo nombres de variables, nunca
valores** (`config.ts:60-62`) — un detalle deliberado para que los logs de
arranque no filtren secretos.

### 3.5 Endpoints HTTP

Ruteo completo en `http-transport.ts:497-809`. Esta es la superficie externa
entera:

| Método | Ruta | Auth | Qué hace |
|---|---|---|---|
| `GET` | `/health` | ninguna | Estado + `probe_ok`. **Payload redactado para llamadores remotos** |
| `GET` | `/.well-known/oauth-authorization-server` | ninguna | Metadatos RFC 8414 |
| `GET` | `/.well-known/oauth-protected-resource` | ninguna | Metadatos RFC 9728 |
| `GET` | `/.well-known/oauth-protected-resource/mcp` | ninguna | Ídem, variante con ruta |
| `POST` | `/oauth/register` | ninguna | Registro dinámico de cliente (RFC 7591) |
| `GET` | `/oauth/authorize` | ninguna | Pantalla de consentimiento (HTML) |
| `POST` | `/oauth/authorize` | CSRF | Envío del formulario → redirección con `code` |
| `POST` | `/oauth/token` | PKCE | Canje de código por access token |
| `GET` | `/admin/users` | `MCP_ADMIN_PASSWORD` | Lista la allowlist |
| `POST` | `/admin/users` | `MCP_ADMIN_PASSWORD` | Agrega un email a la allowlist |
| `DELETE` | `/admin/users/:email` | `MCP_ADMIN_PASSWORD` | Revoca usuario, tokens y caché |
| `POST` | `/mcp` | Bearer OAuth | JSON-RPC de MCP (inicia sesión si no hay `Mcp-Session-Id`) |
| `GET` | `/mcp` | Bearer OAuth | Stream SSE de la sesión |
| `DELETE` | `/mcp` | Bearer OAuth | Cierra la sesión |
| *cualquiera* | *otra* | Bearer OAuth | `404 not_found` |

Dos detalles del ruteo que importan:

1. **El orden no es accidental.** `/health`, `/.well-known/*`, `/oauth/*` y
   `/admin/*` se resuelven **antes** del control de token (`:501-605`). Todo lo
   demás cae al bloque de autenticación de `:626-658`. Es decir, el catch-all 404
   también exige token — no se puede sondear el árbol de rutas sin credencial.

2. **El rate limit de auth se evalúa antes que la auth misma** (`:608-623`), para
   que una IP baneada no llegue siquiera a probar un token nuevo.

#### Redacción de `/health`

`isLocalHealthCaller` (`:333-348`) decide si el payload lleva `odoo_url` y
`odoo_db`:

| Socket | XFF | `trustProxy` | Resultado |
|---|---|---|---|
| no loopback | — | — | Redactado |
| loopback | ausente | — | Completo |
| loopback | presente | `false` | **Redactado** (XFF es falsificable) |
| loopback | presente | `true` | Decide según el primer salto del XFF |

> **Para la réplica:** en Cloud Run el tráfico llega desde el frontend de Google,
> no desde loopback, así que `/health` siempre devuelve la versión redactada
> `{ok, mode, probe_ok}`. Es el comportamiento correcto. **No pongas
> `MCP_TRUST_PROXY=true`** salvo que tengas un proxy en el mismo host, que en
> Cloud Run no es el caso.

### 3.6 Las 10 herramientas

Registradas en `tools/index.ts`. Todas aceptan `allowed_company_ids` y
`active_company_id` opcionales.

| Herramienta | Firma esencial | Validación específica |
|---|---|---|
| `odoo_search_read` | `model, domain, fields, limit=80, offset=0, order` | `limit` entero positivo |
| `odoo_read` | `model, ids[≥1], fields` | ids enteros positivos |
| `odoo_create` | `model, values` (objeto o array) | — |
| `odoo_write` | `model, ids[≥1], values` | — |
| `odoo_unlink` | `model, ids[≥1]` | — |
| `odoo_search_count` | `model, domain` | — |
| `odoo_execute` | `model, method, args, kwargs` | `method` contra `^[a-z_][a-z0-9_]*$` |
| `odoo_run_report` | `report_id, doc_ids[≥1]` | Devuelve PDF en base64 |
| `odoo_call_action` | `model, ids[≥1], action_name, context` | `action_name` con el regex de método |
| `odoo_fields_get` | `model, attributes` | Introspección de esquema |

**Todo `model` pasa por `^[a-z][a-z0-9_.]*$`** (`schemas.ts:5-8`), como defensa
en profundidad contra nombres que se escapen del `dotted_snake_case` que Odoo
espera.

La superficie es **deliberadamente genérica**: no hay herramientas por módulo.
Funciona sobre cualquier Odoo, incluidos módulos custom que no existían cuando se
escribió el servidor — Claude descubre qué hay instalado leyendo los recursos.
**Esto es exactamente lo que hace el modelo replicable sin trabajo de
integración por cliente.**

#### Los 7 recursos

Poblados una sola vez al arrancar por el sondeo de capacidades, servidos desde un
cierre sobre el snapshot — sin reconsultar a Odoo (`resources.ts:11-44`):

`odoo://modules` · `odoo://reports` · `odoo://server-actions` ·
`odoo://companies` · `odoo://currencies` · `odoo://fiscal-year` ·
`odoo://user-context`

Si un campo del sondeo falló, el recurso devuelve el objeto `{error: …}` en vez
de lanzar. Transparencia sobre el fallo, no ocultamiento.

### 3.7 El flujo OAuth 2.1

Implementado en `oauth.ts`. Es un servidor de autorización **completo y
embebido** en el propio MCP.

```
Claude Desktop / Cowork
  │
  ├─1─> GET  /.well-known/oauth-protected-resource     → descubre el AS
  ├─2─> GET  /.well-known/oauth-authorization-server   → metadatos RFC 8414
  ├─3─> POST /oauth/register                           → DCR, obtiene client_id
  ├─4─> GET  /oauth/authorize?…code_challenge=…S256    → pantalla de consentimiento
  │        el usuario escribe su email + su API key de Odoo
  ├─5─> POST /oauth/authorize                          → valida y redirige con ?code
  ├─6─> POST /oauth/token   (code_verifier)            → access token
  └─7─> POST /mcp  Authorization: Bearer <token>       → ya opera
```

Lo que ocurre en el paso 5, en orden (`oauth.ts:459-567`):

1. **CSRF**: el `csrf_token` del formulario se compara con la cookie `mcp-csrf`
   mediante `timingSafeEqual`. La cookie es `HttpOnly; SameSite=Strict;
   Path=/oauth/authorize; Max-Age=600` (`:150`).
2. **Allowlist**: si el email no está autorizado, página de error genérica —
   **la misma respuesta exista o no el email** (`:502-513`).
3. **Verificación contra Odoo**: llama a `common.authenticate` **directamente por
   `jsonRpc`**, no por `execute_kw` (`:515-528`). Este fue uno de los cinco
   hotfixes de v0.2.2: enrutarlo por `object.execute_kw` devolvía falsy en
   silencio y se manifestaba como «credenciales inválidas» con credenciales
   correctas.
4. **Cifrado** de la API key y emisión del código: 16 bytes aleatorios en hex,
   TTL de 10 minutos, un solo uso.

Y en el paso 6 (`:574-731`): tipo de contenido, longitudes de campo, existencia
del código, **replay**, expiración, coincidencia de `client_id` y de
`redirect_uri`, y finalmente PKCE con comparación de tiempo constante. Solo
entonces se marca el código como usado y se emite el token.

**Parámetros del AS embebido:**

| Parámetro | Valor | Fuente |
|---|---|---|
| PKCE | **Solo S256** — se rechaza `plain` | `oauth.ts:438` |
| `token_endpoint_auth_methods_supported` | `["none"]` (cliente público) | `:245` |
| `grant_types_supported` | `["authorization_code"]` — **sin refresh** | `:244` |
| TTL del código | 600 s, un solo uso | `:558` |
| **TTL del access token** | **Ninguno** | ver abajo |
| Tope de tokens por usuario | 10, FIFO | `user-store.ts:213-219` |
| Rate limit de DCR | 10 por IP / 60 s | `oauth.ts:90-91` |
| Tope de clientes registrados | 1000 | `:88` |
| Tope de códigos pendientes | 1000 | `:89` |
| `redirect_uri` permitidas | `https://` o loopback | `:339-352` |

> **Los access tokens no expiran.** No hay TTL ni refresh: el token vale hasta que
> alguien lo revoque explícitamente. Está documentado como decisión consciente en
> `docs/v0.2.1-oauth.md:146-149`. Para un cliente regulado esto no pasa una
> auditoría — ver [sección 8](#8-riesgos-y-deuda-técnica).

### 3.8 El user store

`user-store.ts`. Un único archivo JSON con esta forma:

```jsonc
{
  "version": 1,
  "users": [{
    "email": "…",                  // texto plano, minúsculas
    "status": "allowed|registered",
    "registered_at": "…",
    "encrypted_api_key": "…",      // base64(iv[12] ‖ ciphertext ‖ authTag[16])
    "odoo_url": "…", "odoo_db": "…"
  }],
  "tokens": [{
    "token_hash": "…",             // SHA-256 hex — el token crudo NUNCA se guarda
    "email": "…", "issued_at": "…"
  }]
}
```

Propiedades relevantes:

- **Escritura atómica**: escribe a `.tmp`, `rename`, `chmod 0600` en cada flush
  (`:107-129`). Los flush se serializan en una cola de promesas (`:342-345`).
- **Los tokens se guardan solo como hash SHA-256.** `resolveToken` (`:245-262`)
  recorre el mapa comparando con `timingSafeEqual`. Es O(n) por petición, pero n
  está acotado por 10 × usuarios.
- **Un registro que no descifra se salta, no rompe el arranque** (`:317-334`). Es
  la vía de degradación cuando se rota `MCP_ENCRYPTION_KEY`.
- **Un fallo de flush no se relanza** (`:122-128`): se loguea y el proceso sigue
  con el estado en memoria. Consecuencia directa: en Cloud Run, donde el flush
  «funciona» pero escribe a un disco efímero, **el fallo es silencioso**.

**Cifrado** (`encryption.ts`): AES-256-GCM, IV fresco de 12 bytes por operación,
tag de autenticación de 16 bytes. `decrypt` **no captura** el error de
`decipher.final()` a propósito — es el mecanismo de detección de manipulación
(`:72-75`).

**Rotación de `MCP_ENCRYPTION_KEY` es destructiva.** No hay re-cifrado versionado:
los registros bajo la llave vieja fallan al descifrar y se saltan, y cada usuario
afectado tiene que volver a hacer el flujo OAuth
(`docs/v0.2.1-oauth.md:152-165`).

### 3.9 Controles de seguridad — inventario verificado

| Control | Implementación | Ubicación |
|---|---|---|
| Contenedor sin root | `adduser -S app`, `USER app` | `Dockerfile:24-26` |
| HSTS | `max-age=31536000; includeSubDomains` cuando `X-Forwarded-Proto: https` | `http-transport.ts:285-289` |
| Advertencia de TLS ausente | Si en 60 s no se ve tráfico https, avisa por stderr | `:837-848` |
| Tope de cuerpo | 64 KiB en `/mcp`, `/oauth/*` y `/admin/*` → 413 | `:148`, `oauth.ts:92`, `admin.ts:60` |
| Rate limit de auth | 20 fallos por IP / 60 s → 429 | `:184-186` |
| Rate limit de admin | 5 fallos por IP / 60 s → 429 | `admin.ts:120-124` |
| Rate limit de DCR | 10 registros por IP / 60 s | `oauth.ts:286-289` |
| Tope de sesiones MCP | 100 concurrentes → 503 | `:151`, `:720-724` |
| Timeout de sesión inactiva | 30 min, barrido cada 5 min | `:166-169` |
| Comparación en tiempo constante | Tokens, CSRF, PKCE, contraseña de admin | `user-store.ts:254`, `oauth.ts:176`, `:697`, `admin.ts:158` |
| Validación de caracteres de XFF | Fuera del conjunto seguro → `'invalid'` | `:296-310` |
| Redacción de PII en logs | `password\|credit_card\|token\|secret\|api_key` | `sanitize.ts:4` |
| Timeout de RPC a Odoo | 30 s con `AbortController` | `rpc.ts:16`, `:80-81` |
| Errores internos sin filtración | Devuelve `{error_type: "InternalError"}` genérico | `tools/execute.ts:124-134` |
| Barrido de mapas de rate limit | Tope duro de 10.000 entradas, evicción del 25% | `:153-157`, `:200-221` |

#### El control más interesante: la identidad no se puede sobrescribir

`buildContext` (`context.ts:11-32`) construye el contexto de Odoo mezclando
`session.userContext` → `extraContext` del llamador → y **reaplica al final** los
campos autoritativos `uid`, `allowed_company_ids` y `company_id`.

El orden es el control. Un `context` malicioso pasado a `odoo_call_action` no
puede suplantar a otro usuario ni saltar a otra compañía, porque sus valores se
pisan después. Y `validateCompanySubset` (`:39-48`) verifica además que toda
compañía pedida esté en el conjunto de la sesión.

Es la clase de detalle que separa un MCP de juguete de uno desplegable. Vale la
pena preservarlo tal cual en cualquier réplica.

#### Aislamiento por usuario

En modo HTTP cada petición resuelve su propio cliente de Odoo:

```
petición → token → email  (http-transport.ts:644-658)
  → AsyncLocalStorage con {user_id: email}   (:779-794)
    → clientResolver lee el ALS              (bin.ts:81-99)
      → caché LRU por email, o construye uno nuevo
        con las credenciales descifradas de ese usuario
```

Caché LRU de 100 entradas, TTL de inactividad de 30 minutos
(`bin.ts:72-77`, `client-cache.ts`). **Cada llamada a Odoo va con la API key del
usuario final**, así que los permisos de Odoo y la pista de auditoría son los de
esa persona, no los de una cuenta de servicio compartida. Es el argumento
comercial más fuerte del diseño.

### 3.10 Configuración desplegada hoy — verificada

`gcloud run services describe odoo-mcp-sellside --region southamerica-west1
--project odoo-serverless-ss-001`, ejecutado el 28-jul-2026. Revisión activa
`odoo-mcp-sellside-00016-zdv`, última actualización 28-jul-2026 00:33 UTC.

| Parámetro | Valor desplegado | Observación |
|---|---|---|
| Imagen | `…/cloud-run-source-deploy/odoo-mcp-sellside:oauth21` | Tag manual, no un SHA |
| Puerto | 8080 | `MCP_PORT=8080` coherente |
| Recursos | 512 Mi / 1000 m | |
| Concurrencia | 80 | |
| Timeout | 300 s | Menor que el de arranque del propio servicio |
| Ingress | `all` | Alcanzable desde internet |
| **Escalado** | **Min 0 / Max 1** a nivel de servicio, **Max 5** en la anotación de revisión | Ver más abajo |
| **Volúmenes** | **Ninguno** | Confirma §6.3 |
| **Service account** | **`843056793102-compute@developer.gserviceaccount.com`** | La **default de compute** |
| Startup probe | TCP cada 240 s, timeout 240 s, umbral 1 | No usa `/health` |

**Variables de entorno:** `MODE=http`, `MCP_PORT=8080`, `AUTH_SERVER`,
`RESOURCE_URI`, `RESOURCE_NAME`, `SCOPES_SUPPORTED=odoo:read,odoo:write`.

**Secretos:** `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME` y `ODOO_API_KEY` ← este
último mapeado desde el secreto **`SELLSIDE_ODOO_PASSWORD`**.

De aquí salen seis lecturas, en orden de gravedad.

#### (a) Lo desplegado es un fork del fork, y su fuente no está versionado

`MODE=http` está puesto, pero **`MCP_ENCRYPTION_KEY` y `MCP_ADMIN_PASSWORD` no
están definidas** — ni como variable ni como secreto. Según `config.ts:94-115`
esa combinación termina en `process.exit(1)` antes de abrir el puerto, con una
guarda incondicional.

Y sin embargo el servicio arranca limpio. Sondeo del 28-jul-2026:

```
$ curl -s .../health
{"ok":true,"mode":"http","probe_ok":true}

$ gcloud run services logs read … | grep -i "config_error\|startup_error"
(sin resultados)

$ curl -si -X POST .../mcp | head -5
HTTP/2 401
strict-transport-security: max-age=31536000; includeSubDomains
content-type: application/json
www-authenticate: Bearer realm="odoo-mcp-sellside", resource_metadata="https://…/.well-known/oauth-protected-resource/mcp"
cache-control: no-store
```

Contrastando esas respuestas contra el fuente del fork, la imagen resulta ser
**el fork con modificaciones locales**:

| Señal observada | Fork v0.2.2 | Producción | |
|---|---|---|---|
| `/health` para llamador remoto | `{ok, mode, probe_ok}` (`:531`) | idéntico, mismo orden de claves | ✅ fork |
| HSTS | `max-age=31536000; includeSubDomains` (`:287`) | idéntico | ✅ fork |
| Esquemas de herramientas | regex, `limit` 80 (§3.1) | idénticos | ✅ fork |
| `realm` del `WWW-Authenticate` | **`"MCP"`, literal** (`:265`) | **`"odoo-mcp-sellside"`** = `RESOURCE_NAME` | ⚠️ modificado |
| Ruta de `resource_metadata` | `/.well-known/oauth-protected-resource` (`:264`) | **…`/mcp`** | ⚠️ modificado |
| `cache-control: no-store` | no lo emite (`:269-278`) | presente | ⚠️ modificado |
| Arranque sin `MCP_ENCRYPTION_KEY` | `exit(1)` (`config.ts:94-104`) | arranca | ⚠️ modificado |

**El fuente fue recuperado.** Extraído de la imagen en ejecución
(`sha256:12dadcc9…`) el 28-jul-2026 y publicado en el repositorio privado
**`CTOSellside/odoo-mcp-sellside`**, rama `rescate/oauth21`. Lo que sigue sale de
leerlo, ya no de inferirlo por cabeceras HTTP.

**No es un parche improvisado: es una migración deliberada y bien hecha.** El
build es v0.2.2 con exactamente cinco archivos modificados —el resto de las
diferencias son fines de línea— más un directorio nuevo y un pipeline:

| Cambio | Detalle |
|---|---|
| **Eliminados** | `oauth.ts`, `user-store.ts`, `encryption.ts`, `consent-page.ts`, `admin.ts`, `client-cache.ts`, `cli-auth.ts`, `cli-users.ts` — **el servidor de autorización embebido completo** |
| **Añadido** | `src/resource-server/` — 553 líneas, port a TypeScript de `mcp_resource_server/` del PR #1 |
| **Modificados** | `http-transport.ts` (234 l.), `bin.ts` (209 l.), `server.ts` (134 l.), `config.ts` (79 l.), `types.ts` (24 l.) |
| **Nuevo** | `cloudbuild-odoo-mcp.yaml` — el pipeline de despliegue |

Es decir: **el desajuste Node/Python de §5 ya está resuelto.** Alguien portó el
resource server de Python a TypeScript y lo cableó. La calidad del port es buena:

- **`verifier.ts`** — verificación de JWT con `jose`: lista blanca `RS256`
  (cierra `alg:none` y la confusión HS256), descubrimiento del JWKS con
  comprobación de que el `issuer` publicado coincide con el configurado,
  **comparación exacta de `aud` contra la URI canónica**, y `requiredClaims`
  sobre `exp/iat/iss/aud/sub`. El `AccessToken` **no conserva el JWT original**,
  así que no hay nada que reenviar a Odoo aunque se quisiera — es la prohibición
  de passthrough hecha estructura, no disciplina.
- **`scopes.ts`** — mapa herramienta → scope para las 10 herramientas, con
  denegar-por-defecto. `odoo_execute` y `odoo_call_action` exigen `odoo:write`
  porque invocan métodos arbitrarios y no se puede saber de antemano si escriben:
  es el lado seguro del error.
- **`server.ts:72-131`** — la política está **realmente conectada**, y por
  partida doble: un `Proxy` sobre `registerTool` filtra el catálogo para que
  `tools/list` anuncie solo lo usable, **y cada `tools/call` revalida el scope
  contra el token de esa petición**. Lo segundo cubre el caso de una sesión HTTP
  abierta con un token y reutilizada después con otro.
- **`config.ts`** — no «relajó» la validación: **retiró las variables** que ya no
  aplican y agregó una lista `RETIRED_ENV_VARS` que **avisa si alguna sigue
  puesta**, precisamente para que nadie crea que el servicio está protegido por
  algo que ya no hace nada. Y falla cerrado: `loadResourceConfig` lanza si faltan
  `RESOURCE_URI` o `AUTH_SERVER`, así que el servidor **se niega a arrancar si no
  puede validar tokens**.

Corrijo entonces lo que escribí antes con menos información: caractericé esto
como «un build que relajó la validación». Es lo contrario — es una migración de
un AS embebido a uno externo, documentada en el propio código, y con el modelo de
autorización más estricto que el original.

**Lo que sí era cierto y sigue siéndolo:** hasta hoy ese fuente no estaba en
ningún repositorio. Ya lo está, pero el trabajo no termina ahí — ver §9.

#### (b) `Min: 0` — sin consecuencia para este build

Escrito antes de recuperar el fuente, esto era un riesgo serio: el servicio
escala a cero y no tiene volúmenes, así que un user store en disco se perdería en
cada arranque en frío.

**Ya no aplica al servicio actual.** El build eliminó `user-store.ts` por
completo: no hay estado en disco que perder. Las sesiones se validan contra JWT
firmados por el AS externo, y el JWKS se re-descubre solo.

Sigue aplicando, en cambio, a **cualquier réplica construida desde el fork
limpio**, que es de lo que trata §6.3. La conclusión práctica está en §6.1: la
base correcta para replicar ya no es el fork, sino este repositorio.

#### (c) El escalado se contradice consigo mismo

`Scaling: Auto (Min: 0, Max: 1)` a nivel de servicio, pero
`Scaling: Max instances: 5` en la anotación de la revisión. El valor de servicio
manda sobre el de revisión, así que el efectivo debería ser **1** — pero conviene
confirmarlo, porque el 5 es exactamente lo que fija
`03-configurar-mcp.sh:11-12`.

#### (d) El script 03 sí se ejecutó, sobre el servicio equivocado

`AUTH_SERVER`, `RESOURCE_URI`, `RESOURCE_NAME` y `SCOPES_SUPPORTED` son
literalmente las cuatro variables que fija `03-configurar-mcp.sh:15-17`. Están
puestas. El script corrió.

Pero esas variables las consume el middleware **Python** de `sellside-oauth`, y
la validación de entorno de la pieza A (`config.ts:19-39`) ni las conoce — zod
descarta las claves desconocidas en silencio. Es exactamente lo que el propio
script advierte en su cabecera: *«lo único que consigues es un servicio con
variables de entorno bonitas»*. El desajuste Node/Python de §5 no es teórico:
**ya se materializó en producción**.

#### (e) Corre con la service account default de compute

`843056793102-compute@developer.gserviceaccount.com` trae por defecto el rol
`Editor` sobre todo el proyecto. Un compromiso del MCP no se queda en Odoo: se
lleva el proyecto entero.

Es precisamente contra esto que `01-infraestructura.sh:25-26` deja escrito *«No
se usa la default de compute: toda la flota cuelga de ella y no conviene darle
otro rol crítico»*. La recomendación existe y no se aplicó a este servicio.

#### (f) El secreto se llama `SELLSIDE_ODOO_PASSWORD`

`ODOO_API_KEY` se alimenta del secreto `SELLSIDE_ODOO_PASSWORD`. El nombre puede
ser solo herencia, pero si el valor **es la contraseña web** del usuario y no una
clave API:

- No se puede revocar sin cambiarle la contraseña a la persona.
- Sirve para entrar por la interfaz web de Odoo, no solo por RPC.
- No aparece en el listado de claves API, así que no hay rastro de su existencia.

El diseño de la pieza A asume una clave API por usuario, revocable de forma
independiente (`README.md:38-42`). Vale la pena confirmar qué hay dentro.

---

## 4. Pieza B — el MCP de Rosa Control Center

`rosa-control-center/backend/services/mcp.service.js` + `routes/mcp.routes.js`.
Un segundo servidor MCP, completamente independiente, dentro del BFF.

| Aspecto | Cómo está |
|---|---|
| Herramientas | 5: `search_read`, `read`, `create`, `write`, `unlink` |
| Transporte | **SSE** (`SSEServerTransport`) — el transporte legado de MCP |
| Rutas | `GET /mcp/sse`, `POST /mcp/message` |
| Autenticación | **Ninguna** |
| Protocolo hacia Odoo | XML-RPC (`/xmlrpc/2`), no JSON-RPC |
| Credenciales | Una cuenta de servicio compartida, desde Secret Manager |
| Instancia Odoo | Fija en `'MOM'` en las 5 herramientas |
| Validación de `model` | Ninguna — `z.string()` a secas |
| Exposición | `--allow-unauthenticated` en Cloud Run |

Hay que decirlo con claridad: **`/mcp/sse` y `/mcp/message` están abiertos a
internet sin ninguna autenticación**, y desde ahí se puede llamar `odoo_unlink`
sobre cualquier modelo de la instancia MOM con las credenciales del servicio.
`server.js:47-51` monta el router sin middleware de auth, y `cloudbuild.yaml:27`
despliega con `--allow-unauthenticated`.

Además hay un defecto de concurrencia: `mcp.routes.js:6` declara **un solo
`transport` a nivel de módulo**. El segundo cliente que se conecte sobrescribe la
referencia del primero, y los mensajes del primero se enrutan al stream del
segundo.

> **Para la réplica: no repliques esto.** Es el prototipo que quedó montado. La
> acción correcta sobre el sistema actual es cerrar `/mcp/*` de Rosa —o exigirle
> el mismo Bearer que el resto de la API— y dejar la pieza A como único MCP.
> Está fuera del alcance de este documento, pero es la recomendación.

---

## 5. Pieza C — `sellside-oauth`, el AS externo

PR #1, sin mergear. Un servidor de autorización OAuth 2.1 **externo** en Python
(FastAPI + Firestore), pensado como AS compartido para cinco MCP, más una
librería de resource server.

Está bien hecho: 41 tests en verde, PKCE S256 obligatorio, `aud` canónica por
RFC 8707, refresh rotativo con revocación en cadena, sin passthrough del token,
scopes con denegar-por-defecto. El detalle está en el cuerpo del PR #1.

**Y está en producción.** El servicio `sellside-auth` es el `AUTH_SERVER` contra
el que `odoo-mcp-sellside` valida sus tokens hoy (§3.10a). El PR #1 lista como
pendiente «Integrar el middleware en el código real de `odoo-mcp-sellside`» — esa
integración **ya se hizo**, portando `mcp_resource_server/` de Python a
TypeScript, y el port vive en `CTOSellside/odoo-mcp-sellside`.

Queda entonces un problema de forma, no de fondo: **el AS corre desde un PR sin
mergear, y su resource server desde una rama de rescate.** Dos servicios
productivos cuya rama canónica no existe. Es el mismo riesgo de §3.10(a),
duplicado.

> **Nota de corrección.** Una versión anterior de este documento afirmaba que
> había un desajuste Node/Python irresuelto y proponía tres salidas. Estaba
> escrito antes de recuperar el fuente y era incorrecto: la salida 1 —portar el
> middleware a TypeScript— ya estaba tomada y funcionando. Se deja anotado
> porque la conclusión afectaba a la recomendación de réplica.

### 5.1 Y hay un tercer MCP: `odoo-mcp-mom`

Descubierto en los comentarios de `cloudbuild-odoo-mcp.yaml:12-17`, del propio
repositorio rescatado:

> *«Ya no se despliega `odoo-mcp-mom` desde aquí. Ese servicio sigue corriendo la
> imagen anterior (`…/odoo-mcp`) y se autentica con `MCP_STATIC_TOKEN`, que este
> código ya no acepta.»*

Es decir, el patrimonio tiene **un cuarto MCP** que no aparecía en el mapa de §2:

| Servicio | Odoo | Auth | Estado |
|---|---|---|---|
| `odoo-mcp-sellside` | Sellside | OAuth 2.1 contra `sellside-auth` | Migrado |
| **`odoo-mcp-mom`** | **MOM** | **`MCP_STATIC_TOKEN` compartido** | **Sin migrar** |
| MCP dentro de `rosa-control-center` | MOM | ninguna | Prototipo |

`odoo-mcp-mom` no se ha verificado en este documento: no sé su configuración, su
service account, ni si está abierto a internet. **Es el primer hueco que hay que
cerrar en el inventario** — y un token estático compartido sobre un Odoo con
`odoo_unlink` merece la misma atención que se le dio a `odoo-mcp-sellside`.

---

## 6. Cómo replicarlo en otra cuenta de GCP

### 6.1 Primero, la decisión de arquitectura

| | **Opción 1 — OAuth embebido** | **Opción 2 — AS externo** |
|---|---|---|
| Base de código | Fork limpio `CTOSellside/odoo-mcp` | **`CTOSellside/odoo-mcp-sellside`** + `sellside-oauth` |
| Identidad del usuario | Email + API key de Odoo, en la pantalla de consentimiento | Google Sign-In del dominio del cliente |
| Credencial contra Odoo | **Una por usuario final**, cifrada en reposo | **Una del servidor**, compartida |
| Autorización | Allowlist de emails | **Scopes por herramienta**, denegar-por-defecto |
| Estado en disco | User store JSON → problema en Cloud Run (§6.3) | **Ninguno** — JWT verificados contra el JWKS del AS |
| Escala | Un AS por MCP | Un AS para N MCP |
| Esfuerzo | Un `gcloud run deploy` | Dos servicios + un cliente OAuth de Google |
| Estado | Código upstream, **nunca desplegado por nosotros** | **Corriendo en producción hoy** |

**Recomendación: Opción 2.** Esto cambió respecto de la primera versión de este
documento, y el motivo es el fuente recuperado (§3.10a): la opción 2 no es un
proyecto por empezar, es lo que ya está funcionando, con el modelo de
autorización más estricto y sin el defecto de estado en disco que arrastra la
opción 1 en Cloud Run.

Lo que hay que hacer para replicarla no es construir nada nuevo, sino
**parametrizar lo que existe**: el issuer, la URI del recurso, el dominio de
correo permitido y el cliente OAuth de Google. La pieza A sigue siendo relevante
como referencia —y su modo `stdio` como piloto de bajo riesgo—, pero ya no es la
base de despliegue.

> El runbook de §6.5 quedó escrito para la opción 1 y **sirve de estructura, no
> de receta**: las fases 1, 2, 4, 5 y 6 son equivalentes; cambia el conjunto de
> secretos (sin `MCP_ENCRYPTION_KEY` ni `MCP_ADMIN_PASSWORD`, con las variables
> del resource server) y se agrega el despliegue del AS. La fuente autoritativa
> para esa parte es `sellside-oauth/deploy/` más
> `cloudbuild-odoo-mcp.yaml` del repositorio rescatado — que ya trae el pipeline
> completo y, deliberadamente, **no** pasa `--allow-unauthenticated`.

### 6.2 Inventario de recursos GCP por cliente

Con la Opción 1, un solo proyecto:

| Recurso | Para qué | Notas |
|---|---|---|
| Proyecto GCP | Aislamiento por cliente | Uno por cliente, sin excepción |
| APIs | `run`, `artifactregistry`, `cloudbuild`, `secretmanager` | Sin Firestore en la Opción 1 |
| Artifact Registry | Imagen del contenedor | Misma región que Cloud Run |
| Cloud Run: `odoo-mcp-<cliente>` | El servidor | Ver 6.3 para `min/max-instances` |
| Service account dedicada | Identidad del servicio | **No usar la default de compute** |
| Secret Manager × 6 | Ver 6.4 | Acceso solo para la SA del servicio |
| Usuario de Odoo `mcp_user` | Cuenta de sondeo | Grupos de seguridad acotados |

Y del lado de Odoo, por **cada usuario final**: su propia clave API, generada en
Preferencias → Seguridad de la cuenta.

### 6.3 El user store en Cloud Run — defecto crítico

**Esto hay que resolverlo antes de replicar. No es opcional.**

El user store es un archivo en `MCP_USER_STORE_PATH`, por defecto
`/var/lib/odoo-mcp/users.json` (`config.ts:137`). En Cloud Run el sistema de
archivos del contenedor es **efímero y en memoria**. De ahí se siguen tres cosas,
todas verificables en el código:

1. **Arranque en frío ⇒ store vacío.** `load()` recibe `ENOENT`, loguea
   `"user store not found, starting fresh"` y arranca sin nada
   (`user-store.ts:288-293`). Se pierden **la allowlist y todos los tokens
   emitidos**. Cada usuario tiene que rehacer el flujo OAuth y el admin tiene que
   volver a cargar la allowlist entera.

2. **`max-instances > 1` ⇒ el conjunto de usuarios se parte.** Cada instancia
   tiene su propio archivo. Un token emitido por la instancia A da 401 en la B.
   El comportamiento observable es un conector que funciona de forma
   intermitente, que es lo peor que puede pasar en soporte.

3. **El fallo es silencioso.** El flush a un disco efímero *tiene éxito*; y aunque
   fallara, `doFlush` no relanza — loguea y sigue (`:122-128`). Nada en `/health`
   lo delata.

**Confirmado en el despliegue actual** (§3.10): el servicio **no tiene ningún
volumen montado** y corre con **`Min: 0`**. Es decir, el escenario 1 no es un
riesgo latente — ocurre cada vez que el servicio queda ocioso y escala a cero.

> Con una salvedad importante: §3.10(a) muestra que faltan `MCP_ENCRYPTION_KEY` y
> `MCP_ADMIN_PASSWORD`, sin las cuales la pieza A no arranca en modo HTTP. Si lo
> desplegado es un build modificado, es probable que también haya prescindido del
> user store —sería inusable con `Min: 0`— y entonces este defecto no aplica al
> despliegue actual **pero sigue aplicando a cualquier réplica** que use el código
> del fork tal cual, que es de lo que trata esta sección.

#### Opciones, de menos a más trabajo

| Opción | Qué implica | Veredicto |
|---|---|---|
| **A.** `min-instances=1`, `max-instances=1` | Configuración pura. Sobrevive mientras la instancia viva; se pierde todo en cada despliegue y en cada reinicio de la plataforma | Aceptable **solo** para un piloto con pocos usuarios y expectativas claras |
| **B.** Volumen de Cloud Storage montado con FUSE | Sin tocar código. Añade latencia por escritura y no arregla el escenario multi-instancia (dos instancias escribiendo el mismo JSON se pisan) | Frágil; el `rename` atómico no lo es sobre GCS |
| **C.** Portar `UserStore` a Firestore | Implementar la misma interfaz de 10 métodos (`user-store.ts:57-69`) sobre Firestore. Resuelve arranques en frío y multi-instancia. Consistencia transaccional en el consumo de códigos | **La correcta.** Estimo un día de trabajo, más tests |
| **D.** Ir a la Opción 2 de arquitectura | `sellside-auth` ya persiste en Firestore. Pero arrastra el desajuste Node/Python de la sección 5 | Solo si además necesitas AS compartido |

**Recomendación: A para el piloto, C antes de facturarle a nadie.** La interfaz
`UserStore` está limpiamente aislada detrás de una factoría, así que C es un
reemplazo local, no una refactorización.

### 6.4 Modelo de secretos

Seis secretos en Secret Manager, inyectados con `--set-secrets`:

| Secreto | Cómo generarlo |
|---|---|
| `ODOO_URL` | — |
| `ODOO_DB` | — |
| `ODOO_USERNAME` | Login de `mcp_user` |
| `ODOO_API_KEY` | Odoo → Preferencias → Seguridad de la cuenta → Nueva clave API |
| `MCP_ENCRYPTION_KEY` | `openssl rand -base64 32` — **exactamente 32 bytes** |
| `MCP_ADMIN_PASSWORD` | `openssl rand -hex 32` |

Reglas que vienen del código, no del gusto:

- **`MCP_ENCRYPTION_KEY` se genera una vez por despliegue y no se rota a la
  ligera**: rotarla invalida todas las credenciales guardadas
  (`docs/v0.2.1-oauth.md:152-165`).
- **`MCP_ADMIN_PASSWORD` se rota libremente** — no tiene dependencias en disco.
- **Nunca en la línea de comandos del `gcloud run deploy`**: queda en el historial
  de revisiones del servicio. Este patrón ya está bien resuelto en
  `sellside-oauth/deploy/02-desplegar-as.sh:37-43` — cópialo.

### 6.5 Runbook

```bash
# ---- Variables del cliente --------------------------------------------------
export PROJECT="mcp-<cliente>-001"
export REGION="southamerica-west1"        # o la del cliente
export SERVICE="odoo-mcp-<cliente>"
export SA="${SERVICE}@${PROJECT}.iam.gserviceaccount.com"

# ---- Fase 1 · Infraestructura ----------------------------------------------
gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
                       cloudbuild.googleapis.com secretmanager.googleapis.com

gcloud iam service-accounts create "$SERVICE" \
  --display-name="MCP de Odoo — <cliente>"
# IAM no es consistente de inmediato: espera a que propague antes del binding.
# Ver sellside-oauth/deploy/01-infraestructura.sh:36-43 para el bucle de espera.

# ---- Fase 2 · Secretos ------------------------------------------------------
for s in ODOO_URL ODOO_DB ODOO_USERNAME ODOO_API_KEY; do
  read -rsp "$s: " v; echo
  printf '%s' "$v" | gcloud secrets create "$s" --data-file=-
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
done

openssl rand -base64 32 | tr -d '\n' | \
  gcloud secrets create MCP_ENCRYPTION_KEY --data-file=-
openssl rand -hex 32 | tr -d '\n' | \
  gcloud secrets create MCP_ADMIN_PASSWORD --data-file=-
for s in MCP_ENCRYPTION_KEY MCP_ADMIN_PASSWORD; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
done

# ---- Fase 3 · Despliegue ----------------------------------------------------
# Desde un clon del fork odoo-mcp. El Dockerfile ya trae MODE=http y puerto 3000.
git clone https://github.com/CTOSellside/odoo-mcp && cd odoo-mcp

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --port 3000 \
  --set-secrets="ODOO_URL=ODOO_URL:latest,ODOO_DB=ODOO_DB:latest,ODOO_USERNAME=ODOO_USERNAME:latest,ODOO_API_KEY=ODOO_API_KEY:latest,MCP_ENCRYPTION_KEY=MCP_ENCRYPTION_KEY:latest,MCP_ADMIN_PASSWORD=MCP_ADMIN_PASSWORD:latest" \
  --set-env-vars="MODE=http,MCP_PORT=3000,MCP_USER_STORE_PATH=/tmp/users.json" \
  --min-instances=1 --max-instances=1 \
  --no-allow-unauthenticated          # se abre en la fase 5, no antes

# MCP_PUBLIC_URL no se puede fijar todavía: la URL no existe hasta el primer
# deploy. Se agrega en la fase 4.
# min=max=1 es obligatorio hasta resolver la sección 6.3 (opción A).
```

```bash
# ---- Fase 4 · Fijar el issuer ----------------------------------------------
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
       --format='value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars="MCP_PUBLIC_URL=${URL}"
```

> `MCP_PUBLIC_URL` queda en los metadatos publicados y en cada token emitido.
> **Cambiarlo después rompe todas las conexiones existentes de Claude.** Si el
> cliente va a querer `mcp.sucliente.cl`, decídelo aquí y monta el dominio
> antes de emitir el primer token. Es la misma lección que quedó anotada en
> `sellside-oauth/deploy/env.sh:23-29`.

```bash
# ---- Fase 5 · Verificar ANTES de abrir el acceso ---------------------------
# Con --no-allow-unauthenticated, se prueba con un ID token de IAM: pasa el
# filtro de la plataforma y llega al código, que debe rechazarlo igual.
TOKEN="$(gcloud auth print-identity-token)"

curl -s -H "Authorization: Bearer $TOKEN" "$URL/health" | jq
# Esperado: {"ok":true,"mode":"http","probe_ok":true}   ← redactado, correcto

curl -si -H "Authorization: Bearer $TOKEN" -X POST "$URL/mcp" | head -5
# Esperado: 401 + WWW-Authenticate: Bearer realm="MCP", resource_metadata="…"
# Ese 401 lo produce el código, no IAM. Si no aparece, NO abras el acceso.

curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/.well-known/oauth-protected-resource" | jq
# Verifica que "resource" y "authorization_servers" apunten a la URL correcta.

# ---- Fase 6 · Abrir y cargar la allowlist ----------------------------------
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --member=allUsers --role=roles/run.invoker

ADMIN="$(gcloud secrets versions access latest --secret=MCP_ADMIN_PASSWORD)"
curl -X POST "$URL/admin/users" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"email":"persona@cliente.cl"}'
```

Alta del usuario final en Claude Desktop: Configuración → Conectores → Agregar
conector personalizado → `<URL>/mcp`. El flujo OAuth es automático; solo verá la
pantalla de consentimiento, donde escribe **su** email y **su** clave API de Odoo.

Para Claude Code, que no ejecuta el baile OAuth: `npx @netlinksinc/odoo-mcp auth
<URL>` imprime el token, y luego `claude mcp add --header "Authorization: Bearer
<token>" <URL>/mcp` (`docs/v0.2.1-oauth.md:124-149`).

### 6.6 Lo que cambia por cliente y lo que no

**Cambia:** proyecto GCP, región, nombre del servicio, los 4 secretos de Odoo,
las 2 llaves generadas, la allowlist, `MCP_PUBLIC_URL`, y los grupos de seguridad
del `mcp_user` en Odoo.

**No cambia:** el código. Cero. La superficie de herramientas es genérica y los
recursos `odoo://` describen la instancia en tiempo de ejecución, así que un Odoo
con módulos custom funciona sin tocar nada.

**Eso es el activo replicable**: el trabajo por cliente es configuración y
gobierno de permisos de Odoo, no desarrollo.

### 6.7 Checklist de seguridad del lado de Odoo

El último control antes de los datos **no está en este código** — está en Odoo, y
no se puede verificar desde fuera:

- [ ] Usuario `mcp_user` dedicado para el sondeo de arranque, no `admin`
- [ ] Grupos de seguridad acotados a los modelos que el caso de uso necesita
- [ ] Cada usuario final con su propia clave API, nunca compartida
- [ ] Reglas de registro (record rules) revisadas para los modelos alcanzables
- [ ] Respaldos verificados **antes** de conectar: `odoo_unlink` existe y borra
- [ ] Probado contra una instancia de desarrollo antes de apuntar a producción
- [ ] `ODOO_MCP_DEBUG` **sin** definir en producción

Y una consideración legal que en Chile aplica: el conector transmite datos de
Odoo a la API de Claude como parte de su operación normal. Si el cliente maneja
datos personales, revisa la Ley 21.719 y el DPA correspondiente antes de
conectar.

---

## 7. Diferencias operativas entre Fly.io y Cloud Run

El proyecto está diseñado para Fly.io — `fly.toml` es canónico y la guía de
despliegue lo trata como objetivo principal. Correrlo en Cloud Run funciona, pero
hay cuatro diferencias que conviene tener presentes:

| Aspecto | Fly.io (diseño original) | Cloud Run (lo nuestro) |
|---|---|---|
| Sistema de archivos | Volumen persistente disponible | **Efímero** → sección 6.3 |
| Instancias | `min_machines_running = 1`, proceso estable | Escala a cero por defecto |
| Health check | `[[services.http_checks]]` sobre `/health` | Hay que configurarlo aparte |
| `/health` redactado | El proxy corre en el mismo host → loopback | Nunca loopback → siempre redactado |

Ninguna es bloqueante salvo la primera, que sí lo es.

---

## 8. Riesgos y deuda técnica

Ordenados por lo que le pasa al cliente si no se atienden.

| # | Riesgo | Impacto | Dónde |
|---|---|---|---|
| 1 | **`/mcp/sse` de Rosa sin autenticación** | Escritura y borrado en Odoo MOM desde internet, sin credencial | §4 |
| 2 | **`odoo-mcp-mom` con token estático compartido** | Cuarto MCP sin auditar, con un secreto único no revocable por persona sobre un Odoo con `odoo_unlink` | §5.1 |
| 3 | **Corre con la service account default de compute** | Rol `Editor` sobre todo el proyecto: un compromiso del MCP no se queda en Odoo | §3.10(e) |
| 4 | **Dos servicios productivos sin rama canónica** | `sellside-auth` corre desde un PR sin mergear; el resource server desde una rama de rescate. Nadie puede rebuildear con confianza | §5, §3.10(a) |
| 5 | `ODOO_API_KEY` viene de `SELLSIDE_ODOO_PASSWORD` | Si es la contraseña web y no una clave API: no es revocable por separado y sirve para entrar por la UI | §3.10(f) |
| 6 | Startup probe TCP en vez de `/health` | Un contenedor que abre el puerto pero falla el sondeo a Odoo se marca sano | §3.10 |
| 7 | Escalado contradictorio (Max 1 vs 5) | Sin consecuencia hoy —no hay estado en disco— pero indica configuración a la deriva | §3.10(c) |
| 8 | Variables inertes en producción | El script 03 dejó `SCOPES_SUPPORTED` etc. sobre un servicio que ya las lee de otra forma; `config.ts` avisa, pero conviene limpiarlas | §3.10(d) |
| 9 | Bug de concurrencia SSE en Rosa | Mensajes cruzados entre clientes | `mcp.routes.js:6` |
| 10 | Dependencia de un fork upstream | Los parches de seguridad de NETLINKS no llegan solos a ninguna de las dos ramas | `CTOSellside/odoo-mcp` |
| 11 | User store efímero + `Min: 0` | **Ya no aplica al servicio actual.** Aplica solo a una réplica construida desde el fork limpio | §6.3, §3.10(b) |

**El orden cambió dos veces**, y conviene decir por qué. En la primera versión el
riesgo 1 era el user store efímero; tras verificar el despliegue pasó a ser «no
se sabe qué código corre»; y tras recuperar el fuente ambos se disolvieron —el
primero porque el build no tiene estado en disco, el segundo porque el código ya
está versionado y resultó ser mejor de lo que aparentaba.

Lo que queda arriba es lo que **no** se ha tocado: dos MCP sobre el Odoo de MOM
con autenticación débil o nula, y una service account con permisos de más.

---

## 9. Recomendación

### Seguridad — no espera a la réplica

1. **Cerrar `/mcp/*` de Rosa Control Center.** Exposición activa, sin
   autenticación, sobre un Odoo productivo.
2. **Auditar `odoo-mcp-mom`** (§5.1) y migrarlo al mismo modelo que
   `odoo-mcp-sellside`. El pipeline ya contempla el paso: darle su `AUTH_SERVER`
   y su `RESOURCE_URI` y devolver el despliegue a `cloudbuild-odoo-mcp.yaml`.
3. **Service account dedicada** para los MCP en vez de la default de compute, con
   permisos mínimos. Es un `gcloud run services update --service-account`.
4. **Confirmar qué hay dentro de `SELLSIDE_ODOO_PASSWORD`.** Si es una contraseña
   y no una clave API, reemplazarla por una clave API de un `mcp_user` dedicado.

### Gobierno del código — condición previa para replicar

5. **Consolidar `CTOSellside/odoo-mcp-sellside`.** La rama `rescate/oauth21` es
   un rescate, no una rama canónica: revisarla, mergearla a `main`, y **conectar
   el trigger de Cloud Build a `cloudbuild-odoo-mcp.yaml`** para que el próximo
   despliegue salga de un commit y no de un `docker build` manual.
6. **Mergear el PR #1** para que `sellside-auth` tenga rama canónica. Antes,
   **correr la revisión externa que ya está escrita** en
   `sellside-oauth/REVISION-EXTERNA.md` — sigue pendiente y ahora hay más
   superficie que revisar, incluido el port a TypeScript.
7. **Anclar el upstream.** Registrar que la base es `@netlinksinc/odoo-mcp`
   v0.2.2 (`63ae499`) y suscribirse a sus releases, o adoptar el código
   formalmente. Hoy no hay ninguna de las dos, y el fork ya divergió.

### Réplica

8. **Replicar con la Opción 2** (§6.1): `odoo-mcp-sellside` + `sellside-oauth`,
   parametrizando issuer, URI del recurso, dominio de correo y cliente OAuth de
   Google. No hay que construir nada nuevo — pero sí hacer primero el punto 5,
   porque no se replica desde una rama de rescate.
9. **Portar `UserStore` a Firestore** solo si además se quiere ofrecer la Opción 1
   (credencial de Odoo por usuario final). No es necesario para la Opción 2, que
   no tiene estado en disco.

---

## Anexo A — qué verifiqué y qué no

Escrito aparte y a propósito: un documento de arquitectura que no distingue lo
leído de lo supuesto no sirve para decidir.

### Verificado leyendo el código

- Toda la pieza A: `CTOSellside/odoo-mcp` en `63ae499`. Endpoints, flujo OAuth,
  cifrado, user store, herramientas, límites y controles de seguridad. Cada
  referencia `archivo:línea` de este documento apunta a ese commit.
- Toda la pieza B: `rosa-control-center` en `9e54001`, incluido `cloudbuild.yaml`.
- Pieza C: rama `claude/oauth-mcp-sellside-gcp-gg30q6` del PR #1.
- **La configuración desplegada de `odoo-mcp-sellside`** (§3.10), vía
  `gcloud run services describe` el 28-jul-2026, más el sondeo de `/health`,
  `/mcp` y los logs.
- **El fuente exacto del build en producción** — extraído de la imagen
  `sha256:12dadcc9a48aa15ef0787a00658bb87b76162261f270d60ad471b811b37403ca` y
  publicado en `CTOSellside/odoo-mcp-sellside@rescate/oauth21`. Diffeado archivo
  por archivo contra `CTOSellside/odoo-mcp@63ae499`, normalizando fines de línea.
  Las referencias de §3.10(a) apuntan a ese árbol.

### No verificado — supuestos declarados

1. **Que la imagen `:oauth21` sea la que sirve la revisión activa.** La revisión
   `00016` referencia ese tag, y el comportamiento observado (realm
   `odoo-mcp-sellside`, ruta de metadatos con sufijo, arranque sin
   `MCP_ENCRYPTION_KEY`) coincide con el fuente rescatado en los cuatro puntos que
   lo distinguen del fork. La coincidencia es suficiente para trabajar, pero es
   un tag mutable: quien pueda escribir en Artifact Registry puede reapuntarlo.
   **Ese es justamente el motivo del punto 5 de §9** — desplegar por `$COMMIT_SHA`,
   como ya prevé `cloudbuild-odoo-mcp.yaml:25`, y no por tag.

2. **`odoo-mcp-mom`** (§5.1). Descubierto en un comentario del pipeline; no
   inspeccionado. Se desconocen su configuración, su service account, su política
   IAM y de dónde salió su imagen. Es el hueco más grande que queda:
   ```bash
   gcloud run services describe odoo-mcp-mom --region southamerica-west1
   gcloud run services get-iam-policy odoo-mcp-mom --region southamerica-west1
   ```

3. **`sellside-auth`.** Existe —el MCP valida contra él y el flujo funciona— pero
   no se inspeccionó su despliegue, y su fuente sigue en un PR sin mergear:
   ```bash
   gcloud run services list --region southamerica-west1
   curl -s https://sellside-auth-843056793102.southamerica-west1.run.app/.well-known/oauth-authorization-server | jq
   ```

4. **La política IAM de `odoo-mcp-sellside`.** `Ingress: all` confirma que la red
   lo permite y el pipeline **no** pasa `--allow-unauthenticated`
   (`cloudbuild-odoo-mcp.yaml:52`), pero el acceso público se abre a mano con
   `05-abrir-acceso.sh` y `describe` no muestra los bindings. Que el sondeo desde
   Cloud Shell haya recibido un `401` del código —y no un `403` de la
   plataforma— sugiere que `allUsers` **sí** está concedido:
   ```bash
   gcloud run services get-iam-policy odoo-mcp-sellside --region southamerica-west1
   ```

5. **Toda la pieza B, por partida doble.**

   **(a) Repositorio equivocado.** §4 se escribió leyendo
   `CTOSellside/rosa-control-center@9e54001`, que **no es el oficial** — el
   oficial es `Sellside-SpA/rosa-control-center`. No pude contrastarlos: el
   entorno de esta sesión no permite adjuntar repositorios de otra organización.
   Es posible que el espejo esté atrasado y que en el oficial `/mcp/*` ya tenga
   autenticación. **Contrastar es lo primero:**
   ```bash
   git clone https://github.com/Sellside-SpA/rosa-control-center /tmp/oficial
   diff -r /tmp/oficial/backend /tmp/espejo/backend
   sed -n '40,60p' /tmp/oficial/backend/server.js       # ¿hay middleware antes de /mcp?
   cat /tmp/oficial/backend/routes/mcp.routes.js
   ```

   **(b) Alcanzabilidad.** Aun en el espejo, que `/mcp/sse` esté efectivamente
   abierto lo deduzco de `--allow-unauthenticated` en `cloudbuild.yaml:27` más la
   ausencia de middleware en `server.js:47-51`. No lo probé: sondear un endpoint
   de escritura sobre un Odoo productivo no es algo que se haga sin autorización
   explícita.

6. **El tarball de Cloud Build del 27-jul 22:52 UTC**
   (`gs://odoo-serverless-ss-001_cloudbuild/source/1785192752.084703-…tgz`). Es el
   único de los 20 builds listados con `storageSource` —los otros 19 usan
   `repoSource`— y su hora cae dentro de la ventana en que se escribió
   `sellside-oauth`, así que probablemente sea el AS en Python. Sin descomprimir.

   Dato colateral del mismo listado: **no hay ningún build a la hora de la
   revisión `00016`** (28-jul 00:33 UTC), lo que confirma que salió de un
   `gcloud run services update` —el script 03— y no de un despliegue.

7. **El segundo proyecto GCP.** Solo conozco su número, `30942737227`, por las
   URLs en el código. No sé su ID ni su relación organizativa con
   `odoo-serverless-ss-001`.

8. **Qué emite `sellside-auth` en el claim `scope`.** El resource server exige
   `odoo:read` / `odoo:write` (`scopes.ts:16-17`), pero no verifiqué qué scopes
   concede el AS ni con qué criterio. Si concediera siempre ambos, la política por
   herramienta quedaría correctamente implementada y vacía de efecto.

9. **Los tests.** El rescate extrajo `packages/*/src`, no `packages/*/tests`. No
   sé si el port a TypeScript trae pruebas propias ni si las 41 del PR #1 tienen
   equivalente. Es lo primero que hay que mirar al consolidar la rama (§9, punto 5).

---

*Documento generado a partir del código en los commits citados. Las referencias
`archivo:línea` son estables respecto de esos commits.*
