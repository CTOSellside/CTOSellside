#!/usr/bin/env bash
# Fase 0 — qué existe hoy. No modifica nada.
set -euo pipefail
source "$(dirname "$0")/env.sh"

gcloud config set project "$PROJECT" >/dev/null

echo
echo "== APIs habilitadas =="
gcloud services list --enabled --format='table(config.name)'

echo
echo "== Servicios de Cloud Run en $REGION =="
gcloud run services list --region "$REGION" \
  --format='table(metadata.name, status.url, spec.template.spec.containers[0].image)'

echo
echo "== Imagen del MCP =="
gcloud run services describe "$MCP_SERVICE" --region "$REGION" \
  --format='value(spec.template.spec.containers[0].image)' || true

echo
echo "== Quién puede invocar el MCP hoy =="
gcloud run services get-iam-policy "$MCP_SERVICE" --region "$REGION" --format=yaml || true

echo
echo "== Builds recientes (¿hay código fuente?) =="
gcloud builds list --limit=20 \
  --format='table(id, source.repoSource.repoName, images[0], createTime)' || true

echo
echo "== Repositorios de imágenes =="
gcloud artifacts repositories list --location="$REGION" || true

cat <<'NOTA'

Los clientes OAuth no se listan bien por CLI. Revísalos en:
  https://console.cloud.google.com/apis/credentials?project=odoo-serverless-ss-001

Si lo que ves son «IDs de cliente de OAuth 2.0», eso es un *cliente*: sirve para
llamar APIs de Google. Este trabajo necesita lo contrario, un servidor de
autorización propio. Ese cliente sí se reutiliza, pero solo para el login de la
pantalla de consentimiento (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).
NOTA
