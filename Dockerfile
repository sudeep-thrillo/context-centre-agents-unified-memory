FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir pip setuptools wheel
RUN pip install --no-cache-dir .

COPY . .

CMD ["uv", "run", "mcp", "dev", "mcp_server.py"]
