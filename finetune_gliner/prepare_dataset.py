"""Convertit le dataset PII colonne-classification → format GLiNER span NER.

Usage:
    python finetune_gliner/prepare_dataset.py

Input (anonymise/finetune/data/train.jsonl):
  {"table": "clients", "column": "nom", "values": ["David Neri"], "strategy": "mask_name"}

Output (finetune_gliner/data/train.jsonl):
  {"tokenized_text": ["nom", ":", "David", "Neri"], "ner": [[2, 3, "PERSON"]]}
"""
from __future__ import annotations
import json
import re
from pathlib import Path

SRC_DIR = Path("/home/dne/Documents/anonymise/finetune/data")
OUT_DIR = Path(__file__).parent / "data"

STRATEGY_TO_LABEL = {
    "mask_name": "PERSON",
    "mask_email": "EMAIL",
    "mask_phone": "PHONE",
    "mask_date": "DATE",
    "mask_address": "ADDRESS",
    "mask_plate": "LICENSE_PLATE",
    "mask_id": "ID",
    "redact": "PII",
}

TEMPLATES = [
    "{field}: {value}",
    "La colonne {field} contient {value}.",
    "{value} est la valeur de {field}.",
]


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", text)


def find_span(tokens: list[str], value_tokens: list[str]) -> tuple[int, int] | None:
    for i in range(len(tokens) - len(value_tokens) + 1):
        if tokens[i:i + len(value_tokens)] == value_tokens:
            return i, i + len(value_tokens) - 1
    return None


def convert_example(ex: dict, template: str) -> dict | None:
    label = STRATEGY_TO_LABEL.get(ex.get("strategy", ""))
    if label is None:
        return None
    for value in ex.get("values", [])[:3]:
        text = template.format(field=ex.get("column", "col"), value=value)
        tokens = tokenize(text)
        value_tokens = tokenize(value)
        span = find_span(tokens, value_tokens)
        if span:
            return {"tokenized_text": tokens, "ner": [list(span) + [label]]}
    return None


def convert_split(src: Path, dst: Path, templates: list[str]) -> int:
    if not src.exists():
        print(f"Source not found: {src}")
        return 0
    examples = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    with dst.open("w", encoding="utf-8") as f:
        for ex in examples:
            for tmpl in templates:
                converted = convert_example(ex, tmpl)
                if converted:
                    f.write(json.dumps(converted, ensure_ascii=False) + "\n")
                    count += 1
                    break
    return count


if __name__ == "__main__":
    for split in ("train", "val"):
        src = SRC_DIR / f"{split}.jsonl"
        dst = OUT_DIR / f"{split}.jsonl"
        n = convert_split(src, dst, TEMPLATES)
        print(f"{split}: {n} examples → {dst}")
