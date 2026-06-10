# scripts/dev/ — outils Phase 0 du plan proxy

## dummy_proxy.py

Proxy forward-only de capture. Ne modifie rien, log tout. Utilisé pour
vérifier que Claude Code respecte `ANTHROPIC_BASE_URL` et pour inventorier
les endpoints touchés.

### Lancement

Trois terminaux ouverts en parallèle.

**Terminal 1 — le proxy** :
```bash
cd ~/Documents/llm-anon-gateway
mcp_server/venv/bin/python scripts/dev/dummy_proxy.py | tee /tmp/proxy.log
```

Sortie attendue à l'écran : ligne JSON `{"event":"proxy.start", "port":8788, ...}`.

**Terminal 2 — tcpdump pour l'inventaire (étape 0.1)** :
```bash
# Note : sudo requis pour tcpdump
sudo tcpdump -i any -nn -w /tmp/cc-baseline.pcap 'host api.anthropic.com or host claude.ai or host console.anthropic.com' &
# Pour analyse ultérieure :
#   tshark -r /tmp/cc-baseline.pcap -Y 'tls.handshake.extensions_server_name' \
#     -T fields -e tls.handshake.extensions_server_name | sort -u
```

**Terminal 3 — Claude Code via le proxy** :
```bash
# IMPORTANT : nouvelle session shell pour ne pas hériter d'autres env vars
cd ~/Documents/llm-anon-gateway
ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude
```

### Ce qu'on cherche à valider

**Étape 0.2 — routing HTTP nu** :
- Dans le terminal 3, taper un prompt simple. Ex : `bonjour`.
- Dans le terminal 1, voir au moins une ligne `request.in` arriver, suivie
  d'une `response.start` puis `response.done`.
- Si Claude Code répond normalement → ✅ `ANTHROPIC_BASE_URL` respecté en HTTP nu.
- Si erreur 401/403/TLS rejet → ✅ également utile à savoir (passe en mkcert).
- Si **aucun** trafic n'arrive sur le proxy → ❌ la variable d'env est
  ignorée. Vérifier dans tcpdump si le trafic passe en direct à
  api.anthropic.com.

**Étape 0.1 — inventaire trafic** :
- Pendant que tu utilises Claude Code, regarder dans le proxy log tous les
  `path` distincts qui passent. Typiquement on attend `/v1/messages`. Si
  d'autres apparaissent (`/oauth/...`, `/v1/files`, `/v1/usage`, etc.),
  les noter — ils devront aussi être pris en charge par le vrai proxy.
- Analyser le `.pcap` après coup pour voir si des connexions à
  `*.anthropic.com` contournent le proxy (auquel cas la variable d'env
  n'attrape pas tout).

**Étape 0.3 — refresh OAuth** :
- Laisser Claude Code ouvert et utilisé en début de session.
- Attendre **au moins 60 minutes** sans le fermer.
- Renvoyer un prompt et observer :
    - Si la réponse arrive normalement → ✅ le refresh OAuth passe par le proxy.
    - Si erreur 401 sortant du proxy → ❌ le refresh contourne. Inspecter
      le pcap pendant cette fenêtre pour identifier l'endpoint OAuth.

### Critères de sortie Phase 0

Les trois questions auxquelles on doit avoir répondu avant d'écrire du
vrai code proxy :

1. Claude Code respecte-t-il `ANTHROPIC_BASE_URL` en HTTP nu ?
2. Tous les endpoints `*.anthropic.com` passent-ils par le proxy, ou
   certains contournent ?
3. Le refresh OAuth (après ≥1h) passe-t-il aussi par le proxy ?

Trois oui → on attaque la phase 1.
Un non → on rediscute du shape du plan avant de coder.
