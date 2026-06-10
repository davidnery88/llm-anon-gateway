FROM python:3.11-slim-bookworm

LABEL maintainer="David Miguel Loureiro Neri <david@neri.contact>"
LABEL description="LLM Anonymization Gateway - Metadata server"
LABEL org.opencontainers.image.source="https://github.com/davidneri/llm-anon-gateway"

WORKDIR /app

# ODBC + driver SQL Server (pour pyodbc / scan multi-DB)
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./gateway/
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
