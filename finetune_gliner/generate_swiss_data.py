#!/usr/bin/env python3
"""Générateur de données synthétiques suisses pour fine-tuning NER.

Génère des exemples réalistes pour les formats suisses spécifiques :
- Numéros AVS (756.XXXX.XXXX.XX)
- IBAN suisses (CHXX XXXX XXXX XXXX XXXX X)
- Plaques d'immatriculation cantonales
- Numéros de téléphone suisses
- Numéros de contrat assurance
- Numéros de police d'assurance

Usage:
    python finetune_gliner/generate_swiss_data.py

Output:
    finetune_gliner/data_swiss/train.jsonl
    finetune_gliner/data_swiss/val.jsonl
"""
from __future__ import annotations
import json
import random
import re
from pathlib import Path

random.seed(42)

OUT_DIR = Path(__file__).parent / "data_swiss"

# Cantons suisses avec codes
CANTONS = {
    "VD": "Vaud", "GE": "Genève", "ZH": "Zürich", "BE": "Bern",
    "VS": "Valais", "FR": "Fribourg", "NE": "Neuchâtel", "JU": "Jura",
    "SO": "Solothurn", "BS": "Basel-Stadt", "BL": "Basel-Landschaft",
    "AG": "Aargau", "TG": "Thurgau", "SG": "St. Gallen", "AR": "Appenzell Ausserrhoden",
    "AI": "Appenzell Innerrhoden", "GR": "Graubünden", "TI": "Ticino",
    "UR": "Uri", "SZ": "Schwyz", "OW": "Obwalden", "NW": "Nidwalden",
    "LU": "Luzern", "ZG": "Zug", "GL": "Glarus", "SH": "Schaffhausen"
}

# Codes bancaires suisses (exemples fictifs)
BANK_CODES = [
    "00230", "00246", "00762", "04835", "08390", "09000", "08401",
    "00221", "08914", "00767", "08705", "00243", "08324", "00228"
]

# Noms suisses réalistes (génériques)
FIRST_NAMES = [
    "Hans", "Peter", "Urs", "Thomas", "Andreas", "Marco", "Daniel", "Christian",
    "Sabine", "Anna", "Maria", "Claudia", "Monica", "Sandra", "Laura", "Elena",
    "Jean", "Pierre", "Michel", "Jacques", "Philippe", "François", "Laurent",
    "Marie", "Sophie", "Isabelle", "Catherine", "Nathalie", "Valérie", "Sylvie",
    "Luca", "Matteo", "Alessandro", "Francesco", "Giulia", "Sara", "Chiara"
]

LAST_NAMES = [
    "Müller", "Meier", "Schmid", "Keller", "Weber", "Huber", "Steiner", "Fischer",
    "Gerber", "Brunner", "Baumann", "Frei", "Zimmermann", "Moser", "Widmer", "Wyss",
    "Dubois", "Martin", "Robert", "Richard", "Petit", "Moreau", "Laurent", "Simon",
    "Bernasconi", "Fontana", "Ferrari", "Romano", "Colombo", "Ricci", "Conti", "Esposito"
]

STREETS = [
    "Rue du Lac", "Route de Genève", "Chemin des Fleurs", "Avenue de la Gare",
    "Bahnhofstrasse", "Dorfstrasse", "Hauptstrasse", "Seestrasse",
    "Via Cantonale", "Piazza Centrale", "Corso San Gottardo", "Viale Stazione"
]

CITIES = [
    "Lausanne", "Genève", "Zürich", "Bern", "Bâle", "Lucerne", "Saint-Gall",
    "Winterthur", "Lugano", "Bienne", "Thoune", "Fribourg", "Neuchâtel",
    "Sion", "Martigny", "Yverdon-les-Bains", "Montreux", "Vevey", "Nyon"
]


