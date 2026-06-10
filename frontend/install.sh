#!/usr/bin/env bash
# install_hooks.sh — installe les hooks Claude Code sur n'importe quelle machine
#
# Usage :
#   curl -s http://192.168.1.13:8001/install | bash
#   -- ou --
#   bash install_hooks.sh --gateway http://192.168.1.13:8001 --key anon_xxx

set -e

GATEWAY_URL="${GATEWAY_URL:-http://192.168.1.13:8001}"
GATEWAY_API_KEY="${GATEWAY_API_KEY:-anon_d1efd11561695c12bafe87c0f37c5a758cbc64fd6ec084b4d68cd083a1602ab5}"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --gateway) GATEWAY_URL="$2"; shift 2 ;;
    --key)     GATEWAY_API_KEY="$2"; shift 2 ;;
    *) echo "Usage: $0 [--gateway URL] [--key API_KEY]"; exit 1 ;;
  esac
done

HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.local.json"

echo ""
echo "  LLM Anonymization Gateway — Installation des hooks"
echo "  Gateway : $GATEWAY_URL"
echo ""

# ── 1. Vérifier Python3 ───────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "  ✗ python3 requis mais non trouvé. Installe Python 3.8+."
  exit 1
fi
echo "  ✓ python3 $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── 2. Vérifier que le gateway répond ────────────────────────────────────────
echo -n "  Connexion au gateway... "
if ! python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('$GATEWAY_URL/api/config', timeout=5)
    print('ok')
except Exception as e:
    print('ERREUR:', e)
    sys.exit(1)
"; then
  echo "  ✗ Gateway inaccessible. Vérifie que le service tourne et que $GATEWAY_URL est correct."
  exit 1
fi

# ── 3. Créer le dossier hooks ─────────────────────────────────────────────────
mkdir -p "$HOOKS_DIR"

# ── 4. Écrire hook_anonymize_prompt.py ───────────────────────────────────────
cat > "$HOOKS_DIR/hook_anonymize_prompt.py" << 'PYEOF'
#!/usr/bin/env python3
import json, os, sys, urllib.error, urllib.request

GATEWAY_URL    = os.environ.get("GATEWAY_URL", "http://localhost:8000").rstrip("/")
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
TIMEOUT_SEC    = 5.0
BOLD, RED, RESET = "\033[1m", "\033[31m", "\033[0m"

def _read_stdin():
    try: return json.loads(sys.stdin.read() or "{}")
    except: return {}

def _block(reason):
    print(f"\n{BOLD}{RED}⛔ Prompt bloqué — {reason}{RESET}\n", file=sys.stderr)
    return 2

def _get_config():
    try:
        req = urllib.request.Request(f"{GATEWAY_URL}/api/config")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read())
    except: return None

def _anonymize(text):
    if not GATEWAY_API_KEY:
        return None, "GATEWAY_API_KEY non défini"
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/anonymize",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GATEWAY_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read()).get("anonymized_text", text), None
    except urllib.error.HTTPError as e:
        return None, f"Gateway erreur HTTP {e.code}"
    except (urllib.error.URLError, OSError):
        return None, f"Gateway injoignable ({GATEWAY_URL})"
    except json.JSONDecodeError:
        return None, "Réponse gateway invalide"

