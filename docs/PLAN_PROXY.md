# Plan — proxy zero-trust transparent Claude Code ↔ Anthropic + chantiers connexes

**Statut** : Phases 0 → 4 ✅ livrées et commitées. Phase 5 (dry-run démo complète) reste à faire avant prod.
**Deadline** : pas de date dure — « pas de démo tant que pas prêt ». Qualité d'abord.
**Effort estimé** : 4-6 jours, séquentiel principalement.

**Trois chantiers intégrés dans ce plan** :
1. Proxy sidecar HTTPS interceptant Claude Code ↔ Anthropic
2. KB column_labels contextuelle (db, schema, table)
3. Migration auth machine-à-machine de bearer keys vers OAuth client_credentials

---

## 1. Objectif en une phrase

Remplacer la pile **hooks + MCP** par un **proxy local unique** qui intercepte tout
le trafic Claude Code ↔ `api.anthropic.com`, anonymise tout texte sortant et
désanonymise tout texte entrant, en streaming. Une seule règle, un seul point
de défaillance, un seul endroit à auditer.

---

## 2. Architecture cible

```
   [Toi]  →  [Claude Code]  ⇄  [Sidecar Proxy 127.0.0.1:8788]  ⇄  [api.anthropic.com]
                                       │
                                       ├── /anonymize  (existant, NER local)
                                       ├── /deanonymize (existant)
                                       └── Redis local (mappings)
```

- Le proxy s'ajoute au **sidecar existant** (nouveau endpoint sur un autre port,
  ex. 8788, pour ne pas casser les usages curl sur 8787).
- Claude Code est configuré pour parler au proxy au lieu d'Anthropic via
  `ANTHROPIC_BASE_URL=http://127.0.0.1:8788`.
- Le proxy expose **la même surface API** qu'Anthropic (au minimum
  `POST /v1/messages` en streaming SSE).
- L'OAuth token de Claude Code est forwardé tel quel — le proxy n'a pas
  besoin de le comprendre, juste de le relayer.

