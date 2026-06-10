# LLM Anonymization Gateway

Proxy LLM avec anonymisation automatique des données sensibles (conformité nLPD suisse).  
Compatible API OpenAI. Intègre un serveur MCP pour Claude Code.

## Démarrage rapide

### 1. Prérequis

- Docker Compose ≥ 2.20
- Ollama avec `qwen3-pii` chargé (voir [MODEL.md](MODEL.md))
- Python 3.11+ pour le MCP server local

### 2. Configuration

```bash
cp .env.example .env
# Remplir : ANTHROPIC_API_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD
```

### 3. Démarrage (serveur gateway)

```bash
docker compose up -d
# Attendre que tous les services soient healthy (~30s)
docker compose ps
```

### 4. Démarrage (sidecar local sur poste utilisateur)

```bash
cd sidecar
docker compose up -d
# Vérifier que le sidecar est healthy
curl http://localhost:8787/healthz
```

### 5. Générer une clé API

```bash
# Avec docker compose en cours
docker compose exec gateway python scripts/generate_api_key.py
# Ou en local avec POSTGRES_DSN configuré
python scripts/generate_api_key.py --postgres-dsn "postgresql://postgres:changeme@localhost:5432/anondb"
```

La clé est affichée **une seule fois** — la noter immédiatement.

### 6. MCP Server (Claude Code)

```bash
cd mcp_server
pip install -r requirements.txt
```

Ajouter dans `.claude/settings.local.json` de chaque utilisateur :

```json
{
  "mcpServers": {
    "anon-gateway": {
      "command": "python",
      "args": ["/chemin/absolu/vers/mcp_server/server.py"],
      "env": {
        "GATEWAY_URL": "http://192.168.x.x:8000",
        "GATEWAY_API_KEY": "anon_xxxx_clé_personnelle"
      }
    }
  }
}
```

### 7. Frontend

