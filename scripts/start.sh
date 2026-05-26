#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GCS_SERVICE_ACCOUNT_PATH:-}" ]]; then
  echo "Error: GCS_SERVICE_ACCOUNT_PATH is not set. Copy .env.example to .env or export the variable."
  exit 1
fi

docker compose up -d --build
