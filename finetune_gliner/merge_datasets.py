#!/usr/bin/env python3
"""Fusionne le dataset international existant avec les données suisses synthétiques.

Usage:
    python finetune_gliner/merge_datasets.py

Input:
    - finetune_gliner/data/train.jsonl (international)
    - finetune_gliner/data_swiss/train.jsonl (suisse)

Output:
    - finetune_gliner/data_merged/train.jsonl
    - finetune_gliner/data_merged/val.jsonl
"""
from __future__ import annotations
import json
import random
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).parent / "data"
DATA_SWISS_DIR = Path(__file__).parent / "data_swiss"
OUT_DIR = Path(__file__).parent / "data_merged"


def load_jsonl(path: Path) -> list[dict]:
    """Charge un fichier JSONL."""
    if not path.exists():
        print(f"  ⚠ Fichier non trouvé: {path}")
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_jsonl(examples: list[dict], path: Path) -> int:
    """Sauvegarde un dataset en JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    return len(examples)


def main():
    print("Fusion des datasets international + suisse...")
    
    # Charger les datasets
    print("\n1. Chargement des datasets:")
    intl_train = load_jsonl(DATA_DIR / "train.jsonl")
    intl_val = load_jsonl(DATA_DIR / "val.jsonl")
    swiss_train = load_jsonl(DATA_SWISS_DIR / "train.jsonl")
    swiss_val = load_jsonl(DATA_SWISS_DIR / "val.jsonl")
    
    print(f"  International: {len(intl_train)} train + {len(intl_val)} val")
    print(f"  Suisse: {len(swiss_train)} train + {len(swiss_val)} val")
    
    # Fusionner
    print("\n2. Fusion:")
    merged_train = intl_train + swiss_train
    merged_val = intl_val + swiss_val
    
    # Mélanger
    random.shuffle(merged_train)
    random.shuffle(merged_val)
    
    print(f"  Train fusionné: {len(merged_train)} exemples")
    print(f"  Val fusionné: {len(merged_val)} exemples")
    
    # Statistiques par label
    print("\n3. Distribution des labels:")
    label_counts = {}
    for ex in merged_train:
        for span in ex.get("ner", []):
            label = span[2] if len(span) > 2 else "UNKNOWN"
            label_counts[label] = label_counts.get(label, 0) + 1
    
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        pct = count / len(merged_train) * 100
        print(f"  {label:20s}: {count:5d} ({pct:5.1f}%)")
    
    # Sauvegarder
    print("\n4. Sauvegarde:")
    train_path = OUT_DIR / "train.jsonl"
    val_path = OUT_DIR / "val.jsonl"
    
    n_train = save_jsonl(merged_train, train_path)
    n_val = save_jsonl(merged_val, val_path)
    
    print(f"  Train: {n_train} exemples → {train_path}")
    print(f"  Val: {n_val} exemples → {val_path}")
    
    print("\n✅ Fusion terminée!")
    print(f"\nProchaine étape: Ré-entraîner le modèle avec")
    print(f"  python finetune_gliner/train.py --data-dir finetune_gliner/data_merged")


if __name__ == "__main__":
    main()
