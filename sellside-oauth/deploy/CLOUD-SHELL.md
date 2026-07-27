# Desplegar desde Cloud Shell

Bloques para copiar y pegar en `https://shell.cloud.google.com`. Cloud Shell ya
viene autenticado y con `gcloud`, `git`, `python3` y `openssl`.

El único paso que **no** se puede hacer por CLI es autorizar la redirect URI en
el cliente OAuth de Google: `gcloud` solo administra los clientes de IAP y no
expone ese campo. Por eso el issuer se calcula primero (paso 2) y la consola se
toca una sola vez, antes de desplegar.

---

## 1. Traer el código

```bash
cd ~
git clone -b claude/oauth-mcp-sellside-gcp-gg30q6 \
  https://github.com/CTOSellside/CTOSellside.git
cd CTOSellside/sellside-oauth
```

Si ya lo clonaste antes:

```bash
cd ~/CTOSellside && git fetch origin claude/oauth-mcp-sellside-gcp-gg30q6 \
  && git checkout claude/oauth-mcp-sellside-gcp-gg30q6 && git pull
cd sellside-oauth
```

## 2. Fijar el entorno y ver el issuer

```bash
export PROJECT=odoo-serverless-ss-001
export REGION=southamerica-west1
gcloud config set project $PROJECT

source deploy/env.sh
echo "Redirect URI a autorizar en el cliente OAuth de Google:"
echo "  ${ISSUER}/callback/google"
```

`env.sh` resuelve el número de proyecto solo. Anota esa URL: es lo único que va
a la consola.

## 3. Correr las pruebas (opcional, ~1 min)

Vale la pena antes de tocar el proyecto: si algo del entorno está raro, se sabe
acá y no a mitad del despliegue.

```bash
python3 -m venv /tmp/venv && source /tmp/venv/bin/activate
pip install -q -e '.[dev]' pytest-asyncio
pytest -q
deactivate
```

## 4. Consola: autorizar la redirect URI

`https://console.cloud.google.com/apis/credentials?project=odoo-serverless-ss-001`

En el cliente OAuth 2.0 existente (tipo *aplicación web*), en **URIs de
redireccionamiento autorizados**, agregar exactamente la URL del paso 2:

```
https://sellside-auth-<PROJECT_NUMBER>.southamerica-west1.run.app/callback/google
```

De la misma pantalla salen el **client ID** y el **client secret** para el paso 6.

Si el cliente existente no es de tipo aplicación web, crea uno nuevo ahí mismo:
**Crear credenciales → ID de cliente de OAuth → Aplicación web**.

## 5. Inventario e infraestructura

```bash
deploy/00-inventario.sh          # solo lee; nada se modifica
deploy/01-infraestructura.sh     # APIs, Firestore, service account, llave RSA
```

`01` es idempotente: se puede repetir sin duplicar nada. Si Firestore ya existe
o el secreto ya está creado, lo dice y sigue.

## 6. Desplegar el servidor de autorización

```bash
export GOOGLE_CLIENT_ID='...apps.googleusercontent.com'
read -rsp 'Google client secret: ' GOOGLE_CLIENT_SECRET; echo
export GOOGLE_CLIENT_SECRET

export ALLOWED_EMAIL_DOMAINS=sellside.cl   # sin esto, cualquier cuenta de Google entra

deploy/02-desplegar-as.sh
```

`read -rsp` evita que el secreto quede en el historial del shell.

Tarda unos minutos: Cloud Build construye la imagen desde el `Dockerfile`.

Comprobación rápida:

```bash
curl -s ${ISSUER}/.well-known/oauth-authorization-server | python3 -m json.tool
```

> Si más adelante rotas el secreto de Google, el script no lo actualiza —solo
> crea el secreto si no existe—. Para cambiarlo:
> ```bash
> printf '%s' "$NUEVO_SECRETO" | gcloud secrets versions add google-oauth-client-secret --data-file=-
> gcloud run services update sellside-auth --region $REGION \
>   --set-secrets=GOOGLE_CLIENT_SECRET=google-oauth-client-secret:latest
> ```

## 7. Configurar el MCP

⚠️ Este paso solo ajusta variables de entorno. **El código de
`odoo-mcp-sellside` tiene que estar validando tokens antes** (ver
`examples/odoo_mcp_app.py`); si no, lo único que consigues es un servicio con
variables bonitas que sigue sin autenticar nada.

```bash
deploy/03-configurar-mcp.sh
```

## 8. Verificar antes de abrir

```bash
USE_IAM_TOKEN=1 deploy/04-verificar.sh
```

El `USE_IAM_TOKEN=1` manda un ID token de Google: atraviesa el filtro de IAM y
llega a tu middleware, que debe rechazarlo igual porque no es un access token
para este recurso. Ese 401 lo produce tu código — que es justo lo que hay que
comprobar antes del paso 9.

## 9. Abrir el acceso público

```bash
deploy/05-abrir-acceso.sh
```

Corre la verificación solo y pide escribir el nombre del servicio para
confirmar. Desde ese momento la única protección del MCP es tu código, y ese MCP
expone `odoo_write` y `odoo_unlink`.

Después, sin credenciales:

```bash
deploy/04-verificar.sh
```

## 10. Registrar el conector en Claude

**Ajustes → Conectores → Añadir conector personalizado**, con:

```bash
echo $RESOURCE_URI
```

---

## Revertir

```bash
deploy/rollback-acceso.sh    # cierra el MCP a todo el mundo, de inmediato
```

## Revocar accesos

```bash
export GOOGLE_CLOUD_PROJECT=$PROJECT
pip install -q google-cloud-firestore
python3 tools/revocar.py listar --sujeto google:1234567890
python3 tools/revocar.py sujeto google:1234567890
```

## Ver logs

```bash
gcloud run services logs read sellside-auth --region $REGION --limit 50
gcloud run services logs read odoo-mcp-sellside --region $REGION --limit 50
```
