# Modèle de sécurité — Anonymization Gateway

Contexte : **domaine assurance** — assurances + assistance, multi-langue FR/EN/DE.  
Conformité : nLPD suisse, RGPD, FINMA.

---

## Architecture zero-trust

```
  [Poste utilisateur]                        [Serveur LAN]
  ┌──────────────────────────┐              ┌──────────────────────────┐
  │                          │              │                          │
  │  Claude Code             │              │  Gateway (:8001)         │
  │    ↓ ANTHROPIC_BASE_URL  │              │   - KB métadonnées       │
  │                          │              │   - Classification col.  │
  │  Sidecar (:8787)         │──── LAN ────→│   - Audit log (hashes)   │
  │   - NER (GLiNER+Presidio)│              │   - Admin API            │
  │   - Redis (mappings PII) │              │   - OAuth tokens         │
  │   - Proxy SSE            │              │        ↕                 │
  │                          │              │  PostgreSQL (anondb)     │
  │  Frontend (:3000)        │              │  Ollama (qwen3-pii)      │
  │                          │              │                          │
  └──────────────────────────┘              └──────────────────────────┘
```

**Principe fondamental** : aucun PII ne quitte le poste utilisateur sous forme lisible. Le gateway central ne voit que des métadonnées (noms de colonnes, counts, hashes SHA-256).

---

## Flux de données

### Ce qui transite par le réseau (LAN)

| Flux | Direction | Contenu | PII ? |
|------|-----------|---------|-------|
| KB snapshot | Gateway → Sidecar | Noms de colonnes, labels PII, patterns regex, toggles | **Non** (métadonnées de schéma) |
| classify_column | Sidecar → Gateway | Metadata de 5 valeurs (longueur, charset, hash SHA-256 prefix) | **Non** (pas de valeurs brutes) |
| audit_log | Sidecar → Gateway | user_id_hash, text_hash, entity_counts, latency_ms | **Non** (hashes + agrégats) |
| OAuth token | Sidecar → Gateway | client_id + client_secret → JWT | **Non** (credentials M2M) |

### Ce qui NE transite JAMAIS par le réseau

| Donnée | Où elle reste |
|--------|---------------|
| Texte brut des prompts utilisateur | Sidecar local (NER) puis Anthropic (anonymisé) |
| Mapping token ↔ valeur PII | Redis local du sidecar (127.0.0.1:6379) |
| Réponses Anthropic dé-anonymisées | Sidecar local (proxy SSE) |

---

## Isolation par utilisateur

### Mode actuel : isolation par `user_id` (hash auth)

Chaque utilisateur a une clé API personnelle ou un JWT OAuth. Le `user_id` est dérivé du hash SHA-256 de cette clé.

**Données isolées** (par user_id) :
- Redis `mapping:{user_id}` → `{token: valeur_pii}` — TTL 24h
- Redis `reverse:{user_id}` → `{valeur_pii: token}` — TTL 24h
- Redis `audit:*` — événements d'anonymisation

**Données partagées** (globales) :
- `column_labels` — KB de headers → labels PII (métadonnées de schéma, pas de PII)
- `custom_patterns` — regex Presidio personnalisées
- `ner_config` — toggles et seuils NER

### Garantie

Deux utilisateurs ne partagent **aucun mapping PII**. Si l'utilisateur A anonymise "David Neri" → `[PERSONNE_1]` et l'utilisateur B anonymise "Marie Curie" → `[PERSONNE_1]`, les mappings sont totalement indépendants. Seul le label `PERSONNE` est partagé (via la KB).

---

## Principes de sécurité

### 1. Rather fail than leak

Si le NER (GLiNER, Presidio, Redis) plante ou timeout, la requête est **bloquée** avec HTTP 503. Elle ne passe jamais en clair.

**Implémentation** :
- `POST /v1/messages` (proxy) : try/except dans `sidecar/proxy.py:72-85`
- `POST /anonymize` (API directe) : try/except dans `sidecar/main.py:188-203`

**Test** : `test_anonymize_fail_safe_on_ner_crash` dans `sidecar/tests/test_sidecar_e2e.py`

### 2. Faux positif > faux négatif

En cas de doute sur une entité, on tokenise. Mieux vaut anonymiser un faux positif (ex: "Lausanne" le club de hockey) que laisser fuiter un vrai PII.

**Implémentation** :
- Seuil GLiNER par défaut : 0.5 (détecte large)
- Cross-check Presidio même sur colonnes au header connu
- Workflow Qwen3 : confidence < 0.7 → `status='pending'`, nécessite validation admin

### 3. Pas de PII côté serveur

Le gateway central ne stocke **jamais** de PII. Même les logs d'audit ne contiennent que des hashes SHA-256 (user_id_hash, text_hash).

**Vérification** : inspecter `gateway/audit_router.py` et `scripts/init_db.sql` — la table `audit_log` n'a pas de colonne texte.

### 4. Éphémère par défaut

