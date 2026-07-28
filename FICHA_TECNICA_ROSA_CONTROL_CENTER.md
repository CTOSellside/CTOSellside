# Ficha Técnica — Rosa Control Center

> Documento generado el **2026-07-28** a partir del análisis del código de
> [`CTOSellside/rosa-control-center`](https://github.com/CTOSellside/rosa-control-center)
> en el commit `9e54001` (rama `master`).

---

## 1. Identificación

| Campo | Valor |
|---|---|
| **Nombre** | Rosa Control Center |
| **Repositorio** | `CTOSellside/rosa-control-center` (GitHub, **privado**) |
| **Rama principal** | `master` (además: `desarrollo`, `staging`, ambas en `f8ccb18`) |
| **Licencia** | No declarada |
| **Tipo** | Monorepo (frontend + backend + IaC + scripts de despliegue) |
| **Propósito** | Dashboard unificado y panel de control con IA para gestionar procesos de negocio, monitorear integraciones (Odoo, Google Workspace, GitHub) y ejecutar operaciones automatizadas |
| **URL productiva** | `https://rosa.sellside.cl` (Cloud Run + dominio custom) |
| **Proyecto GCP actual** | `odoo-serverless-ss-001` (migrado desde `sellside-admin-audit`) |
| **Región** | `southamerica-west1` |

---

## 2. Arquitectura

Aplicación **monolito contenerizado** que sirve el SPA de React y la API desde un
mismo proceso Node.js en Cloud Run, actuando además como **BFF** (Backend-For-Frontend)
frente a microservicios externos y como **servidor MCP** hacia clientes de IA.

```
                     ┌──────────────────────────────────────────┐
  Navegador  ──────► │  Cloud Run: rosa-control-center          │
  (Firebase Auth)    │  ┌────────────────────────────────────┐  │
                     │  │ Express (backend/server.js)        │  │
                     │  │  • /            → SPA React (dist) │  │
                     │  │  • /api/*       → API REST         │  │
                     │  │  • /mcp/sse|msg → Servidor MCP     │  │
                     │  └────────────────────────────────────┘  │
                     └───┬───────────┬───────────┬──────────────┘
                         │           │           │
              Vertex AI  │      XML-RPC          │ HTTP (OIDC/Bearer)
           gemini-2.5-   │           │           │
              flash      ▼           ▼           ▼
                   ┌──────────┐ ┌─────────┐ ┌──────────────────────────┐
                   │ Vertex AI│ │  Odoo   │ │ sellside-workspace-search│
                   │ (tools)  │ │ MOM /   │ │  (Gmail/Drive/Chat)      │
                   └──────────┘ │ SELLSIDE│ └──────────────────────────┘
                                └─────────┘
                         │
                         ▼
                   Firestore (chats, perfiles, árbol, pulse_checks,
                              workspace_scheduled_events)
```

**Patrones aplicados**
- MVC ligero en backend: `routes/` → `controllers/` → `services/`.
- Multi-tenant por instancia Odoo: `MOM` y `SELLSIDE`, seleccionable desde la UI ("Global Context Switcher") e inyectado de forma invisible al prompt del modelo.
- Multi-hop tool calling con circuit breaker (`MAX_HOPS = 15`) y *exponential backoff* ante 429 de Vertex AI.
- Protocolo propietario **A2UI**: el modelo emite bloques `<a2ui>{...}</a2ui>` que el frontend renderiza como componentes (`DynamicForm`, `DataGrid`, `EmailList`).

---

## 3. Stack tecnológico

### Frontend (`frontend/`)
| Componente | Versión |
|---|---|
| React | ^19.2.0 |
| Vite | ^7.3.1 |
| Recharts (gráficos) | ^3.8.1 |
| Firebase Web SDK (Auth) | ^12.10.0 |
| ESLint | ^9.39.1 |
| Estilos | **Vanilla CSS** (política explícita: prohibido TailwindCSS) |

### Backend (`backend/`) — Node.js 20, ESM
| Dependencia | Versión | Uso |
|---|---|---|
| `express` | ^4.19.0 | Servidor HTTP y API |
| `@google-cloud/vertexai` | ^1.10.0 | Motor de IA (Gemini 2.5 Flash) vía IAM, sin API keys |
| `@modelcontextprotocol/sdk` | ^1.27.1 | Servidor MCP sobre SSE |
| `firebase-admin` | ^13.7.0 | Firestore + verificación de identidad |
| `@google-cloud/secret-manager` | ^6.1.1 | Recuperación dinámica de secretos |
| `xmlrpc` | ^1.3.2 | Cliente XML-RPC contra Odoo |
| `google-auth-library` | ^10.6.2 | Tokens OIDC hacia microservicios |
| `zod`, `cors`, `dotenv`, `moment-timezone` | — | Validación, CORS, config, husos horarios |

### Infraestructura
- **Cloud Run** (`southamerica-west1`, `--allow-unauthenticated` + middleware propio de auth).
- **Cloud Build** con `cloudbuild.yaml`; imagen en **Artifact Registry** (`cloud-run-source-deploy/rosa-control-center`).
- **Firebase Hosting** (`sellside-admin-audit`) con *rewrite* total hacia el servicio de Cloud Run.
- **Secret Manager** inyectado como variables de entorno con `--set-secrets`.
- **Terraform** (`developer_connect.tf`): Developer Connect + Private Service Connect + Service Directory hacia Secure Source Manager.
- **Docker multi-stage**: etapa 1 compila el frontend, etapa 2 empaqueta el backend y copia `dist/` a `public/`.

---

## 4. Estructura del repositorio

```
rosa-control-center/
├── backend/                  # API Express (ESM)
│   ├── server.js             # Bootstrap, estáticos, headers COOP, montaje de routers
│   ├── routes/               # index, integrations, mcp, profile, tree, pulse, agile, workspace
│   ├── controllers/          # chat, integrations, profile, reports, tree
│   ├── services/             # ai, odoo, mcp, workspace, chatops, profile
│   └── utils/                # firebase, secrets, logger (SRELogger)
├── frontend/                 # SPA React + Vite
│   └── src/components/       # chat, reports, tree, garden, kudos, agile, auth, profile, a2ui
├── Dockerfile                # Build multi-stage
├── cloudbuild.yaml           # QA (node --check) → build → push → deploy
├── .cloudbuild/triggers.yaml # Trigger push sobre ^master$
├── developer_connect.tf      # IaC Terraform
├── deploy.ps1 / rollback.ps1 / create_secrets.ps1   # Operación desde Windows
├── ROSA_AI_CULTURE.md        # System prompt y blueprint cultural del agente
├── backlog.md / history.md   # Sprint backlog y bitácora de Epics
└── README.md
```

**Métricas de código**
- 94 archivos versionados, ~1.3 MB, **≈16.800 líneas** totales (≈8.900 excluyendo lockfiles).
- Componente más grande: `frontend/src/components/garden/AgentGarden.jsx` (533 líneas).
- Servicio más grande: `backend/services/ai.service.js` (251 líneas).

---

## 5. Superficie funcional

### Vistas del frontend (`App.jsx`)
| Vista | Componente | Función |
|---|---|---|
| `chat` | `ChatPanel` | Conversación con Rosa, hilos persistidos, renderizado A2UI |
| `reports` | `ReportsDashboard` | KPIs de asistencia e inventario Odoo con Recharts |
| `tree` | `DirectoryTree` | Árbol de carpetas/enlaces CRUD por contexto |
| `garden` | `AgentGarden` | Gestión de espacios y agentes de Google Chat |
| `kudos` | `KudosApp` | Envío de tarjetas Cards V2 de reconocimiento a Chat |
| `agile` | `ScrumDashboard` | Radar ágil: autoevaluaciones y agendamiento de capacitaciones |
| `profile` | `UserProfile` | Perfil semántico del usuario (ontología inyectada al prompt) |
| — | `Login` | Autenticación con Firebase (Google sign-in) |

### API REST (`/api`)
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health`, `/health/connectors` | Health checks (polling desde el Sidebar) |
| GET | `/firebase-status` | Estado de Firebase Admin |
| POST | `/ai/chat` | Conversación con Rosa (con **RBAC por email**) |
| GET | `/ai/threads`, `/ai/threads/:id` | Historial de hilos en Firestore |
| GET | `/reports/:instance/attendance` | Reporte de puntualidad (`hr.attendance` en Odoo) |
| GET | `/integrations/odoo/inventory` | Inventario en vivo |
| GET | `/integrations/workspace/search` | Búsqueda en Gmail/Drive vía microservicio |
| POST | `/integrations/chatops/alert` | Alertas ChatOps |
| GET/POST/PUT/DELETE | `/tree/:context[/:id]` | CRUD del árbol de directorios |
| GET/POST | `/profile/config` | Perfil semántico del usuario |
| GET/POST | `/workspace/chat/spaces`, `/chat/message`, `/chat/schedule`, `/chat/cron` | Espacios, envío, programación y despacho diferido de mensajes de Chat |
| GET | `/agile/scrum`, POST `/agile/scrum/schedule-training` | Radar ágil y agendamiento |
| GET | `/pulse` | Landing HTML de check-in de bienestar; persiste en `pulse_checks` |

### Servidor MCP (`/mcp`)
Servidor `odoo-erp-server` expuesto por SSE (`GET /mcp/sse`, `POST /mcp/message`) con 5 herramientas: **`odoo_search_read`, `odoo_read`, `odoo_create`, `odoo_write`, `odoo_unlink`** — lectura y mutación bidireccional sobre cualquier modelo del ERP desde clientes externos (p. ej. Dialogflow CX vía OpenAPI/SSE).

### Herramientas de la IA (function calling en Vertex AI)
`getOdooInventory`, `odoo_search_read`, `odoo_create`, `send_workspace_message`.

---

## 6. Seguridad

**Controles implementados**
- Secretos en **Google Cloud Secret Manager**, inyectados en el despliegue (`ODOO_*`, `SELLSIDE_ODOO_*`, `AUDITOR_WORKSPACE_CREDENTIALS`); política declarada de cero credenciales hardcodeadas.
- Vertex AI sin API keys: autenticación por **IAM** de la service account de Cloud Run.
- **RBAC por lista blanca de emails** en `chat.controller.js`, separando quién puede operar la instancia `MOM` de la `SELLSIDE`.
- Autenticación de usuarios con Firebase Auth; propagación de Bearer token hacia microservicios.
- Cabecera `Cross-Origin-Opener-Policy: same-origin-allow-popups` para el popup de login.
- Paso de QA en el pipeline: `node --check` sobre todo el backend antes de construir la imagen.

**Puntos de atención**
- El servicio corre con `--allow-unauthenticated`; la autorización depende enteramente de la lógica de aplicación.
- El `.gitignore` original usaba backslashes de Windows, lo que provocó el commit de `node_modules`, `.env` y una llave de service account. Corregido en el PR #1, pero **los secretos siguen presentes en el historial de git** (la purga quedó como tarea pendiente).
- La config web de Firebase está hardcodeada como *default* en `frontend/src/firebase.js` (valores públicos por diseño, con override vía `VITE_*`).
- Listas de autorización embebidas en código: agregar un usuario requiere redeploy.
- No hay protección de rama en `master` ni GitHub Actions; el trigger despliega directo a producción con cada push.

---

## 7. CI/CD y operación

**Pipeline** (`cloudbuild.yaml`, trigger sobre push a `^master$`):
1. QA — `node --check` recursivo sobre `backend/`.
2. `docker build` de la imagen multi-stage.
3. `docker push` a Artifact Registry.
4. `gcloud run deploy` con `--set-secrets`.
5. `options.logging: CLOUD_LOGGING_ONLY` (requerido por la service account custom del trigger).

**Scripts de apoyo (PowerShell/Windows)**: `deploy.ps1` (despliegue con validación previa de proyecto para evitar "despliegues fantasma"), `rollback.ps1`, `create_secrets.ps1`, `clean_secrets.bat`, `cred_helper.ps1`, `git-cred-wrapper.cmd`.

**Observabilidad**: `SRELogger` propio como middleware Express, con salida a Cloud Logging.

---

## 8. Calidad y madurez

| Dimensión | Estado |
|---|---|
| Tests automatizados | ❌ Ausentes. Existen 6 scripts manuales de smoke (`backend/test-odoo.js`, `test-vertex.js`, `test-rpc.js`, `test-remote.js`, `test_chat.js`) sin framework ni ejecución en CI |
| Linting | ⚠️ Solo frontend (ESLint 9); backend únicamente `node --check` en CI |
| CI | ⚠️ Cloud Build (sin GitHub Actions, sin `.github/`) |
| Documentación | ✅ Sólida: `README.md`, `ROSA_AI_CULTURE.md`, `backlog.md`, `history.md` con 17 Epics trazados |
| Tipado | ❌ JavaScript puro (sin TypeScript) |
| Datos simulados | ⚠️ `/api/agile/scrum` devuelve datos mock hardcodeados |
| Codificación de archivos | ⚠️ Historial de archivos en UTF-16 por `Out-File` de PowerShell (corregido en PR #3); `history.md` conserva secciones con mojibake |

---

## 9. Historial del proyecto

| Métrica | Valor |
|---|---|
| Commits | 37 |
| Primer commit | 2026-03-12 — *Initial commit: Scaffold Rosa Control Center* |
| Último commit | 2026-07-07/08 |
| Autores | `RepuestosMOM <cio@repuestosmom.cl>` (29), `Rosa <rosa@sellside.cl>` (5), `CTO Sellside SpA` (3) |
| Pull requests | 3, todos mergeados el 2026-07-08 |
| Issues abiertos | 0 |
| Releases / tags | Ninguno |

**Línea de tiempo**
- **Mar 2026** — Epics 1–14: scaffolding, Secret Manager + Firebase, capa API, dashboard, cerebro IA (migración de `@google/genai` a `@google-cloud/vertexai`), conexión XML-RPC con Odoo, servidor MCP, integración Google Workspace, prompt chips, context switcher, informes con Recharts.
- **Abr 2026** — Epics 15–17: Cards V2 de Google Chat, KudosApp, herramientas de mutación en el MCP, perfiles semánticos por usuario, migración de Secure Source Manager de vuelta a **GitHub privado** (pivot FinOps), fixes de OAuth/COOP.
- **Jul 2026** — Saneamiento: limpieza de secretos filtrados y `.gitignore`, migración del proyecto GCP a `odoo-serverless-ss-001`, arreglo del logging de Cloud Build y de la codificación UTF-16.

---

## 10. Deuda técnica y recomendaciones

**Prioridad alta**
1. Purgar el historial de git de los secretos filtrados (`temp_secret.json`) y cerrar definitivamente el proyecto `sellside-admin-audit`.
2. Migrar `SERVICE_ACCOUNT_JSON` / `SELLSIDE_AUDIT_SA_KEY` a ADC o Secret Manager (tarea ya listada en el backlog).
3. Introducir tests automatizados y ejecutarlos en el pipeline antes del deploy.

**Prioridad media**
4. Externalizar las listas RBAC a Firestore o custom claims, para evitar redeploys.
5. Reemplazar el mock de `/api/agile/scrum` por consultas reales a Firestore.
6. Proteger `master` (revisión obligatoria) y añadir un entorno de staging real — las ramas `desarrollo` y `staging` están desactualizadas respecto de `master`.
7. Reemplazar los scripts PowerShell por equivalentes portables, coherente con la política declarada de "Cloud-Only".

**Prioridad baja**
8. Normalizar toda la documentación a UTF-8 (`history.md` tiene mojibake).
9. Evaluar TypeScript o JSDoc + `checkJs` para el backend.
10. Estandarizar el manejo de errores del `aiService` (hoy devuelve `status: 'error'` con HTTP 200).

---

## 11. Repositorios relacionados

| Repositorio | Relación |
|---|---|
| `sellside-workspace-search` | Microservicio externo de Gmail/Drive/Chat consumido vía BFF (no está en esta organización de GitHub) |
| `CTOSellside/rosalia-odoo` | Repositorio público del ecosistema Odoo de Sellside |
