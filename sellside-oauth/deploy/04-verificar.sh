#!/usr/bin/env bash
# Fase 4 — las comprobaciones que tienen que pasar antes de registrar el
# conector en Claude y antes de abrir el acceso público.
#
# Hay un huevo y una gallina: mientras el MCP siga cerrado por IAM, una petición
# sin credenciales la corta Cloud Run con 403 y nunca llega a tu código. Para
# probar el 401 *antes* de abrir:
#
#     USE_IAM_TOKEN=1 deploy/04-verificar.sh
#
# Eso manda un ID token de Google: pasa el filtro de IAM y llega al middleware,
# que debe rechazarlo igual —no es un access token emitido para este recurso—.
# Es justo la prueba que interesa: el 401 lo produce tu código, no la ausencia
# de permisos.
#
# Sale con código != 0 si alguna falla, para poder encadenarlo:
#     USE_IAM_TOKEN=1 deploy/04-verificar.sh && deploy/05-abrir-acceso.sh
set -uo pipefail
source "$(dirname "$0")/env.sh"

AS="$ISSUER"
MCP="$MCP_BASE_URL"
FALLOS=0

AUTH=()
if [[ "${USE_IAM_TOKEN:-0}" == "1" ]]; then
  AUTH=(-H "authorization: Bearer $(gcloud auth print-identity-token)")
  echo "(modo IAM: se envía un ID token de Google para atravesar Cloud Run)"
fi

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fallo() { printf '  \033[31m✗\033[0m %s\n' "$1"; FALLOS=$((FALLOS + 1)); }

echo
echo "1. El MCP rechaza peticiones sin un access token válido"
RESPUESTA="$(curl -s -o /dev/null -D - -X POST "$MCP/mcp" "${AUTH[@]}" \
  -H 'content-type: application/json' -d '{"jsonrpc":"2.0","method":"tools/list","id":1}')"
PRIMERA="$(head -1 <<<"$RESPUESTA")"
if grep -qi '^HTTP/.* 401' <<<"$RESPUESTA"; then
  ok "responde 401"
elif grep -qi '^HTTP/.* 403' <<<"$RESPUESTA" && [[ ${#AUTH[@]} -eq 0 ]]; then
  fallo "403 de IAM: el servicio sigue cerrado. Reintenta con USE_IAM_TOKEN=1"
else
  fallo "esperaba 401; devolvió: $PRIMERA"
fi
if grep -qi 'www-authenticate:.*resource_metadata=' <<<"$RESPUESTA"; then
  ok "WWW-Authenticate trae resource_metadata"
else
  fallo "falta WWW-Authenticate con resource_metadata"
fi

echo
echo "2. Metadatos del recurso (RFC 9728)"
PRM="$(curl -s "${AUTH[@]}" "$MCP/.well-known/oauth-protected-resource/mcp")"
grep -q 'authorization_servers' <<<"$PRM" || \
  PRM="$(curl -s "${AUTH[@]}" "$MCP/.well-known/oauth-protected-resource")"
if grep -q "$AS" <<<"$PRM"; then
  ok "authorization_servers incluye $AS"
else
  fallo "el documento no apunta al AS: $PRM"
fi
if grep -q "$RESOURCE_URI" <<<"$PRM"; then
  ok "resource = $RESOURCE_URI"
else
  fallo "la URI canónica publicada no coincide con RESOURCE_URI"
fi

echo
echo "3. Metadatos del servidor de autorización (RFC 8414)"
ASM="$(curl -s "$AS/.well-known/oauth-authorization-server")"
for campo in issuer authorization_endpoint token_endpoint registration_endpoint jwks_uri; do
  if grep -q "\"$campo\"" <<<"$ASM"; then ok "$campo"; else fallo "falta $campo"; fi
done
if grep -q 'code_challenge_methods_supported' <<<"$ASM" && \
   grep -q 'S256' <<<"$ASM" && ! grep -q '"plain"' <<<"$ASM"; then
  ok "PKCE S256 y nada de plain"
else
  fallo "PKCE mal declarado en los metadatos del AS"
fi

echo
echo "4. Registro dinámico (RFC 7591)"
REG="$(curl -s -X POST "$AS/register" -H 'content-type: application/json' \
  -d '{"client_name":"prueba de verificación","redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}')"
if grep -q '"client_id"' <<<"$REG"; then
  ok "devuelve client_id"
else
  fallo "no devolvió client_id: $REG"
fi

echo
echo "5. El MCP rechaza un token forjado (alg=none)"
BASURA="eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhdGFjYW50ZSJ9."
if [[ ${#AUTH[@]} -gt 0 ]]; then
  echo "  – omitido: en modo IAM la cabecera Authorization ya va ocupada."
  echo "    Repite esta comprobación después de abrir el acceso."
else
  CODIGO="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$MCP/mcp" \
    -H "authorization: Bearer $BASURA" -H 'content-type: application/json' -d '{}')"
  if [[ "$CODIGO" == "401" ]]; then
    ok "token forjado rechazado (401)"
  else
    fallo "un token con alg=none obtuvo $CODIGO"
  fi
fi

echo
if [[ $FALLOS -eq 0 ]]; then
  echo "Todo verde."
  echo "  · Si aún no abriste el acceso:  deploy/05-abrir-acceso.sh"
  echo "  · Si ya está abierto:  Claude → Ajustes → Conectores → Añadir conector"
  echo "    personalizado → $RESOURCE_URI"
  exit 0
fi
echo "$FALLOS comprobación(es) fallida(s). No abras el acceso público todavía."
exit 1
