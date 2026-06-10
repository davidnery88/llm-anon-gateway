# Spec — Scan multi-DB / multi-serveurs depuis l'admin UI

Date : 2026-06-07
Statut : validé (design approuvé section par section)

## Contexte & objectif

Permettre à un admin de **configurer N serveurs de base de données** et de lancer un
**scan batch** qui classe les colonnes via le classeur `qwen3-pii`, en écrivant les
résultats dans la knowledge base `column_labels` avec `status='pending'` pour validation
humaine. Objectif : peupler la KB de colonnes sans script ad-hoc, sur plusieurs sources
(DWH analytique, bases opérationnelles assurance/assistance…).

Item issu de `ROADMAP.md` → « Évolutions futures → Scan multi-DB / multi-serveurs ».

## Décisions de design (actées)

1. **Exécution du scan : côté gateway** (centralisé). ⚠️ **Assouplissement zero-trust assumé** :
   le gateway se connecte aux DB et lit des valeurs réelles (PII) pendant le scan.
   Mitigations obligatoires : compte DB lecture seule recommandé, **réduction immédiate des
   valeurs en métadonnées**, **aucun log** des valeurs, credentials chiffrés at-rest, seules
   3 valeurs d'échantillon conservées par colonne (pour la revue admin, comme le workflow Qwen actuel).
   La PII n'existe que **transitoirement en RAM** du gateway pendant le scan (brèche bornée).
   *(Alternative hybride/zero-trust écartée par l'utilisateur : aurait nécessité un agent local.)*
2. **Moteurs supportés** : PostgreSQL, MySQL/MariaDB, SQL Server, SQLite (démo/tests).
3. **Vues incluses** : tables **ET** vues par défaut (`include_views=true`), schémas système exclus.
4. **UI** : section admin « Sources de données », clean/sobre, via skill `frontend-design` à l'impl.

## Architecture

Tout côté gateway (FastAPI + PostgreSQL existant). Nouveaux modules :
- `gateway/dwh_sources.py` — CRUD sources + chiffrement credentials (Fernet).
- `gateway/db_connectors.py` — abstraction multi-moteur via **SQLAlchemy Core** (sync).
- `gateway/scanner.py` — moteur de scan en tâche background + suivi de progression.
- `gateway/value_metadata.py` — calcul des métadonnées (repris de `sidecar/column_classifier._value_metadata`).
- `gateway/dwh_router.py` — endpoints `/admin/dwh_sources/*` (pattern `admin_router` + `X-Admin-Secret`).

Réutilise l'existant : `ColumnClassifier.classify(value_metadata=...)`, `column_labels` (upsert /
pending / bulk_approve), section « À valider » du frontend.

## Data flow

```
POST /admin/dwh_sources/{id}/scan
  -> crée scan_jobs(status=running) ; asyncio.create_task(scan(...))
scan():
  src = get(id); creds = Fernet.decrypt(src.password_encrypted)
  engine = sqlalchemy(src, creds)
  dbs = src.db_filter or connector.list_databases()
  refs = colonnes (tables+vues, hors schémas système) de toutes les dbs
  scan_jobs.total_cols = len(refs)
  for ref in refs:
    if cancelled: break
    if column_labels.exists(ref, status='active'): continue        # incrémental
    vals = connector.sample_values(ref, n=5)                        # PII transitoire en RAM
    meta = value_metadata(vals)
    label,conf = classifier.classify(value_metadata=meta)          # métadonnées only -> qwen
    if label:
        column_labels.upsert(label, source='qwen3',
            status='active' if conf>=seuil else 'pending',
            db_name=ref.db, table_name=ref.table, sample_values=vals[:3])
        scan_jobs.found_labels += 1
    scan_jobs.update(scanned_cols+1, current_db, current_table)     # progression live
  scan_jobs.finish('done')  # ou 'failed'+error sur exception ; try/except par table
```

## Modèle de données (`scripts/init_db.sql`)