Ouvrir `http://localhost:3000` (ou l'IP LAN du serveur) — saisir sa clé API, visualiser le mapping, dé-anonymiser des textes.

## Images Docker

Deux images Docker sont disponibles sur Docker Hub :

### Gateway (serveur central)

```bash
docker pull davidneri/anon-gateway:latest
```

**Contenu** : FastAPI gateway, PostgreSQL, nginx (frontend)  
**Ports** : 8001 (API), 3000 (frontend HTTP), 3443 (frontend HTTPS)  
**Taille** : ~150 MB

### Sidecar (poste utilisateur)

```bash
docker pull davidneri/anon-sidecar:latest
```

**Contenu** : FastAPI sidecar, GLiNER, Presidio, spaCy, Redis  
**Ports** : 8787 (API locale)  
**Taille** : ~2 GB (modèles ML inclus)

### Build local

```bash
# Builder les images localement
./scripts/docker_build.sh [VERSION]

# Push vers Docker Hub
./scripts/docker_push.sh [VERSION]
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Poste utilisateur                                           │
│                                                             │
│  Claude Code                                                │
│    ↓ ANTHROPIC_BASE_URL=http://localhost:8787               │
│                                                             │
│  Sidecar Proxy (:8787)                                      │
│    ├── Anonymise prompts sortants (GLiNER + Presidio)       │
│    ├── Forward vers api.anthropic.com                       │
│    ├── Dé-anonymise réponses entrantes (streaming SSE)      │
│    └── Redis local (mappings tokens ↔ PII, TTL 24h)         │
│                                                             │
│  Frontend (:3000)                                           │
│    ├── admin.html (gestion patterns, clés API)              │
│    ├── demo_dashboard.html (détections PII en temps réel)   │
│    └── demo_mask.html (bouton démo masque)                  │
│                                                             │
│  MCP Server                                                 │
│    └── query_db(sql) → anonymise résultats via sidecar      │
└─────────────────────────────────────────────────────────────┘
                            ↓ HTTPS (optionnel)
┌─────────────────────────────────────────────────────────────┐
│ Serveur LAN                                                  │
│                                                             │
│  Gateway API (:8001)                                        │
│    ├── /api/kb/snapshot (knowledge base colonnes)           │
│    ├── /api/classify_column (classification qwen3-pii)      │
│    ├── /api/audit (logs audit)                              │
│    ├── /admin/* (gestion config, patterns, clés)            │
│    └── /oauth/token (authentification M2M)                  │
│                                                             │
│  PostgreSQL (anondb)                                        │
│    ├── column_labels (KB headers → labels PII)              │
│    ├── custom_patterns (regex Presidio custom)              │
│    ├── api_keys (clés API utilisateurs)                     │
│    └── audit_log (logs anonymisés)                          │
│                                                             │
│  Ollama (qwen3-pii)                                         │
│    └── Classification colonnes ambiguës                     │
│                                                             │
│  Frontend nginx (:3000 HTTP, :3443 HTTPS)                   │
│    └── Sert admin.html + proxy vers gateway                 │
└─────────────────────────────────────────────────────────────┘
```

## Formats supportés

| Format | Détection | Hint NER |
|--------|-----------|----------|
| Markdown table | `\|` dans 2+ lignes | header de colonne |
| CSV | comma count cohérent | header de colonne |
| JSON | commence par `{` ou `[` | clé JSON |
| Texte libre | fallback | — |

## Pile NER

1. **GLiNER** `urchade/gliner_multi_pii-v1` (ou fine-tuné via `finetune_gliner/`) — NER rapide CPU
2. **Presidio** — patterns IBAN CH, AVS, email, téléphone, numéro de police/contrat
3. **qwen3-pii via Ollama** — fallback colonnes ambiguës (tabulaire uniquement, voir [MODEL.md](MODEL.md))

## Déploiement équipe (5 users)

- **Serveur LAN** : RTX 5080, 16 GB RAM — fait tourner Docker Compose + Ollama
- **Chaque utilisateur** : 1 clé API personnelle, sidecar installé localement
- **Sessions isolées** : le mapping Redis est isolé par `user_id` dérivé de la clé API
- **Ports exposés sur le LAN** : 8001 (gateway), 3000 (frontend HTTP), 3443 (frontend HTTPS)
- **Redis et PostgreSQL** : non exposés hors du réseau Docker

## Interface d'administration

Accessible à `http://SERVER_IP:3000/admin.html` — requiert le `ADMIN_SECRET` défini dans `.env`.

| Section | Action |
|---|---|
| Entités actives | Activer / désactiver chaque type de PII détecté |
| Seuil GLiNER | Ajuster la sensibilité (0.1 = tout détecter, 0.9 = très strict) |
| Patterns personnalisés | Ajouter des regex pour les identifiants internes |
| Clés API | Créer et révoquer les clés des utilisateurs |

La configuration est persistée en base et rechargée à chaud — pas de redémarrage nécessaire.

## Base de démo

Une base SQLite de démo est disponible dans `demo/demo.sqlite` avec un schéma réaliste (clients, véhicules, contrats, sinistres, interventions, agents).

```bash
# Régénérer la base de démo
python3 demo/seed_db.py

# Lancer la démo scriptée
bash demo/zero_trust/run_demo.sh
```

## Sécurité

Voir [docs/SECURITY.md](docs/SECURITY.md) pour :
- Architecture zero-trust (PII ne quitte jamais le poste utilisateur)
- Isolation par user_id (mappings Redis isolés)
- Fail-safe NER (bloquer si panne, jamais laisser passer en clair)
- HTTPS (reverse proxy TLS avec mkcert)
- Conformité RGPD/nLPD

## Licence

Ce projet est sous licence **PolyForm Noncommercial 1.0.0**.

**Usage autorisé** : personnel, éducatif, recherche, usage interne non-commercial  
**Usage interdit** : vente, intégration dans produits commerciaux, SaaS commercial

Pour une licence commerciale, contacter : david@neri.contact

Voir [LICENSE](LICENSE) pour le texte complet et [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) pour les licences des dépendances.

## V2 — Roadmap

- [x] Format XML (defusedxml) + JSON/XML imbriqué dans une valeur de colonne (varchar) — voir `sidecar/formats.py`, `_anonymize_structured`
- [x] SQL brut INSERT/UPDATE/DELETE/SELECT (sqlglot) — autonome + embarqué, voir `sidecar/sql_anon.py`
- [ ] NER microservice séparé (scale horizontal gateway indépendant du NER)
- [ ] Streaming SSE sur /v1/chat/completions
- [ ] Logs structurés PostgreSQL (metadata sans PII)