Les mappings PII dans Redis local ont un TTL de 24h. Au redémarrage du sidecar, Redis est vide (pas de persistance AOF/RDB sur les mappings).

**Implémentation** : `sidecar/docker-compose.yml` — Redis avec `--appendonly yes` mais le volume `redis_data` n'est pas monté en persistant (rebuild = reset).

**Rationale** : si un poste utilisateur est compromis, l'attaquant ne peut pas déchiffrer l'historique des conversations passées.

---

## Risques résiduels documentés

### 1. System prompt non anonymisé

**Décision** : le champ `system` du payload Anthropic Messages n'est PAS anonymisé.

**Gain** : -1 à -2s par tour de conversation (pas de NER sur le system prompt).

**Risque** : si CLAUDE.md ou un system prompt custom contient du PII (ex: "Mon email est david@example.com"), il fuitera vers Anthropic.

**Justification** : le system prompt vient typiquement du framework Claude Code (instructions internes, contexte projet). Les PII utilisateur sont dans les messages, pas dans le system. Le gain de perf justifie le risque résiduel.

**Implémentation** : `sidecar/proxy_anonymizer.py:130-136` — appel à `_anon_system()` commenté.

**Test** : `test_system_prompt_is_skipped` dans `sidecar/tests/test_proxy_anonymizer.py`.

**Mitigation future** : si un cas d'usage légitime nécessite du PII dans le system, réactiver l'anonymisation ou ajouter un scanner regex léger.

### 2. Tokens OAuth chiffrés sur le LAN (HTTPS)

**Décision** : les appels sidecar → gateway utilisent HTTPS avec des certificats mkcert.

**Implémentation** :
- `scripts/setup_https.sh` génère les certs pour l'IP du serveur LAN
- nginx (service `frontend`) fait reverse proxy TLS sur le port 3443
- Le sidecar monte la CA mkcert (`certs/ca.pem`) et set `SSL_CERT_FILE` pour que httpx fasse confiance

**Installation sur les postes clients** :
1. Copier `certs/ca.pem` sur le poste
2. Installer avec `mkcert -install` (ou manuellement dans le trousseau de certificats)
3. Configurer `GATEWAY_URL=https://<IP_SERVEUR>:3443` dans le sidecar

**Risque résiduel** : si la CA mkcert est compromise, un attaquant peut générer des certs valides pour n'importe quel domaine. La CA doit être protégée (accès restreint, pas de commit dans le repo).

**Fallback HTTP** : si HTTPS n'est pas configuré, le sidecar peut toujours utiliser `GATEWAY_URL=http://<IP_SERVEUR>:8001` (port 8001 = gateway direct, sans TLS). À éviter en production.

### 3. Scan multi-DB (gateway-side)

**Décision** : lors d'un scan, le gateway se connecte directement aux bases configurées et lit des valeurs réelles (échantillon par colonne) pour alimenter la classification Qwen3.

**Assouplissement assumé du zero-trust** : le serveur central touche de la PII de production pendant la durée du scan. C'est un écart délibéré au principe "pas de PII côté serveur", circonscrit à ce workflow admin.

**Mitigations** :
- Compte DB **lecture seule** recommandé (le gateway n'a aucune raison d'écrire).
- Les valeurs lues sont **immédiatement réduites en métadonnées** (longueur, charset, regex hint, hash SHA-256) dans `gateway/value_metadata.py` ; la valeur brute n'est pas conservée.
- **Aucun log** des valeurs d'échantillon (seuls les méta-indicateurs sont loggués).
- **3 valeurs d'exemple** par colonne uniquement sont retenues (stockées dans `scan_jobs` pour la revue admin, même workflow que la validation Qwen3 → `pending`).
- **Credentials chiffrés at-rest** avec Fernet (`DWH_ENC_KEY` hors DB) dans la table `dwh_sources` ; la clé ne transite pas par la DB.
- Endpoints `/admin/dwh_sources/*` **admin-only** (header `X-Admin-Secret`).

**Brèche bornée** : la PII n'existe que **transitoirement en RAM** du gateway pendant le scan. Elle n'est jamais écrite en clair sur disque ni dans les logs. Une fois le scan terminé, seules les métadonnées et les 3 exemples d'échantillon subsistent en DB.

### 4. classify_column : side-channel assumé

**Décision** : le sidecar envoie des métadonnées de 5 valeurs d'exemple au gateway pour classification Qwen3.

**Données envoyées** : longueur, charset, has_spaces, has_punctuation, regex_hint, sample_hash (SHA-256 prefix 8 chars).

**Risque** : un attaquant qui intercepte ces métadonnées pourrait inférer partiellement le type de donnée (ex: "13 chiffres, regex_hint=avs" → numéro AVS). Mais pas la valeur brute.

**Justification** : c'est le "side-channel PII" assumé du modèle zero-trust. Les métadonnées seules ne permettent pas de reconstruire le PII. Le gain (classification contextuelle) justifie le risque.