```sql
CREATE TABLE dwh_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    db_type VARCHAR(16) NOT NULL,         -- postgresql|mysql|sqlserver|sqlite
    host VARCHAR(255), port INTEGER,
    username VARCHAR(128),
    password_encrypted TEXT,              -- Fernet, jamais en clair, jamais renvoyé par l'API
    options JSONB DEFAULT '{}',           -- {sqlite_path, odbc_driver, sslmode, include_views}
    db_filter TEXT[] DEFAULT '{}',        -- [] = toutes les DB
    last_scan_at TIMESTAMPTZ, last_scan_status VARCHAR(16),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE scan_jobs (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES dwh_sources(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL DEFAULT 'running',  -- running|done|failed|cancelled
    total_cols INTEGER DEFAULT 0, scanned_cols INTEGER DEFAULT 0, found_labels INTEGER DEFAULT 0,
    current_db VARCHAR(64), current_table VARCHAR(128),
    error TEXT, started_at TIMESTAMPTZ DEFAULT NOW(), finished_at TIMESTAMPTZ
);
```
`column_labels` inchangé (`db_name`/`table_name` existent déjà ; `table_name` contient table ou vue).

## Connecteurs (`db_connectors.py`)

SQLAlchemy Core + drivers **synchrones** (appelés via `asyncio.to_thread`) :
PostgreSQL=`psycopg2-binary`, MySQL=`pymysql`, SQL Server=`pyodbc` (ODBC+msodbcsql18 dans le Dockerfile),
SQLite=`sqlite3`. Interface : `list_databases()`, `list_objects(db, include_views)` (via `inspect()`:
`get_table_names`+`get_view_names`+`get_columns`, hors schémas système), `sample_values(ref, n)`.

## Endpoints (`dwh_router.py`, `/admin`, `X-Admin-Secret`)

- `GET/POST /admin/dwh_sources`, `PUT/DELETE /admin/dwh_sources/{id}`
- `POST /admin/dwh_sources/{id}/test` → ok + liste des DB
- `POST /admin/dwh_sources/{id}/scan` (body: DBs) → `{job_id}`
- `GET /admin/dwh_sources/scan/{job_id}` → progression
- `POST /admin/dwh_sources/scan/{job_id}/cancel`

## Frontend (`admin.html`)

Section « Sources de données » : liste (cartes, badge type, dernier scan), formulaire add/edit
(nom/type/host/port/user/password/include_views/filtre), bouton « Tester » (→ DB cochables),
bouton « Lancer le scan » (→ barre de progression par poll). Résultats → section « À valider »
existante. Style clean/sobre cohérent avec l'existant (skill `frontend-design`).

## Sécurité

- `DWH_ENC_KEY` (env, Fernet) ; **absente → refus de stocker des credentials** (fail-safe).
- Mots de passe write-only (jamais renvoyés).
- Compte DB lecture seule recommandé (doc).
- Aucun log des valeurs ; seules 3 samples/colonne conservées pour la revue.
- Admin-only. Risque zero-trust documenté dans `docs/SECURITY.md` (à mettre à jour : section scan gateway-side).

## Tests

- **Unitaires** : round-trip Fernet, `value_metadata`, skip incrémental, connecteurs (SQLite réel, autres mockés).
- **e2e** : `demo/demo.sqlite` comme source → créer source → scan → asserts `column_labels`
  peuplé en `pending`, `scan_jobs` passe à `done`. Classifier Qwen **mocké** (pas de modèle requis).

## Dépendances

`sqlalchemy`, `psycopg2-binary`, `pymysql`, `pyodbc`, `cryptography` ;
Dockerfile gateway : `unixodbc` + `msodbcsql18`.

## Hors scope (YAGNI / futur)

- Parallélisation de la classification (le scan séquentiel ~0.8s/colonne suffit pour démarrer).
- Reprise de job après redémarrage du gateway (un job `running` interrompu = marqué `failed`).
- Dédoublonnage colonne table-vs-vue (entrées distinctes pour l'instant ; à réviser si bruit).
- Chiffrement de la clé `DWH_ENC_KEY` elle-même (gérée comme secret d'env, comme `OAUTH_SIGNING_KEY`).
