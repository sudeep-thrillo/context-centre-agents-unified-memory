#!/usr/bin/env bash
set -euo pipefail

# Use this script on the VM after checking out the repository.
# Ensure Docker and docker-compose are installed before running.

docker compose pull || true
docker compose up -d --build
