# GLiNER Fine-tuning — Domaine assurance CH

Fine-tune `urchade/gliner_multi_pii-v1` sur les données PII assurance suisse (FR/DE/IT/EN).

## Prérequis

- Machine avec GPU (RTX 4080 recommandé, ~12 GB VRAM)
- Dataset source dans `/home/dne/Documents/anonymise/finetune/data/`

## Étapes

### 1. Préparer le dataset

```bash
pip install gliner[train]
python finetune_gliner/prepare_dataset.py
# Output: finetune_gliner/data/train.jsonl + val.jsonl
```

### 2. Vérifier le format

```bash
head -2 finetune_gliner/data/train.jsonl
# Expected: {"tokenized_text": ["nom", ":", "David", "Neri"], "ner": [[2, 3, "PERSON"]]}
```

### 3. Lancer le training (~2-4h sur RTX 4080)

```bash
python finetune_gliner/train.py
```

Surveille la `eval_loss` : doit converger vers < 0.2 après epoch 2.

### 4. Activer le modèle fine-tuné

Une fois le training terminé, mettre dans `.env` du gateway :

```
GLINER_MODEL=/chemin/absolu/vers/finetune_gliner/models/gliner-pii-ch
```

Relancer `docker compose up gateway --build`.

## Format du dataset GLiNER

```json
{"tokenized_text": ["David", "Neri", "a", "un", "sinistre"], "ner": [[0, 1, "PERSON"]]}
```

- `ner` : liste de `[start_token_idx, end_token_idx, label]` (indices inclusifs)
- Labels : `PERSON`, `EMAIL`, `PHONE`, `DATE`, `ADDRESS`, `LICENSE_PLATE`, `ID`, `PII`

## Entraînement Qwen (classification de colonnes — qwen3-pii)

En plus de GLiNER (NER), ce dossier entraîne aussi le classifieur de colonnes
`qwen3-pii` (couche 4 du pipeline, voir `MODEL.md`) à partir des **mêmes datasets**.

```
prepare_dataset.py      → data/        (base)
generate_swiss_data.py  → data_swiss/  (PII assurance CH)
merge_datasets.py       → data_merged/ (fusion = dataset d'entraînement Qwen)
```

```bash
pip install transformers torch accelerate datasets

# Fine-tune Qwen2.5-0.5B-Instruct sur data_merged/ (~RTX 4090, ~1h)
python finetune_gliner/train_qwen.py
# → sortie : finetune_gliner/models/qwen-pii-ch/
```

Puis convertir en GGUF et importer dans Ollama (`ollama create qwen3-pii -f Modelfile`),
et activer via `OLLAMA_MODEL=qwen3-pii` dans le `.env`. Détails dans `MODEL.md` (Option 3).
