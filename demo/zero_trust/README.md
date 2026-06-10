# Démo zero-trust

Matériel de démo pour l'architecture v2 : NER + mappings sur le poste utilisateur,
le serveur ne traite plus aucun PII.

## Préparation

Sur la machine de démo, il faut deux stacks tournants :

```bash
# 1. Gateway metadata-only (Postgres + admin + classify + kb_snapshot)
cd ~/Documents/llm-anon-gateway
docker compose up -d
docker compose ps   # gateway + postgres + frontend, healthy en ~30s

# 2. Sidecar zero-trust (NER + Redis local + KB sync depuis le gateway)
bash sidecar/install.sh --gateway http://localhost:8001
# (5-10 min la première fois — build de l'image avec modèles bakés)

# Vérifier
curl -fsS http://127.0.0.1:8787/healthz
# → {"status":"ok"}
```

Vérifier que la DB de démo existe :

```bash
test -f demo/demo.sqlite && echo "✓ DB démo OK" || python3 demo/seed_db.py
```

## Lancer la démo — version live Claude Code (recommandée)

C'est la démo « vrai produit » : une session Claude Code interactive branchée sur
le proxy, + un bouton web pour montrer l'effet de masquage.

**Fenêtre 1 — navigateur, le bouton masque** :
```bash
xdg-open http://localhost:3000/demo_mask.html   # IMPÉRATIF via localhost:3000 (CORS)
# Optionnel : http://localhost:3000/demo_dashboard.html (détections PII en direct)
```

**Fenêtre 2 — Claude Code branché sur le proxy** :
```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

**Les 3 scènes (tu tapes dans Claude Code)** :
1. *Phrase libre* — « Confirme à Julien Maillard (julien.maillard@bluewin.ch) que son IBAN
   CH56 0023 0023 1234 5678 9 est enregistré. » → réponse avec les vrais noms.
2. *CSV* — colle un extrait clients et demande l'analyse du risque résiliation.
3. *Money shot DB* — « Liste-moi via query_db les 3 clients avec le plus de
   sinistres. » (Claude demandera l'autorisation `query_db` la 1ʳᵉ fois — accepte.)

**Le beat masque** : pose une question → réponse en vrais noms → **clique le bouton
(Masque MIS)** → re-pose la même question → la réponse sort en `[PERSONNE_x]`.
Punchline : « Anthropic ne voit QUE ça ». Le bouton agit sur la *prochaine* réponse.

> Pour zéro prompt de permission en live, pré-autorise une fois dans
> `.claude/settings.local.json` : `{"permissions":{"allow":["mcp__anon-gateway__query_db"]}}`.

## Lancer la démo — version curl scriptée (fallback déterministe)

Si tu préfères le déroulé curl pas-à-pas (sans LLM), deux terminaux côte à côte :

**Terminal 1 — slides** :
```bash
xdg-open demo/zero_trust/slides.html
# 13 slides · ← → pour naviguer, F pour plein écran
```

**Terminal 2 — script live** :
```bash
bash demo/zero_trust/run_demo.sh
# 8 étapes · Entrée entre chaque
```

## Ce que la démo montre

1. **Texte libre** — les 3 couches NER tournent sur la machine (GLiNER + Presidio en local, qwen reste serveur)
2. **CSV tabulaire** — la KB de colonnes synchronisée localement reconnaît `client_nom`, `iban`, etc.
3. **Idempotence locale** — `David Neri` reçoit le même token dans toute la session, mais reboot = reset
4. **Defense in depth** — la colonne `commentaire` (texte libre) est passée au peigne fin par GLiNER+Presidio
5. **Multilingue** — même client en allemand (`vorname`, `ahv`, `bemerkung`) reconnu via la KB
6. **KB sync** — le gateway distribue les 147 entrées + les patterns custom, sans jamais voir de PII
7. **Mapping local** — Redis sidecar : tokens visibles uniquement sur le poste de l'employé
8. **Pattern à chaud** — admin ajoute un regex côté gateway → `POST /refresh` sur le sidecar → la fleet apprend

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `SIDECAR_URL` | `http://127.0.0.1:8787` | URL du sidecar local |
| `GATEWAY_URL` | `http://localhost:8001` | URL du gateway métadonnées |
| `ADMIN_SECRET` | `dneri2.1` | Secret admin pour étape 8 |
| `GATEWAY_API_KEY` | clé hard-codée demo | Pour les appels admin |
| `SQLITE_DB` | `demo/demo.sqlite` | DB de démonstration |

Le token X-Sidecar-Token est lu automatiquement depuis
`~/.config/anon-sidecar/token` (écrit par `sidecar/install.sh`).

## Points d'attention pendant la démo

- **L'admin UI tourne sur le gateway LAN** (port 3000 — `admin.html`). Tu peux l'ouvrir
  pendant l'étape 8 pour montrer le pattern apparaître dans la section "Patterns".
- **Le frontend utilisateur** (`frontend/index.html`) tape `127.0.0.1:8787` — il faut
  donc l'ouvrir sur la machine où le sidecar tourne. CORS configuré pour `localhost:3000`.
- **Le réseau peut être coupé** entre l'étape 1 et l'étape 8 : sauf les étapes
  6 (KB depuis gateway) et 8 (pattern depuis gateway), tout marche offline. Bon
  argumentaire à sortir pendant la démo si tu veux dramatiser.

## Fallback : la démo v1 centralisée

L'ancienne démo `archive/demo_v1/run_demo.sh` ne tourne plus sur cette branche (les endpoints
`/api/anonymize` etc. ont été retirés en phase 4 du refacto). Pour la rejouer,
checkout un commit antérieur :

```bash
git log --oneline | grep "demo materials"
git checkout <hash>
```