def main():
    payload = _read_stdin()
    prompt  = payload.get("prompt") or ""
    if not prompt.strip(): return 0
    cfg = _get_config()
    if cfg is None: return _block(f"Gateway injoignable ({GATEWAY_URL})")
    if not cfg.get("hook_enabled", True): return 0
    anonymized, error = _anonymize(prompt)
    if error: return _block(error)
    if anonymized == prompt: return 0
    print(json.dumps({"prompt": anonymized}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
PYEOF

# ── 5. Écrire hook_deanonymize_response.py ───────────────────────────────────
cat > "$HOOKS_DIR/hook_deanonymize_response.py" << 'PYEOF'
#!/usr/bin/env python3
import json, os, sys, urllib.error, urllib.request

GATEWAY_URL    = os.environ.get("GATEWAY_URL", "http://localhost:8000").rstrip("/")
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
TIMEOUT_SEC    = 5.0
DIM, BOLD, CYAN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[0m"

def _read_stdin():
    try: return json.loads(sys.stdin.read() or "{}")
    except: return {}

def _get_config():
    try:
        req = urllib.request.Request(f"{GATEWAY_URL}/api/config")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read())
    except: return None

def _last_assistant_text(path):
    try:
        lines = open(path, encoding="utf-8").readlines()
    except: return ""
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw: continue
        try: entry = json.loads(raw)
        except: continue
        role = entry.get("type") or entry.get("role")
        if role != "assistant": continue
        msg = entry.get("message", entry)
        content = msg.get("content")
        if isinstance(content, str): return content
        if isinstance(content, list):
            texts = [b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
            if texts: return "\n".join(t for t in texts if t)
    return ""

def _deanonymize(text):
    if not GATEWAY_API_KEY: return None
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/deanonymize",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GATEWAY_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read()).get("result", "")
    except: return None

def main():
    payload    = _read_stdin()
    transcript = payload.get("transcript_path") or ""
    if not transcript: return 0
    cfg = _get_config()
    if cfg and not cfg.get("deanon_enabled", True): return 0
    text = _last_assistant_text(transcript)
    if not text.strip(): return 0
    decoded = _deanonymize(text)
    if not decoded or decoded == text: return 0
    sep = "─" * 30
    print(file=sys.stderr)
    print(f"  {CYAN}{sep} réponse décodée · sur ta machine uniquement {sep}{RESET}", file=sys.stderr)
    for line in decoded.splitlines():
        print(f"  {DIM}│{RESET} {line}", file=sys.stderr)
    print(file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
PYEOF

chmod +x "$HOOKS_DIR/hook_anonymize_prompt.py" "$HOOKS_DIR/hook_deanonymize_response.py"

# ── 6. Mettre à jour settings.local.json ─────────────────────────────────────
HOOK_CMD_ANON="GATEWAY_URL=$GATEWAY_URL GATEWAY_API_KEY=$GATEWAY_API_KEY python3 $HOOKS_DIR/hook_anonymize_prompt.py"
HOOK_CMD_DEANON="GATEWAY_URL=$GATEWAY_URL GATEWAY_API_KEY=$GATEWAY_API_KEY python3 $HOOKS_DIR/hook_deanonymize_response.py"

# Lire le fichier existant ou créer un objet vide
if [ -f "$SETTINGS" ]; then
    EXISTING=$(cat "$SETTINGS")
else
    EXISTING="{}"
fi

# Injecter les hooks (python3 merge propre)
python3 << PYEOF
import json, sys

existing = json.loads('''$EXISTING''')
existing.setdefault("hooks", {})
existing["hooks"]["UserPromptSubmit"] = [{
    "matcher": "",
    "hooks": [{"type": "command", "command": "$HOOK_CMD_ANON"}]
}]
existing["hooks"]["Stop"] = [{
    "matcher": "",
    "hooks": [{"type": "command", "command": "$HOOK_CMD_DEANON"}]
}]

with open("$SETTINGS", "w") as f:
    json.dump(existing, f, indent=2, ensure_ascii=False)
print("  ✓ settings.local.json mis à jour")
PYEOF

# ── 7. Test final ─────────────────────────────────────────────────────────────
echo -n "  Test d'anonymisation... "
RESULT=$(echo '{"prompt": "Test avec M. Jean Dupont à Genève"}' | \
  GATEWAY_URL="$GATEWAY_URL" GATEWAY_API_KEY="$GATEWAY_API_KEY" \
  python3 "$HOOKS_DIR/hook_anonymize_prompt.py" 2>/dev/null)

if echo "$RESULT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('ok —', d['prompt'])" 2>/dev/null; then
  echo ""
  echo "  ✓ Installation terminée. Relance Claude Code pour activer les hooks."
else
  echo "  ⚠ Hook installé mais test d'anonymisation sans résultat (texte sans PII détecté ou gateway indisponible)."
fi
echo ""
