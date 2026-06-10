"""[EXPÉRIMENTAL / PARKÉ] Fine-tune GLiNER2 (fastino) pour la PII assurance CH.

⚠️ Ne converge PAS sur ce dataset : le modèle de base fastino/gliner2-privacy-filter-PII-multi
est déjà bon en PII zero-shot, et l'entraîner sur data_merged le fait COLLAPSE (tous les
labels renvoient toutes les spans), dès quelques centaines de steps. Bugs réglés en route
(eval_strategy, fp16->bf16 NaN, labels négatifs) mais le collapse persiste. Conclusion :
ne pas fine-tuner ; le sidecar garde urchade/gliner_multi_pii-v1. Script conservé pour
référence / reprise éventuelle. Voir aussi le benchmark zero-shot (urchade ≈ fastino base).

Fine-tune GLiNER2 (fastino) pour la PII assurance CH (FR/DE/IT/EN).

Part du checkpoint privacy-filter PII multilingue et le spécialise sur le dataset
suisse. Convertit le format urchade ({tokenized_text, ner}) vers le format GLiNER2
({input, output:{entities:{label:[mentions]}}}).

Prérequis : pip install "gliner2[local]"
Usage (H100, ~30 min) :
    python finetune_gliner/train_gliner2.py
Output :
    finetune_gliner/models/gliner2-pii-ch/
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# Schéma fixe de labels. CHAQUE exemple liste TOUS ces labels (listes vides pour
# les absents) => supervision négative, sinon le modèle collapse (prédit tout
# pour tous les labels). PII (catch-all) exclu pour ne pas tout absorber.
G2_LABELS = ["PERSON", "EMAIL", "PHONE", "DATE", "ADDRESS",
             "LICENSE_PLATE", "ID", "IBAN", "CONTRACT", "POLICY"]


def to_gliner2(examples: list[dict]) -> list[dict]:
    """urchade {tokenized_text, ner:[[start,end,label]]} -> GLiNER2 {input, output}.

    Les indices ner sont inclusifs (cf. finetune_gliner/README.md). Chaque exemple
    inclut tout G2_LABELS (négatifs = listes vides).
    """
    out: list[dict] = []
    for ex in examples:
        toks = ex.get("tokenized_text", [])
        spans = ex.get("ner", [])
        if not toks:
            continue
        text = " ".join(toks)
        entities: dict[str, list[str]] = {lab: [] for lab in G2_LABELS}
        for span in spans:
            if len(span) >= 3:
                a, b, label = int(span[0]), int(span[1]), span[2]
                if label in entities:
                    entities[label].append(" ".join(toks[a : b + 1]))
        out.append({"input": text, "output": {"entities": entities}})
    return out


def write_jsonl(rows: list[dict], path: Path) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune GLiNER2 sur PII assurance CH")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data_merged")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "models" / "gliner2-pii-ch")
    parser.add_argument("--base", default="fastino/gliner2-privacy-filter-PII-multi")
    parser.add_argument("--epochs", type=int, default=1)  # 3 epochs => collapse, 1 => discrimine
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    OUT = args.output_dir
    OUT.mkdir(parents=True, exist_ok=True)

    print("Conversion des datasets au format GLiNER2…")
    train_rows = to_gliner2(load_jsonl(args.data_dir / "train.jsonl"))
    val_rows = to_gliner2(load_jsonl(args.data_dir / "val.jsonl"))
    train_path = write_jsonl(train_rows, OUT / "_train_g2.jsonl")
    val_path = write_jsonl(val_rows, OUT / "_val_g2.jsonl")
    print(f"Train: {len(train_rows)} | Val: {len(val_rows)}")

    from gliner2 import GLiNER2
    from gliner2.training.trainer import GLiNER2Trainer, TrainingConfig

    print(f"Chargement du modèle de base {args.base}…")
    model = GLiNER2.from_pretrained(args.base)

    config = TrainingConfig(
        output_dir=str(OUT),
        experiment_name="gliner2-pii-ch",
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        # bf16 (pas fp16) : mDeBERTa-v3 overflow en fp16 -> loss=NaN. bf16 stable sur H100.
        fp16=False,
        bf16=True,
        save_best=True,
        metric_for_best="eval_loss",
        logging_steps=50,
    )
    trainer = GLiNER2Trainer(model, config)
    trainer.train(train_data=str(train_path), eval_data=str(val_path))

    print(f"Sauvegarde du modèle → {OUT}")
    model.save_pretrained(str(OUT))

    # SELF-TEST sur env cohérent (le pod) : recharger + vérifier la discrimination
    # des labels. Si chaque label renvoie toutes les spans => modèle/format cassé.
    print("\n=== SELF-TEST (reload + extract_entities) ===")
    try:
        reloaded = GLiNER2.from_pretrained(str(OUT))
        txt = "David Dupont habite Rue du Lac 12 à Genève. IBAN CH9300762011623852957."
        res = reloaded.extract_entities(txt, ["PERSON", "ADDRESS", "IBAN", "EMAIL", "PHONE"])
        print("SELFTEST_OUT:", json.dumps(res, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print("SELFTEST_ERROR:", repr(e))

    print(f"\n✅ GLiNER2 fine-tuné → {OUT}")
    print(f"Pour l'utiliser dans le sidecar : GLiNER_MODEL={OUT.resolve()} (charger via gliner2.GLiNER2)")


if __name__ == "__main__":
    main()
