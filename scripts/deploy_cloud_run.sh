#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-context-centre-mcp-v1}"
COLLECTION_ROOT="${COLLECTION_ROOT:-prod}"
FIRESTORE_DATABASE_ID="${FIRESTORE_DATABASE_ID:-(default)}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-}"

# Scaling / concurrency (see docs/sales-copilot-mcp-recommendations.md).
# min-instances 1 keeps a warm instance so MCP sessions aren't lost to cold-start
# churn; max-instances lifts the old service-level cap of 5; session-affinity
# routes a client's follow-up requests back to the instance holding its session.
MIN_INSTANCES="${MIN_INSTANCES:-1}"
MAX_INSTANCES="${MAX_INSTANCES:-40}"
CONCURRENCY="${CONCURRENCY:-40}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-1}"
MCP_FS_CONCURRENCY="${MCP_FS_CONCURRENCY:-40}"

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
  --set-env-vars "FIRESTORE_DOCUMENTS_COLLECTION=${COLLECTION_ROOT},FIRESTORE_DATABASE_ID=${FIRESTORE_DATABASE_ID},MCP_TRANSPORT=http,MCP_HOST=0.0.0.0,MCP_FS_CONCURRENCY=${MCP_FS_CONCURRENCY}" \
  --port 8080 \
  --min-instances "$MIN_INSTANCES" \
  --max-instances "$MAX_INSTANCES" \
  --concurrency "$CONCURRENCY" \
  --session-affinity \
  --memory "$MEMORY" \
  --cpu "$CPU" \
  $( [[ -n "$SERVICE_ACCOUNT" ]] && printf '--service-account %s' "$SERVICE_ACCOUNT" )

echo "Deployment complete. Use \"gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format='value(status.url)'\" to get the service URL."