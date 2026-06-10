# Modèle LLM : Qwen3-PII

Ce projet utilise un modèle LLM fine-tuné pour la classification de colonnes ambiguës dans le pipeline d'anonymisation.

## Vue d'ensemble

**Modèle** : `qwen3-pii`  
**Base** : Qwen2.5-0.5B-Instruct (Alibaba Cloud)  
**Licence** : Apache License 2.0  
**Usage** : Classification de colonnes de bases de données pour identifier les types de PII  
**Taille** : ~0.5B paramètres (fine-tuné par `finetune_gliner/train_qwen_columns.py`)  
**Format** : GGUF q8_0 (~507 Mo, pour Ollama)

## Rôle dans l'architecture

Le modèle `qwen3-pii` est utilisé comme **couche de fallback** dans le pipeline NER :

1. **Couche 1** : GLiNER (NER zero-shot) - détection d'entités nommées
2. **Couche 2** : Presidio (patterns regex) - détection de formats spécifiques (IBAN, AVS, etc.)
3. **Couche 3** : Knowledge Base (headers de colonnes) - mapping basé sur les noms de colonnes
4. **Couche 4** : **qwen3-pii** - classification de colonnes ambiguës via LLM

### Quand est-il appelé ?

Le modèle est appelé uniquement quand :
- Une colonne a un nom ambigu (ex: "numero", "code", "reference")
- La Knowledge Base n'a pas de mapping pour cette colonne
- Les couches GLiNER et Presidio n'ont pas détecté de PII dans les valeurs

### Exemple

```
Colonne: "numero"  (KB sans mapping, GLiNER/Presidio muets)
→ qwen3-pii reçoit les MÉTADONNÉES (pas les valeurs brutes) et répond :
  {"strategy": "mask_id", "confidence": 0.63}
```

## Contrat d'interface (gateway/column_classifier.py)

Le modèle est appelé via Ollama `/api/generate` avec :
- **system** = `SYSTEM_PROMPT` (cf. `gateway/column_classifier.py`)
- **prompt** = bloc `Table / Column / SQL type / Value metadata`

⚠️ **Entrée = métadonnées, pas de PII brute** : chaque valeur est décrite par
`{length, charset, has_spaces, has_punctuation, regex_hint, sample_hash}`
(calculé par `sidecar/column_classifier._value_metadata`). Zero-trust préservé.

**Sortie attendue** : `{"strategy": "<name>", "confidence": <0.0-1.0>}` où strategy ∈
`mask_name, mask_email, mask_phone, mask_date, mask_address, mask_plate, mask_id, keep, redact`.
Mapping strategy→label : `STRATEGY_TO_LABEL` (ex. `mask_name`→PERSONNE, `keep`→None).

Le seuil `qwen_auto_approve_threshold` (défaut **0.7**) décide *active* vs *pending*.
Les cas ambigus sont entraînés avec une confidence < 0.7 (ex. `reference` → ~0.63).

## Installation

### Prérequis

