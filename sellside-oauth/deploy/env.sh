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

# DECIDIDO: el issuer es la URL *.run.app. Es estable, no depende de que la
# región soporte mapeo de dominios y no exige balanceador global.
#
# Queda grabado en los metadatos publicados y en el claim `iss` de cada token ya
# emitido: cambiarlo rompe todas las conexiones existentes de Claude. Si algún
# día hay que mover a auth.sellside.cl, no es editar esta línea — es una
# migración con reautorización de todos los conectores.
export ISSUER="${ISSUER:-https://${AUTH_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app}"
export MCP_BASE_URL="${MCP_BASE_URL:-https://${MCP_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app}"
export RESOURCE_URI="${RESOURCE_URI:-${MCP_BASE_URL}/mcp}"

export AUTH_SA="${AUTH_SA:-sellside-auth@${PROJECT}.iam.gserviceaccount.com}"
export JWT_SECRET_NAME="${JWT_SECRET_NAME:-oauth-jwt-signing-key}"

echo "PROJECT=$PROJECT  REGION=$REGION"
echo "ISSUER=$ISSUER"
echo "RESOURCE_URI=$RESOURCE_URI"
