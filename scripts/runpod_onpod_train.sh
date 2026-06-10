#!/usr/bin/env bash
# Exécuté SUR le pod RunPod. Argument: MODE = gliner | qwen
# Stratégie : fast-fail sur une tranche de 200 lignes (rejoue le vrai script),
# puis run complet 3 epochs, puis prune checkpoints + tar du modèle.
set -euo pipefail

MODE="${1:?usage: runpod_onpod_train.sh <gliner|qwen>}"
WORK=/workspace/finetune_gliner
cd "$WORK"

echo "[$(date +%T)] === Setup deps ($MODE) ==="
export PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_DISABLE_TELEMETRY=1
# numpy<2 : compat avec le torch de l'image. Deps spécifiques par modèle.
pip install -q --no-cache-dir "numpy<2"
if [ "$MODE" = "gliner" ]; then
  # GLiNER2 (fastino) — librairie + Trainer dédiés.
  pip install -q --no-cache-dir "gliner2[local]"
else
  pip install -q --no-cache-dir "transformers==4.46.3" "accelerate>=1.1.0,<1.3" "datasets<3.2"
fi

# Tranche de test (dataset selon le mode)
mkdir -p /tmp/tiny
if [ "$MODE" = "gliner" ]; then
  head -n 200 data_merged/train.jsonl > /tmp/tiny/train.jsonl
  head -n 60  data_merged/val.jsonl   > /tmp/tiny/val.jsonl
else
  head -n 200 data_columns/train.jsonl > /tmp/tiny/train.jsonl
  head -n 60  data_columns/val.jsonl   > /tmp/tiny/val.jsonl
fi

echo "[$(date +%T)] === FAST-FAIL ($MODE, 200 lignes) ==="
if [ "$MODE" = "gliner" ]; then
  python train_gliner2.py --data-dir /tmp/tiny --output-dir /tmp/dry_gliner2 --epochs 1
else
  python train_qwen_columns.py --data-dir /tmp/tiny --output-dir /tmp/dry_qwen
fi
echo "[$(date +%T)] === FAST-FAIL OK -> run complet ==="

if [ "$MODE" = "gliner" ]; then
  python train_gliner2.py
  MODELDIR="models/gliner2-pii-ch"
else
  python train_qwen_columns.py
  MODELDIR="models/qwen-pii-ch"
fi

echo "[$(date +%T)] === Prune checkpoints + archive ==="
rm -rf "$MODELDIR"/checkpoint-* || true
# Capture les versions exactes (pour reproduire le chargement en local/sidecar)
pip freeze 2>/dev/null | grep -iE "^(transformers|tokenizers|gliner|gliner2|torch|peft|numpy)==" > "$MODELDIR/VERSIONS.txt" || true
echo "--- VERSIONS ---"; cat "$MODELDIR/VERSIONS.txt" || true
tar czf "/workspace/model_${MODE}.tar.gz" -C models "$(basename "$MODELDIR")"
ls -lh "/workspace/model_${MODE}.tar.gz"
echo "[$(date +%T)] === DONE ${MODE} ==="
