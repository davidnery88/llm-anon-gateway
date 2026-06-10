# Sidecar zero-trust

Daemon local qui héberge le NER (GLiNER + Presidio + spaCy fr) + le store de
mappings token↔PII (Redis en mémoire). Tourne **sur la machine de chaque
utilisateur**, écoute uniquement sur 127.0.0.1.

Aucune PII ne quitte le poste. Le gateway LAN ne sert plus que pour :
- pull périodique de la knowledge base de colonnes (métadonnées, pas PII)
- appel du classifier qwen3-pii sur les colonnes ambiguës (`/api/classify_column`,
  side-channel assumé — voir plan)

## Installation rapide

### Linux / macOS

```bash
git clone <repo> ~/llm-anon-gateway
cd ~/llm-anon-gateway
bash sidecar/install.sh --gateway http://192.168.1.13:8001
```

Ce script :
1. Vérifie Docker + Docker Compose
2. Génère un token X-Sidecar-Token dans `~/.config/anon-sidecar/token` (mode 0600)
3. Build l'image (~2 GB, 5-10 min au premier lancement)
4. Démarre la stack docker (sidecar + Redis local)
5. (Linux) écrit une unit systemd-user pour démarrage automatique au boot

### Windows (Docker Desktop + WSL2)

Pré-requis : Docker Desktop installé et démarré (`docker info` doit répondre).

```powershell
git clone <repo> $env:USERPROFILE\llm-anon-gateway
cd $env:USERPROFILE\llm-anon-gateway
powershell -ExecutionPolicy Bypass -File sidecar\install.ps1 -Gateway http://192.168.1.13:8001
```

Équivalent du script bash :
1. Vérifie Docker Desktop
2. Génère un token dans `%USERPROFILE%\.config\anon-sidecar\token` (ACL : utilisateur courant uniquement)
3. Build l'image
4. Démarre la stack
5. Pas d'équivalent systemd — Docker Desktop relance les containers au login si "Start Docker Desktop when you sign in" est activé (Settings → General). Le compose a déjà `restart: unless-stopped`.

À l'issue (toutes plateformes), les hooks Claude Code et le MCP server pointent
vers `http://127.0.0.1:8787` et lisent le token depuis `~/.config/anon-sidecar/token`
sans config supplémentaire (sur Windows, Python résout `Path.home()` vers
`C:\Users\<user>` donc le path matche).

## Désinstallation

Linux / macOS :
```bash
bash sidecar/uninstall.sh           # stop + remove containers + systemd, garde le token
bash sidecar/uninstall.sh --purge   # idem + efface token, env et marker
```

Windows :
```powershell
powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1
powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1 -Purge
```

## Vérification

```bash
curl -fsS http://127.0.0.1:8787/healthz
# → {"status": "ok"}

curl -sS http://127.0.0.1:8787/anonymize \
  -H "X-Sidecar-Token: $(cat ~/.config/anon-sidecar/token)" \
  -H "Content-Type: application/json" \
  -d '{"text": "David Neri habite Lausanne."}'
# → {"anonymized_text": "[PERSONNE_1] habite [LOCALISATION_1].", "mapping": {...}}
```

## Persistence

**Volontairement zéro.** Le Redis local tourne avec `--save "" --appendonly no`.
Au reboot, tous les mappings sont effacés. `[PERSONNE_1]` désignera potentiellement
une autre personne dans la session suivante — c'est le compromis intentionnel
"propre sur disque chiffré ou pas" du modèle de menace.

## Démarrage au boot (Linux)

L'install.sh écrit une unit systemd-user. Pour qu'elle démarre même sans session
ouverte :

```bash
loginctl enable-linger $USER
```

## Logs

```bash
docker logs -f anon-sidecar       # logs JSON structurés
docker logs -f anon-sidecar-redis # Redis local
```

## Variables d'environnement (via `~/.config/anon-sidecar/env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `GATEWAY_URL` | `http://host.docker.internal:8001` | Pour pull KB + classify_column |
| `GATEWAY_API_KEY` | _vide_ | Clé Bearer pour appeler le gateway |
| `ANON_SIDECAR_TOKEN` | _généré_ | Secret partagé pour X-Sidecar-Token |
| `ANON_SIDECAR_ALLOWED_ORIGIN` | `http://localhost:3000` | CORS depuis le frontend LAN |
