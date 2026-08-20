#!/usr/bin/env bash
# Fase 3 — configurar el MCP como resource server.
#
# Este script solo ajusta configuración. El código del MCP ya tiene que estar
# validando tokens (ver mcp_resource_server/ y examples/odoo_mcp_app.py); si no,
# lo único que consigues es un servicio con variables de entorno bonitas.
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo "== Subiendo el techo de concurrencia (hoy max-instances=1) =="
gcloud run services update "$MCP_SERVICE" \
  --region="$REGION" --max-instances="${MCP_MAX_INSTANCES:-5}"

echo "== Apuntando el MCP a su servidor de autorización =="
gcloud run services update "$MCP_SERVICE" \
  --region="$REGION" \
  --update-env-vars="^;^AUTH_SERVER=${ISSUER};RESOURCE_URI=${RESOURCE_URI};RESOURCE_NAME=${MCP_SERVICE};SCOPES_SUPPORTED=odoo:read,odoo:write"
# El prefijo ^;^ cambia el separador a ';' — si no, la coma de SCOPES_SUPPORTED
# se lee como otra variable.

echo
echo "Ahora verifica ANTES de abrir el acceso:  deploy/04-verificar.sh"
