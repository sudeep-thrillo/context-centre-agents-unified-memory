# context-centre-agents-unified-memory

A small MCP toolset that exposes lead data from Google Cloud Storage. This repository is configured for Python dependency management via `pyproject.toml` and Docker deployment using Docker Compose.

## Requirements

- Python 3.14+
- Docker
- Docker Compose
- A Google Cloud service account JSON file with access to the `context_center_stage` bucket

## Environment

The service uses the following environment variables:

- `GCS_SERVICE_ACCOUNT_PATH`: absolute path to the service account JSON file
- `GOOGLE_APPLICATION_CREDENTIALS`: set automatically in Docker to the same path

If you run locally, set `GCS_SERVICE_ACCOUNT_PATH` either in `.env` or in your shell.

## Local development

Install dependencies and run locally:

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install .
GCS_SERVICE_ACCOUNT_PATH=/path/to/service-account.json uv run mcp dev mcp_server.py
```

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

## Docker Compose

The repository includes `docker-compose.yml` for production-ready deployment. The service mounts the service account secret into the container at `/run/secrets/google-credentials.json`.

## Scripts

- `scripts/start.sh`: start the service locally with Docker Compose
- `scripts/deploy_vm.sh`: pull and run Docker Compose on a VM

## Secrets and cleanup

- Do not commit your service account JSON file.
- The project includes `.gitignore` and `.dockerignore` to keep secrets, virtual environments, and build artifacts out of version control.
