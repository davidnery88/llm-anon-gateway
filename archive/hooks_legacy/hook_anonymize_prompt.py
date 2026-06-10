#!/usr/bin/env python3
"""UserPromptSubmit hook — anonymise le prompt via le sidecar local.

Le sidecar tourne sur 127.0.0.1 sur la machine utilisateur, gère le NER
local + le mapping Redis local. Aucune PII ne quitte le poste.

Si le sidecar est indisponible ou la config désactivée, bloque l'envoi
(exit 2) — politique fail-safe : "rather fail than leak".
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:8787").rstrip("/")
TIMEOUT_SEC = 5.0
TOKEN_PATH = Path(os.environ.get("ANON_SIDECAR_TOKEN_PATH",
                                 str(Path.home() / ".config" / "anon-sidecar" / "token")))


def _load_token() -> str:
    try:
        return TOKEN_PATH.read_text().strip()
    except (FileNotFoundError, OSError):
        return ""


_TOKEN = _load_token()

BOLD = "\033[1m"
RED = "\033[31m"
RESET = "\033[0m"


def _read_stdin() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _block(reason: str) -> int:
    print(f"\n{BOLD}{RED}⛔ Prompt bloqué — {reason}{RESET}\n", file=sys.stderr)
    return 2


def _healthz_ok() -> bool:
    try:
        req = urllib.request.Request(f"{SIDECAR_URL}/healthz")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status == 200
    except Exception:
        return False


def _anonymize(text: str) -> tuple[str | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if _TOKEN:
        headers["X-Sidecar-Token"] = _TOKEN
    try:
        req = urllib.request.Request(
            f"{SIDECAR_URL}/anonymize",
            data=json.dumps({"text": text}).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read()).get("anonymized_text", text), None
    except urllib.error.HTTPError as e:
        return None, f"Sidecar erreur HTTP {e.code} ({SIDECAR_URL})"
    except (urllib.error.URLError, OSError):
        return None, f"Sidecar injoignable ({SIDECAR_URL}) — `systemctl --user status anon-sidecar`"
    except json.JSONDecodeError:
        return None, f"Réponse sidecar invalide ({SIDECAR_URL})"


def main() -> int:
    payload = _read_stdin()
    prompt = payload.get("prompt") or ""
    if not prompt.strip():
        return 0

    if not _healthz_ok():
        return _block(f"Sidecar injoignable ({SIDECAR_URL}) — démarrer le service avant d'envoyer")

    anonymized, error = _anonymize(prompt)
    if error is not None:
        return _block(error)

    if anonymized == prompt:
        return 0

    print(json.dumps({"prompt": anonymized}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
