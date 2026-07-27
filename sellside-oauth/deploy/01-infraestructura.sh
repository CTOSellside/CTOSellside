#!/usr/bin/env bash
# Fase 1 — APIs, Firestore, service account y llave de firma.
# Idempotente: se puede correr de nuevo sin romper nada.
set -euo pipefail
source "$(dirname "$0")/env.sh"

gcloud config set project "$PROJECT" >/dev/null

echo "== Habilitando APIs =="
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com

echo "== Base de datos Firestore =="
if gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  echo "   ya existe"
else
  gcloud firestore databases create --location="$REGION" --type=firestore-native
fi

echo "== Service account dedicada =="
# No se usa la default de compute: toda la flota cuelga de ella y no conviene
# darle otro rol crítico.
if gcloud iam service-accounts describe "$AUTH_SA" >/dev/null 2>&1; then
  echo "   ya existe"
else
  gcloud iam service-accounts create sellside-auth \
    --display-name="OAuth AS para los MCP"

  # IAM no es inmediatamente consistente: la cuenta recién creada tarda unos
  # segundos en ser visible para add-iam-policy-binding, que falla con un
  # "does not exist" desconcertante.
  echo -n "   esperando a que propague"
  for _ in $(seq 1 20); do
    if gcloud iam service-accounts describe "$AUTH_SA" >/dev/null 2>&1; then break; fi
    echo -n "."
    sleep 3
  done
  echo
fi

echo "== Permiso de Firestore =="
for intento in $(seq 1 6); do
  if gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:${AUTH_SA}" \
      --role="roles/datastore.user" \
      --condition=None >/dev/null 2>&1; then
    echo "   roles/datastore.user concedido"
    break
  fi
  if [[ $intento -eq 6 ]]; then
    echo "   no se pudo conceder roles/datastore.user tras 6 intentos" >&2
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:${AUTH_SA}" \
      --role="roles/datastore.user" --condition=None
    exit 1
  fi
  echo "   reintentando ($intento/6)…"
  sleep 5
done

echo "== Llave de firma de los JWT =="
if gcloud secrets describe "$JWT_SECRET_NAME" >/dev/null 2>&1; then
  echo "   ya existe (no se rota aquí: ver README, sección de rotación)"
else
  TMP_KEY="$(mktemp)"
  trap 'shred -u "$TMP_KEY" 2>/dev/null || rm -f "$TMP_KEY"' EXIT
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$TMP_KEY"
  gcloud secrets create "$JWT_SECRET_NAME" --data-file="$TMP_KEY"
fi

gcloud secrets add-iam-policy-binding "$JWT_SECRET_NAME" \
  --member="serviceAccount:${AUTH_SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

echo "== TTL de las colecciones efímeras =="
# Firestore borra solo los documentos vencidos; sin esto la base crece para siempre.
for collection in oauth_auth_requests oauth_codes oauth_refresh_tokens; do
  gcloud firestore fields ttls update expire_at \
    --collection-group="$collection" --enable-ttl --quiet || \
    echo "   (revisa a mano el TTL de $collection)"
done

echo
echo "Infraestructura lista. Siguiente: deploy/02-desplegar-as.sh"