def generate_avs() -> str:
    """Génère un numéro AVS suisse valide (756.XXXX.XXXX.XX)."""
    part1 = random.randint(1000, 9999)
    part2 = random.randint(1000, 9999)
    part3 = random.randint(10, 99)
    
    # Variations de format
    fmt = random.choice(["dots", "spaces", "dashes", "plain"])
    if fmt == "dots":
        return f"756.{part1}.{part2}.{part3}"
    elif fmt == "spaces":
        return f"756 {part1} {part2} {part3}"
    elif fmt == "dashes":
        return f"756-{part1}-{part2}-{part3}"
    else:
        return f"756{part1}{part2}{part3}"


def generate_iban_ch() -> str:
    """Génère un IBAN suisse valide (CHXX XXXX XXXX XXXX XXXX X)."""
    check = random.randint(10, 99)
    bank = random.choice(BANK_CODES)
    account = "".join([str(random.randint(0, 9)) for _ in range(12)])
    
    # Format avec espaces tous les 4 caractères
    raw = f"CH{check}{bank}{account}"
    parts = [raw[i:i+4] for i in range(0, len(raw), 4)]
    return " ".join(parts)


def generate_license_plate() -> str:
    """Génère une plaque d'immatriculation suisse (canton + numéro)."""
    canton = random.choice(list(CANTONS.keys()))
    number = random.randint(1, 999999)
    
    # Variations de format
    fmt = random.choice(["space", "dash", "nospace"])
    if fmt == "space":
        return f"{canton} {number}"
    elif fmt == "dash":
        return f"{canton}-{number}"
    else:
        return f"{canton}{number}"


def generate_phone_ch() -> str:
    """Génère un numéro de téléphone suisse (+41 XX XXX XX XX)."""
    prefix = random.choice(["21", "22", "26", "31", "32", "33", "34", "41", "43", "44", "52", "55", "56", "61", "62", "71", "81", "91"])
    number = "".join([str(random.randint(0, 9)) for _ in range(7)])
    
    # Variations de format
    fmt = random.choice(["international", "national", "spaces", "dots"])
    if fmt == "international":
        return f"+41 {prefix} {number[:3]} {number[3:5]} {number[5:]}"
    elif fmt == "national":
        return f"0{prefix} {number[:3]} {number[3:5]} {number[5:]}"
    elif fmt == "spaces":
        return f"+41 {prefix} {number}"
    else:
        return f"+41.{prefix}.{number[:3]}.{number[3:]}"


def generate_contract_number() -> str:
    """Génère un numéro de contrat assurance générique."""
    year = random.randint(2020, 2026)
    number = random.randint(10000, 99999)
    
    # Variations de format
    fmt = random.choice(["dash", "slash", "dot", "nospace"])
    if fmt == "dash":
        return f"ASSU-{year}-{number}"
    elif fmt == "slash":
        return f"ASSU/{year}/{number}"
    elif fmt == "dot":
        return f"ASSU.{year}.{number}"
    else:
        return f"ASSU{year}{number}"


def generate_policy_number() -> str:
    """Génère un numéro de police d'assurance."""
    prefix = random.choice(["POL", "POLICE", "P"])
    number = random.randint(100000, 999999)
    
    fmt = random.choice(["dash", "nospace"])
    if fmt == "dash":
        return f"{prefix}-{number}"
    else:
        return f"{prefix}{number}"


def tokenize(text: str) -> list[str]:
    """Tokenise un texte en mots et ponctuation."""
    return re.findall(r"\w+|[^\w\s]", text)


def find_span(tokens: list[str], value_tokens: list[str]) -> tuple[int, int] | None:
    """Trouve la position d'une valeur dans une liste de tokens."""
    for i in range(len(tokens) - len(value_tokens) + 1):
        if tokens[i:i + len(value_tokens)] == value_tokens:
            return i, i + len(value_tokens) - 1
    return None