**Tout ce qui n'est plus utilisé après ce pivot** : hooks UserPromptSubmit /
Stop / PreToolUse, MCP `anon-gateway` (optionnel — peut rester pour
l'ergonomie SQL). À supprimer ou archiver une fois le proxy stable.

---

## 3. Découpage en phases (commit après chaque)

### Phase 0 — Go / No-Go (2-4h, time-boxé strict)

C'est la phase qui décide si tout le plan tient. Trois validations
séquentielles, chacune indépendante. Si **une seule** échoue, on s'arrête
et on rediscute (pas de "je trouverai un workaround").

**Étape 0.1 — Inventaire trafic réel (30 min)**

Avant tout code, capturer le trafic d'une session Claude Code normale :

```
sudo tcpdump -i any -w /tmp/cc-baseline.pcap host '*.anthropic.com'
# en parallèle : claude → quelques échanges → exit
```

Identifier **tous** les hosts/endpoints touchés. Si Claude Code parle à
plus que `api.anthropic.com/v1/messages` (telemetry, file upload, OAuth
refresh URL, etc.), chacun doit être identifié maintenant. Le proxy
devra soit les forwarder tels quels, soit on accepte une fuite résiduelle
documentée.

**Étape 0.2 — Routing HTTP nu (30 min)**

Proxy forward-only de 50 lignes en Python qui logue chaque requête et
forwarde verbatim. Démarrer en HTTP sur `127.0.0.1:8788`. Setter
`ANTHROPIC_BASE_URL=http://127.0.0.1:8788` et lancer Claude Code.

- ✅ Si une requête utilisateur normale round-trip → continue
- ❌ Si Claude Code refuse HTTP (exige TLS) → étape mkcert (1/2 jour
  supplémentaire à prévoir)
- ❌ Si Claude Code ignore complètement la variable d'env → plan B
  obligatoire, on arrête

**Étape 0.3 — Refresh OAuth (≥1h d'attente)**

Le piège silencieux : `ANTHROPIC_BASE_URL` peut ne rediriger que
`/v1/messages` et laisser les requêtes OAuth `/oauth/*` aller en direct
à api.anthropic.com. Tout marche pendant ~1h puis casse au refresh
token.

Test : envoyer un prompt, attendre **au moins 60 minutes**, envoyer un
deuxième prompt. Pendant ces 60 min, faire d'autres trucs.

- ✅ Si le 2e prompt passe encore par le proxy → vraiment OK
- ❌ Si erreur 401 ou contournement → le proxy doit aussi intercepter
  les flux OAuth, ce qui est un autre niveau de complexité. STOP, on
  discute avant.

**Go/No-go global** : les 3 étapes doivent passer. Si l'une bloque,
plan B = garder les hooks pour la démo, le proxy passe en v2 post-démo.

### Phase 1 — Skeleton proxy SSE (3-4h)
Implémenter dans `sidecar/proxy.py` :
- Route `POST /v1/messages` en FastAPI
- Forward 1:1 vers api.anthropic.com avec `httpx.AsyncClient(http2=True)`
- Streaming SSE : recevoir les `data: {...}` chunks d'Anthropic et les
  re-streamer côté client tel quel
- Pass-through complet des headers (Authorization, anthropic-version, etc.)
- Aucune modification de contenu encore — vrai mode "tunnel"

**Critère de succès** : Claude Code via le proxy fonctionne identiquement
à Claude Code direct. Latence ajoutée < 50ms.

### Phase 2 — Anonymisation outbound (4-6h)

**La frontière de sécurité réelle est ici.** Lister exhaustivement TOUS
les champs du payload Anthropic Messages API qui peuvent contenir du PII :

| Champ | Source du PII | Critique ? |
|---|---|---|
| `messages[*].content[*].text` (role=user) | Prompt utilisateur | **OUI** |
| `messages[*].content[*].text` (role=assistant) | Re-injecté à chaque tour | **OUI** |
| `messages[*].content[*]` type=`tool_use` `.input` | Args structurés (objet JSON) | **OUI** |
| `messages[*].content[*]` type=`tool_result` `.content` | **Sortie de Bash, Read, MCP, etc.** — c'est ICI que les outputs d'outils reviennent dans le contexte | **OUI — c'est l'angle mort principal** |
| `system` (string ou array) | CLAUDE.md, git status, context auto-injecté | **OUI** |
| `tools[*].description` / `tools[*].input_schema` | Définitions, normalement statiques | Faible |
| `metadata.user_id` | Hash user, pas de PII clair | Non |

Pour chacun, le proxy doit :
1. Extraire le texte
2. Appeler `/anonymize` (avec hash-cache pour ne pas refaire le NER sur
   les messages déjà vus dans cette session)
3. Réinjecter dans le payload reconstruit

**Note `tool_result`** : c'est le champ qui contient la sortie d'un `cat
clients.csv`, d'un `psql -c "SELECT..."`, etc. Sans son anonymisation,
le proxy ne sert à rien — on a juste déplacé les hooks. Tests dédiés
obligatoires pour ce cas.

**Cache** : les `messages` antérieurs au tour courant ont déjà été
anonymisés au tour précédent. Hash-cache (sha256 du texte original →
anonymisé) évite le recalcul. Mapping consistent garanti par Redis local.

**Critère de succès** : 
- tcpdump sur `api.anthropic.com:443` : zéro nom client sortant
- Test spécifique : prompt "lance `cat demo/demo.sqlite | head -50`"
  → tcpdump doit montrer le `tool_result` contenant des `[PERSONNE_X]`,
  pas des vrais noms.

### Phase 3 — Désanonymisation inbound streaming (4-6h)
Le plus dur. Les chunks SSE arrivent par morceaux et un placeholder peut être
**coupé en plein milieu** (ex: chunk 1 finit par `[PERSON`, chunk 2 commence
par `NE_3] habite...`).

Stratégie :
- Buffer rolling : garder en attente uniquement la **queue** qui matche
  un préfixe de placeholder potentiel : regex `\[[A-Z_]+(_\d*)?$` sur
  la fin du buffer. Tout ce qui est avant cette queue se flush.
- ⚠ Ne PAS utiliser une heuristique naïve "buffer si présence d'un `[`
  sans `]`" → casse sur les markdown links `[link](url)`, sur les
  exemples JSON, sur les bouts de code. Le regex strict ci-dessus évite
  ce piège.
- Une fois la queue complétée par le chunk suivant, ou si elle ne matche
  plus un préfixe valide, on remplace les placeholders connus du mapping
  Redis et on flush.
- Tests dédiés obligatoires avec : markdown links, JSON nested, blocs de
  code Python contenant des listes Python `[1,2,3]`.

Édge case : Claude répond en tool_use avec arguments structurés JSON. Les
placeholders peuvent être dans des valeurs string de l'objet `input` du
tool_use. Décoder JSON, désanonymiser les strings, ré-encoder.

**Critère de succès** : la démo bout-en-bout marche, Claude Code affiche les
vrais noms à l'utilisateur, le streaming reste fluide (pas de hoquet visible).

### Phase 4 — Retrofit démo + nettoyage (livrée)

État final :
- `.claude/settings.local.json` : ne contient plus que le MCP `anon-gateway`.
  Les hooks `UserPromptSubmit` + `Stop` ont été retirés (le proxy fait le
  job en un seul point, plus cohérent et sans risque de double-anonymisation).
- `archive/hooks_legacy/` : reçoit les 2 fichiers hooks (anonymize +
  deanonymize) qui sortent du chemin actif.
- `archive/hooks_rejected/` : déjà contient les 5 hooks Bash/Read/Grep/
  WebFetch écrits puis abandonnés (cf commit 4d4dfb9f).
- MCP `anon-gateway` conservé : utile pour `query_db` qui simplifie
  l'analyse de DB. Plus une frontière de sécurité — c'est le proxy qui
  garantit zero-trust — mais une ergonomie pour Claude.
- `demo/zero_trust/walkthrough.html` : setup mis à jour, étape 4 est
  désormais `ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude`. Les scènes
  expliquent que c'est le proxy qui désanonymise pour l'affichage local.
- Section fail-safe : décrit le 503 du proxy au lieu de l'erreur hook.

### Phase 4.5 — KB contextuelle (db, table) — 1 jour

Objectif : permettre que `numero` dans `contrats` ait un label différent de
`numero` dans `marches`, avec fallback prudent quand le contexte manque.

**Migration SQL** (`scripts/init_db.sql` + script de migration séparé) :
```sql
ALTER TABLE column_labels ADD COLUMN db_name VARCHAR(64);
ALTER TABLE column_labels ADD COLUMN table_name VARCHAR(128);
ALTER TABLE column_labels DROP CONSTRAINT column_labels_header_norm_key;
ALTER TABLE column_labels
  ADD CONSTRAINT column_labels_unique
  UNIQUE NULLS NOT DISTINCT (header_norm, db_name, table_name);
```

Les 147 entrées seedées passent en `db_name=NULL, table_name=NULL` → restent
le fallback générique. Pas de perte de données.

**Lookup hiérarchique** dans `sidecar/column_labels.py` :
```
1. exact match (header, db, table)
2. (header, NULL, table)
3. (header, db, NULL)
4. (header, NULL, NULL)
```

**Doctrine d'ambiguïté** : en cas de plusieurs matchs à un même niveau, on
prend le **label le plus sensible** (PII > NONE). « Rather false positive
than leak », fidèle à la doctrine projet.

**Extraction du contexte au runtime** :
- MCP `query_db` : parser le `FROM table` du SQL avec `sqlparse`. Enrichir
  l'appel `/anonymize` avec `{db_name, table_name}`.
- Proxy : par défaut pas de contexte (texte libre). Une heuristique
  optionnelle peut inférer table_name si le `tool_input.command` contient
  un `FROM <table>` ou un nom de fichier qui matche.
- CSV ad-hoc collé par l'utilisateur : pas de contexte, fallback.

**Admin UI** : ajouter colonnes db/table dans la liste, et un filtre par
table. Permettre de gérer les conflits explicitement (vue groupée par
header).

### Phase 4.6 — OAuth client_credentials gateway — 0.5-1 jour

Remplacer l'auth bearer du gateway par OAuth client_credentials, qui est le
standard machine-à-machine en entreprise.

**Choix IdP** :
- Démo + tests : Keycloak local en docker-compose (image officielle, setup
  ~15 min)
- Prod : Azure AD ou IdP interne — switch via env var, pas de
  code à toucher

**Côté gateway** (`gateway/main.py`) :
- Remplacer le middleware bearer-hash par une validation JWT contre l'IdP
- Lib : `python-jose[cryptography]` ou `authlib`
- Cache JWKs avec TTL pour éviter de retaper l'IdP à chaque requête
- Scope check : refuser si `scope` du token ne contient pas
  `gateway:kb-read` ou équivalent

**Côté sidecar** (`sidecar/kb_client.py`) :
- Au démarrage : appel `POST /token` à l'IdP avec `client_id` + `client_secret`
  → récupère un access_token (typiquement 1h TTL)
- Cache du token + refresh proactif à 80% du TTL
- Ajouter `Authorization: Bearer <token>` aux appels gateway

**Suppression de la table `api_keys`** : migration qui drop la table après
confirmation que tous les sidecars passent en OAuth. Le marker dans
`/admin/keys` de l'UI disparait, remplacé par un lien vers l'IdP.

**Test** : kill l'IdP en plein run → le sidecar doit échouer proprement
(message clair, pas de fallback silencieux en clair).

### Phase 5 — Tests + démo (2-3h)
Tests à écrire avant la démo :
- E2E : prompt avec PII typique → tcpdump confirme zéro fuite sortante
- Streaming : conversation longue (50 tours) → latence stable, mappings
  consistants
- Tool use : Claude appelle un tool, les arguments string sont désanonymisés
  à l'arrivée
- Échec sidecar : que se passe-t-il si le proxy se coupe en plein streaming ?
  → policy fail-safe identique aux hooks (couper la conv, message clair)

Rejouer le scénario complet de démo (4 scènes) sur un build fresh.

---

## 4. Décisions techniques à figer avant phase 1

| Question | Reco par défaut |
|---|---|
| Framework HTTP | FastAPI (déjà dans le sidecar) |
| Client HTTP sortant | `httpx` avec `http2=True` (Anthropic supporte HTTP/2) |
| Port proxy | 8788 (8787 reste l'API NER curl) |
| Variable d'env Claude Code | `ANTHROPIC_BASE_URL=http://127.0.0.1:8788` |
| TLS local | éviter si HTTP nu marche. Sinon mkcert + ajout trust store WSL. |
| Persistence mappings | Redis local (déjà en place) |
| Auth proxy | aucune (loopback), pareil que le sidecar actuel |
| Logging | JSON structuré sur stdout, **jamais de texte de message en clair** |

---

## 5. Risques et plan B

| Risque | Probabilité | Mitigation |
|---|---|---|
| `ANTHROPIC_BASE_URL` ne redirige PAS les flux OAuth (`/oauth/*`), refresh casse à 1h | **Élevée** — c'est le risque silencieux le plus probable | Phase 0.3 explicite (attendre 1h+). Si avéré, plan B obligatoire. |
| Claude Code ignore `ANTHROPIC_BASE_URL` complètement | Faible | Phase 0.2 le détecte en 30 min. |
| TLS pinning sur OAuth-bearer → HTTP nu refusé | Moyenne | Phase 0.2. mkcert ajoute 0.5j. |
| Streaming SSE plus complexe que prévu (HTTP/2, multiplexage) | Moyenne | Budget 6h sur phase 3. Si > 1 jour, on stoppe. |
| Buffer rolling casse sur markdown/JSON | Moyenne | Regex strict `\[[A-Z_]+(_\d*)?$` + tests adversariaux. |
| Format `tool_result` complexe (multimédia, refs) | Moyenne | Parser défensif, pass-through si format inconnu (avec warn log). |
| Endpoints autres que /v1/messages exposent du PII (telemetry, upload) | Moyenne | Phase 0.1 inventaire complet. |
| Conv très longue → recoût NER énorme | Faible | Cache hash → anonymisé en mémoire. |

**Pas de critère d'abandon temporel** : la deadline n'est plus contrainte
dure (« pas de démo tant que pas prêt »). Si phase 0 révèle un obstacle
majeur (OAuth refresh, TLS pinning), on prend le temps de le résoudre
proprement plutôt que de revenir au plan B.

---

## 6. Verification finale (avant démo)

Liste à cocher :

1. [ ] `tcpdump -i any host api.anthropic.com -A` pendant une conv complète
   → aucun nom, email, IBAN, téléphone des clients dans la sortie réseau.
2. [ ] `docker logs anon-sidecar` : les logs ne contiennent jamais de texte
   de message en clair (juste counts, hashes, latences).
3. [ ] Démo 4 scènes du walkthrough rejouée bout-en-bout sans intervention
   manuelle.
4. [ ] Fail-safe : kill le sidecar pendant une conv → Claude Code affiche
   une erreur claire et NE continue PAS en clair.
5. [ ] Latence perçue acceptable (< 500ms ajoutés sur un prompt typique).
6. [ ] Test "adversarial light" : Claude lance `cat clients.csv` en Bash →
   le contenu est anonymisé à la remontée vers Anthropic (vérifié tcpdump).

---

## 7. Fichiers à créer / modifier

À créer :
- `sidecar/proxy.py` — l'implémentation du proxy SSE
- `sidecar/anonymize_payload.py` — helpers de parsing/reconstruction des
  payloads Anthropic Messages API
- `sidecar/tests/test_proxy_streaming.py` — tests sur le chunking

À modifier :
- `sidecar/main.py` — monter le router proxy
- `sidecar/docker-compose.yml` — exposer 8788 en plus de 8787
- `sidecar/install.sh` — exporter `ANTHROPIC_BASE_URL` quelque part
  (instructions pour `~/.bashrc` ou `~/.zshrc`)
- `demo/zero_trust/walkthrough.html` — réécrire les étapes setup et scènes
  pour refléter "tout passe par le proxy"
- `.claude/settings.local.json` — retirer les hooks (et le MCP si on tranche
  pour B en phase 4)
- `ROADMAP.md` — déplacer le proxy de "post-démo" vers "fait" une fois shipped

À supprimer après stabilisation :
- `scripts/hooks/hook_*.py` (les 5-7 hooks écrits aujourd'hui)
- `scripts/hooks/bash_anonymize_wrapper.py`
