"""Génère un dataset synthétique colonnes -> strategy au format EXACT du gateway.

- Réutilise sidecar.column_classifier._value_metadata pour que les métadonnées
  d'entraînement soient identiques à celles produites à l'inférence.
- Sortie : finetune_gliner/data_columns/{train,val}.jsonl
  Chaque ligne : {"user": "<bloc Table/Column/SQL type/Value metadata>",
                  "assistant": "{\"strategy\": ..., \"confidence\": ...}"}
- Le SYSTEM_PROMPT (constant) est ajouté côté script d'entraînement.

Strategies (cf. gateway/column_classifier.py STRATEGY_TO_LABEL) :
  mask_name, mask_email, mask_phone, mask_date, mask_address,
  mask_plate, mask_id, keep, redact
"""
from __future__ import annotations
import json
import random
import sys
from pathlib import Path

# Réutilise la logique de métadonnées du sidecar (source de vérité)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sidecar.column_classifier import _value_metadata  # noqa: E402

random.seed(42)
OUT = Path(__file__).parent / "data_columns"
SQL_TEXT = ["varchar", "text", "char(20)", "varchar(255)"]
SQL_NUM = ["integer", "numeric", "bigint", "decimal(10,2)"]

FIRST = ["David", "Anna", "Marco", "Sophie", "Luca", "Petra", "Jean", "Elena",
         "Thomas", "Chiara", "Hans", "Camille", "Stefan", "Giulia", "Nadia"]
LAST = ["Dupont", "Müller", "Rossi", "Favre", "Bianchi", "Keller", "Moret",
        "Schmid", "Ferrari", "Meier", "Morand", "Brunner", "Conti", "Roux"]
STREETS = ["Rue du Lac", "Bahnhofstrasse", "Via Nassa", "Avenue de la Gare",
           "Chemin des Vignes", "Dorfstrasse", "Place du Marché"]
CITIES = ["Genève", "Zürich", "Lugano", "Lausanne", "Bern", "Basel", "Sion"]


def names():
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def emails():
    return f"{random.choice(FIRST)}.{random.choice(LAST)}@example.ch".lower()


def phones():
    return random.choice([
        f"+41 {random.randint(21,79)} {random.randint(100,999)} {random.randint(10,99)} {random.randint(10,99)}",
        f"0{random.randint(21,79)}{random.randint(1000000,9999999)}",
    ])


def dates():
    return random.choice([
        f"{random.randint(1,28):02d}.{random.randint(1,12):02d}.{random.randint(1950,2024)}",
        f"{random.randint(1950,2024)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
    ])


def addresses():
    return f"{random.choice(STREETS)} {random.randint(1,99)}"


def plates():
    return random.choice([
        f"{random.choice(['GE','VD','ZH','TI','BE','VS'])} {random.randint(1000,999999)}",
        f"{random.choice(['GE','VD','ZH'])}-{random.randint(1000,99999)}",
    ])


def avs():
    return f"756{random.randint(1000000000,9999999999)}"


def ibans():
    return f"CH{random.randint(10,99)}{random.randint(10000,99999)}{random.randint(100000000000,999999999999)}"


def refs():
    return random.choice([f"REF-{random.randint(10000,99999)}", f"{random.randint(1000000,9999999)}",
                          f"CTR{random.randint(100000,999999)}"])


def amounts():
    return random.choice([f"{random.randint(10,99999)}.{random.randint(0,99):02d}", f"{random.randint(1,9999)}"])


def comments():
    return random.choice([
        "Le client a appelé pour modifier son contrat.",
        "Sinistre déclaré, expertise en cours.",
        "Kunde wünscht Rückruf am Montag.",
        "Documenti ricevuti, pratica aperta.",
    ])


# (strategy, [noms de colonnes], générateur de valeur, confidence)
SPECS = [
    ("mask_name", ["nom", "prenom", "nom_client", "name", "vorname", "nachname",
                   "cognome", "nome", "titulaire", "assure", "beneficiaire", "contact"], names, 0.95),
    ("mask_email", ["email", "mail", "courriel", "e_mail", "adresse_email", "email_pro"], emails, 0.97),
    ("mask_phone", ["tel", "telephone", "phone", "mobile", "natel", "no_tel", "fax", "telefon"], phones, 0.93),
    ("mask_date", ["date", "date_naissance", "dob", "geburtsdatum", "data_nascita",
                   "date_sinistre", "date_contrat"], dates, 0.92),
    ("mask_address", ["adresse", "address", "rue", "strasse", "via", "lieu", "localite", "domicile"], addresses, 0.9),
    ("mask_plate", ["plaque", "immatriculation", "kennzeichen", "no_plaque", "targa"], plates, 0.9),
    ("mask_id", ["no_avs", "avs", "ahv", "iban", "no_contrat", "contrat", "police",
                 "no_police", "client_id", "no_client", "dossier"], None, 0.9),
    ("keep", ["montant", "prix", "amount", "betrag", "importo", "quantite", "qty",
              "total", "solde", "taux", "age", "nombre"], amounts, 0.88),
    ("redact", ["commentaire", "notes", "remarque", "description", "diagnostic",
                "motif", "observation", "bemerkung"], comments, 0.8),
]

# id : mélange avs / iban / refs
ID_GENS = [avs, ibans, refs]
# cas ambigus -> confidence basse, on penche vers mask_id (sur-anonymiser = sûr)
AMBIG_COLS = ["numero", "code", "reference", "ref", "no", "num", "identifiant"]


def make_user(table: str, column: str, sql_type: str, values: list[str]) -> str:
    meta = [_value_metadata(v) for v in values]
    return (
        f"Table: {table}\nColumn: {column}\n"
        f"SQL type: {sql_type}\nValue metadata: {json.dumps(meta, ensure_ascii=False)}"
    )


def gen(n_per_spec: int = 700, n_ambig: int = 600) -> list[dict]:
    rows: list[dict] = []
    tables = ["clients", "contrats", "sinistres", "vehicules", "agents", "interventions"]
    for strategy, cols, valgen, conf in SPECS:
        for _ in range(n_per_spec):
            col = random.choice(cols)
            sql_type = random.choice(SQL_NUM if strategy in ("keep",) else SQL_TEXT)
            g = random.choice(ID_GENS) if strategy == "mask_id" else valgen
            values = [g() for _ in range(random.randint(2, 4))]
            user = make_user(random.choice(tables), col, sql_type, values)
            asst = json.dumps({"strategy": strategy, "confidence": round(random.uniform(conf - 0.05, min(conf + 0.04, 0.99)), 2)})
            rows.append({"user": user, "assistant": asst})
    # cas ambigus : noms vagues + valeurs numériques -> mask_id, confidence basse
    for _ in range(n_ambig):
        col = random.choice(AMBIG_COLS)
        values = [random.choice([refs, avs])() for _ in range(random.randint(2, 4))]
        user = make_user(random.choice(tables), col, random.choice(SQL_TEXT), values)
        asst = json.dumps({"strategy": "mask_id", "confidence": round(random.uniform(0.55, 0.68), 2)})
        rows.append({"user": user, "assistant": asst})
    random.shuffle(rows)
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = gen()
    split = int(len(rows) * 0.9)
    (OUT / "train.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows[:split]), encoding="utf-8")
    (OUT / "val.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows[split:]), encoding="utf-8")
    print(f"Total: {len(rows)} | train: {split} | val: {len(rows)-split}")
    print("Exemple:", rows[0]["user"][:120], "->", rows[0]["assistant"])


if __name__ == "__main__":
    main()