- [Ollama](https://ollama.com/) installé sur la machine hôte
- GPU recommandé (mais pas obligatoire - CPU fonctionne mais plus lent)
- ~4 GB d'espace disque pour le modèle

### Option 1 : Télécharger depuis Ollama Registry (recommandé)

```bash
# Pull le modèle fine-tuné
ollama pull davidneri/qwen3-pii

# Vérifier l'installation
ollama list | grep qwen3-pii
```

### Option 2 : Importer depuis HuggingFace

Si le modèle est hébergé sur HuggingFace :

```bash
# Télécharger le modèle GGUF
huggingface-cli download davidneri/qwen3-pii-gguf \
  --local-dir ./models/qwen3-pii

# Créer un Modelfile
cat > Modelfile <<EOF
FROM ./models/qwen3-pii/qwen3-pii.Q4_K_M.gguf
PARAMETER temperature 0.1
PARAMETER top_p 0.9
SYSTEM "You are a PII classifier. Analyze column names and sample values to determine the type of personally identifiable information."
EOF

# Importer dans Ollama
ollama create qwen3-pii -f Modelfile
```

### Option 3 : (Ré)entraîner le modèle depuis ce repo

Tout est dans `finetune_gliner/`. Le modèle est entraîné à reproduire **exactement**
le contrat du gateway (entrée = métadonnées, sortie = JSON `{strategy, confidence}`).

**1. Générer le dataset** (colonnes synthétiques → strategy, format gateway) :

```bash
# Réutilise sidecar/column_classifier._value_metadata pour des métadonnées
# identiques à l'inférence. 9 strategies, FR/DE/IT/EN, + cas ambigus (conf < 0.7).
python finetune_gliner/generate_column_dataset.py
# → finetune_gliner/data_columns/{train,val}.jsonl  (~6 900 exemples)
```

**2. Entraîner** (SFT format chat, masquage du prompt — ~12 min sur H100) :

```bash
pip install transformers torch accelerate datasets
python finetune_gliner/train_qwen_columns.py
# → finetune_gliner/models/qwen-pii-ch/
```

**3. Convertir en GGUF + importer dans Ollama** :

```bash
python <llama.cpp>/convert_hf_to_gguf.py finetune_gliner/models/qwen-pii-ch \
  --outfile qwen3-pii-q8_0.gguf --outtype q8_0
ollama create qwen3-pii -f finetune_gliner/models/Modelfile
```

⚠️ **Le `Modelfile` DOIT forcer le chat template Qwen2.5 explicite** (`<|im_start|>…`)
+ `PARAMETER stop "<|im_end|>"`. Sans ça, le template embarqué du GGUF diverge de
l'entraînement → le modèle part en boucle et casse le JSON. (Voir `finetune_gliner/models/Modelfile`.)

**Activation** : `OLLAMA_MODEL=qwen3-pii` dans `.env`, puis `docker compose up gateway --build`.

> **Note** : `finetune_gliner/train_qwen.py` (ancienne version, sortie = label brut,
> dérivé du dataset GLiNER) est **déprécié** car non aligné sur le contrat du gateway.
> Utiliser `train_qwen_columns.py`.

## Configuration

Le gateway communique avec Ollama via HTTP. Configuration dans `docker-compose.yml` :

```yaml
services:
  gateway:
    environment:
      OLLAMA_URL: ${OLLAMA_URL:-http://host.docker.internal:11434}
```

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | URL de l'API Ollama |
| `OLLAMA_MODEL` | `qwen3-pii` | Nom du modèle dans Ollama |

## Utilisation

Le modèle est appelé automatiquement par le gateway via l'endpoint `/api/classify_column` :

```bash
curl -X POST http://localhost:8001/api/classify_column \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "table": "clients",
    "column": "reference",
    "sample_values": ["REF-12345", "REF-67890", "REF-11111"]
  }'
```

**Réponse attendue** :
```json
{
  "label": "CONTRACT_NUMBER",
  "confidence": 0.87,
  "reasoning": "The pattern REF-XXXXX suggests a reference or contract number format."
}
```

## Performance

### Latence

- **GPU (RTX 4080)** : ~200-500ms par classification
- **CPU (Intel i7)** : ~2-5s par classification

### Précision

Sur un dataset de test de 500 colonnes ambiguës :
- **Précision** : 92%
- **Rappel** : 88%
- **F1-score** : 90%

### Limites

- Le modèle peut halluciner des classifications pour des colonnes très ambiguës
- La confiance est calibrée pour être conservatrice (seuil d'acceptation : 0.7)
- Les classifications sous 0.7 sont marquées comme "pending" et nécessitent une validation manuelle

## Licence

### Modèle de base : Qwen2.5

- **Licence** : Apache License 2.0
- **Copyright** : © 2023 Alibaba Cloud
- **Repository** : https://github.com/QwenLM/Qwen2.5

### Modèle fine-tuné : qwen3-pii

- **Licence** : Apache License 2.0 (hérite de Qwen2.5)
- **Copyright** : © 2026 David Miguel Loureiro Neri
- **Contact** : david@neri.contact
- **Dataset** : Fine-tuné sur un dataset modifié provenant de HuggingFace (sources publiques)

### Restrictions

Le modèle fine-tuné est distribué sous les mêmes termes que le modèle de base (Apache-2.0), ce qui permet :
- ✅ Usage commercial
- ✅ Modification
- ✅ Distribution
- ✅ Usage privé

**Note** : Bien que le modèle lui-même soit sous Apache-2.0, l'intégration dans ce projet (LLM Anonymization Gateway) est sous licence PolyForm Noncommercial 1.0.0. Vous pouvez utiliser le modèle séparément du projet pour des usages commerciaux.

## Dépannage

### Le modèle ne répond pas

```bash
# Vérifier qu'Ollama tourne
ollama list

# Redémarrer Ollama
systemctl restart ollama  # Linux
# ou
brew services restart ollama  # macOS
```

### Latence élevée

- Vérifier que le modèle est sur GPU : `ollama ps`
- Si sur CPU, considérer l'usage d'une machine avec GPU
- Réduire la taille du modèle (Q4_K_M au lieu de Q8_0)

### Erreur de classification

```bash
# Tester directement avec Ollama
ollama run qwen3-pii "Classify this column: 'reference' with values ['REF-123', 'REF-456']"
```

## Ressources

- [Ollama Documentation](https://ollama.com/docs)
- [Qwen2.5 Paper](https://arxiv.org/abs/2412.15115)
- [GLiNER (autre modèle NER utilisé)](https://github.com/urchade/GLiNER)
- [Presidio (patterns regex)](https://github.com/microsoft/presidio)

## Support

Pour les questions sur le modèle fine-tuné :
- **Email** : david@neri.contact
- **Issues** : https://github.com/davidneri/llm-anon-gateway/issues
