# context-centre-agents-unified-memory

A small MCP toolset that exposes lead data from Google Firestore. This repository is configured for Python dependency management via `pyproject.toml` and Docker deployment using Docker Compose.

## Requirements

- Python 3.14+
- Docker
- Docker Compose
- A Google Cloud service account JSON file with Firestore access, or Cloud Run service account credentials when deployed

## Environment

The service uses the following environment variables:

- `GOOGLE_APPLICATION_CREDENTIALS`: path to the Google Cloud service account JSON file
- `GCS_SERVICE_ACCOUNT_PATH`: legacy alias for the same credentials path
- `FIRESTORE_DOCUMENTS_COLLECTION`: top-level Firestore collection root (default: `dev_documents`)
- `FIRESTORE_DATABASE_ID`: Firestore database ID to use (default: `(default)`). Set this to `context-center` if your project uses a custom Firestore database.

If you run locally, set `GOOGLE_APPLICATION_CREDENTIALS` or `GCS_SERVICE_ACCOUNT_PATH` in `.env` or in your shell. For production, set `FIRESTORE_DOCUMENTS_COLLECTION=prod` and `FIRESTORE_DATABASE_ID=context-center` if required.

## Local development

Install dependencies and run locally:

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install .
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json uv run mcp dev mcp_server.py
```

If you want to use `GCS_SERVICE_ACCOUNT_PATH` as a legacy alias, that also works.

If you have `pip` 23+ and want editable mode, use:

```bash
python3 -m pip install -e .
```

## Docker

1. Copy the example env file:

```bash
cp .env.example .env
```

2. Update `.env` with your host path to the service account file.

3. Build and start with Docker Compose:

```bash
docker compose up -d --build
```

4. View logs:

```bash
docker compose logs -f context_centre_mcp
```

## Connecting Claude Code to the MCP server

The MCP server now supports HTTP transport on port `8000` and exposes the streamable HTTP route at `/mcp`.

- In Docker Compose, the service is available at `http://localhost:8000/mcp`.
- On a remote VM, use `http://<vm-hostname-or-ip>:8000/mcp`.

If Claude Code supports a remote MCP endpoint, configure it to point at:

```text
http://<host>:8000/mcp
```

The server expects a streamable HTTP client that accepts `text/event-stream`.

If Claude Code uses a specific tool registration, register the remote MCP endpoint and ensure the container host is reachable from Claude Code.

If you are running Claude Code locally, use:

```text
http://localhost:8000/mcp
```

## Docker Compose

The repository includes `docker-compose.yml` for production-ready deployment. The service mounts the service account secret into the container at `/run/secrets/google-credentials.json`.

## Cloud Run deployment

This project can deploy as a Cloud Run service named `context-centre-mcp-v1`.

1. Ensure `gcloud` is installed and authenticated:

```bash
gcloud auth login
```

2. Set your project and optionally region:

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1
export COLLECTION_ROOT=prod
```

3. Deploy with the helper script:

```bash
./scripts/deploy_cloud_run.sh
```

The script builds the container, pushes it to Google Container Registry, and deploys the Cloud Run service.

Once deployed, connect Claude Webapp/Desktop to the streamable MCP endpoint:

```text
https://context-centre-mcp-v1-<hash>-<region>.run.app/mcp
```

If the service is public, the endpoint should be accessible from Claude using `http(s)` and `text/event-stream`.

## Scripts

- `scripts/start.sh`: start the service locally with Docker Compose
- `scripts/deploy_vm.sh`: pull and run Docker Compose on a VM

## Secrets and cleanup

- Do not commit your service account JSON file.
- The project includes `.gitignore` and `.dockerignore` to keep secrets, virtual environments, and build artifacts out of version control.
