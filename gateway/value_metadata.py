"""Calcul des métadonnées d'une valeur (longueur, charset, regex_hint, hash).

Repris VERBATIM de sidecar/column_classifier.py : le gateway scanne lui-même les
DB (mode gateway-side) et doit produire les MÊMES métadonnées que le sidecar pour
que le classeur qwen3-pii reçoive le format attendu.
"""
from __future__ import annotations
import hashlib
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ALPHA_RE = re.compile(r"^[a-zA-Z]+$")
_ALPHANUM_RE = re.compile(r"^[a-zA-Z0-9]+$")

_REGEX_HINTS = [
    (re.compile(r"^[0-9]{13}$"), "avs"),
    (re.compile(r"^(CH|LI)[0-9]{2}[0-9]{5}[A-Za-z0-9]{5,17}$"), "iban_ch"),
    (re.compile(r"^(\+41|0041)?[0-9]{2,3}\s?[0-9]{3}\s?[0-9]{2}\s?[0-9]{2}$"), "phone"),
    (re.compile(r"^[0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4}$"), "date"),
    (re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), "date"),
    (re.compile(r"^[0-9]{2}:[0-9]{2}(:[0-9]{2})?$"), "date"),
]


def _charset(v: str) -> str:
    if _EMAIL_RE.match(v): return "email"
    if _URL_RE.match(v): return "url"
    if _DIGITS_RE.match(v): return "digits"
    if _ALPHA_RE.match(v): return "alpha"
    if _ALPHANUM_RE.match(v): return "alphanum"
    return "mixed"


def _regex_hint(v: str) -> str:
    for pat, hint in _REGEX_HINTS:
        if pat.match(v):
            return hint
    return "none"


def value_metadata(value: str) -> dict:
    value = str(value)
    return {
        "length": len(value),
        "charset": _charset(value),
        "has_spaces": " " in value,
        "has_punctuation": bool(re.search(r"[.,;:!?'\-/@&]", value)),
        "regex_hint": _regex_hint(value),
        "sample_hash": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8],
    }
