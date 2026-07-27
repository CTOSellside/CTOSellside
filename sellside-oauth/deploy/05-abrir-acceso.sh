#!/usr/bin/env bash
# El paso irreversible: dar invoker a allUsers en el MCP.
#
# Desde este momento la única protección del servicio es tu código. Ese MCP
# expone odoo_write y odoo_unlink.
#
# Para revertir: deploy/rollback-acceso.sh
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo
echo "Vas a permitir que CUALQUIERA en internet invoque $MCP_SERVICE."
echo "A partir de ahí, lo único que separa a un desconocido de odoo_unlink es"
echo "la validación de tokens de tu propio código."
echo

if [[ "${SKIP_VERIFY:-0}" != "1" ]]; then
  echo "Ejecutando la verificación previa (USE_IAM_TOKEN=1)…"
  if ! USE_IAM_TOKEN=1 "$(dirname "$0")/04-verificar.sh"; then
    echo
    echo "La verificación falló. No se abre nada."
    exit 1
  fi
fi

read -r -p "Escribe el nombre del servicio para confirmar (${MCP_SERVICE}): " CONFIRMACION
if [[ "$CONFIRMACION" != "$MCP_SERVICE" ]]; then
  echo "Cancelado."
  exit 1
fi

gcloud run services add-iam-policy-binding "$MCP_SERVICE" \
  --region="$REGION" --member=allUsers --role=roles/run.invoker

# La binding existente de la service account de compute se mantiene: lo que ya
# consumía el MCP por IAM sigue funcionando igual.

echo
echo "Hecho. Verifica de nuevo, ahora sin credenciales:"
echo "  deploy/04-verificar.sh"
