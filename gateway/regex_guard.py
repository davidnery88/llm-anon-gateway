"""Validation des regex avant insertion en KB (patterns auto-générés par Qwen).

Un pattern stocké ici est compilé par Presidio et exécuté sur chaque requête
de tous les sidecars : un regex pathologique (backtracking catastrophique)
ferait tomber l'anonymiseur. On rejette donc syntaxe invalide, longueur
excessive et quantificateurs non bornés imbriqués (heuristique ReDoS —
ne couvre pas les alternations chevauchantes type (a|aa)+).
"""
from __future__ import annotations

import re

try:  # Python 3.11+
    from re import _parser as _sre_parse
except ImportError:  # pragma: no cover
    import sre_parse as _sre_parse

MAX_REGEX_LENGTH = 200
# Au-delà de cette borne, une répétition est traitée comme non bornée.
_UNBOUNDED_THRESHOLD = 50


def _subpatterns(av):
    """Itère les SubPattern imbriqués dans l'argument d'un opcode (BRANCH, SUBPATTERN, ...)."""
    if isinstance(av, _sre_parse.SubPattern):
        yield av
    elif isinstance(av, (tuple, list)):
        for item in av:
            yield from _subpatterns(item)


def _has_nested_unbounded_repeat(node, in_repeat: bool = False) -> bool:
    for op, av in node:
        if str(op) in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            _min, _max, sub = av
            unbounded = _max == _sre_parse.MAXREPEAT or _max > _UNBOUNDED_THRESHOLD
            if unbounded and in_repeat:
                return True
            if _has_nested_unbounded_repeat(sub, in_repeat or unbounded):
                return True
        else:
            for sub in _subpatterns(av):
                if _has_nested_unbounded_repeat(sub, in_repeat):
                    return True
    return False


def validate_regex(regex: str) -> tuple[bool, str]:
    """Retourne (ok, raison). ok=False → ne pas stocker le pattern."""
    if not regex or not regex.strip():
        return False, "regex vide"
    if len(regex) > MAX_REGEX_LENGTH:
        return False, f"regex trop long ({len(regex)} > {MAX_REGEX_LENGTH})"
    try:
        re.compile(regex)
        parsed = _sre_parse.parse(regex)
    except re.error as e:
        return False, f"regex invalide : {e}"
    if _has_nested_unbounded_repeat(parsed):
        return False, "quantificateurs non bornés imbriqués (risque ReDoS)"
    return True, ""
