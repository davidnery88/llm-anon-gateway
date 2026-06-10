"""Fine-tune gliner_multi_pii-v1 sur le dataset assurance CH + formats suisses.

Prérequis : pip install gliner[train]
Usage (RTX 5090, ~45min) :
    python finetune_gliner/train.py                          # utilise data_merged (international + suisse)
    python finetune_gliner/train.py --data-dir finetune_gliner/data  # utilise uniquement l'international

Output:
    finetune_gliner/models/gliner-pii-ch-swiss/   ← modèle fine-tuné
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune GLiNER sur dataset PII")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent / "data_merged",
        help="Répertoire contenant train.jsonl et val.jsonl (défaut: data_merged)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Répertoire de sortie du modèle (défaut: auto selon data-dir)"
    )
    args = parser.parse_args()

    DATA_DIR = args.data_dir
    if args.output_dir:
        OUT_DIR = args.output_dir
    elif "merged" in str(DATA_DIR):
        OUT_DIR = Path(__file__).parent / "models" / "gliner-pii-ch-swiss"
    else:
        OUT_DIR = Path(__file__).parent / "models" / "gliner-pii-ch"
    
    # GLiNER pour NER PII
    BASE_MODEL = "urchade/gliner_multi_pii-v1"

    from gliner import GLiNER
    from gliner.training import TrainingArguments

    train_data = load_jsonl(DATA_DIR / "train.jsonl")
    val_data = load_jsonl(DATA_DIR / "val.jsonl")
    print(f"Dataset: {DATA_DIR}")
    print(f"Train: {len(train_data)} | Val: {len(val_data)}")

    model = GLiNER.from_pretrained(BASE_MODEL)

    args_train = TrainingArguments(
        output_dir=str(OUT_DIR),
        learning_rate=3e-5,
        weight_decay=0.01,
        num_train_epochs=3,
        per_device_train_batch_size=32,
        per_device_eval_batch_size=32,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        fp16=True,
    )

    # train_model() construit en interne le DataCollator GLiNER (prepare_labels=True)
    # adapté au format {tokenized_text, ner}. Le Trainer transformers brut planterait
    # (collator par défaut → "too many dimensions 'str'").
    model.train_model(
        train_dataset=train_data,
        eval_dataset=val_data,
        training_args=args_train,
        output_dir=str(OUT_DIR),
    )
    model.save_pretrained(str(OUT_DIR))
    print(f"\n✅ Modèle sauvegardé → {OUT_DIR}")
    print(f"\nPour utiliser le modèle fine-tuné, mettre dans .env :")
    print(f"  GLINER_MODEL={OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