**Mitigation** : rate limit 30/min sur `/api/classify_column`, pas de log côté gateway des valeurs reçues.

### 5. Blocs image/document refusés par le proxy

**Décision** : les blocs `image` et `document` du payload Anthropic Messages sont REFUSÉS (HTTP 400) au lieu d'être forwardés verbatim.

**Risque évité** : un screenshot ou un PDF envoyé via Claude Code peut contenir du PII en clair (base64) — sans OCR local, le sidecar ne peut pas l'anonymiser.

**Conséquence UX** : coller une image ou lire un PDF dans Claude Code via le proxy échoue avec un message explicite. C'est le tradeoff fail-closed assumé ("Rather fail than leak").

**Implémentation** : `UnsupportedBlockError` dans `sidecar/proxy_anonymizer.py`, mappée en 400 dans `sidecar/proxy.py`.

**Test** : `test_proxy_rejects_image_block_with_400` dans `sidecar/tests/test_proxy_e2e.py`.

**Mitigation future** : OCR local (tesseract) sur les images avant anonymisation, ou pass-through opt-in par config explicite.

---

## Politique de migration : mode A → mode B

### Mode A (actuel) : isolation par user_id

Chaque utilisateur a ses propres mappings PII. Aucun partage.

### Mode B (futur) : isolation par tenant (équipe/projet)

Si un cas d'usage légitime émerge (ex: équipe de 5 analystes sur le même dossier), on pourrait passer à un modèle où les mappings sont partagés au niveau d'un `tenant_id` (groupe d'utilisateurs).

**Conditions pour migrer** :
1. Validation juridique (DPO) — le partage de mappings PII entre utilisateurs est-il conforme nLPD ?
2. Audit de sécurité — un mapping partagé est un honeypot plus attractif (plus de PII dans un seul Redis)
3. Implémentation technique — ajouter un champ `tenant_id` dans les clés Redis, modifier l'auth pour supporter les groupes

**Garantie préservée** : même en mode B, les mappings restent isolés entre tenants. Seul le partage intra-tenant est autorisé.

---

## Conformité RGPD / nLPD

### Droits des personnes

| Droit | Comment c'est garanti |
|-------|----------------------|
| Information | L'utilisateur voit les PII détectées dans le dashboard sidecar (`:8787/events`) |
| Accès | `GET /mapping` retourne tous les tokens ↔ valeurs PII de la session |
| Effacement | `DELETE /mapping` clear le Redis local (24h TTL auto + manuel) |
| Portabilité | Les mappings sont exportables via `GET /mapping` (JSON) |

### Registre des traitements

Le traitement "anonymisation PII pour LLM" doit être documenté dans le registre RGPD de l'organisation :
- Responsable : [à compléter]
- Finalité : permettre l'usage de LLM (Claude) sur des données client sans fuite PII
- Base légale : intérêt légitime (amélioration service interne)
- Destinaires : service interne uniquement (pas de transfert hors entreprise)
- Durée de conservation : mappings éphémères (24h max), audit log 90j

### Analyse d'impact (DPIA)

Une DPIA est recommandée avant la mise en production car :
- Traitement de données personnelles à grande échelle (clients assurance)
- Transfert de données vers un tiers (Anthropic, même anonymisé)
- Données sensibles possibles (numéros AVS, IBAN, plaques d'immatriculation)

---

## Audit et traçabilité

### Audit log

Chaque anonymisation génère un événement envoyé au gateway :
- `user_id_hash` : SHA-256 de la clé API utilisateur
- `text_hash` : SHA-256 du texte original (pas le texte brut)
- `entity_counts` : nombre d'entités par label (PERSONNE: 3, EMAIL: 1, etc.)
- `sources` : origine de la détection (gliner: 2, presidio: 1, kb_exact: 1)
- `latency_ms` : temps de traitement NER
- `format` : type de donnée (freetext, csv, json, table)

**Rétention** : 90 jours minimum (conformité RGPD). Configurable via politique de rétention Postgres.

**Accès** : admin uniquement via `GET /admin/audit` (à implémenter).

### Logs applicatifs

Les logs sidecar/gateway sont structurés (JSON) et envoyés sur stdout. Ils ne contiennent **jamais** de PII :
- `sidecar.proxy.anonymized` : `out_bytes`, `elapsed_ms`
- `sidecar.ner.detect` : `text_len`, `threshold`, `gliner` (spans), `presidio` (spans), `total`
- `gateway.audit.insert` : `user_id_hash`, `latency_ms`

**Exclusion** : les valeurs PII détectées ne sont **jamais** loguées. Seuls les spans (start, end) et les labels le sont.

---

## Références

- `ROADMAP.md` — liste des items sécurité faits et à faire
- `docs/PLAN_PROXY.md` — plan d'implémentation du proxy zero-trust
- `sidecar/README.md` — installation et configuration du sidecar local
- `README.md` — vue d'ensemble du projet
