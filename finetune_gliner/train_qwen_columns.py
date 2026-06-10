"""Fine-tune Qwen2.5-0.5B-Instruct comme classeur de colonnes du GATEWAY.

Aligné sur gateway/column_classifier.py :
- format chat : system = SYSTEM_PROMPT (verbatim), user = bloc "Table/Column/SQL type/Value metadata"
- cible = JSON {"strategy": ..., "confidence": ...}
On masque le prompt (labels=-100) : le modèle n'apprend QUE la réponse JSON.

Prérequis : transformers, accelerate, datasets, torch
Usage : python finetune_gliner/train_qwen_columns.py
Output : finetune_gliner/models/qwen-pii-ch/
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
from datasets import Dataset

# COPIE VERBATIM de gateway/column_classifier.py SYSTEM_PROMPT — garder synchronisé.
SYSTEM_PROMPT = (
    "You are a PII column classifier. "
    "Given a database table name, column name, SQL type, and value metadata, "
    "return the single anonymization strategy that applies to this column. "
    "Value metadata describes sample values WITHOUT revealing the actual content. "
    "Each metadata entry contains: length (character count), charset (digits/alpha/alphanum/mixed/email/url), "
    "has_spaces (boolean), has_punctuation (boolean), regex_hint (matched pattern like iban_ch/avs/phone/email/date/none), "
    "and sample_hash (first 8 chars of SHA-256, for debugging only). "
    "Use the patterns in the metadata to infer the data type. For example: "
    "13 digits with regex_hint=avs indicates a Swiss AVS number; "
    "length ~35 with charset=alphanum and regex_hint=iban_ch indicates a Swiss IBAN; "
    "charset=email with regex_hint=email indicates an email address; "
    "charset=digits with regex_hint=phone indicates a phone number; "
    "charset=mixed with has_spaces=true and has_punctuation=true may indicate a name or address. "
    "Reply with ONLY a JSON object (no prose, no markdown fences) of the form: "
    '{"strategy": "<name>", "confidence": <0.0-1.0>}. '
    "The strategy must be exactly one of: mask_name, mask_email, mask_phone, "
    "mask_date, mask_address, mask_plate, mask_id, keep, redact. "
    "Confidence is your subjective certainty between 0.0 and 1.0."
)
MAX_LEN = 640


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data_columns")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "models" / "qwen-pii-ch")
    args = parser.parse_args()

    BASE = "Qwen/Qwen2.5-0.5B-Instruct"
    tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(BASE, trust_remote_code=True, device_map="auto")

    def encode(ex: dict) -> dict:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": ex["user"]}]
        prompt_ids = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True)
        answer_ids = tok(ex["assistant"], add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
        input_ids = (prompt_ids + answer_ids)[:MAX_LEN]
        labels = ([-100] * len(prompt_ids) + answer_ids)[:MAX_LEN]
        attn = [1] * len(input_ids)
        pad = MAX_LEN - len(input_ids)
        input_ids += [tok.pad_token_id] * pad
        labels += [-100] * pad
        attn += [0] * pad
        return {"input_ids": input_ids, "attention_mask": attn, "labels": labels}

    train_raw = load_jsonl(args.data_dir / "train.jsonl")
    val_raw = load_jsonl(args.data_dir / "val.jsonl")
    print(f"Train: {len(train_raw)} | Val: {len(val_raw)}")
    train_ds = Dataset.from_list(train_raw).map(encode, remove_columns=["user", "assistant"])
    val_ds = Dataset.from_list(val_raw).map(encode, remove_columns=["user", "assistant"])

    targs = TrainingArguments(
        output_dir=str(args.output_dir),
        learning_rate=2e-5,
        weight_decay=0.01,
        num_train_epochs=3,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=16,
        gradient_accumulation_steps=2,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        fp16=True,
        logging_steps=20,
        save_total_limit=1,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train_ds, eval_dataset=val_ds)
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tok.save_pretrained(str(args.output_dir))
    print(f"\n✅ Modèle sauvegardé → {args.output_dir}")


if __name__ == "__main__":
    main()
