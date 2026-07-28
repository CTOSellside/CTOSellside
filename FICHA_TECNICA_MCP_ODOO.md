# MCP de Odoo — Descripción técnica y guía de réplica

**Autor:** CTO Sellside · **Fecha:** 28 de julio de 2026 · **Estado:** vigente

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
| `CTOSellside/odoo-mcp` | Fork de `NETLINKSAF/odoo-mcp` — el código de la pieza A | Sin modificaciones locales |
| `CTOSellside/rosa-control-center` | BFF + SPA que además expone la pieza B | Activo |
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

La conclusión es inequívoca: alguien tomó el fork, **parametrizó el `realm` y la
URL de metadatos con `RESOURCE_NAME`/`RESOURCE_URI`, relajó la validación de
entorno y lo apuntó al AS externo** vía `AUTH_SERVER`. Es decir: **la
integración con `sellside-auth` ya se hizo, en Node, y funciona.** El desajuste
Node/Python de §5 fue resuelto por alguien — pero la solución **no está en
ningún repositorio**.

Eso reordena dos cosas de este documento:

- **La buena:** el MCP **sí exige token**. El 401 con `WWW-Authenticate` bien
  formado descarta el peor escenario. El sondeo a Odoo funciona (`probe_ok:true`)
  y el servicio no se está reiniciando.
- **La mala:** lo que hoy toca los datos de Odoo es un artefacto **no auditable
  y no reproducible**. No se puede revisar, ni parchear, ni rebuildear, ni
  replicar. Si esa imagen desaparece de Artifact Registry, el sistema no se puede
  reconstruir. Y como el user store ya no está en juego (el build lo eliminó o lo
  dejó sin usar, coherente con que faltan sus dos variables), tampoco sabemos
  **dónde valida los tokens** ni qué hace con las credenciales.

