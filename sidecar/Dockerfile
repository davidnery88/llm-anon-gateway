# Sidecar zero-trust : NER local (GLiNER + Presidio + spaCy fr) + token store
# (Redis local en mémoire). Tourne sur 127.0.0.1 sur la machine utilisateur.
#
# L'image est volumineuse (~2 GB) parce qu'elle bake les modèles ML pour que
# le démarrage à froid n'ait pas besoin du réseau. C'est volontaire.

FROM python:3.12-slim

LABEL maintainer="David Miguel Loureiro Neri <david@neri.contact>"
LABEL description="LLM Anonymization Sidecar - Local NER proxy"
LABEL org.opencontainers.image.source="https://github.com/davidneri/llm-anon-gateway"

# 1. Outils système nécessaires à la compilation des wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Python dependencies
COPY sidecar/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Pre-pull spaCy French model
RUN python -m spacy download fr_core_news_md

# 4. Pre-pull GLiNER weights to the HF cache so the container starts offline
ENV HF_HOME=/opt/hf
RUN python -c "from huggingface_hub import snapshot_download; \
               snapshot_download(repo_id='urchade/gliner_multi_pii-v1', cache_dir='/opt/hf')"

# 5. Application code
COPY sidecar/ ./sidecar/

# 6. Server config
ENV PYTHONUNBUFFERED=1 \
    SIDECAR_PORT=8787 \
    GLINER_MODEL=urchade/gliner_multi_pii-v1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8787/healthz || exit 1

CMD ["uvicorn", "sidecar.main:app", "--host", "0.0.0.0", "--port", "8787"]
