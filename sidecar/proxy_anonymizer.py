# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

"""Phase 2 du plan PROXY — anonymisation outbound du payload Anthropic Messages.

Parse le body JSON d'une requête `POST /v1/messages` et anonymise tous les
champs textuels qui peuvent contenir du PII *avant* le forward vers
api.anthropic.com :

| Champ                                              | Source PII typique             |
|----------------------------------------------------|--------------------------------|
| `system` (str ou list[{type:text, text:...}])      | **SKIP** — pas de PII typiquement, gain perf |
| `messages[*].content` (str)                        | Prompt utilisateur, réponse    |
| `messages[*].content[*].text`                      | Texte multi-blocs              |
| `messages[*].content[*]` type=tool_use `.input`    | Arguments structurés d'outil   |
| `messages[*].content[*]` type=tool_result `.content`| **Sortie Bash/Read/MCP — frontière critique** |

**Note sécurité** : Le champ `system` n'est PAS anonymisé (gain -1 à -2s par tour).
Risque résiduel : si CLAUDE.md ou un system prompt custom contient du PII, il fuitera.
Décision documentée dans docs/SECURITY.md.

Cache par hash SHA-256 du texte original : la conversation entière re-transite à
chaque tour, mais les vieux messages restent identiques — on évite de re-NER-iser
à chaque appel.

Tokenisation cohérente garantie par le sidecar Anonymizer (Redis local mappe
"Julien Maillard" → [PERSONNE_1] de façon stable durant toute la session).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sidecar.anonymizer import Anonymizer
from sidecar.cache import CacheService
from sidecar.ner import NERConfig
from sidecar.logging_config import get as _log

_logger = _log("sidecar.proxy_anonymizer")


class UnsupportedBlockError(Exception):
    """Bloc non anonymisable (image, document) — on refuse plutôt que de
    forwarder du contenu binaire potentiellement PII en clair (fail-closed)."""

    def __init__(self, block_type: str):
        self.block_type = block_type
        super().__init__(block_type)


# Détecte un placeholder déjà produit par le pipeline : `[LABEL_N]`.
# Sert à éviter la double-anonymisation : si un tool_result vient déjà
# du MCP `anon-gateway` (qui anonymise ses sorties), le texte contient
# essentiellement des placeholders. Re-passer GLiNER dessus produit des
# faux positifs (le placeholder lui-même est confondu avec un nom propre),
# créant une cascade [PERSONNE_X] → [PERSONNE_Y] → [PERSONNE_Z]...
_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")
_ALREADY_ANONYMIZED_RATIO = 0.30


def _is_already_anonymized(text: str) -> bool:
    """True si le texte est essentiellement composé de placeholders.

    Heuristique : si la somme des longueurs des placeholders dépasse 30%
    du texte total, on considère qu'il vient d'un tool déjà sécurisé et
    on évite de re-NER (qui inventerait de nouveaux tokens en cascade)."""
    if not text:
        return False
    matches = _PLACEHOLDER_RE.findall(text)
    if not matches:
        return False
    placeholder_chars = sum(len(m) for m in matches)
    return placeholder_chars / len(text) >= _ALREADY_ANONYMIZED_RATIO


# Sentinel pour masquer les placeholders avant NER. Lowercase + underscores
# pour que NER (qui chasse les noms propres) l'ignore, et ASCII pur pour
# rester JSON-safe en cas de fuite résiduelle.
_SENTINEL_FMT = "anonph{i}xx"
_SENTINEL_RE = re.compile(r"anonph(\d+)xx")


def _mask_placeholders(text: str) -> tuple[str, list[str]]:
    """Remplace tous les `[LABEL_N]` par des sentinels indexés, et retourne
    le texte masqué + la liste des placeholders originaux dans l'ordre."""
    sentinels: list[str] = []

    def repl(m: re.Match) -> str:
        sentinels.append(m.group(0))
        return _SENTINEL_FMT.format(i=len(sentinels) - 1)

    masked = _PLACEHOLDER_RE.sub(repl, text)
    return masked, sentinels


def _unmask_placeholders(text: str, sentinels: list[str]) -> str:
    """Restaure les placeholders à partir des sentinels. Tolère qu'un
    sentinel ait été légèrement transformé par NER (ex. capitalisation,
    whitespace inséré) en utilisant un regex sur l'index."""
    if not sentinels:
        return text

    def repl(m: re.Match) -> str:
        i = int(m.group(1))
        return sentinels[i] if 0 <= i < len(sentinels) else m.group(0)

    return _SENTINEL_RE.sub(repl, text, count=0)