**Probablemente sea recuperable.** El repositorio de Artifact Registry se llama
`cloud-run-source-deploy`, que es el nombre que Cloud Run usa para los despliegues
`--source`. Eso implica que hubo un Cloud Build, y que **el tarball del fuente
quedó en el bucket de staging** (`gs://<proyecto>_cloudbuild/source/…`). Ver
[Anexo A](#anexo-a--qué-verifiqué-y-qué-no) para los comandos.

#### (b) `Min: 0` — el arranque en frío es rutina, no excepción

El servicio escala a cero. Combinado con la ausencia de volúmenes, esto convierte
el defecto de §6.3 de «se pierde si reinicia» a **«se pierde cada vez que el
servicio queda ocioso»**. Si la pieza A es la que corre, todo usuario que vuelva
después de un rato de inactividad recibe 401 y tiene que rehacer el flujo OAuth.

Esto encaja con la explicación (a.2): un build que quitó la validación
probablemente también prescindió del user store, porque con `Min: 0` sería
inusable.

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

## 5. Pieza C — `sellside-oauth`, y el desajuste que hay que resolver

PR #1, sin mergear. Un servidor de autorización OAuth 2.1 **externo** en Python
(FastAPI + Firestore), pensado como AS compartido para cinco MCP, más una
librería de resource server que cada MCP montaría como middleware ASGI.

Está bien hecho: 41 tests en verde, PKCE S256 obligatorio, `aud` canónica por
RFC 8707, refresh rotativo con revocación en cadena, sin passthrough del token,
scopes con denegar-por-defecto. El detalle está en el cuerpo del PR #1.

**Pero hay un desajuste de arquitectura que este documento tiene que dejar
anotado:**

El middleware de `sellside-oauth` es **Python ASGI**. El MCP que corre en
producción, `odoo-mcp-sellside`, es **Node/TypeScript** — confirmado en la
[sección 3.1](#31-identificación-y-confirmación). Un middleware ASGI no se monta
sobre un servidor `node:http`. Integrarlo exige una de tres:

1. **Portar** `mcp_resource_server/` a TypeScript y montarlo en
   `http-transport.ts`.
2. Poner un **proxy Python delante** del MCP en Cloud Run (sidecar o servicio
   intermedio) que valide el token y reenvíe.
3. **No integrarlo** y usar el AS que la pieza A ya trae embebido.

El propio PR #1 lo lista como pendiente —«Integrar el middleware en el código
real de `odoo-mcp-sellside` (no está en este repo)»— pero no señala que el
lenguaje no coincide. Es una decisión que hay que tomar antes de invertir más en
esa vía.

Y hay una duplicación de fondo: **la pieza A ya es un servidor de autorización
OAuth 2.1 completo**. `sellside-auth` solo se justifica si de verdad quieres *un*
AS para cinco MCP distintos con identidad delegada a Google Sign-In. Si el
objetivo es exponer el MCP de Odoo a Claude, la pieza A ya lo resuelve sola.

---

## 6. Cómo replicarlo en otra cuenta de GCP

### 6.1 Primero, la decisión de arquitectura

| | **Opción 1 — OAuth embebido** | **Opción 2 — AS externo** |
|---|---|---|
| Qué despliegas | Solo la pieza A | Pieza A + `sellside-auth` + integración |
| Identidad del usuario | Email + API key de Odoo, en la pantalla de consentimiento | Google Sign-In del dominio del cliente |
| Esfuerzo | Un `gcloud run deploy` | Portar el middleware (ver sección 5) |
| Escala | Un AS por MCP | Un AS para N MCP |
| Estado | **Funciona hoy, en producción** | Sin desplegar, con un desajuste abierto |

**Recomendación: Opción 1 para el primer cliente replicado.** La opción 2 solo
paga si el cliente va a tener varios MCP (Odoo + Twilio + SendGrid + …) y quiere
una sola pantalla de login corporativa. No empieces por ahí.

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
| 1 | **El fuente de lo desplegado no está versionado** | La imagen `:oauth21` es el fork con modificaciones que no están en ningún repo. No se puede auditar, parchear, rebuildear ni replicar lo que hoy toca los datos de Odoo | §3.10(a) |
| 2 | **`/mcp/sse` de Rosa sin autenticación** | Escritura y borrado en Odoo MOM desde internet, sin credencial | §4 |
| 3 | **Corre con la service account default de compute** | Rol `Editor` sobre todo el proyecto: un compromiso del MCP no se queda en Odoo | §3.10(e) |
| 4 | User store efímero + `Min: 0` | El build desplegado parece haberlo eliminado, así que aplica a la **réplica**, no al servicio actual | §6.3, §3.10(b) |
| 5 | `ODOO_API_KEY` viene de `SELLSIDE_ODOO_PASSWORD` | Si es la contraseña web y no una clave API: no es revocable por separado y sirve para entrar por la UI | §3.10(f) |
| 6 | **Los access tokens no expiran** | Un token filtrado sirve hasta que alguien lo revoque a mano | `docs/v0.2.1-oauth.md:146` |
| 7 | Desajuste Node/Python del PR #1 | Ya materializado: el script 03 dejó variables inertes en producción | §5, §3.10(d) |
| 8 | Startup probe TCP en vez de `/health` | Un contenedor que abre el puerto pero falla el sondeo a Odoo se marca sano | §3.10 |
| 9 | Escalado contradictorio (Max 1 vs 5) | Si el efectivo fuera 5, dispara el riesgo 4 | §3.10(c) |
| 10 | Rotación destructiva de `MCP_ENCRYPTION_KEY` | Rotar por higiene obliga a que todos reautoricen | `user-store.ts:317-334` |
| 11 | Bug de concurrencia SSE en Rosa | Mensajes cruzados entre clientes | `mcp.routes.js:6` |
| 12 | Dependencia de un fork upstream | Los parches de seguridad de NETLINKS no llegan solos | `CTOSellside/odoo-mcp` |
| 13 | `resolveToken` es O(n) | Irrelevante hoy (n ≤ 10 × usuarios); no escala a miles | `user-store.ts:250-257` |

**Los tres primeros son los que importan, y el orden cambió** tras verificar el
despliegue. El sondeo confirmó que el MCP **sí exige token** —el peor escenario
está descartado—, pero dejó el riesgo 1 como el más grave: hay código sin
control de versiones tocando datos de un ERP productivo, y recuperarlo es
condición previa para replicar cualquier cosa.

---

## 9. Recomendación

0. **Recuperar el fuente de la imagen `:oauth21`** desde el tarball de Cloud
   Build (Anexo A, punto 1), diffearlo contra el fork y commitearlo. Va antes que
   todo lo demás: es código sin control de versiones tocando un ERP productivo, y
   además —si trae la integración con `sellside-auth` hecha en Node— es
   probablemente **la mejor base para la réplica**, mejor que el fork limpio del
   §6.5. Lo mismo para `sellside-auth`.
1. **Cerrar `/mcp/*` de Rosa Control Center.** Es una exposición activa, no una
   deuda. No espera a la réplica.
1b. **Darle al MCP una service account dedicada** en vez de la default de
   compute, con permisos mínimos. Es un `gcloud run services update`.
2. **Portar `UserStore` a Firestore** (opción C de §6.3). Es lo que convierte
   esto de «funciona en una instancia» a «producto replicable».
3. **Para el primer cliente replicado, Opción 1** — OAuth embebido, un proyecto,
   `min=max=1` hasta que esté el punto 2. El runbook de §6.5 es ejecutable tal
   cual.
4. **Decidir sobre `sellside-oauth` antes de invertirle más.** Si la respuesta es
   «un AS para cinco MCP», hay que portar el middleware a TypeScript. Si es «solo
   Odoo», el PR #1 se archiva y se documenta por qué.
5. **Correr la revisión externa que ya está escrita** —
   `sellside-oauth/REVISION-EXTERNA.md`— antes de cualquier decisión sobre el
   punto 4.
6. **Anclar el fork.** Fijar la versión upstream que corremos y suscribirse a sus
   releases, o adoptar el código formalmente. Hoy no hay ninguna de las dos.

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
- **Que el conector vivo `Rosa_Odoo_MCP_GCP` corre el *código* de la pieza A**:
  los esquemas expuestos coinciden con `schemas.ts` en regex, defaults y cadenas
  de descripción (§3.1). Esto identifica el código, **no el servicio que lo
  aloja** — ver el punto 1 de abajo.
- **La configuración desplegada de `odoo-mcp-sellside`** (§3.10), vía
  `gcloud run services describe` el 28-jul-2026.

### No verificado — supuestos declarados

1. **El fuente exacto detrás del tag `:oauth21`.** Resuelto parcialmente: §3.10(a)
   demuestra por comportamiento observable que es el fork con modificaciones
   locales, y que **exige token**. Lo que sigue sin saberse es el **código**: qué
   más se cambió además de lo que asoma en las cabeceras HTTP, dónde valida los
   tokens, y qué hace con las credenciales de Odoo ahora que el user store no
   tiene sus variables.

   El fork no contiene configuración de despliegue para GCP —solo `Dockerfile` y
   `fly.toml`— y el tag es manual, no un SHA. **Pero el nombre del repositorio de
   Artifact Registry, `cloud-run-source-deploy`, indica un despliegue `--source`,
   y eso deja rastro.** Para recuperarlo:
   ```bash
   # ¿Cuándo se construyó la imagen y qué otras hay?
   gcloud artifacts docker images list \
     southamerica-west1-docker.pkg.dev/odoo-serverless-ss-001/cloud-run-source-deploy/odoo-mcp-sellside \
     --include-tags --format='table(version,tags,createTime)'

   # El build que la produjo — `source` apunta al tarball del fuente
   gcloud builds list --limit 20 \
     --format='table(id,createTime,status,source.storageSource.bucket,source.storageSource.object)'

   # Y se descarga:
   gsutil cp gs://<bucket>/<object> /tmp/fuente-oauth21.tgz
   ```
   Si el tarball aparece, **eso es el fuente perdido**: se descomprime, se
   diffea contra `CTOSellside/odoo-mcp@63ae499` y se commitea. Deja de ser un
   riesgo y pasa a ser el punto de partida de la réplica —posiblemente mejor que
   el fork limpio, porque ya trae la integración con el AS externo hecha en Node.

   Si el tarball ya no está (los buckets de staging tienen ciclo de vida), queda
   la vía de último recurso: `docker pull` de la imagen y extraer
   `/app/packages/odoo-mcp/dist` — es JavaScript compilado desde TypeScript, sin
   minificar, perfectamente legible.

1b. **Si `sellside-auth` está desplegado.** El MCP tiene `AUTH_SERVER` apuntando
   a `https://sellside-auth-843056793102.southamerica-west1.run.app` y valida
   tokens contra él, así que debe existir — pero el PR #1 no está mergeado, con
   lo cual **también sería un despliegue sin fuente versionado**. Mismo problema,
   segundo servicio:
   ```bash
   gcloud run services list --region southamerica-west1
   curl -s https://odoo-mcp-sellside-843056793102.southamerica-west1.run.app/.well-known/oauth-protected-resource/mcp
   ```

3. **Si `/mcp/sse` de Rosa está efectivamente alcanzable desde internet.** Lo
   deduzco de `--allow-unauthenticated` en `cloudbuild.yaml:27` más la ausencia
   de middleware en `server.js:47-51`. No lo probé: sondear un endpoint de
   escritura sobre un Odoo productivo no es algo que se haga sin autorización
   explícita. **Recomiendo verificarlo hoy.**

4. **El segundo proyecto GCP.** Solo conozco su número, `30942737227`, por las
   URLs en el código. No sé su ID ni su relación organizativa con
   `odoo-serverless-ss-001`.

4b. **La política IAM del servicio.** `Ingress: all` confirma que la red lo
   permite, pero `describe` no muestra los bindings. Falta:
   ```bash
   gcloud run services get-iam-policy odoo-mcp-sellside --region southamerica-west1
   ```
   Si aparece `allUsers` con `roles/run.invoker`, la única protección es el
   código — y el punto 1 dice que no sabemos cuál es.

5. **Cuántos usuarios usan hoy el conector** y con qué frecuencia. Determina si
   la opción A de §6.3 es tolerable como estado transitorio o ya está causando
   incidentes que se atribuyen a otra cosa.

---

*Documento generado a partir del código en los commits citados. Las referencias
`archivo:línea` son estables respecto de esos commits.*
