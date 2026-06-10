# Roadmap

Contexte : **domaine assurance** — assurances + assistance, multi-langue FR/EN/DE.

Principe directeur : **faux positif > faux négatif**. Toute amélioration doit préserver la garantie qu'aucun PII ne fuit vers Anthropic.

---

## Fait

### Refacto zero-trust gateway (mai 2026)

Le gateway central a été dépouillé : toute la chaîne d'anonymisation (NER, mapping Redis, deanon) tourne désormais en sidecar local sur chaque poste utilisateur. Le gateway ne garde plus que les métadonnées (KB de colonnes, patterns custom, clés API, classifier qwen3-pii via `/api/classify_column`). Aucun PII n'est jamais stocké ou logué côté serveur.

Composants déposés au passage :
- `gateway/anonymizer.py`, `gateway/cache.py`, `gateway/ner.py`, `gateway/formats.py` — déplacés dans `sidecar/`
- `gateway/anthropic_proxy.py` (jamais commit, prototype SSE) — OAuth-via-hooks est la voie documentée
- routes `POST /v1/chat/completions`, `POST /api/anonymize`, `POST /api/deanonymize`, `GET/DELETE /api/mapping`, `GET /api/config` — retirées
- service `redis` du `docker-compose.yml` racine

Reste à faire : phase 6 (matériel démo zero-trust), packaging install pour Windows/macOS (actuellement Linux + Docker uniquement).

### Cache partagé header → label (knowledge base de colonnes)

Détection PII basée sur les headers de colonnes, avec mapping persistant et partagé entre utilisateurs (les mappings, pas les données — pas de PII stocké côté serveur dans ce cache).

- Static map FR/EN/DE (147 entrées seedées) couvrant assurance, assistance, carte grise
- Normalisation déterministe + fuzzy matching RapidFuzz (seuil 88, ambiguïté = fallback NER)
- Workflow validation humaine : Qwen3 à confidence < 0.7 → `status='pending'`, n'est utilisé que par l'admin une fois validé
- Cross-check Presidio regex même sur colonne au header connu (par excès de prudence)
- Admin UI : sections "Mappings actifs" et "À valider" avec bulk approve
- 30 tests unitaires + e2e passants

Fichiers principaux : `gateway/column_labels.py`, `gateway/column_classifier.py`, `gateway/anonymizer.py`, `gateway/admin_router.py`, `frontend/admin.html`, `scripts/init_db.sql`.

### Fail-safe NER sur tous les chemins (juin 2026)

Si GLiNER/Presidio/Redis plante ou timeout, **bloquer la requête** avec HTTP 503 au lieu de la laisser passer en clair. Politique : *rather fail than leak*.

