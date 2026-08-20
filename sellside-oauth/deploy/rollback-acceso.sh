#!/usr/bin/env bash
# Cierra el MCP: quita el invoker de allUsers.
#
# Es el corte de emergencia. Deja fuera a Claude, pero también a cualquiera con
# un token robado, de inmediato y sin esperar a que expire nada.
set -euo pipefail
source "$(dirname "$0")/env.sh"

gcloud run services remove-iam-policy-binding "$MCP_SERVICE" \
  --region="$REGION" --member=allUsers --role=roles/run.invoker

echo
echo "Cerrado. Política actual:"
gcloud run services get-iam-policy "$MCP_SERVICE" --region="$REGION" --format=yaml
