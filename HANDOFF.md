# HANDOFF — LLM Anon Gateway

## Livré (session courante — audit "trous dans la raquette" + correctifs)

### Audit complet (4 axes : proxy PII, NER, sécurité gateway, tests/ops)
Constats critiques tous corrigés dans cette session, sauf reliquat listé en "À faire".

### Garde-fou ReDoS sur les regex (gateway)
- `gateway/regex_guard.py` — `validate_regex()` : syntaxe, longueur ≤ 200,
  quantificateurs non bornés imbriqués (heuristique via `re._parser`)
- Branché dans `config_store.upsert_pattern_pending()` (patterns Qwen → rejet silencieux + log `pattern.rejected`)
- Branché dans `admin_router.create_pattern` (→ 400 explicite)
- Défense en profondeur sidecar : `ner.py::_build_presidio` ignore les patterns KB non compilables
- Tests : `gateway/tests/test_regex_guard.py` (12), `test_admin.py`, `sidecar/tests/test_ner_patterns.py`

### Blocs image/document refusés par le proxy (fail-closed)
- Avant : pass-through verbatim → PII binaire (screenshots, PDF) fuitait vers Anthropic
- Maintenant : `UnsupportedBlockError` → HTTP 400 explicite, rien ne part
- Les types inconnus texte (thinking…) restent pass-through
- Documenté : `docs/SECURITY.md` §Risques résiduels 5 (avec mitigation future = OCR local)

### Bugs de prod corrigés
- `classify_router.py:77` déballait 2 valeurs alors que `classify()` renvoie un 3-tuple
  depuis le commit Qwen → **`/api/classify_column` répondait 500 systématiquement**
- `column_classifier.py:123` : early-return 2-tuple résiduel
- `admin_router.py:128` : `extra={"name": ...}` (clé réservée LogRecord) → crash de
  `POST /admin/patterns` dès que le logging est configuré

### XSS admin UI
- `frontend/admin.html` : `p.name`, `p.regex`, `p.entity_label` passés par `escapeHtml()`
  (les patterns Qwen arrivent de données externes)

### Tests gateway débloqués (60 → tous verts)
- Installé en user : `asyncpg`, `slowapi`, `python-multipart` (pip --break-system-packages)
- `gateway/tests/conftest.py` : stubs fallback asyncpg/slowapi si non installés
- Tests obsolètes `test_column_classifier.py` mis au contrat 3-tuple
- **État : 174/174 verts** (`python3 -m pytest sidecar/tests/ gateway/tests/ -q`)

### Ops
- `activer-sidecar.sh` : Redis lancé avec `--bind 127.0.0.1 --maxmemory 256mb
  --maxmemory-policy allkeys-lru` (aligné sur le docker-compose)

---

## À faire

### Priorité haute
- [ ] Valider le proxy en conditions réelles (sidecar lancé, Claude Code pointé dessus)
- [ ] Tester la détection automatique de regex avec une vraie DB assurance

### Reliquat audit (priorité normale)
- [ ] `sidecar/cache.py` : normaliser valeur (strip) avant lookup reverse — "John" vs " John"
      donnent 2 placeholders ; `clear_mapping()` ne supprime pas les clés `counter:*`
- [ ] `gateway/auth.py:60` + `oauth_router.py:142` : `hmac.compare_digest()` au lieu de `!=`
- [ ] Rate-limit sur `/oauth/token` et `/oauth/sidecar_token` (brute force client_secret)
- [ ] `DWH_ENC_KEY` absent du docker-compose → déchiffrement credentials DWH plantera
- [ ] `docker-compose.yml` : `restart: unless-stopped` + healthcheck sur le service gateway
- [ ] `sidecar/ner.py` TODO(ner-1char-fp) : filtrer les entités GLiNER 1 caractère (alias SQL)
- [ ] Couverture détection : cartes de crédit (Luhn), codes postaux CH/FR, pièces d'identité non-AVS
- [ ] Couverture tests : `sidecar/proxy.py`, `anonymizer.py`, `cache.py` sans tests unitaires directs ;
      flux Qwen→regex→Presidio sans test e2e
- [ ] DB de test : générer données synthétiques assurance FR avec faker
- [ ] `desactiver-sidecar.sh` : option `--keep-config` si plusieurs dossiers de test

---

## Architecture rappel

```
Claude Code
    ↓ ANTHROPIC_BASE_URL=http://127.0.0.1:8787
Sidecar (machine user) — NER local, Redis local
    ↓ token OAuth/API key forwardé tel quel
api.anthropic.com
```

```
Gateway (machine serveur) — métadonnées uniquement, aucun PII
├── KB colonnes (column_labels)
├── Patterns custom Presidio (custom_patterns) — garde-fou regex_guard à l'insertion
├── Classifier Qwen (Ollama)
└── PostgreSQL
```

## Commandes utiles

```bash
# Tests (locaux, sans Docker) — 174 tests
python3 -m pytest sidecar/tests/ gateway/tests/ -q

# Démarrer pour test proxy
./activer-sidecar.sh
./preparer-test-proxy.sh
cd ~/proxy-test && claude

# Rollback
./desactiver-sidecar.sh
```
