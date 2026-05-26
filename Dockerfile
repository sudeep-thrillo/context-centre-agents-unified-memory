FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir pip setuptools wheel
RUN pip install --no-cache-dir .

COPY . .

CMD ["python3", "mcp_server.py"]
