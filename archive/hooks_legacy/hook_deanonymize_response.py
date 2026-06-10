#!/usr/bin/env python3
"""Stop hook — affiche la réponse décodée dans le terminal après chaque tour.

Lit le dernier message assistant dans le transcript JSONL, le soumet au
sidecar local /deanonymize, et affiche le résultat sur stderr (visible
utilisateur, non transmis à Anthropic).

Variable d'environnement :
  SIDECAR_URL — URL du sidecar local (défaut : http://127.0.0.1:8787)
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

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _read_stdin() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _last_assistant_text(transcript_path: str) -> str:
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return ""

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue

        role = entry.get("type") or entry.get("role")
        if role != "assistant":
            continue

        message = entry.get("message", entry)
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                return "\n".join(t for t in texts if t)
    return ""


def _deanonymize(text: str) -> str | None:
    headers = {"Content-Type": "application/json"}
    if _TOKEN:
        headers["X-Sidecar-Token"] = _TOKEN
    try:
        req = urllib.request.Request(
            f"{SIDECAR_URL}/deanonymize",
            data=json.dumps({"text": text}).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read()).get("result", "")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def main() -> int:
    payload = _read_stdin()
    transcript = payload.get("transcript_path") or ""
    if not transcript:
        return 0

    text = _last_assistant_text(transcript)
    if not text.strip():
        return 0

    decoded = _deanonymize(text)
    if not decoded or decoded == text:
        return 0

    sep = "─" * 30
    print(file=sys.stderr)
    print(f"  {CYAN}{sep} réponse décodée · sur ta machine uniquement {sep}{RESET}", file=sys.stderr)
    for line in decoded.splitlines():
        print(f"  {DIM}│{RESET} {line}", file=sys.stderr)
    print(file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
