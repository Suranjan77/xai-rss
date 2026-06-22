# idigest application image (web UI + CLI + ingestion + email).
#
# This containers the CPU side of idigest. The GPU pieces stay on the host:
#   - the local LLM (llama-server / Gemma) — reached over the network
#   - audio TTS (F5-TTS, ROCm) — host-only; audio is skipped inside the container
# See docs/DOCKER.md.
FROM python:3.12-slim

# opencv-python-headless needs libglib at runtime; the rest ship manylinux wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# App assets (seed corpus, config defaults). Secrets come from a mounted
# config.local.toml; data/caches are mounted volumes.
COPY config.toml ./
COPY data/seed ./data/seed

# Caches and DB live on mounted volumes (see docker-compose.yml).
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    HF_HOME=/app/.cache/hf \
    IDIGEST_CONFIG_DIR=/app \
    IDIGEST_SEED_DIR=/app/data/seed \
    IDIGEST_DB=/app/data/db/idigest.sqlite3 \
    IDIGEST_LLM_BASE_URL=http://127.0.0.1:8080

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["idigest", "serve-web"]