- `POST /v1/messages` (proxy) : déjà protégé depuis mai 2026 (`sidecar/proxy.py:72-85`)
- `POST /anonymize` (API directe, hooks legacy, MCP query_db) : try/except ajouté dans `sidecar/main.py`, retourne 503 structuré
- `POST /deanonymize` : pas de fail-safe nécessaire (pas d'appel NER, juste string replace)
- Test e2e ajouté : `test_anonymize_fail_safe_on_ner_crash` dans `sidecar/tests/test_sidecar_e2e.py`

### Audit log des anonymisations

Table Postgres `audit_log` : timestamp, user_id (hash), text_hash, entity_counts par label, latency_ms, source du label (static / fuzzy / qwen3 / NER). Aucun texte en clair, aucun PII.

- Sidecar push via `audit_client.py` (fire-and-forget, `sidecar/anonymizer.py:63-79`)
- Gateway reçoit via `audit_router.py` (`POST /api/audit`, rate-limited 120/min)
- Table `audit_log` dans `scripts/init_db.sql` avec index sur timestamp et user_id_hash

### Redis maxmemory + LRU

- Sidecar : `256mb` + `allkeys-lru` dans `sidecar/docker-compose.yml:18-19`
- Protège contre la croissance mémoire du cache texte→texte et header lookup

### Buffer SSE avec regex stricte

- `proxy_deanonymizer.py:43` — regex `\[(?:[A-Z][A-Z_]*(?:_\d*)?)?$` pour détecter les prefixes de placeholder
- Ne bufferise plus indéfiniment sur du markdown `[link](url)` ou des listes `[1,2,3]`

### Skip anonymisation du system prompt (juin 2026)

Le champ `system` du payload Anthropic Messages n'est PAS anonymisé. Gain perf : -1 à -2s par tour.

- `sidecar/proxy_anonymizer.py:130-136` — appel à `_anon_system()` commenté
- Risque documenté : si CLAUDE.md ou un system prompt custom contient du PII, il fuitera
- Test ajouté : `test_system_prompt_is_skipped` dans `sidecar/tests/test_proxy_anonymizer.py`
- Voir `docs/SECURITY.md` pour la justification complète

### Documentation sécurité (juin 2026)

`docs/SECURITY.md` — modèle de cloisonnement zero-trust complet :

- Architecture et flux de données (ce qui transite vs reste local)
- Isolation par user_id (mappings PII isolés, KB partagée)
- Principes : fail-safe, faux positif > faux négatif, éphémère par défaut
- Risques résiduels documentés (system prompt skip, HTTP LAN, classify_column side-channel)
- Politique de migration mode A → mode B (isolation par tenant)
- Conformité RGPD/nLPD (droits des personnes, registre, DPIA)
- Audit et traçabilité (audit log, logs applicatifs sans PII)

### Scan multi-DB (juin 2026)

Scan batch depuis l'interface admin de N sources DB (PostgreSQL, MySQL, SQL Server, SQLite) avec classification via qwen3-pii → entrées `column_labels` en `status='pending'` pour validation.

- Tables `dwh_sources` (CRUD + credentials chiffrés Fernet) + `scan_jobs` (historique, progression)
- `gateway/dwh_sources.py` (CRUD + chiffrement Fernet), `gateway/db_connectors.py` (SQLAlchemy multi-moteur, tables+vues), `gateway/scanner.py` (scan background, skip incrémental sur colonnes actives, fail-safe par table), `gateway/value_metadata.py` (réduction valeurs brutes → métadonnées), `gateway/dwh_router.py` (endpoints `/admin/dwh_sources/*`)
- Frontend : section "Sources de données" dans `frontend/admin.html` — config serveur, test connexion, lancement scan, barre de progression
- Classification via qwen3-pii → `column_labels` en `pending` → revue/bulk-approve dans le workflow admin existant
- Assouplissement zero-trust documenté dans `docs/SECURITY.md` (section "Scan multi-DB") ; credentials chiffrés via `DWH_ENC_KEY`

### HTTPS sur le gateway avec mkcert (juin 2026)

Reverse proxy TLS devant le gateway via certificats mkcert.

- `scripts/setup_https.sh` — génère les certs pour l'IP du serveur LAN
- `frontend/nginx.conf` — server block HTTPS (listen 443) avec proxy vers gateway:8000
- `docker-compose.yml` — expose le port 3443, monte les certs
- `sidecar/docker-compose.yml` — monte la CA mkcert, set `SSL_CERT_FILE` pour httpx
- Documentation dans `docs/SECURITY.md` (section "Tokens OAuth chiffrés sur le LAN")

**Usage** :
```bash
./scripts/setup_https.sh 192.168.1.100  # IP du serveur LAN
docker compose down && docker compose up -d
# Côté sidecar : GATEWAY_URL=https://192.168.1.100:3443
```

---

## Sécurité / Compliance

---

## Qualité de détection

### Fine-tuner GLiNER sur données métier assurance

Entraîner un modèle GLiNER spécialisé sur 200-500 exemples annotés (FR/EN/DE) couvrant assurance + assistance + carte grise.

**Pourquoi** : c'est le levier qui transforme vraiment la qualité. Le modèle générique `urchade/gliner_multi_pii-v1` rate les noms rares, les numéros internes, les références métier. Gain attendu : rappel 85% → 98%, F1 +10-15 pts.

**Comment** : annoter via Doccano ou similaire, fine-tuning sur GPU (30 min), évaluation via une suite de tests holdout. Pipeline de réentraînement périodique pour suivre la dérive.

### Résolution de coréférence avant tokenisation

Détecter que "Miguel", "M. Tavares", "Tavares" = même personne → un seul token.

**Pourquoi** : sans coref, le LLM reçoit `[PERSONNE_1] a dit que [PERSONNE_2] avait téléphoné à [PERSONNE_3]` alors que les trois sont la même personne. Raisonnement dégradé.

**Comment** : spaCy coref (FR faible) ou un LLM coref léger en passe pré-tokenisation. Attention aux faux positifs (deux Daniel ≠ même personne).

### Classifier contextuel pour entités ambiguës

Pour les matches NER à score moyen (0.4-0.7), lancer un LLM léger sur le contexte de l'entité pour confirmer ("Lausanne" = ville ou club ?).

**Pourquoi** : améliore la précision sans pénaliser le rappel. Élimine les faux positifs sur entités polysémiques.

**Comment** : seuil paramétrable, appel LLM uniquement sur les cas doutables. À noter : overlap partiel avec le workflow Qwen3 existant — à réviser quand on s'y attaque.

---

## Performance / UX

### Rate limiting sur /v1/messages

Ajouter rate limiting sur le endpoint proxy `sidecar/proxy.py`.

**Pourquoi** : protection abus en cas de déploiement multi-user.

**Comment** : SlowAPI est déjà installé côté gateway. Côté sidecar (loopback), le besoin est moindre — à évaluer si pertinent.

---

## Évolutions futures

---

## Comment contribuer à la roadmap

Cette roadmap est volontairement non-priorisée — les priorités dépendent du contexte (mise en prod imminente vs R&D, audit FINMA vs perf en démo, etc.). Avant de commencer un item :

1. Vérifier qu'il est toujours pertinent (les besoins évoluent)
2. Préciser le scope avec un mini-spec si l'item est gros
3. Suivre le pattern : `superpowers:writing-plans` → `superpowers:subagent-driven-development`
4. Ajouter tests unitaires + e2e en même temps que le code (pas après)
5. Marquer l'item comme fait dans la section "Fait" en haut de ce fichier
