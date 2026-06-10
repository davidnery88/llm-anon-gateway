from __future__ import annotations
import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any

_SQL_START = re.compile(r"(?is)^\s*(INSERT|UPDATE|DELETE|SELECT)\b")


def _looks_like_sql(t: str) -> bool:
    if not _SQL_START.match(t):
        return False
    try:
        import sqlglot
        parsed = sqlglot.parse(t)
        return bool(parsed) and parsed[0] is not None
    except Exception:
        return False


def _looks_like_xml(t: str) -> bool:
    if not t.lstrip().startswith("<"):
        return False
    try:
        import defusedxml.ElementTree as ET
        ET.fromstring(t)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class FieldValue:
    field: str   # nom de colonne / clé JSON (hint pour NER)
    value: str   # valeur brute
    path: str    # chemin pour re-injection (ex: "row0.nom", "assure.nom")


def detect_format(text: str) -> str:
    t = text.strip()
    if _looks_like_sql(t):
        return "sql"
    if _looks_like_xml(t):
        return "xml"
    if t.startswith(("{", "[")):
        return "json"
    lines = [l for l in t.splitlines() if l.strip()]
    if len(lines) >= 2 and sum(1 for l in lines[:3] if "|" in l) >= 2:
        return "table"
    if len(lines) >= 2:
        counts = [l.count(",") for l in lines[:5] if l.strip()]
        if len(counts) >= 2 and len(set(counts)) == 1 and counts[0] >= 2:
            return "csv"
    return "freetext"


def _parse_table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [l for l in text.splitlines() if l.strip() and not set(l.strip()) <= set("|-: ")]
    if not lines:
        return [], []
    headers = [h.strip() for h in lines[0].split("|") if h.strip()]
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if cells:
            rows.append(cells)
    return headers, rows


def _extract_table(text: str) -> list[FieldValue]:
    headers, rows = _parse_table(text)
    pairs = []
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            if cell and ci < len(headers):
                pairs.append(FieldValue(field=headers[ci], value=cell, path=f"row{ri}.{headers[ci]}"))
    return pairs


def _extract_csv(text: str) -> list[FieldValue]:
    reader = csv.DictReader(io.StringIO(text))
    pairs = []
    for ri, row in enumerate(reader):
        for field, value in row.items():
            if value and value.strip():
                pairs.append(FieldValue(field=field.strip(), value=value.strip(), path=f"row{ri}.{field.strip()}"))
    return pairs


def _flatten_json(data: Any, path: str = "") -> list[FieldValue]:
    pairs = []
    if isinstance(data, dict):
        for k, v in data.items():
            sub = f"{path}.{k}" if path else k
            pairs.extend(_flatten_json(v, sub))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            pairs.extend(_flatten_json(item, f"{path}[{i}]"))
    elif isinstance(data, str) and data.strip():
        field = path.split(".")[-1].split("[")[0]
        pairs.append(FieldValue(field=field, value=data, path=path))
    return pairs


def _reinject_json(text: str, mapping: dict) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _reinject_freetext(text, mapping)

    def replace_in(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: replace_in(v) for k, v in node.items()}
        if isinstance(node, list):
            return [replace_in(item) for item in node]
        if isinstance(node, str):
            result = node
            for original in sorted(mapping, key=len, reverse=True):
                result = result.replace(original, mapping[original])
            return result
        return node

    return json.dumps(replace_in(data), ensure_ascii=False)


def _reinject_freetext(text: str, mapping: dict) -> str:
    result = text
    for original in sorted(mapping, key=len, reverse=True):
        result = result.replace(original, mapping[original])
    return result


def _flatten_xml(text: str) -> list[FieldValue]:
    """Parcourt l'arbre XML (defusedxml) → un FieldValue par feuille texte ET attribut.
    Les attributs peuvent porter de la PII (<client nom="David"/>)."""
    try:
        import defusedxml.ElementTree as ET
        root = ET.fromstring(text)
    except Exception:
        return []
    pairs: list[FieldValue] = []

    def walk(el: Any, path: str) -> None:
        cur = f"{path}/{el.tag}" if path else el.tag
        for aname, aval in el.attrib.items():
            if aval and aval.strip():
                pairs.append(FieldValue(field=aname, value=aval, path=f"{cur}@{aname}"))
        txt = (el.text or "").strip()
        if txt:
            pairs.append(FieldValue(field=el.tag, value=txt, path=cur))
        tail = (el.tail or "").strip()   # contenu mixte : texte après l'élément
        if tail:
            pairs.append(FieldValue(field=el.tag, value=tail, path=f"{cur}#tail"))
        for child in el:
            walk(child, cur)

    walk(root, "")
    return pairs


def _reinject_xml(text: str, mapping: dict) -> str:
    """Re-parse + remplace dans les textes/attributs feuilles, re-sérialise.
    Fallback remplacement chaîne si parse impossible (jamais de crash/fuite)."""
    try:
        import defusedxml.ElementTree as ET
        from xml.etree.ElementTree import tostring
        root = ET.fromstring(text)
    except Exception:
        return _reinject_freetext(text, mapping)
    keys = sorted(mapping, key=len, reverse=True)

    def _repl(s: str) -> str:
        for k in keys:
            s = s.replace(k, mapping[k])
        return s

    def walk(el: Any) -> None:
        for aname in list(el.attrib):
            el.attrib[aname] = _repl(el.attrib[aname])
        if el.text:
            el.text = _repl(el.text)
        if el.tail:                       # contenu mixte : texte après l'élément
            el.tail = _repl(el.tail)
        for child in el:
            walk(child)

    walk(root)
    try:
        return tostring(root, encoding="unicode")
    except Exception:
        return _reinject_freetext(text, mapping)


def extract_pairs(text: str, fmt: str) -> list[FieldValue]:
    if fmt == "sql":
        from sidecar.sql_anon import extract_sql_pairs
        return extract_sql_pairs(text)
    if fmt == "xml":
        return _flatten_xml(text)
    if fmt == "table":
        return _extract_table(text)
    if fmt == "csv":
        return _extract_csv(text)
    if fmt == "json":
        return _flatten_json(json.loads(text))
    return []


def reinject(text: str, fmt: str, mapping: dict) -> str:
    if fmt == "sql":
        from sidecar.sql_anon import reinject_sql
        return reinject_sql(text, mapping)
    if fmt == "xml":
        return _reinject_xml(text, mapping)
    if fmt in ("table", "csv"):
        return _reinject_freetext(text, mapping)
    if fmt == "json":
        return _reinject_json(text, mapping)
    return _reinject_freetext(text, mapping)


def augment_pairs_as_text(pairs: list[FieldValue]) -> str:
    return " | ".join(f"{p.field}: {p.value}" for p in pairs)
