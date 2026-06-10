CREATE TABLE IF NOT EXISTS api_keys (
    id          SERIAL PRIMARY KEY,
    user_id     UUID NOT NULL DEFAULT gen_random_uuid(),
    key_hash    CHAR(64) NOT NULL UNIQUE,
    label       VARCHAR(64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active   ON api_keys(key_hash) WHERE active = TRUE;

-- Phase 4.6 — OAuth client_credentials (machine-to-machine).
-- Coexiste avec api_keys (legacy) pendant la transition. Le gateway accepte
-- les deux schémas d'auth ; les clients M2M nouveau code passent par OAuth.
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id          VARCHAR(64) PRIMARY KEY,
    client_secret_hash CHAR(64)     NOT NULL,
    client_name        VARCHAR(128),
    scopes             VARCHAR(256) NOT NULL DEFAULT 'kb:read classify:write',
    active             BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    last_used_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_active ON oauth_clients(client_id) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS ner_config (
    id                  SERIAL PRIMARY KEY,
    active_labels       TEXT[] NOT NULL DEFAULT ARRAY[
        'PERSONNE','DATE','LOCALISATION','ORG',
        'AVS','IBAN','TEL','EMAIL','POLICE','CONTRAT'
    ],
    gliner_threshold    FLOAT   NOT NULL DEFAULT 0.5,
    gliner_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    presidio_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    classifier_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    deanon_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    hook_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    qwen_auto_approve_threshold FLOAT NOT NULL DEFAULT 0.7,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Forward migration on already-initialised DBs
ALTER TABLE ner_config
    ADD COLUMN IF NOT EXISTS qwen_auto_approve_threshold FLOAT NOT NULL DEFAULT 0.7;

-- Ensure exactly one config row always exists
INSERT INTO ner_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS column_labels (
    id            SERIAL PRIMARY KEY,
    header_norm   VARCHAR(128) NOT NULL,
    header_raw    VARCHAR(255) NOT NULL,
    label         VARCHAR(32) NOT NULL,
    source        VARCHAR(16) NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'active',
    confidence    FLOAT DEFAULT 1.0,
    occurrences   INTEGER DEFAULT 1,
    sample_values TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    -- Phase 4.5 — KB contextuelle. NULL = règle générique tous-contextes.
    -- Lookup hiérarchique : (db,table) > (NULL,table) > (db,NULL) > (NULL,NULL).
    db_name       VARCHAR(64),
    table_name    VARCHAR(128),
    -- NULLS NOT DISTINCT : Postgres 15+. Considère NULL == NULL pour l'unicité,
    -- donc (header_norm, NULL, NULL) ne peut exister qu'une seule fois.
    CONSTRAINT column_labels_unique UNIQUE NULLS NOT DISTINCT (header_norm, db_name, table_name)
);
CREATE INDEX IF NOT EXISTS idx_column_labels_header_norm ON column_labels(header_norm);
CREATE INDEX IF NOT EXISTS idx_column_labels_status ON column_labels(status);
CREATE INDEX IF NOT EXISTS idx_column_labels_context ON column_labels(header_norm, db_name, table_name);

CREATE TABLE IF NOT EXISTS custom_patterns (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(64) NOT NULL UNIQUE,
    regex        TEXT NOT NULL,
    entity_label VARCHAR(32) NOT NULL,
    score        FLOAT NOT NULL DEFAULT 0.8,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    source       VARCHAR(16) NOT NULL DEFAULT 'manual',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Forward migration on already-initialised DBs
ALTER TABLE custom_patterns ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'manual';

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id_hash  CHAR(64) NOT NULL,
    text_hash     CHAR(64) NOT NULL,
    entity_counts JSONB NOT NULL DEFAULT '{}',
    sources       JSONB NOT NULL DEFAULT '{}',
    latency_ms    INTEGER NOT NULL,
    format        VARCHAR(16),
    field_count   INTEGER,
    token_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id_hash);

CREATE TABLE IF NOT EXISTS dwh_sources (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(64) NOT NULL UNIQUE,
    db_type            VARCHAR(16) NOT NULL,
    host               VARCHAR(255),
    port               INTEGER,
    username           VARCHAR(128),
    password_encrypted TEXT,
    options            JSONB NOT NULL DEFAULT '{}',
    db_filter          TEXT[] NOT NULL DEFAULT '{}',
    last_scan_at       TIMESTAMPTZ,
    last_scan_status   VARCHAR(16),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scan_jobs (
    id            SERIAL PRIMARY KEY,
    source_id     INTEGER REFERENCES dwh_sources(id) ON DELETE CASCADE,
    status        VARCHAR(16) NOT NULL DEFAULT 'running',
    total_cols    INTEGER NOT NULL DEFAULT 0,
    scanned_cols  INTEGER NOT NULL DEFAULT 0,
    found_labels  INTEGER NOT NULL DEFAULT 0,
    current_db    VARCHAR(64),
    current_table VARCHAR(128),
    error         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS scan_jobs_source_idx ON scan_jobs(source_id);