class PayloadAnonymizer:
    def __init__(
        self,
        anonymizer: Anonymizer,
        cache: CacheService,
        user_id: str,
        ner_config: NERConfig | None = None,
        max_cache_entries: int = 10_000,
    ):
        self._anon = anonymizer
        self._cache = cache
        self._user_id = user_id
        self._ner_config = ner_config
        self._hash_cache: dict[str, str] = {}
        self._max_cache = max_cache_entries

    def reset_hash_cache(self) -> None:
        """À appeler quand le mapping Redis est cleared (DELETE /mapping).
        Sinon la hash cache contient des références à des tokens qui n'existent
        plus en Redis → la désanon inbound ne peut pas les résoudre."""
        self._hash_cache.clear()

    async def anonymize_body(self, body_bytes: bytes) -> bytes:
        if not body_bytes:
            return body_bytes

        try:
            payload = json.loads(body_bytes)
        except json.JSONDecodeError as e:
            _logger.warning("payload.parse_failed", extra={"err": str(e)})
            return body_bytes  # fail-open uniquement sur JSON invalide : Anthropic répondra 400 lui-même

        n_blocks_processed = 0
        n_cache_hits = 0

        # 1. system field — SKIP : pas de PII typiquement (CLAUDE.md = instructions framework).
        # Gain perf : -1 à -2s par tour. Risque documenté dans docs/SECURITY.md.
        # Si un system prompt custom contient du PII, il fuitera vers Anthropic.
        # Pour réactiver : décommenter les 3 lignes ci-dessous.
        # if "system" in payload:
        #     payload["system"], hits = await self._anon_system(payload["system"])
        #     n_cache_hits += hits

        # 2. messages array
        for msg in payload.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"], hit = await self._anon_text(content)
                n_blocks_processed += 1
                if hit:
                    n_cache_hits += 1
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        hits = await self._anon_block(block)
                        n_blocks_processed += 1
                        n_cache_hits += hits

        _logger.info("payload.anonymized", extra={
            "blocks": n_blocks_processed,
            "cache_hits": n_cache_hits,
            "cache_size": len(self._hash_cache),
        })

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def _anon_block(self, block: dict) -> int:
        """Anonymise un bloc de message (type-aware). Retourne le nb de cache hits."""
        bt = block.get("type")

        if bt == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                block["text"], hit = await self._anon_text(text)
                return 1 if hit else 0

        elif bt == "tool_use":
            # input est un objet JSON arbitraire — on parcourt les feuilles strings
            block["input"], hits = await self._anon_json_leaves(block.get("input"))
            return hits

        elif bt == "tool_result":
            # **Le champ critique** : sortie de Bash, Read, MCP, etc.
            # Peut être string ou list[{type:text, text:...}].
            content = block.get("content")
            if isinstance(content, str):
                block["content"], hit = await self._anon_text(content)
                return 1 if hit else 0
            if isinstance(content, list):
                hits = 0
                for sub in content:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        text = sub.get("text", "")
                        if isinstance(text, str):
                            sub["text"], hit = await self._anon_text(text)
                            if hit:
                                hits += 1
                return hits

        elif bt in ("image", "document"):
            # Contenu binaire (base64) : pas d'OCR local, impossible à anonymiser.
            raise UnsupportedBlockError(bt)

        # Autres types inconnus (thinking, ...) : texte généré par le modèle,
        # qui n'a vu que des placeholders — pass-through.
        return 0

    async def _anon_system(self, system: Any) -> tuple[Any, int]:
        if isinstance(system, str):
            anon, hit = await self._anon_text(system)
            return anon, (1 if hit else 0)
        if isinstance(system, list):
            hits = 0
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        block["text"], hit = await self._anon_text(text)
                        if hit:
                            hits += 1
            return system, hits
        return system, 0

    async def _anon_json_leaves(self, obj: Any) -> tuple[Any, int]:
        """Walk un objet JSON arbitraire, anonymise les strings feuilles."""
        if isinstance(obj, str):
            anon, hit = await self._anon_text(obj)
            return anon, (1 if hit else 0)
        if isinstance(obj, dict):
            hits = 0
            out: dict = {}
            for k, v in obj.items():
                # Les clés sont des noms de paramètres tool — pas du PII.
                out[k], h = await self._anon_json_leaves(v)
                hits += h
            return out, hits
        if isinstance(obj, list):
            hits = 0
            out_list = []
            for item in obj:
                anon, h = await self._anon_json_leaves(item)
                out_list.append(anon)
                hits += h
            return out_list, hits
        # int, float, bool, None : pass-through
        return obj, 0

    async def _anon_text(self, text: str) -> tuple[str, bool]:
        """Hash-cached anonymize. Retourne (anonymized_text, cache_hit)."""
        if not text or not text.strip():
            return text, False

        # Bypass : texte déjà essentiellement composé de placeholders.
        # Évite la cascade `[PERSONNE_X]` → `[PERSONNE_Y]` quand un
        # tool_result vient d'un tool déjà-anonymisant (MCP anon-gateway).
        if _is_already_anonymized(text):
            return text, False

        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._hash_cache.get(h)
        if cached is not None:
            return cached, True

        # Masque les placeholders déjà présents avec des sentinels avant
        # NER pour éviter la double-anonymisation. Sans ça, NER prend
        # `[PERSONNE_156]` pour un nom propre et l'envoie au token store,
        # créant une chaîne `[PERSONNE_163] -> "[PERSONNE_156]"` polluante.
        masked_text, sentinels = _mask_placeholders(text)

        anon_text, _mapping = await self._anon.anonymize(
            masked_text, self._cache, self._user_id, self._ner_config,
        )

        anon_text = _unmask_placeholders(anon_text, sentinels)

        # LRU naïf : si on dépasse, on dégage la moitié des entrées au hasard
        # (le cas typique est : peu de variation, donc peu d'évictions)
        if len(self._hash_cache) >= self._max_cache:
            for k in list(self._hash_cache.keys())[: self._max_cache // 2]:
                del self._hash_cache[k]

        self._hash_cache[h] = anon_text
        return anon_text, False