def create_example(field: str, value: str, label: str) -> dict | None:
    """Crée un exemple au format GLiNER."""
    templates = [
        f"{field}: {value}",
        f"La colonne {field} contient {value}.",
        f"{value} est la valeur de {field}.",
    ]
    template = random.choice(templates)
    tokens = tokenize(template)
    value_tokens = tokenize(value)
    span = find_span(tokens, value_tokens)
    
    if span:
        return {"tokenized_text": tokens, "ner": [list(span) + [label]]}
    return None


def generate_examples() -> list[dict]:
    """Génère tous les exemples suisses."""
    examples = []
    
    # AVS (500 exemples)
    for _ in range(500):
        field = random.choice(["no_avs", "avs", "numero_avs", "ahv", "sozialversicherungsnummer"])
        value = generate_avs()
        ex = create_example(field, value, "ID")
        if ex:
            examples.append(ex)
    
    # IBAN CH (300 exemples)
    for _ in range(300):
        field = random.choice(["iban", "iban_ch", "compte", "konto", "bank_account"])
        value = generate_iban_ch()
        ex = create_example(field, value, "IBAN")
        if ex:
            examples.append(ex)
    
    # Plaques (200 exemples)
    for _ in range(200):
        field = random.choice(["plaque", "immatriculation", "kennzeichen", "license_plate", "vehicule"])
        value = generate_license_plate()
        ex = create_example(field, value, "LICENSE_PLATE")
        if ex:
            examples.append(ex)
    
    # Téléphones CH (300 exemples)
    for _ in range(300):
        field = random.choice(["telephone", "tel", "phone", "mobil", "natel", "handy"])
        value = generate_phone_ch()
        ex = create_example(field, value, "PHONE")
        if ex:
            examples.append(ex)
    
    # Contrats (400 exemples)
    for _ in range(400):
        field = random.choice(["contrat", "no_contrat", "vertrag", "contract", "police"])
        value = generate_contract_number()
        ex = create_example(field, value, "CONTRACT")
        if ex:
            examples.append(ex)
    
    # Polices (200 exemples)
    for _ in range(200):
        field = random.choice(["police", "no_police", "police_number", "versicherungsschein"])
        value = generate_policy_number()
        ex = create_example(field, value, "POLICY")
        if ex:
            examples.append(ex)
    
    # Adresses suisses complètes (100 exemples)
    for _ in range(100):
        field = random.choice(["adresse", "address", "adresse_complete"])
        street = random.choice(STREETS)
        number = random.randint(1, 200)
        npa = random.randint(1000, 9999)
        city = random.choice(CITIES)
        value = f"{street} {number}, {npa} {city}"
        ex = create_example(field, value, "ADDRESS")
        if ex:
            examples.append(ex)
    
    # Noms suisses (200 exemples)
    for _ in range(200):
        field = random.choice(["nom", "name", "client_nom", "prenom", "nachname", "vorname"])
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        value = f"{first} {last}"
        ex = create_example(field, value, "PERSON")
        if ex:
            examples.append(ex)
    
    return examples


def split_dataset(examples: list[dict], val_ratio: float = 0.1) -> tuple[list[dict], list[dict]]:
    """Sépare en train/val."""
    random.shuffle(examples)
    val_size = int(len(examples) * val_ratio)
    return examples[val_size:], examples[:val_size]


def save_dataset(examples: list[dict], path: Path) -> int:
    """Sauvegarde un dataset en JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    return len(examples)


if __name__ == "__main__":
    print("Génération de données suisses synthétiques...")
    examples = generate_examples()
    print(f"  {len(examples)} exemples générés")
    
    train_examples, val_examples = split_dataset(examples)
    
    train_path = OUT_DIR / "train.jsonl"
    val_path = OUT_DIR / "val.jsonl"
    
    n_train = save_dataset(train_examples, train_path)
    n_val = save_dataset(val_examples, val_path)
    
    print(f"  Train: {n_train} exemples → {train_path}")
    print(f"  Val: {n_val} exemples → {val_path}")
    print("Terminé!")
