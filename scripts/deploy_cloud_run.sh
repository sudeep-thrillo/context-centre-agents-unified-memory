#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-context-centre-mcp-v1}"
COLLECTION_ROOT="${COLLECTION_ROOT:-prod}"
FIRESTORE_DATABASE_ID="${FIRESTORE_DATABASE_ID:-(default)}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: PROJECT_ID is required. Export PROJECT_ID before running."
  exit 1
fi

if [[ -n "$SERVICE_ACCOUNT" ]]; then
  echo "Using service account: $SERVICE_ACCOUNT"
fi

echo "Deploying Cloud Run service '$SERVICE_NAME' to project '$PROJECT_ID' in region '$REGION'..."

gcloud config set project "$PROJECT_ID"
gcloud config set run/region "$REGION"

gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}" .

gcloud run deploy "$SERVICE_NAME" \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "FIRESTORE_DOCUMENTS_COLLECTION=${COLLECTION_ROOT},FIRESTORE_DATABASE_ID=${FIRESTORE_DATABASE_ID},MCP_TRANSPORT=http,MCP_HOST=0.0.0.0" \
  --port 8080 \
  $( [[ -n "$SERVICE_ACCOUNT" ]] && printf '--service-account %s' "$SERVICE_ACCOUNT" )

echo "Deployment complete. Use \"gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format='value(status.url)'\" to get the service URL."