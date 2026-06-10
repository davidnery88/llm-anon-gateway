# Test du proxy Claude Code — guide déroulement & rollback

## Ce que les tests vérifient

`sidecar/tests/test_proxy_e2e.py` couvre le tunnel complet :

| Test | Ce qui est vérifié |
|---|---|
| `test_proxy_anonymizes_outbound` | "David Neri" → `[PERSONNE_1]` dans le body forwardé vers Anthropic |
| `test_proxy_deanonymizes_inbound` | `[PERSONNE_1]` → "David Neri" dans la réponse retournée à Claude Code |
| `test_proxy_forwards_auth_header` | Le token Teams/OAuth est bien forwardé tel quel vers Anthropic |
| `test_proxy_failsafe_on_anonymization_crash` | Si NER crashe → 503, jamais le PII en clair |
| `test_proxy_upstream_error_returns_502` | Si Anthropic est injoignable → 502 |

---

## 1. Lancer les tests (Docker requis — gliner non installé localement)

```bash
# Depuis la racine du projet
cd "/home/dne/Documents/llm-anon-gateway (with qwen)"

# Build l'image sidecar (5-10 min la première fois, ~30s ensuite)
docker build -f sidecar/Dockerfile -t anon-sidecar-test .

# Lancer les tests proxy uniquement
docker run --rm anon-sidecar-test \
  python -m pytest sidecar/tests/test_proxy_e2e.py -v

# Ou tous les tests sidecar d'un coup
docker run --rm anon-sidecar-test \
  python -m pytest sidecar/tests/ -v
```

Résultat attendu : 5 tests `PASSED`.

---

## 2. Test en conditions réelles — config scoped à un dossier

La config ci-dessous **s'applique uniquement au dossier `~/proxy-test/`**.
Si quelque chose se passe mal, tu sors du dossier et Claude Code redevient normal immédiatement.

### 2a. Démarrer le sidecar

```bash
cd "/home/dne/Documents/llm-anon-gateway (with qwen)/sidecar"
docker compose up -d
# Vérifier qu'il écoute
curl http://127.0.0.1:8787/healthz   # → {"status": "ok"}
```

### 2b. Créer le dossier de test avec la config scoped

```bash
mkdir -p ~/proxy-test/.claude
cat > ~/proxy-test/.claude/settings.json << 'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"
  }
}
EOF
```

### 2c. Tester

```bash
cd ~/proxy-test
claude   # Claude Code démarre en passant par le sidecar
```

Vérifier dans les logs sidecar que les requêtes arrivent :

```bash
docker logs anon-sidecar --follow
# Tu dois voir : proxy.anonymized, proxy.request, proxy.stream_done
```

---

## 3. Rollback complet (30 secondes)

### Rollback config Claude Code
```bash
# Option A — supprimer la config du dossier de test
rm ~/proxy-test/.claude/settings.json

# Option B — supprimer tout le dossier de test
rm -rf ~/proxy-test
```

Après ça, `claude` dans n'importe quel autre dossier pointe directement sur
`api.anthropic.com` — aucun changement global, aucun risque de blocage.

### Rollback sidecar
```bash
cd "/home/dne/Documents/llm-anon-gateway (with qwen)/sidecar"
docker compose down
```

### Vérification post-rollback
```bash
# Claude Code pointe bien vers Anthropic directement
cd ~
claude -p "dis bonjour"   # doit fonctionner normalement sans le sidecar
```

---

## 4. Pourquoi la config est scoped et ne casse pas le reste

Claude Code lit `.claude/settings.json` dans le dossier courant au démarrage.
Les autres dossiers n'ont pas ce fichier → comportement normal.
Aucune variable d'environnement globale n'est modifiée.
Aucune modification dans `~/.bashrc` ou `~/.zshrc`.

Si le sidecar est arrêté mais que `settings.json` est encore là :
Claude Code recevra un `502` et refusera la connexion — il ne fallbackera
**pas** silencieusement vers Anthropic (c'est voulu : fail-safe).
Dans ce cas, stopper le sidecar OU supprimer `settings.json`.
