# Imagen de sellside-auth. Cloud Run puede construirla con `--source .`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY auth_server ./auth_server
COPY mcp_resource_server ./mcp_resource_server
COPY main.py ./

# Cloud Run inyecta PORT. No corre como root.
RUN useradd --create-home --uid 1001 app
USER app

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
