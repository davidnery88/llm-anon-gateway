"""[DÉPRÉCIÉ] — utiliser train_qwen_columns.py à la place.

Ce script produit un modèle qui sort un LABEL BRUT à partir d'un prompt français,
ce qui NE correspond PAS au contrat du gateway (gateway/column_classifier.py attend
du JSON {"strategy","confidence"} à partir de MÉTADONNÉES). Conservé pour référence
historique uniquement. Voir MODEL.md (Option 3) et train_qwen_columns.py.

Fine-tune Qwen2.5-0.5B-Instruct pour classification PII de colonnes.

Prérequis : pip install transformers torch accelerate datasets
Usage (RTX 4090, ~1h) :
    python finetune_gliner/train_qwen.py

Output:
    finetune_gliner/models/qwen-pii-ch/   ← modèle fine-tuné
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
from datasets import Dataset


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def convert_to_qwen_format(examples: list[dict]) -> list[dict]:
    """Convertit les exemples GLiNER au format Qwen pour classification."""
    qwen_examples = []
    
    for ex in examples:
        tokens = ex.get("tokenized_text", [])
        ner_spans = ex.get("ner", [])
        
        if not ner_spans:
            continue
        
        # Reconstruire le texte
        text = " ".join(tokens)
        
        # Extraire le nom de colonne et la valeur
        # Format attendu: "colonne: valeur" ou "La colonne X contient Y"
        if ":" in text:
            parts = text.split(":", 1)
            column_name = parts[0].strip()
            value = parts[1].strip()
        else:
            continue
        
        # Extraire le label PII
        for span in ner_spans:
            if len(span) >= 3:
                label = span[2]
                
                # Convertir en prompt de classification
                prompt = f"""Classifie cette colonne de base de données.

Nom de colonne: {column_name}
Exemple de valeur: {value}

Quel type de donnée personnelle (PII) cette colonne contient-elle?

Réponds uniquement avec UN de ces labels:
- PERSON: nom de personne
- EMAIL: adresse email
- PHONE: numéro de téléphone
- DATE: date
- ADDRESS: adresse postale
- ID: identifiant (AVS, numéro client, etc.)
- LICENSE_PLATE: plaque d'immatriculation
- IBAN: numéro de compte bancaire
- CONTRACT: numéro de contrat
- POLICY: numéro de police d'assurance
- PII: autre donnée personnelle
- NONE: pas de donnée personnelle

Label:"""
                
                qwen_examples.append({
                    "text": prompt + " " + label,
                    "prompt": prompt,
                    "label": label
                })
                break  # Un seul label par exemple
    
    return qwen_examples


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune Qwen pour classification PII")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent / "data_merged",
        help="Répertoire contenant train.jsonl et val.jsonl (défaut: data_merged)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "models" / "qwen-pii-ch",
        help="Répertoire de sortie du modèle"
    )
    args = parser.parse_args()

    DATA_DIR = args.data_dir
    OUT_DIR = args.output_dir
    BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"

    print(f"Chargement du modèle {BASE_MODEL}...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    # Charger en fp32 : avec fp16=True (AMP), charger le modèle directement en
    # float16 provoque "Attempting to unscale FP16 gradients" au 1er step.
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        trust_remote_code=True,
        device_map="auto"
    )
    
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print("Chargement des données...")
    train_data_raw = load_jsonl(DATA_DIR / "train.jsonl")
    val_data_raw = load_jsonl(DATA_DIR / "val.jsonl")
    
    print("Conversion au format Qwen...")
    train_data = convert_to_qwen_format(train_data_raw)
    val_data = convert_to_qwen_format(val_data_raw)
    
    print(f"Train: {len(train_data)} | Val: {len(val_data)}")
    
    # Tokenisation
    def tokenize_function(examples):
        tokenized = tokenizer(
            examples["text"],
            padding="max_length",
            truncation=True,
            max_length=512,
            return_tensors="pt"
        )
        tokenized["labels"] = tokenized["input_ids"].clone()
        return tokenized
    
    train_dataset = Dataset.from_list(train_data)
    val_dataset = Dataset.from_list(val_data)

    # remove_columns : retire les colonnes string (text/prompt/label) après
    # tokenisation, sinon le data collator tente de les convertir en tenseur
    # → ValueError: too many dimensions 'str'.
    train_dataset = train_dataset.map(
        tokenize_function, batched=True, remove_columns=train_dataset.column_names
    )
    val_dataset = val_dataset.map(
        tokenize_function, batched=True, remove_columns=val_dataset.column_names
    )

    print("Configuration de l'entraînement...")
    training_args = TrainingArguments(
        output_dir=str(OUT_DIR),
        learning_rate=3e-5,
        weight_decay=0.01,
        num_train_epochs=3,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=16,
        gradient_accumulation_steps=2,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        fp16=True,
        logging_steps=10,
        save_total_limit=2,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
    )

    print("Démarrage de l'entraînement...")
    trainer.train()
    
    print(f"Sauvegarde du modèle → {OUT_DIR}")
    trainer.save_model(str(OUT_DIR))
    tokenizer.save_pretrained(str(OUT_DIR))
    
    print(f"\n✅ Modèle sauvegardé → {OUT_DIR}")
    print(f"\nPour utiliser le modèle fine-tuné:")
    print(f"  model = AutoModelForCausalLM.from_pretrained('{OUT_DIR.resolve()}')")
    print(f"  tokenizer = AutoTokenizer.from_pretrained('{OUT_DIR.resolve()}')")


if __name__ == "__main__":
    main()
