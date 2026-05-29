FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir pip setuptools wheel
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8080

CMD ["python3", "mcp_server.py"]
