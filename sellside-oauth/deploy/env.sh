#!/usr/bin/env bash
# Variables comunes a todos los scripts. Se carga con `source deploy/env.sh`.
#
# El PROJECT_NUMBER aparece en las URLs de Cloud Run. Si no lo conoces, este
# script lo resuelve solo.

set -euo pipefail

export PROJECT="${PROJECT:-odoo-serverless-ss-001}"
export REGION="${REGION:-southamerica-west1}"

export AUTH_SERVICE="${AUTH_SERVICE:-sellside-auth}"
export MCP_SERVICE="${MCP_SERVICE:-odoo-mcp-sellside}"

if [[ -z "${PROJECT_NUMBER:-}" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  export PROJECT_NUMBER
fi

# El issuer queda grabado en los metadatos y en cada token emitido. Cambiarlo
# rompe todas las conexiones existentes: se elige una vez.
export ISSUER="${ISSUER:-https://${AUTH_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app}"
export MCP_BASE_URL="${MCP_BASE_URL:-https://${MCP_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app}"
export RESOURCE_URI="${RESOURCE_URI:-${MCP_BASE_URL}/mcp}"

export AUTH_SA="${AUTH_SA:-sellside-auth@${PROJECT}.iam.gserviceaccount.com}"
export JWT_SECRET_NAME="${JWT_SECRET_NAME:-oauth-jwt-signing-key}"

echo "PROJECT=$PROJECT  REGION=$REGION"
echo "ISSUER=$ISSUER"
echo "RESOURCE_URI=$RESOURCE_URI"
