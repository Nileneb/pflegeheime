FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libcairo2 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY marktradar/ ./marktradar/
COPY docker/seed/ /seed/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Query-Pfad: Embeddings (bge-m3) lokal auf GPU-Host. qwen-Chat → Cloud
# (OLLAMA_CHAT_HOST + OLLAMA_API_KEY + CHAT_MODEL via compose-Secrets gesetzt).
ENV PFLEGE_DB=/data/pflege.db \
    PFLEGE_MCP_TRANSPORT=streamable-http \
    PFLEGE_MCP_HOST=0.0.0.0 \
    PFLEGE_MCP_PORT=8088 \
    EMBED_HOST=http://192.168.178.11:11434 \
    EMBED_MODEL=bge-m3 \
    EMBED_DIM=1024
EXPOSE 8088
VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
