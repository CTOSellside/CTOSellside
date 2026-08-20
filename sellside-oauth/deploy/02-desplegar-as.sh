#!/usr/bin/env bash
# Fase 2 — desplegar sellside-auth en Cloud Run.
#
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET son el cliente OAuth dedicado que
# autentica personas en la pantalla de consentimiento. Su redirect URI
# autorizada debe ser:
#   ${ISSUER}/callback/google
set -euo pipefail
source "$(dirname "$0")/env.sh"

: "${GOOGLE_CLIENT_ID:?exporta GOOGLE_CLIENT_ID antes de desplegar}"
: "${GOOGLE_CLIENT_SECRET:?exporta GOOGLE_CLIENT_SECRET antes de desplegar}"

# Un client_id con la forma equivocada no rompe el despliegue: rompe el login,
# al final del flujo, con un `invalid_client` de Google que no dice de dónde
# sale. Más barato cortar acá.
if [[ ! "$GOOGLE_CLIENT_ID" =~ ^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$ ]]; then
  echo "GOOGLE_CLIENT_ID no tiene forma de client ID de Google:" >&2
  echo "  $GOOGLE_CLIENT_ID" >&2
  echo "Se espera: <número de proyecto>-<alfanumérico>.apps.googleusercontent.com" >&2
  echo "Cópialo de https://console.cloud.google.com/apis/credentials?project=${PROJECT}" >&2
  exit 1
fi
if [[ ${#GOOGLE_CLIENT_SECRET} -lt 20 ]]; then
  echo "GOOGLE_CLIENT_SECRET parece truncado (${#GOOGLE_CLIENT_SECRET} caracteres)." >&2
  echo "Los secretos de Google rondan los 35 y empiezan por GOCSPX-." >&2
  exit 1
fi

ALLOWED_EMAIL_DOMAINS="${ALLOWED_EMAIL_DOMAINS:-sellside.cl}"
PROTECTED_RESOURCES="${PROTECTED_RESOURCES:-$RESOURCE_URI}"

cd "$(dirname "$0")/.."

# El secreto de Google se guarda en Secret Manager, no en la línea de comando
# de un despliegue que queda en el historial de revisiones.
if ! gcloud secrets describe google-oauth-client-secret >/dev/null 2>&1; then
  printf '%s' "$GOOGLE_CLIENT_SECRET" | \
    gcloud secrets create google-oauth-client-secret --data-file=-
  gcloud secrets add-iam-policy-binding google-oauth-client-secret \
    --member="serviceAccount:${AUTH_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
fi

gcloud run deploy "$AUTH_SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$AUTH_SA" \
  --set-secrets="JWT_SIGNING_KEY=${JWT_SECRET_NAME}:latest,GOOGLE_CLIENT_SECRET=google-oauth-client-secret:latest" \
  --set-env-vars="^;^ISSUER=${ISSUER};PROTECTED_RESOURCES=${PROTECTED_RESOURCES};STORAGE_BACKEND=firestore;GOOGLE_CLOUD_PROJECT=${PROJECT};IDP_MODE=google;GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID};ALLOWED_EMAIL_DOMAINS=${ALLOWED_EMAIL_DOMAINS};ALLOWED_REDIRECT_HOSTS=claude.ai,claude.com" \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=3

# --allow-unauthenticated es correcto acá: el AS tiene que ser alcanzable por el
# navegador del usuario y por Claude. Su protección es el protocolo, no IAM.
# --min-instances=1 evita que un arranque en frío bote el flujo a mitad de camino.

echo
echo "Comprueba los metadatos:"
echo "  curl -s ${ISSUER}/.well-known/oauth-authorization-server | jq"
echo
echo "Recuerda autorizar en el cliente OAuth de Google la redirect URI:"
echo "  ${ISSUER}/callback/google"
